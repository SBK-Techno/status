#!/usr/bin/env python3
"""Build the exact status artifact tree the platform deploy consumes.

The platform owns the artifact layout. ``scripts/deploy/status_artifact.py`` in
SBK-Techno/platform is the validator, and this module loads it instead of
repeating the required names, manifest names, retention ledger, or content-hash
shape. One entry point writes a clean tree containing only the files that
validator publishes: the two pages, their content-hashed site assets, the
published brand assets, both manifests, and the retention ledger with the
retired hashed names it still protects.

The live ``status.json`` is deliberately absent. The publisher writes it beside
the release tree, so a release swap can never replace a fresh heartbeat with
the one that was current when the artifact was built.

Usage:
  artifact_build.py --root DIR --out DIST [--platform DIR]
  artifact_build.py --root DIR --check [--platform DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import platform_contract
import site_build


class ArtifactError(Exception):
    pass


def _brand_assets(root: Path) -> dict[str, bytes]:
    manifest = site_build.load_brand_manifest(root)
    outputs: dict[str, bytes] = {}
    for asset in site_build.published_brand_assets(manifest).values():
        name = asset.get("file")
        digest = asset.get("sha256")
        size = asset.get("bytes")
        if not isinstance(name, str) or not name:
            raise ArtifactError("brand.manifest.json has a published asset without a file")
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ArtifactError(f"published brand asset is missing: {name}")
        data = path.read_bytes()
        if not isinstance(digest, str) or hashlib.sha256(data).hexdigest() != digest:
            raise ArtifactError(f"{name}: digest does not match brand.manifest.json")
        if not isinstance(size, int) or len(data) != size:
            raise ArtifactError(f"{name}: size does not match brand.manifest.json")
        outputs[name] = data
    if not outputs:
        raise ArtifactError("brand.manifest.json publishes no asset")
    return outputs


def _retained(root: Path, contract: platform_contract.ArtifactContract) -> dict[str, bytes]:
    path = root / contract.retention
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"{contract.retention} is not JSON: {exc}") from None
    retired = document.get("retired") if isinstance(document, dict) else None
    if not isinstance(retired, list):
        raise ArtifactError(f"{contract.retention}: retired assets must be a list")
    outputs: dict[str, bytes] = {contract.retention: path.read_bytes()}
    for entry in retired:
        name = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not contract.is_content_hashed(name):
            raise ArtifactError(
                f"{contract.retention}: retired file is not content-addressed: {name!r}"
            )
        asset = root / name
        if not asset.is_file() or asset.is_symlink():
            raise ArtifactError(f"{contract.retention}: retired file is missing: {name}")
        outputs[name] = asset.read_bytes()
    return outputs


def expected_artifact(
    root: Path, contract: platform_contract.ArtifactContract | None = None
) -> dict[str, bytes]:
    """Return every published file, keyed by its artifact-relative path."""
    contract = contract or platform_contract.artifact_contract()
    outputs = site_build.expected_site_outputs(root)
    outputs.update(_brand_assets(root))
    outputs["brand.manifest.json"] = (root / "brand.manifest.json").read_bytes()
    outputs.update(_retained(root, contract))
    required = set(contract.required)
    missing = required - set(outputs)
    if missing:
        raise ArtifactError(
            "the build does not produce the platform-required files: "
            + ", ".join(sorted(missing))
        )
    for name in outputs:
        if name in contract.required or name in contract.manifests:
            continue
        if name == contract.retention:
            continue
        if not contract.is_content_hashed(name):
            raise ArtifactError(f"{name}: published asset is not content-addressed")
    return outputs


def tree_digest(files: dict[str, bytes]) -> str:
    """Digest the canonical path-and-content list, not filesystem metadata."""
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(files[name]).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def write_tree(out: Path, files: dict[str, bytes]) -> None:
    out = out.resolve()
    parent = out.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=parent))
    try:
        for name in sorted(files):
            path = temporary / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[name])
        if out.exists() or out.is_symlink():
            if out.is_symlink() or not out.is_dir():
                raise ArtifactError(f"refusing to replace a non-directory: {out}")
            shutil.rmtree(out)
        temporary.replace(out)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def check(root: Path, platform: Path | None = None) -> tuple[str, str]:
    """Build twice into isolated trees and prove both are identical and valid."""
    contract = platform_contract.artifact_contract(platform)
    first = expected_artifact(root, contract)
    second = expected_artifact(root, contract)
    if first != second:
        differing = sorted(set(first) ^ set(second))
        raise ArtifactError(
            "two builds disagree on the artifact tree"
            + (f": {', '.join(differing[:5])}" if differing else "")
        )
    with tempfile.TemporaryDirectory(prefix="sbk-status-artifact-") as temporary:
        base = Path(temporary)
        for label, files in (("a", first), ("b", second)):
            tree = base / label
            write_tree(tree, files)
            contract.validate(tree)
    return tree_digest(first), f"{len(first)} files"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--platform", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", type=Path, help="write the artifact tree here")
    mode.add_argument(
        "--check",
        action="store_true",
        help="build twice, prove identical output, and validate both trees",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.check:
            digest, detail = check(root, args.platform)
            print(f"artifact reproducible: {digest} ({detail})")
            return 0
        out = args.out
        assert out is not None
        resolved = out.resolve()
        if resolved == root or root not in resolved.parents:
            raise ArtifactError("--out must be a directory below the status checkout")
        contract = platform_contract.artifact_contract(args.platform)
        files = expected_artifact(root, contract)
        write_tree(resolved, files)
        contract.validate(resolved)
        print(f"artifact written: {resolved} ({len(files)} files, {tree_digest(files)})")
        return 0
    except (
        ArtifactError,
        platform_contract.ContractError,
        site_build.BuildError,
        json.JSONDecodeError,
        OSError,
    ) as exc:
        print(f"status artifact: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
