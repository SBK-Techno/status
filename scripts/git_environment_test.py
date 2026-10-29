#!/usr/bin/env python3
"""Prove a pre-push GIT_DIR cannot redirect the status checks.

Git exports repository-local variables to hooks. The publish fixtures commit
into temporary repositories with ``git -C``, and the governance check reads the
platform checkout the same way, so an inherited GIT_DIR used to point both at
the status repository. This test creates a decoy repository, exports its
GIT_DIR and GIT_WORK_TREE, and requires the real fixtures and the governance
check to pass. The bypass assertion shows that an unsanitized child really does
read the decoy, so the test would fail without the helper.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import git_environment


def make_repository(path: Path) -> None:
    # The test may itself run from a hook. Create fixtures with the hook
    # variables removed so a bypassed helper cannot commit into the real
    # repository; the decoy is exported explicitly for the assertions.
    with mock.patch.dict(
        os.environ, git_environment.sanitized_environment(), clear=True
    ):
        path.mkdir(parents=True)
        git_environment.run_git(path, "init", "-q", check=True)
        git_environment.run_git(
            path,
            "-c",
            "user.name=git environment fixture",
            "-c",
            "user.email=git-environment@example.invalid",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "fixture",
            check=True,
        )


def decoy_environment(decoy: Path) -> dict[str, str]:
    environment = git_environment.sanitized_environment()
    environment["GIT_DIR"] = str(decoy / ".git")
    environment["GIT_WORK_TREE"] = str(decoy)
    return environment


class GitEnvironmentTest(unittest.TestCase):
    def test_run_git_ignores_an_exported_repository(self) -> None:
        with tempfile.TemporaryDirectory(prefix="status-git-env-") as temporary:
            base = Path(temporary)
            target = base / "target"
            decoy = base / "decoy"
            make_repository(target)
            make_repository(decoy)
            environment = decoy_environment(decoy)

            # The control: a direct child with the hook environment reads the
            # decoy, so the fixture below would fail without the helper.
            bypassed = subprocess.run(
                ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(bypassed.returncode, 0, bypassed.stderr)
            self.assertEqual(
                Path(bypassed.stdout.strip()).resolve(), decoy.resolve()
            )

            with mock.patch.dict(os.environ, environment, clear=True):
                result = git_environment.run_git(
                    target, "rev-parse", "--show-toplevel", text=True
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()).resolve(), target.resolve())

    def test_publish_fixtures_ignore_an_exported_git_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="status-git-env-") as temporary:
            decoy = Path(temporary) / "decoy"
            make_repository(decoy)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "scripts",
                    "-p",
                    "publish_check_test.py",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
                env=decoy_environment(decoy),
            )
            self.assertEqual(
                result.returncode,
                0,
                f"publish fixtures read the decoy repository:\n"
                f"{result.stdout}\n{result.stderr}",
            )

    def test_governance_check_ignores_an_exported_git_directory(self) -> None:
        platform = Path(
            os.environ.get("PLATFORM_DIR")
            or os.environ.get("PLATFORM")
            or ROOT / ".toolchain" / "platform"
        ).resolve()
        if not platform.is_dir():
            self.skipTest(f"platform checkout not found: {platform}")
        with tempfile.TemporaryDirectory(prefix="status-git-env-") as temporary:
            decoy = Path(temporary) / "decoy"
            make_repository(decoy)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "check_governance.py"),
                    "--platform",
                    str(platform),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=600,
                env=decoy_environment(decoy),
            )
            self.assertEqual(
                result.returncode,
                0,
                f"governance check read the decoy repository:\n"
                f"{result.stdout}\n{result.stderr}",
            )


if __name__ == "__main__":
    unittest.main()
