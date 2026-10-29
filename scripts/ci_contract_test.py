#!/usr/bin/env python3
"""Contract tests for the validate-only gate and the local publication split.

The Makefile owns the list of checks. The single workflow may prepare the
pinned toolchain and invoke the gate, but it must not keep a private list or
inline a copy of a check. These tests parse the real files, so the contract
cannot drift silently.

The platform host publishes the live status document and its signed receipt
locally. This repository validates the page source, the document, and the
build; it must never grow a dispatch workflow, a repository write, a GitHub
token, or an HMAC verification path of its own.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
OPERATIONS = ROOT / "docs" / "OPERATIONS.md"
WORKFLOW_DIR = ROOT / ".github" / "workflows"
VALIDATE_WORKFLOW = WORKFLOW_DIR / "validate.yml"
TOOLCHAIN_ACTION = ROOT / ".github" / "actions" / "toolchain" / "action.yml"
SCRIPT_DIR = ROOT / "scripts"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
ACTION_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
STEP = re.compile(r"(?m)^( +)- name:\s*(.+?)\s*$")

# The publication path this repository must not carry. Each value is assembled
# from fragments so this test file does not match its own scan.
DEAD_REFERENCES = tuple(
    "".join(parts)
    for parts in (
        ("status-", "dispatch-token"),
        ("status-", "heartbeat-hmac"),
        ("STATUS_HEARTBEAT_", "HMAC"),
        ("heartbeat_", "signature"),
        ("verify_", "heartbeat"),
        ("publish_", "check"),
        ("publish", "-check"),
        ("publish", ".yml"),
        ("GITHUB_", "TOKEN"),
        ("GH_", "TOKEN"),
        ("contents: ", "write"),
        ("actions: ", "write"),
        ("git ", "push"),
    )
)
DEAD_SCRIPTS = tuple(
    "".join(parts) + ".py"
    for parts in (
        ("publish_", "check"),
        ("publish_", "check_test"),
        ("verify_", "heartbeat"),
        ("heartbeat_", "signature"),
        ("heartbeat_", "signature_test"),
    )
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def make_variable(text: str, name: str) -> str:
    match = re.search(
        rf"(?m)^{re.escape(name)}[ \t]*(?::=|\?=)[ \t]*(.*?)[ \t]*$", text
    )
    if match is None:
        raise AssertionError(f"Makefile does not define {name}")
    return match.group(1)


def make_recipe(text: str, target: str) -> str:
    match = re.search(
        rf"(?m)^{re.escape(target)}:[^\n]*(?:\n[ \t][^\n]*)*", text
    )
    if match is None:
        raise AssertionError(f"Makefile does not define the {target} target")
    return match.group(0)


def steps(text: str) -> list[tuple[str, str, int]]:
    """Return (name, body, indent) for every workflow/composite step."""
    matches = list(STEP.finditer(text))
    parsed: list[tuple[str, str, int]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        parsed.append((match.group(2), text[match.end() : end], len(match.group(1))))
    return parsed


def run_body(body: str, indent: int) -> str | None:
    field = " " * (indent + 2)
    content = " " * (indent + 4)
    match = re.search(rf"(?m)^{re.escape(field)}run:\s*(.*)$", body)
    if match is None:
        return None
    value = match.group(1).strip()
    if value and value not in {"|", "|-", ">", ">-"}:
        return value
    lines: list[str] = []
    for line in body[match.end() :].splitlines():
        if line.strip() and not line.startswith(content):
            break
        if line.startswith(content):
            lines.append(line[len(content) :])
        elif not line.strip():
            lines.append("")
    return "\n".join(lines).strip()


def run_steps(text: str) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    for name, body, indent in steps(text):
        command = run_body(body, indent)
        if command is not None:
            commands.append((name, command))
    return commands


def scanned_files() -> list[Path]:
    """The executable and operational surfaces that could reintroduce the path.

    The generated governance files are owned by the pinned platform checkout
    and verified byte for byte by `make governance-check`; they are regenerated
    when the pin advances, not edited here.
    """
    files = [
        MAKEFILE,
        OPERATIONS,
        *sorted(WORKFLOW_DIR.glob("*.y*ml")),
        *sorted(SCRIPT_DIR.glob("*.py")),
        *sorted(SCRIPT_DIR.glob("*.sh")),
        *sorted(SCRIPT_DIR.glob("*.mjs")),
    ]
    return [path for path in files if path.is_file()]


class GateContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.makefile = read(MAKEFILE)
        self.validate = read(VALIDATE_WORKFLOW)
        self.toolchain = read(TOOLCHAIN_ACTION)

    # Makefile ---------------------------------------------------------

    def test_makefile_owns_one_gate_list(self) -> None:
        targets = make_variable(self.makefile, "GATE_TARGETS").split()
        self.assertEqual(len(targets), len(set(targets)))
        self.assertTrue(
            {"brand-check", "render-check", "artifact-check", "statuscheck", "lint"}
            <= set(targets)
        )
        match = re.search(r"(?m)^validate:(.*)$", self.makefile)
        self.assertIsNotNone(match)
        self.assertEqual(
            match.group(1).strip(),
            "$(GATE_TARGETS)",
            "make validate must expand the shared gate list, not copy it",
        )
        for target in targets:
            self.assertRegex(
                self.makefile,
                rf"(?m)^{re.escape(target)}:",
                f"{target} is in GATE_TARGETS but has no rule",
            )

    def test_gate_runs_the_ci_checks(self) -> None:
        expected = {
            "status-check": (
                'python3 scripts/validate_status.py --root "$(STATUS_ROOT)"'
            ),
            "test": (
                "python3 -m unittest discover -s scripts -p '*_test.py'"
            ),
            "contract": "python3 scripts/ci_contract_test.py",
            "brand-check": (
                "python3 scripts/check_generated.py --root \"$(STATUS_ROOT)\" "
                "--platform \"$(PLATFORM_DIR)\" --brand \"$(BRAND_DIR)\""
            ),
            "render-check": (
                'python3 scripts/build_site.py --root "$(STATUS_ROOT)" --check'
            ),
            "artifact-check": (
                'python3 scripts/artifact_build.py --root "$(STATUS_ROOT)" '
                '--check --platform "$(PLATFORM_DIR)"'
            ),
            "assets-check": (
                'python3 scripts/prune_assets.py --root "$(STATUS_ROOT)" --check'
            ),
            "statuscheck": (
                './cmd/statuscheck -file "$(STATUS_ROOT)/status.json"'
            ),
            "governance-check": (
                'python3 scripts/check_governance.py --platform "$(PLATFORM_DIR)"'
            ),
            "actionlint": "$(GO) run $(ACTIONLINT) $$files",
            "hook": "git config --local core.hooksPath .githooks",
            "hook-check": "core.hooksPath is not .githooks",
            "lint": (
                './cmd/brandlint -root "$(PLATFORM_DIR)" '
                '-brand "$(BRAND_DIR)" -status "$(STATUS_ROOT)"'
            ),
        }
        for target, command in expected.items():
            with self.subTest(target=target):
                self.assertIn(
                    command,
                    make_recipe(self.makefile, target),
                    f"{target} no longer runs its canonical command",
                )
        for target in ("brand-check", "statuscheck", "governance-check", "lint"):
            with self.subTest(toolchain=target):
                self.assertIn(
                    "toolchain-check",
                    make_recipe(self.makefile, target).splitlines()[0],
                    f"{target} must verify the pinned checkouts first",
                )
        self.assertIn(
            "github.com/rhysd/actionlint/cmd/actionlint@v1.7.12",
            make_variable(self.makefile, "ACTIONLINT"),
            "actionlint must stay pinned to the reviewed release",
        )

    def test_brand_generates_and_prunes(self) -> None:
        recipe = make_recipe(self.makefile, "brand")
        self.assertIn(
            './cmd/genstatus -root "$(STATUS_ROOT)" -brand "$(BRAND_DIR)"',
            recipe,
        )
        self.assertIn(
            "-brand-only",
            recipe,
            "the platform must not overwrite the page before the renderer "
            "reads its previous snapshot binding",
        )
        self.assertIn("$(MAKE) --no-print-directory prune", recipe)
        self.assertIn(
            'prune_assets.py --root "$(STATUS_ROOT)" --write',
            make_recipe(self.makefile, "prune"),
        )

    def test_the_publish_decision_helper_is_gone(self) -> None:
        targets = make_variable(self.makefile, "GATE_TARGETS").split()
        dead_target = "".join(("publish", "-check"))
        self.assertNotIn(dead_target, targets)
        self.assertNotRegex(
            self.makefile,
            rf"(?m)^{re.escape(dead_target)}:",
            "the publish decision belonged to the dead dispatch workflow",
        )
        self.assertNotIn("PUBLISH_BASE", self.makefile)
        self.assertNotIn("PUBLISH_DECISION_FILE", self.makefile)
        for name in DEAD_SCRIPTS:
            with self.subTest(removed=name):
                self.assertFalse((SCRIPT_DIR / name).exists(), f"{name} is dead")

    # CI delegation ----------------------------------------------------

    def test_validate_workflow_only_calls_the_make_gate(self) -> None:
        commands = [command for _, command in run_steps(self.validate)]
        self.assertEqual(
            commands,
            ["make validate"],
            "the workflow must not run a check outside the Make target",
        )
        self.assertIn("uses: ./.github/actions/toolchain", self.validate)

    def test_validate_workflow_is_read_only_and_cancels_stale_prs(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.validate)
        match = re.search(
            r"(?m)^concurrency:\n  group: (.+?)\n  cancel-in-progress: (.+?)$",
            self.validate,
        )
        self.assertIsNotNone(match)
        self.assertIn("github.workflow", match.group(1))
        self.assertIn("github.ref", match.group(1))
        self.assertIn("github.event_name == 'pull_request'", match.group(2))

    def test_only_the_validate_workflow_exists(self) -> None:
        workflows = sorted(
            path.name
            for path in WORKFLOW_DIR.iterdir()
            if path.is_file() and path.suffix in {".yml", ".yaml"}
        )
        self.assertEqual(
            workflows,
            ["validate.yml"],
            "publication is local; this repository has one validation workflow",
        )

    def test_no_dispatch_token_or_hmac_path_remains(self) -> None:
        for name in DEAD_REFERENCES:
            with self.subTest(absent=name):
                self.assertNotIn(name, self.makefile)
        for name in DEAD_SCRIPTS:
            with self.subTest(absent_from_scripts=name):
                self.assertFalse((SCRIPT_DIR / name).exists(), name)
        for path in scanned_files():
            text = read(path)
            for token in DEAD_REFERENCES:
                with self.subTest(file=path.name, token=token):
                    self.assertNotIn(
                        token,
                        text,
                        f"{path.name} still carries the dead publication path",
                    )

    def test_verify_published_is_a_documented_post_deploy_step(self) -> None:
        targets = make_variable(self.makefile, "GATE_TARGETS").split()
        self.assertNotIn(
            "verify-published",
            targets,
            "the published check needs the live origin and cannot gate a commit",
        )
        recipe = make_recipe(self.makefile, "verify-published")
        self.assertIn(
            'python3 scripts/verify_published.py --root "$(STATUS_ROOT)" '
            '--url "$(URL)"',
            recipe,
        )
        self.assertNotIn("--signature", recipe)
        self.assertNotIn("--hmac-key-file", recipe)
        self.assertNotIn("--write", recipe)
        self.assertIn("make verify-published URL=", read(OPERATIONS))
        self.assertNotIn("verify-published", self.validate)

    # Pinned inputs ----------------------------------------------------

    def test_toolchain_refs_are_full_commit_shas(self) -> None:
        checkouts = [
            (name, body, indent)
            for name, body, indent in steps(self.toolchain)
            if "uses: actions/checkout@" in body
        ]
        self.assertEqual(len(checkouts), 2, "both toolchain checkouts are required")
        seen: dict[str, tuple[str, str]] = {}
        for name, body, _ in checkouts:
            repository = re.search(r"(?m)^\s+repository:\s*(\S+)\s*$", body)
            reference = re.search(r"(?m)^\s+ref:\s*(\S+)\s*$", body)
            path = re.search(r"(?m)^\s+path:\s*(\S+)\s*$", body)
            credentials = re.search(
                r"(?m)^\s+persist-credentials:\s*(\S+)\s*$", body
            )
            self.assertIsNotNone(repository, name)
            self.assertIsNotNone(reference, name)
            self.assertIsNotNone(path, name)
            self.assertIsNotNone(credentials, name)
            assert repository and reference and path and credentials
            self.assertRegex(
                reference.group(1),
                FULL_SHA,
                f"{repository.group(1)} must be pinned to a full 40-hex commit",
            )
            self.assertEqual(credentials.group(1), "false", name)
            seen[repository.group(1)] = (path.group(1), reference.group(1))
        self.assertEqual(
            set(seen), {"SBK-Techno/platform", "SBK-Techno/brand"}
        )
        self.assertEqual(seen["SBK-Techno/platform"][0], ".toolchain/platform")
        self.assertEqual(seen["SBK-Techno/brand"][0], ".toolchain/brand")
        self.assertEqual(
            self.validate.count("uses: ./.github/actions/toolchain"),
            1,
            "the validate workflow must consume the pinned toolchain action",
        )
        self.assertNotIn(
            "repository:",
            self.validate,
            "toolchain checkouts must stay inside the pinned composite action",
        )
        self.assertNotIn(
            "ref:",
            self.validate,
            "toolchain checkout refs must stay inside the pinned composite action",
        )

    def test_toolchain_reads_private_checkouts_with_deploy_keys(self) -> None:
        checkouts: dict[str, str] = {}
        for name, body, _ in steps(self.toolchain):
            if "uses: actions/checkout@" not in body:
                continue
            repository = re.search(r"(?m)^\s+repository:\s*(\S+)\s*$", body)
            self.assertIsNotNone(repository, name)
            assert repository
            checkouts[repository.group(1)] = body
        credentials = {
            "SBK-Techno/platform": "PLATFORM_READ_KEY",
            "SBK-Techno/brand": "BRAND_READ_KEY",
        }
        self.assertEqual(set(checkouts), set(credentials))
        for repository, secret in credentials.items():
            with self.subTest(repository=repository):
                body = checkouts[repository]
                input_name = secret.lower()
                self.assertIn(
                    f"{input_name}:",
                    self.toolchain,
                    f"the composite action must declare the {input_name} input",
                )
                self.assertIn(
                    f"ssh-key: ${{{{ inputs.{input_name} }}}}",
                    body,
                    f"{repository} must read its deploy key from the composite input",
                )
                self.assertIn(
                    f"{input_name}: ${{{{ secrets.{secret} }}}}",
                    self.validate,
                    f"the workflow must pass {secret} to the composite action",
                )
                self.assertIsNone(
                    re.search(r"(?m)^\s+(?:token|password):", body),
                    f"{repository} must not receive a token in the checkout",
                )
                self.assertNotRegex(
                    body,
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
                    "no key material may be committed",
                )

    def test_actions_are_pinned_to_commit_shas(self) -> None:
        third_party: list[str] = []
        for name, text in (
            ("validate", self.validate),
            ("toolchain action", self.toolchain),
        ):
            for use in re.findall(r"(?m)^\s+uses:\s*(\S+)", text):
                if use.startswith("./"):
                    continue
                third_party.append(use)
                with self.subTest(file=name, action=use):
                    self.assertRegex(use, ACTION_PIN)
        self.assertTrue(
            third_party,
            "the workflow no longer uses the pinned checkout and setup-go actions",
        )


if __name__ == "__main__":
    unittest.main()
