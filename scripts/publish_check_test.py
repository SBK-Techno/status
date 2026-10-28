#!/usr/bin/env python3
"""Fixtures for the publish decision: clean, timestamp-only, changed, blocked."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import prune_assets
import publish_check
import site_build
import status_snapshot

ROOT = SCRIPTS.parent
NON_SITE = {".git", ".github", ".toolchain", "scripts", "__pycache__"}


class Fixture:
    """A committed copy of the site, so ``publish-check`` sees real git history."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="status-publish-")
        self.root = Path(self._temporary.name)
        for item in ROOT.iterdir():
            if item.name in NON_SITE:
                continue
            target = self.root / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copyfile(item, target)
        self.git("init", "-q")
        self.git("add", "-A")
        self.git(
            "-c",
            "user.name=publish fixture",
            "-c",
            "user.email=publish@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        )

    def close(self) -> None:
        self._temporary.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def document(self) -> dict:
        return json.loads((self.root / "status.json").read_text(encoding="utf-8"))

    def write_document(self, document: object) -> None:
        (self.root / "status.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        errors = site_build.write_site(self.root)
        if errors:
            raise AssertionError("; ".join(errors))


@contextlib.contextmanager
def site_fixture():
    fixture = Fixture()
    try:
        yield fixture
    finally:
        fixture.close()


def add_heartbeat(document: dict) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    document["generated_at"] = status_snapshot.iso(now)
    document["expires_at"] = status_snapshot.iso(now + timedelta(minutes=10))


def mutate_timestamp_only(fixture: Fixture) -> None:
    errors = site_build.write_site(fixture.root, snapshot_at="2031-01-02T03:04:05Z")
    if errors:
        raise AssertionError("; ".join(errors))


def mutate_component(fixture: Fixture) -> None:
    document = fixture.document()
    add_heartbeat(document)
    document["overall"] = "degraded"
    document["components"][0]["status"] = "degraded"
    document["components"][0]["detail_fr"] = "Le portail répond lentement."
    fixture.write_document(document)


def mutate_incident(fixture: Fixture) -> None:
    document = fixture.document()
    add_heartbeat(document)
    started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    document["incidents"] = [
        {
            "id": "inc_fixture",
            "status": "investigating",
            "title_fr": "Latence élevée sur le portail",
            "title_en": "Elevated latency on the portal",
            "started_at": status_snapshot.iso(started),
            "updated_at": status_snapshot.iso(started),
            "summary_fr": "Nous enquêtons.",
            "summary_en": "We are investigating.",
        }
    ]
    fixture.write_document(document)


def mutate_generated_asset(fixture: Fixture) -> None:
    """Change a site source and rebuild exactly as ``make brand`` does."""
    stylesheet = fixture.root / "styles.css"
    stylesheet.write_text(
        stylesheet.read_text(encoding="utf-8") + "\n/* publish fixture */\n",
        encoding="utf-8",
    )
    errors = site_build.write_site(fixture.root)
    if errors:
        raise AssertionError("; ".join(errors))
    errors = prune_assets.prune(fixture.root)
    if errors:
        raise AssertionError("; ".join(errors))


def mutate_unrecorded_asset(fixture: Fixture) -> None:
    (fixture.root / "assets" / "legacy.0123456789ab.css").write_text(
        "/* retired without a ledger entry */\n", encoding="utf-8"
    )


def mutate_stale_page(fixture: Fixture) -> None:
    page = fixture.root / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace("</body>", "<!-- drifted -->\n</body>"),
        encoding="utf-8",
    )


def run_cli(fixture: Fixture, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "publish_check.py"),
            "--root",
            str(fixture.root),
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


class PublishCheckTest(unittest.TestCase):
    def test_decision_contract(self) -> None:
        cases = [
            ("identical bytes", lambda fixture: None, publish_check.CLEAN, 0, False),
            (
                "timestamp only",
                mutate_timestamp_only,
                publish_check.TIMESTAMP_ONLY,
                10,
                False,
            ),
            ("changed component", mutate_component, publish_check.CHANGED, 20, True),
            ("changed incident", mutate_incident, publish_check.CHANGED, 20, True),
            (
                "generated asset drift",
                mutate_generated_asset,
                publish_check.CHANGED,
                20,
                True,
            ),
        ]
        for name, mutate, decision, code, changed in cases:
            with self.subTest(name=name), site_fixture() as fixture:
                mutate(fixture)
                report = publish_check.inspect(fixture.root)
                self.assertEqual(report.decision, decision)
                self.assertEqual(report.exit_code, code)
                self.assertEqual(report.changed, changed)
                result = run_cli(fixture)
                self.assertEqual(
                    result.returncode,
                    code,
                    f"unexpected exit for {name}: {result.stdout}{result.stderr}",
                )

    def test_timestamp_only_is_the_snapshot_instant_alone(self) -> None:
        with site_fixture() as fixture:
            mutate_timestamp_only(fixture)
            report = publish_check.inspect(fixture.root)
            self.assertEqual(report.changed_paths, ("index.html",))
            self.assertTrue(report.timestamp_only)
            self.assertFalse(report.status_changed)
            self.assertNotEqual(report.snapshot_from, report.snapshot_to)

    def test_changed_component_changes_the_document_and_page(self) -> None:
        with site_fixture() as fixture:
            mutate_component(fixture)
            report = publish_check.inspect(fixture.root)
            self.assertTrue(report.status_changed)
            self.assertIn("status.json", report.changed_paths)
            self.assertIn("index.html", report.changed_paths)
            self.assertFalse(report.timestamp_only)

    def test_generated_asset_drift_reports_asset_paths(self) -> None:
        with site_fixture() as fixture:
            mutate_generated_asset(fixture)
            report = publish_check.inspect(fixture.root)
            assets = [name for name in report.changed_paths if name.startswith("assets/")]
            self.assertTrue(assets, report.changed_paths)
            self.assertIn("site.manifest.json", report.changed_paths)

    def test_unrecorded_generated_asset_blocks_the_publish(self) -> None:
        with site_fixture() as fixture:
            mutate_unrecorded_asset(fixture)
            report = publish_check.inspect(fixture.root)
            self.assertEqual(report.decision, publish_check.BLOCKED)
            self.assertEqual(report.exit_code, 2)
            self.assertFalse(report.changed)
            self.assertIn("legacy.0123456789ab.css", " ".join(report.retention_errors))
            self.assertEqual(run_cli(fixture).returncode, 2)

    def test_stale_page_blocks_the_publish(self) -> None:
        with site_fixture() as fixture:
            mutate_stale_page(fixture)
            report = publish_check.inspect(fixture.root)
            self.assertEqual(report.decision, publish_check.BLOCKED)
            self.assertIn("index.html", " ".join(report.build_errors))

    def test_decision_file_reports_the_same_decision_without_failing(self) -> None:
        with site_fixture() as fixture:
            output = fixture.root / "github-output"
            result = run_cli(fixture, "--decision-file", str(output))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "changed=false\nreason=clean\nexit_code=0\n",
            )

            mutate_component(fixture)
            output.unlink()
            result = run_cli(fixture, "--decision-file", str(output))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "changed=true\nreason=changed\nexit_code=20\n",
            )

            mutate_unrecorded_asset(fixture)
            output.unlink()
            result = run_cli(fixture, "--decision-file", str(output))
            self.assertEqual(result.returncode, 2)
            self.assertIn("changed=false", output.read_text(encoding="utf-8"))

    def test_unreadable_base_is_blocked(self) -> None:
        with site_fixture() as fixture:
            report = publish_check.inspect(fixture.root, base="does-not-exist")
            self.assertEqual(report.decision, publish_check.BLOCKED)
            self.assertIn("cannot be read", " ".join(report.build_errors))


if __name__ == "__main__":
    unittest.main()
