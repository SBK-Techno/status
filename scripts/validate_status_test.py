#!/usr/bin/env python3
"""Tests for the shared status document validator."""

from __future__ import annotations

import copy
import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("validate_status.py")
SPEC = importlib.util.spec_from_file_location("validate_status", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def valid_document() -> dict:
    return {
        "version": 1,
        "generated_at": "2026-10-24T12:00:00Z",
        "expires_at": "2026-10-24T12:05:00Z",
        "overall": "operational",
        "summary_fr": "Tout va bien.",
        "summary_en": "All good.",
        "components": [
            {
                "id": "portal",
                "label_fr": "Portail",
                "label_en": "Portal",
                "status": "operational",
                "detail_fr": "Disponible.",
                "detail_en": "Available.",
            }
        ],
        "incidents": [],
    }


class ValidateStatusTest(unittest.TestCase):
    def test_accepts_valid_document(self) -> None:
        self.assertEqual(VALIDATOR.validate_document(valid_document()), [])

    def test_accepts_seed_without_timestamps(self) -> None:
        document = valid_document()
        document["generated_at"] = None
        document["expires_at"] = None
        document["overall"] = "unknown"
        document["components"][0]["status"] = "unknown"
        self.assertEqual(VALIDATOR.validate_document(document), [])

    def test_rejects_invalid_documents(self) -> None:
        def run(mutate) -> str:
            document = copy.deepcopy(valid_document())
            mutate(document)
            return " | ".join(VALIDATOR.validate_document(document))

        cases = {
            "version": lambda d: d.update(version=2),
            "half timestamps": lambda d: d.update(expires_at=None),
            "expiry order": lambda d: d.update(expires_at="2026-10-24T11:59:59Z"),
            "lifetime": lambda d: d.update(expires_at="2026-10-24T12:16:00Z"),
            "unknown overall": lambda d: d.update(overall="green"),
            "empty summary": lambda d: d.update(summary_fr=" "),
            "no components": lambda d: d.update(components=[]),
            "duplicate component": lambda d: d["components"].append(d["components"][0]),
            "component field": lambda d: d["components"][0].pop("detail_en"),
            "unknown component field": lambda d: d["components"][0].update(extra=True),
            "incident fields": lambda d: d.update(
                incidents=[
                    {
                        "id": "inc-1",
                        "status": "investigating",
                        "title_fr": "Titre",
                        "title_en": "Title",
                        "started_at": "2026-10-24T12:00:00Z",
                        "updated_at": "2026-10-24T11:00:00Z",
                        "summary_fr": "Résumé",
                        "summary_en": "Summary",
                    }
                ]
            ),
            "offset timestamp": lambda d: d.update(generated_at="2026-10-24T12:00:00+00:00"),
            "unknown top-level": lambda d: d.update(extra=True),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                self.assertNotEqual(run(mutate), "")

    def test_parse_utc_rejects_fractional_seconds(self) -> None:
        self.assertIsNone(VALIDATOR.parse_utc("2026-10-24T12:00:00.000Z"))
        parsed = VALIDATOR.parse_utc("2026-10-24T12:00:00Z")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.year, 2026)


if __name__ == "__main__":
    unittest.main()
