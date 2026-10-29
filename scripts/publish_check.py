#!/usr/bin/env python3
"""Report what a publish would change and decide whether it must commit.

Run after ``make brand``. The comparison base is the published commit (``HEAD``
by default) and the publish surface is every file a publish may write: the
document, the rendered pages, both generated manifests, the retention ledger,
and the content-hashed brand and site assets. The platform edge, its Caddy
configuration, its cache policy, and its deploy scripts are not part of this
repository.

Exit codes:

* ``0``  nothing to publish: the publish surface matches the base commit;
* ``10`` only the snapshot instant changed: the document and every generated
  output match the base, and re-rendering the same document at the base
  commit's instant reproduces the base page byte for byte. The publish adds no
  information, so it must not create a commit;
* ``20`` drift that must be committed: the document or a generated output
  differs from the base;
* ``2``  the site build or the asset retention check fails, so no decision can
  be made.

``--decision-file`` writes ``changed``, ``reason``, and ``exit_code`` in the
GitHub Actions output format and exits ``0`` for every decision except ``2``.
The publish workflow branches on ``changed``; it does not re-implement these
codes in YAML.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import git_environment
import prune_assets
import site_build

CLEAN = "clean"
TIMESTAMP_ONLY = "timestamp-only"
CHANGED = "changed"
BLOCKED = "blocked"

EXIT_CODES = {
    CLEAN: 0,
    TIMESTAMP_ONLY: 10,
    CHANGED: 20,
    BLOCKED: 2,
}

# Files every publish may write in addition to the content-hashed assets.
INDEX_FILES = (
    "status.json",
    "index.html",
    "404.html",
    site_build.SITE_MANIFEST,
    "brand.manifest.json",
    prune_assets.LEDGER,
)
GIT_TIMEOUT = 60


@dataclass(frozen=True)
class Report:
    decision: str
    base: str
    status_note: str
    changed_paths: tuple[str, ...]
    snapshot_from: str | None
    snapshot_to: str | None
    timestamp_only: bool
    build_errors: tuple[str, ...]
    retention_errors: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.decision]

    @property
    def changed(self) -> bool:
        return self.decision == CHANGED

    @property
    def status_changed(self) -> bool:
        return "status.json" in self.changed_paths


def decide(
    *,
    build_errors: tuple[str, ...],
    retention_errors: tuple[str, ...],
    changed_paths: tuple[str, ...],
    timestamp_only: bool,
) -> str:
    """The single no-op decision: blocked, clean, timestamp-only, or changed."""
    if build_errors or retention_errors:
        return BLOCKED
    if not changed_paths:
        return CLEAN
    if timestamp_only:
        return TIMESTAMP_ONLY
    return CHANGED


def is_publish_path(name: str) -> bool:
    """Whether a repository path is document input or generated output."""
    if name in INDEX_FILES:
        return True
    parts = name.split("/")
    if len(parts) == 1:
        return prune_assets.is_generated_asset(name)
    return (
        len(parts) == 2
        and parts[0] == "assets"
        and prune_assets.is_generated_asset(parts[1])
    )


def git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return git_environment.run_git(root, *args, timeout=GIT_TIMEOUT)


def resolve_base(root: Path, base: str) -> str | None:
    try:
        result = git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8").strip()


def commit_paths(root: Path, commit: str) -> set[str]:
    result = git(root, "ls-tree", "-r", "--name-only", "-z", commit)
    if result.returncode != 0:
        raise OSError(result.stderr.decode("utf-8", errors="replace").strip())
    return {
        name
        for name in result.stdout.decode("utf-8").split("\0")
        if name and is_publish_path(name)
    }


def worktree_paths(root: Path) -> set[str]:
    """Tracked and untracked (not ignored) files, including deletions."""
    result = git(root, "ls-files", "-z", "-co", "--exclude-standard")
    if result.returncode != 0:
        raise OSError(result.stderr.decode("utf-8", errors="replace").strip())
    return {
        name
        for name in result.stdout.decode("utf-8").split("\0")
        if name and is_publish_path(name)
    }


def commit_blob(root: Path, commit: str, name: str) -> bytes | None:
    result = git(root, "show", f"{commit}:{name}")
    return result.stdout if result.returncode == 0 else None


def disk_blob(root: Path, name: str) -> bytes | None:
    path = root / name
    return path.read_bytes() if path.is_file() else None


def describe_status(before: bytes | None, after: bytes | None) -> str:
    if before == after:
        return "bytes unchanged"
    if before is None:
        return "bytes changed; the base commit has no status.json"
    if after is None:
        return "bytes changed; status.json is missing"
    note = "bytes changed"
    try:
        old = json.loads(before)
        new = json.loads(after)
    except json.JSONDecodeError:
        return note + "; the document is not valid JSON"
    for document in (old, new):
        if isinstance(document, dict):
            document.pop("generated_at", None)
            document.pop("expires_at", None)
    if old == new:
        note += "; the heartbeat timestamps are the only semantic change"
    return note


def snapshot_only(
    root: Path,
    commit: str,
    base_index: bytes | None,
    changed_paths: tuple[str, ...],
) -> bool:
    """True when the base page is reproduced by applying its own instant.

    ``make brand`` is deterministic for an unchanged document: the previous
    snapshot instant is reused. If a timestamp was regenerated anyway, the
    page differs from the base only because the freshness rules were applied
    at a different instant. Re-rendering the same document and assets at the
    base instant must reproduce the base page exactly.
    """
    if set(changed_paths) != {"index.html"} or base_index is None:
        return False
    instant = site_build.snapshot_at_from_page(
        base_index.decode("utf-8", errors="replace")
    )
    if instant is None:
        return False
    try:
        expected = site_build.expected_site_outputs(root, snapshot_at=instant)
    except (site_build.BuildError, OSError, json.JSONDecodeError):
        return False
    return all(
        commit_blob(root, commit, name) == data
        for name, data in expected.items()
    )


def inspect(root: Path, base: str = "HEAD") -> Report:
    commit = resolve_base(root, base)
    if commit is None:
        return Report(
            decision=BLOCKED,
            base=base,
            status_note="not compared",
            changed_paths=(),
            snapshot_from=None,
            snapshot_to=None,
            timestamp_only=False,
            build_errors=(f"base revision {base!r} cannot be read from git",),
            retention_errors=(),
        )

    try:
        build_errors = tuple(site_build.check_site(root))
    except (site_build.BuildError, OSError, json.JSONDecodeError) as exc:
        build_errors = (str(exc),)
    try:
        retention_errors = tuple(prune_assets.retention_errors(root))
    except (site_build.BuildError, OSError, json.JSONDecodeError) as exc:
        retention_errors = (f"retention check could not run: {exc}",)

    try:
        paths = commit_paths(root, commit) | worktree_paths(root)
        before_status = commit_blob(root, commit, "status.json")
        after_status = disk_blob(root, "status.json")
        changed_paths = tuple(
            name
            for name in sorted(paths)
            if commit_blob(root, commit, name) != disk_blob(root, name)
        )
        base_index = commit_blob(root, commit, "index.html")
        current_index = disk_blob(root, "index.html")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Report(
            decision=BLOCKED,
            base=base,
            status_note="not compared",
            changed_paths=(),
            snapshot_from=None,
            snapshot_to=None,
            timestamp_only=False,
            build_errors=(),
            retention_errors=(f"publish surface could not be read: {exc}",),
        )

    timestamp_only = snapshot_only(root, commit, base_index, changed_paths)
    snapshot_from = (
        site_build.snapshot_at_from_page(
            base_index.decode("utf-8", errors="replace")
        )
        if base_index is not None
        else None
    )
    snapshot_to = (
        site_build.snapshot_at_from_page(
            current_index.decode("utf-8", errors="replace")
        )
        if current_index is not None
        else None
    )
    note = describe_status(before_status, after_status)
    decision = decide(
        build_errors=build_errors,
        retention_errors=retention_errors,
        changed_paths=changed_paths,
        timestamp_only=timestamp_only,
    )
    return Report(
        decision=decision,
        base=base,
        status_note=note,
        changed_paths=changed_paths,
        snapshot_from=snapshot_from,
        snapshot_to=snapshot_to,
        timestamp_only=timestamp_only,
        build_errors=build_errors,
        retention_errors=retention_errors,
    )


def render(report: Report) -> str:
    lines = [f"publish check against {report.base}"]
    lines.append(f"  status.json: {report.status_note}")
    outputs = [name for name in report.changed_paths if name != "status.json"]
    if not outputs:
        lines.append("  generated outputs: unchanged")
    else:
        lines.append("  generated outputs: changed")
        for name in outputs:
            suffix = " (snapshot instant only)" if report.timestamp_only else ""
            lines.append(f"    - {name}{suffix}")
    if report.timestamp_only:
        lines.append(
            "  snapshot: "
            f"{report.snapshot_from or 'missing'} -> {report.snapshot_to or 'missing'}"
        )
    if report.build_errors:
        lines.append("  site build: failed")
        lines.extend(f"    - {error}" for error in report.build_errors)
    else:
        lines.append("  site build: ok")
    if report.retention_errors:
        lines.append("  asset retention: failed")
        lines.extend(f"    - {error}" for error in report.retention_errors)
    else:
        lines.append("  asset retention: ok")

    if report.decision == CLEAN:
        lines.append("  decision: nothing to publish (exit 0)")
    elif report.decision == TIMESTAMP_ONLY:
        lines.append(
            "  decision: only the snapshot instant changed; do not publish "
            "(exit 10)"
        )
    elif report.decision == CHANGED:
        lines.append("  decision: publish (exit 20)")
    else:
        lines.append("  decision: blocked by the gate above (exit 2)")
    return "\n".join(lines)


def write_decision(path: Path, report: Report) -> None:
    lines = [
        f"changed={'true' if report.changed else 'false'}",
        f"reason={report.decision}",
        f"exit_code={report.exit_code}",
    ]
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--base", default="HEAD", help="published commit to compare")
    parser.add_argument(
        "--decision-file",
        type=Path,
        help="append GitHub Actions outputs instead of using exit codes",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = inspect(root, args.base)
    print(render(report))
    if args.decision_file is not None:
        write_decision(args.decision_file, report)
        return EXIT_CODES[BLOCKED] if report.decision == BLOCKED else 0
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
