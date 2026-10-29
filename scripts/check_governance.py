#!/usr/bin/env python3
"""Verify the generated governance files against the pinned platform.

This repository does not carry a copy of `cmd/genguides`. The generator lives
in SBK-Techno/platform, so this check runs that source from the checkout the
composite action pins and compares the rendered files with the committed tree.

The pin has one owner: the `ref` of the platform checkout in
`.github/actions/toolchain/action.yml`. A checkout at another revision, or a
dirty checkout, is refused instead of silently changing the generator.

Usage: python3 scripts/check_governance.py [--platform .toolchain/platform]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import git_environment

ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / ".github" / "actions" / "toolchain" / "action.yml"
PLATFORM_REPOSITORY = "SBK-Techno/platform"
REPOSITORY_NAME = "status"
GENERATOR_TIMEOUT = 300

PLATFORM_PIN = re.compile(
    r"(?ms)^[ \t]+repository:[ \t]*" + re.escape(PLATFORM_REPOSITORY) + r"[ \t]*\n"
    r"[ \t]+ref:[ \t]*(\S+)[ \t]*$"
)


def fail(message: str, code: int = 2) -> NoReturn:
    print(f"governance-check: {message}", file=sys.stderr)
    raise SystemExit(code)


def run(command: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        fail(f"cannot run {command[0]}: {exc}")
    except subprocess.TimeoutExpired:
        fail(f"{' '.join(command)} timed out after {timeout}s")


def pinned_platform() -> str:
    try:
        text = ACTION.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"cannot read {ACTION}: {exc}")
    match = PLATFORM_PIN.search(text)
    if match is None:
        fail(f"{ACTION} does not check out {PLATFORM_REPOSITORY}")
    pin = match.group(1)
    if re.fullmatch(r"[0-9a-f]{40}", pin) is None:
        fail(f"{ACTION} must pin {PLATFORM_REPOSITORY} to a full 40-hex commit SHA")
    return pin


def verify_checkout(platform: Path, pin: str) -> None:
    if not platform.is_dir():
        fail(
            f"platform checkout not found: {platform}; clone {PLATFORM_REPOSITORY} "
            "to .toolchain/platform or pass PLATFORM=/path/to/checkout"
        )
    if not (platform / "cmd" / "genguides").is_dir():
        fail(f"{platform} has no cmd/genguides; check out the pinned platform revision")
    inside = git_environment.run_git(
        platform, "rev-parse", "--is-inside-work-tree", text=True
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        fail(f"{platform} is not a Git work tree; check out {PLATFORM_REPOSITORY} at {pin}")
    head = git_environment.run_git(platform, "rev-parse", "HEAD", text=True)
    if head.returncode != 0:
        fail(f"cannot read the platform revision: {head.stderr.strip()}")
    found = head.stdout.strip()
    if found != pin:
        fail(f"platform checkout is at {found}, but the action pin is {pin}")
    status = git_environment.run_git(
        platform,
        "status",
        "--porcelain",
        "--untracked-files=all",
        text=True,
    )
    if status.returncode != 0:
        fail(f"cannot read the platform status: {status.stderr.strip()}")
    if status.stdout.strip():
        fail("platform checkout has uncommitted changes; the check only trusts the pinned revision")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        type=Path,
        default=ROOT / ".toolchain" / "platform",
        help="pinned SBK-Techno/platform checkout",
    )
    args = parser.parse_args()

    platform = args.platform.resolve()
    pin = pinned_platform()
    verify_checkout(platform, pin)

    result = run(
        [
            os.environ.get("GO", "go"),
            "run",
            "./cmd/genguides",
            "-root",
            ".",
            "-init",
            "-name",
            REPOSITORY_NAME,
            "-dest",
            str(ROOT),
            "-check",
        ],
        platform,
        GENERATOR_TIMEOUT,
    )
    output = (result.stdout + result.stderr).strip()
    if output:
        print(output)
    if result.returncode != 0:
        fail(f"generated governance files disagree with {PLATFORM_REPOSITORY} at {pin}", 1)
    print(f"ok governance: generated files match {PLATFORM_REPOSITORY} at {pin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
