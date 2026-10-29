#!/usr/bin/env python3
"""Consume the status contract owned by SBK-Techno/platform.

The status repository owns the page source, the status document, the build,
and its validation. The platform owns the public edge: the Caddy site block,
the security headers, the cache policy, the artifact layout, the deploy, and
the rollback. This module is the only place the status repository reads those
facts, so no script here declares a second copy.

The platform checkout is a required input, exactly like the brand checkout. It
is resolved from ``--platform``, ``PLATFORM``, or ``.toolchain/platform`` and
fails closed with the exact path when it is missing.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType

ARTIFACT_TOOL = Path("scripts") / "deploy" / "status_artifact.py"
STATUS_LAYOUT = Path("deploy") / "status-layout.env"
NAMING_STANDARD = Path("std") / "names.yaml"
PLATFORM_REPOSITORY = "SBK-Techno/platform"

DOMAIN_LINE = re.compile(r"(?m)^  status:\s*(\S+)\s*$")
DOCUMENT_LINE = re.compile(
    r'(?m)^SBK_PATHS_STATUS_SITE_DOCUMENT="\$\{SBK_PATHS_STATUS_SITE_DOCUMENT:-([^}]+)\}"$'
)


class ContractError(Exception):
    """The pinned platform checkout does not expose the status contract."""


def platform_root(explicit: Path | None = None) -> Path:
    """Resolve the platform checkout and prove the contract files exist."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    elif os.environ.get("PLATFORM"):
        candidates.append(Path(os.environ["PLATFORM"]))
    elif os.environ.get("PLATFORM_DIR"):
        candidates.append(Path(os.environ["PLATFORM_DIR"]))
    else:
        candidates.append(Path(__file__).resolve().parent.parent / ".toolchain" / "platform")
    for candidate in candidates:
        root = candidate.resolve()
        if not root.is_dir():
            raise ContractError(
                f"platform checkout not found: {root}; clone {PLATFORM_REPOSITORY} "
                "to .toolchain/platform or pass --platform"
            )
        for relative in (ARTIFACT_TOOL, STATUS_LAYOUT, NAMING_STANDARD):
            if not (root / relative).is_file():
                raise ContractError(
                    f"{root} has no {relative}; check out {PLATFORM_REPOSITORY} "
                    "at the revision pinned in .github/actions/toolchain/action.yml"
                )
        return root
    raise ContractError("no platform checkout was given")


_MODULES: dict[Path, ModuleType] = {}


def artifact_module(explicit: Path | None = None) -> ModuleType:
    """Load the platform's single artifact contract module."""
    root = platform_root(explicit)
    cached = _MODULES.get(root)
    if cached is not None:
        return cached
    path = root / ARTIFACT_TOOL
    spec = importlib.util.spec_from_file_location("sbk_platform_status_artifact", path)
    if spec is None or spec.loader is None:
        raise ContractError(f"cannot load the status artifact contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULES[root] = module
    return module


@dataclass(frozen=True)
class ArtifactContract:
    root: Path
    required: tuple[str, ...]
    manifests: tuple[str, ...]
    retention: str
    hash_name: re.Pattern[str]

    def is_content_hashed(self, name: str) -> bool:
        return self.hash_name.search(name) is not None

    def validate(self, tree: Path) -> list[str]:
        try:
            return list(artifact_module(self.root).validate(tree.resolve()))
        except Exception as exc:  # the platform module raises ArtifactError
            raise ContractError(f"{tree}: {exc}") from None


def artifact_contract(explicit: Path | None = None) -> ArtifactContract:
    return _artifact_contract(platform_root(explicit))


@lru_cache(maxsize=8)
def _artifact_contract(root: Path) -> ArtifactContract:
    module = artifact_module(root)
    try:
        return ArtifactContract(
            root=root,
            required=tuple(module.REQUIRED),
            manifests=tuple(module.MANIFESTS),
            retention=str(module.RETENTION),
            hash_name=re.compile(module.HASH_NAME),
        )
    except AttributeError as exc:
        raise ContractError(
            f"{root / ARTIFACT_TOOL} no longer publishes the artifact contract: {exc}"
        ) from None


def status_host(explicit: Path | None = None) -> str:
    """Read the public status host from the platform naming standard."""
    root = platform_root(explicit)
    text = (root / NAMING_STANDARD).read_text(encoding="utf-8")
    match = DOMAIN_LINE.search(text)
    if match is None:
        raise ContractError(f"{root / NAMING_STANDARD} declares no status domain")
    host = match.group(1).strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
        raise ContractError(f"invalid status domain in {root / NAMING_STANDARD}: {host!r}")
    return host


def document_name(explicit: Path | None = None) -> str:
    """Read the served status document name from the platform layout projection."""
    root = platform_root(explicit)
    path = root / STATUS_LAYOUT
    text = path.read_text(encoding="utf-8")
    match = DOCUMENT_LINE.search(text)
    if match is None:
        raise ContractError(
            f"{path} does not project SBK_PATHS_STATUS_SITE_DOCUMENT"
        )
    name = Path(match.group(1).strip()).name
    if not name or name in {".", ".."} or "/" in name:
        raise ContractError(f"{path} projects an invalid status document: {name!r}")
    return name


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", type=Path)
    parser.add_argument("--tree", type=Path, help="validate one artifact tree")
    args = parser.parse_args(argv)
    try:
        contract = artifact_contract(args.platform)
        print(f"platform: {contract.root}")
        print(f"required: {', '.join(contract.required)}")
        print(f"manifests: {', '.join(contract.manifests)}")
        print(f"retention: {contract.retention}")
        print(f"status host: {status_host(args.platform)}")
        print(f"document: {document_name(args.platform)}")
        if args.tree is not None:
            files = contract.validate(args.tree)
            print(f"artifact files: {len(files)}")
    except ContractError as exc:
        print(f"platform contract: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
