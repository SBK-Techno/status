#!/usr/bin/env python3
"""Contract tests for the local gate and its CI delegation.

The Makefile owns the list of checks. The workflows may prepare the pinned
toolchain and invoke the gate, but they must not keep a private list or inline
a copy of a check. These tests parse the real files, so the contract cannot
drift silently.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
VALIDATE_WORKFLOW = ROOT / ".github" / "workflows" / "validate.yml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
TOOLCHAIN_ACTION = ROOT / ".github" / "actions" / "toolchain" / "action.yml"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
ACTION_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
STEP = re.compile(r"(?m)^( +)- name:\s*(.+?)\s*$")


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


class GateContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.makefile = read(MAKEFILE)
        self.validate = read(VALIDATE_WORKFLOW)
        self.publish = read(PUBLISH_WORKFLOW)
        self.toolchain = read(TOOLCHAIN_ACTION)

    # Makefile ---------------------------------------------------------

    def test_makefile_owns_one_gate_list(self) -> None:
        targets = make_variable(self.makefile, "GATE_TARGETS").split()
        self.assertEqual(len(targets), len(set(targets)))
        self.assertTrue(
            {"brand-check", "render-check", "statuscheck", "lint"} <= set(targets)
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
            "brand-check": (
                "python3 scripts/check_generated.py --root \"$(STATUS_ROOT)\" "
                "--platform \"$(PLATFORM_DIR)\" --brand \"$(BRAND_DIR)\""
            ),
            "render-check": (
                'python3 scripts/build_site.py --root "$(STATUS_ROOT)" --check'
            ),
            "assets-check": (
                'python3 scripts/prune_assets.py --root "$(STATUS_ROOT)" --check'
            ),
            "statuscheck": (
                './cmd/statuscheck -file "$(STATUS_ROOT)/status.json"'
            ),
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
        for target in ("brand-check", "statuscheck", "lint"):
            with self.subTest(toolchain=target):
                self.assertIn(
                    "toolchain-check",
                    make_recipe(self.makefile, target).splitlines()[0],
                    f"{target} must verify the pinned checkouts first",
                )

    def test_brand_generates_and_prunes(self) -> None:
        recipe = make_recipe(self.makefile, "brand")
        self.assertIn(
            './cmd/genstatus -root "$(STATUS_ROOT)" -brand "$(BRAND_DIR)"',
            recipe,
        )
        self.assertIn("$(MAKE) --no-print-directory prune", recipe)
        self.assertIn(
            'prune_assets.py --root "$(STATUS_ROOT)" --write',
            make_recipe(self.makefile, "prune"),
        )

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

    def test_publish_rebuilds_prunes_and_validates_before_commit(self) -> None:
        commands = run_steps(self.publish)
        brand = next(i for i, command in enumerate(commands) if command[1] == "make brand")
        validate = next(
            i for i, command in enumerate(commands) if command[1] == "make validate"
        )
        commit = next(
            i for i, command in enumerate(commands) if "git push" in command[1]
        )
        self.assertLess(brand, validate, "prune/render must precede the gate")
        self.assertLess(validate, commit, "nothing may be committed before the gate")
        self.assertIn("uses: ./.github/actions/toolchain", self.publish)
        self.assertIn("STATUS_HEARTBEAT_HMAC", self.publish)
        self.assertIn("permissions:\n  contents: write", self.publish)
        self.assertIn("cancel-in-progress: false", self.publish)

        combined = "\n".join(command for _, command in commands)
        for duplicated in (
            "scripts/build_site.py",
            "scripts/validate_status.py",
            "scripts/prune_assets.py",
            "go run ./cmd/",
            "validate_document",
        ):
            with self.subTest(duplicated=duplicated):
                self.assertNotIn(
                    duplicated,
                    combined,
                    "publish must delegate to the same Make targets as CI",
                )
        self.assertIn("chore(status): publish signed heartbeat", combined)

    def test_publish_and_validate_share_the_gate_command(self) -> None:
        validate_commands = [
            command for _, command in run_steps(self.validate) if command.startswith("make ")
        ]
        publish_commands = [
            command for _, command in run_steps(self.publish) if command.startswith("make ")
        ]
        self.assertEqual(validate_commands, ["make validate"])
        self.assertEqual(publish_commands, ["make brand", "make validate"])

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
        for workflow in (self.validate, self.publish):
            self.assertEqual(
                workflow.count("uses: ./.github/actions/toolchain"),
                1,
                "both workflows must consume the pinned toolchain action",
            )
            self.assertNotIn(
                "repository:",
                workflow,
                "toolchain checkouts must stay inside the pinned composite action",
            )
            self.assertNotIn(
                "ref:",
                workflow,
                "toolchain checkout refs must stay inside the pinned composite action",
            )

    def test_actions_are_pinned_to_commit_shas(self) -> None:
        third_party: list[str] = []
        for name, text in (
            ("validate", self.validate),
            ("publish", self.publish),
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
            "the workflows no longer use the pinned checkout and setup-go actions",
        )


if __name__ == "__main__":
    unittest.main()
