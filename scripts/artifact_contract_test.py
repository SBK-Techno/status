#!/usr/bin/env python3
"""Contract tests for the one-owner split and the published artifact.

The status repository owns content, the status document, the build, and its
validation. SBK-Techno/platform owns the edge: the Caddy site block, security
headers, cache policy, artifact layout, deploy, and rollback. These tests fail
when this repository reintroduces a second copy of an edge fact, a GitHub Pages
artifact, or a nondeterministic artifact tree.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import artifact_build
import platform_contract
import site_build

ROOT = SCRIPTS.parent
DOC = ROOT / "docs" / "OPERATIONS.md"

SKIP_DIRS = {".git", ".toolchain", "dist", "__pycache__"}
FORBIDDEN_NAMES = {
    "Caddyfile",
    "cache-policy.json",
    "_headers",
    "netlify.toml",
    "vercel.json",
    "staticwebapp.config.json",
    "firebase.json",
    "CNAME",
    ".nojekyll",
}
CONFIG_SUFFIXES = {".json", ".yml", ".yaml", ".toml", ".env", ".conf"}
EDGE_HEADER = re.compile(
    r"(?i)\b(Content-Security-Policy|Strict-Transport-Security|X-Frame-Options|"
    r"Cache-Control|immutable|no-store)\b"
)
EDGE_CONFIG = re.compile(r"(?m)^\s*(file_server|reverse_proxy|tls\s*\{)\s*$")
DEPLOY_COMMAND = re.compile(r"(?m)^\s*(ssh|scp|rsync|caddy|systemctl|sudo)\b")
PAGES_DEPLOY = re.compile(
    r"(?i)(actions/deploy-pages|pages-build-deployment|github-pages|gh-pages|"
    r"pages:\s*write|environment:\s*github-pages)"
)


def repository_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return sorted(files)


class EdgeOwnershipTest(unittest.TestCase):
    def test_the_status_repository_has_no_edge_configuration(self) -> None:
        found = [
            path.relative_to(ROOT).as_posix()
            for path in repository_files()
            if path.name in FORBIDDEN_NAMES
        ]
        self.assertEqual(
            found,
            [],
            "the platform edge owns the Caddy block, cache policy, custom "
            "domain, and Pages bypass; remove these second owners: "
            + ", ".join(found),
        )

    def test_no_machine_configuration_declares_edge_headers(self) -> None:
        offenders: list[str] = []
        for path in repository_files():
            relative = path.relative_to(ROOT)
            if path.suffix not in CONFIG_SUFFIXES:
                continue
            if path.name in {"status.json", "site.manifest.json", "brand.manifest.json", "asset-retention.json"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if EDGE_HEADER.search(text) or EDGE_CONFIG.search(text):
                offenders.append(relative.as_posix())
        self.assertEqual(
            offenders,
            [],
            "edge headers and the cache policy belong to the platform Caddy "
            "configuration, not this repository: " + ", ".join(offenders),
        )

    def test_no_deploy_or_rollback_script_lives_here(self) -> None:
        offenders: list[str] = []
        for path in repository_files():
            if path.suffix not in {".sh", ".bash"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if DEPLOY_COMMAND.search(text):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(
            offenders,
            [],
            "deploy and rollback belong to SBK-Techno/platform: "
            + ", ".join(offenders),
        )

    def test_no_github_pages_deployment_remains(self) -> None:
        workflows = ROOT / ".github" / "workflows"
        offenders: list[str] = []
        for path in sorted(workflows.glob("*.y*ml")):
            if "page" in path.name.lower() or PAGES_DEPLOY.search(
                path.read_text(encoding="utf-8")
            ):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(
            offenders,
            [],
            "the platform edge is the only deploy owner; Pages is not a "
            "rollback path: " + ", ".join(offenders),
        )

    def test_the_platform_contract_is_the_only_layout_owner(self) -> None:
        contract = platform_contract.artifact_contract()
        self.assertTrue(contract.root.is_dir())
        self.assertIn("index.html", contract.required)
        self.assertIn("404.html", contract.required)
        self.assertIn("site.manifest.json", contract.manifests)
        self.assertIn("brand.manifest.json", contract.manifests)
        self.assertEqual(contract.retention, "asset-retention.json")
        self.assertEqual(platform_contract.document_name(), "status.json")

    def test_the_displayed_host_comes_from_the_naming_standard(self) -> None:
        host = platform_contract.status_host()
        for relative in ("templates/index.html", "templates/404.html"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(host, text, f"{relative} hardcodes the status host")
            self.assertIn("{{status:host}}", text)


class ArtifactContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = platform_contract.artifact_contract()
        cls.files = artifact_build.expected_artifact(ROOT, cls.contract)

    def test_layout_is_exactly_the_platform_published_set(self) -> None:
        self.assertTrue(set(self.contract.required) <= set(self.files))
        self.assertNotIn(platform_contract.document_name(), self.files)
        self.assertIn(self.contract.retention, self.files)
        for name in self.contract.manifests:
            self.assertIn(name, self.files)

    def test_every_non_manifest_file_carries_a_platform_hash(self) -> None:
        fixed = set(self.contract.required) | set(self.contract.manifests)
        fixed.add(self.contract.retention)
        for name in sorted(self.files):
            if name in fixed:
                continue
            with self.subTest(name=name):
                self.assertTrue(
                    self.contract.is_content_hashed(name),
                    f"{name} is not content-addressed by the platform contract",
                )

    def test_every_page_reference_is_in_the_artifact(self) -> None:
        reference = re.compile(r'(?:src|href)="(/[^"?#]+)"')
        for page in ("index.html", "404.html"):
            text = self.files[page].decode("utf-8")
            for target in reference.findall(text):
                if target in {"/", "/404.html", "/status.json"}:
                    continue
                relative = target.lstrip("/")
                with self.subTest(page=page, target=target):
                    self.assertIn(relative, self.files)

    def test_two_builds_are_byte_identical_and_platform_valid(self) -> None:
        digest, detail = artifact_build.check(ROOT, self.contract.root)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertIn("files", detail)
        with self.subTest("same digest twice"):
            again, _ = artifact_build.check(ROOT, self.contract.root)
            self.assertEqual(digest, again)

    def test_retention_ledger_matches_the_artifact(self) -> None:
        ledger = json.loads(self.files[self.contract.retention])
        self.assertEqual(ledger["version"], 1)
        for entry in ledger["retired"]:
            self.assertTrue(self.contract.is_content_hashed(entry["file"]))
            self.assertIn(entry["file"], self.files)


class OperationalDocTest(unittest.TestCase):
    def test_the_relocated_operational_facts_exist(self) -> None:
        self.assertTrue(DOC.is_file(), "docs/OPERATIONS.md is missing")
        text = DOC.read_text(encoding="utf-8")
        lowered = text.lower()
        for fact in (
            "SBK-Techno/platform",
            "artifact",
            "asset-retention.json",
            "content hash",
            "snapshot",
            "contrast",
            "generated README",
        ):
            with self.subTest(fact=fact):
                self.assertIn(fact.lower(), lowered)

    def test_agents_stays_generated_and_the_operations_doc_is_local(self) -> None:
        """AGENTS.md is platform-generated and byte-checked by the gate.

        The link from its status section belongs to the platform template, so a
        hand edit here would fail ``make governance-check``. The repository
        keeps the operational facts in docs/OPERATIONS.md and documents the
        template obligation there instead of creating a second owner.
        """
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Generated by cmd/genguides", agents)
        self.assertIn("DO NOT EDIT", agents)
        self.assertIn("SBK-Techno/platform", agents)
        operations = DOC.read_text(encoding="utf-8")
        self.assertIn("`AGENTS.md`", operations)
        self.assertIn("generated from the platform template", operations)


if __name__ == "__main__":
    unittest.main()
