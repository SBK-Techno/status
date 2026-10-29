"""Canonical sanitization of Git's repository-local environment.

Git exports repository-location variables to hooks and child processes. Any
`git -C <other checkout>` then resolves against the repository that exported
them instead of the path it was given, so a pre-push fixture would commit into
the pushing repository.

The list is Git's `rev-parse --local-env-vars` set, plus the discovery and
quarantine variables it omits, plus the dynamic `GIT_CONFIG_KEY_<n>` and
`GIT_CONFIG_VALUE_<n>` entries. Nothing else is removed: PATH, GO, PLATFORM,
BRAND, proxies, and every other inherited variable reach the child intact.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

GIT_REPOSITORY_VARIABLES = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_QUARANTINE_PATH",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
)
GIT_CONFIG_ENTRY_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def sanitized_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a copy of the environment without repository-local Git state."""
    environment = dict(os.environ if environ is None else environ)
    for name in GIT_REPOSITORY_VARIABLES:
        environment.pop(name, None)
    for name in tuple(environment):
        if name.startswith(GIT_CONFIG_ENTRY_PREFIXES):
            del environment[name]
    return environment


def run_git(
    root: Path,
    *args: str,
    check: bool = False,
    text: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run `git -C root` with the repository-local Git environment removed."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=text,
        timeout=timeout,
        env=sanitized_environment(),
    )
