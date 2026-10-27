#!/usr/bin/env python3
"""Tests for the status document, the static snapshot, and the site gate."""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import re
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

MODULE_PATH = SCRIPTS / "validate_status.py"
SPEC = importlib.util.spec_from_file_location("validate_status", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)

import prune_assets
import site_build
import status_snapshot

ROOT = SCRIPTS.parent


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
            },
            {
                "id": "docs",
                "label_fr": "Documentation",
                "label_en": "Documentation",
                "status": "degraded",
                "detail_fr": "Lente.",
                "detail_en": "Slow.",
            },
        ],
        "incidents": [],
    }


def repository_page(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def repository_manifest() -> dict:
    return json.loads((ROOT / "brand.manifest.json").read_text(encoding="utf-8"))


def repository_csp() -> str:
    return (ROOT / "templates/csp.txt").read_text(encoding="utf-8").strip()


def page_for(document: dict, now: datetime) -> tuple[str, bytes]:
    """Build a realistic page carrying the snapshot for one fixture."""
    status_bytes = json.dumps(document, ensure_ascii=False).encode("utf-8")
    region = status_snapshot.render_snapshot_region(document, now)
    page = site_build.replace_snapshot_region(
        repository_page("index.html"),
        "\n" + region.strip("\n") + "\n    ",
    )
    page = re.sub(
        r'data-snapshot-at="[^"]*"',
        f'data-snapshot-at="{status_snapshot.iso(now)}"',
        page,
        count=1,
    )
    page = re.sub(
        r'data-status-digest="[^"]*"',
        f'data-status-digest="{status_snapshot.document_digest(status_bytes)}"',
        page,
        count=1,
    )
    generated = status_snapshot.parse_timestamp(document.get("generated_at"))
    footer = (
        f'<time datetime="{status_snapshot.iso(generated)}">'
        f"{status_snapshot.format_stamp(generated)}</time>"
        if generated is not None
        else "Aucun"
    )
    page = re.sub(
        r'(<span id="snapshot-time">).*?(</span>)',
        rf"\1{footer}\2",
        page,
        count=1,
        flags=re.DOTALL,
    )
    return page, status_bytes


def temp_copy() -> tempfile.TemporaryDirectory:
    temporary = tempfile.TemporaryDirectory()
    destination = Path(temporary.name)
    for name in (
        "404.html",
        "index.html",
        "styles.css",
        "app.js",
        "status-model.js",
        "status.json",
        "brand.manifest.json",
        "site.manifest.json",
        "asset-retention.json",
        "CNAME",
        ".nojekyll",
    ):
        shutil.copyfile(ROOT / name, destination / name)
    shutil.copytree(ROOT / "templates", destination / "templates")
    shutil.copytree(ROOT / "assets", destination / "assets")
    for asset in VALIDATOR.load_brand_manifest(ROOT)[0]["assets"]:
        if asset.get("publish"):
            shutil.copyfile(ROOT / asset["file"], destination / asset["file"])
    return temporary


class ValidateStatusTest(unittest.TestCase):
    # Document contract -------------------------------------------------

    def test_accepts_valid_document(self) -> None:
        self.assertEqual(VALIDATOR.validate_document(valid_document()), [])

    def test_accepts_seed_without_timestamps(self) -> None:
        document = valid_document()
        document["generated_at"] = None
        document["expires_at"] = None
        document["overall"] = "unknown"
        for component in document["components"]:
            component["status"] = "unknown"
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
            "duplicate component": lambda d: d["components"].append(
                d["components"][0]
            ),
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
            "offset timestamp": lambda d: d.update(
                generated_at="2026-10-24T12:00:00+00:00"
            ),
            "unknown top-level": lambda d: d.update(extra=True),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                self.assertNotEqual(run(mutate), "")

    def test_rejects_null_heartbeat_with_a_healthy_claim(self) -> None:
        document = valid_document()
        document["generated_at"] = None
        document["expires_at"] = None
        errors = " | ".join(VALIDATOR.validate_document(document))
        self.assertIn("overall must be unknown when generated_at is null", errors)
        self.assertIn(
            "components[0].status must be unknown when generated_at is null",
            errors,
        )

    def test_rejects_empty_and_future_heartbeats(self) -> None:
        fixed = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

        empty = valid_document()
        empty["generated_at"] = ""
        empty["expires_at"] = ""
        self.assertIn(
            "generated_at must be null or a UTC ISO 8601 timestamp",
            " | ".join(VALIDATOR.validate_document(empty, now=fixed)),
        )

        future = valid_document()
        future["generated_at"] = "2026-10-25T12:06:00Z"
        future["expires_at"] = "2026-10-25T12:11:00Z"
        self.assertIn(
            "generated_at must not be more than 5 minutes in the future",
            " | ".join(VALIDATOR.validate_document(future, now=fixed)),
        )

        skewed = valid_document()
        skewed["generated_at"] = "2026-10-25T12:04:00Z"
        skewed["expires_at"] = "2026-10-25T12:09:00Z"
        self.assertEqual(VALIDATOR.validate_document(skewed, now=fixed), [])

    def test_accepts_aged_and_expired_documents_for_the_unknown_view(self) -> None:
        fixed = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
        cases = {
            "aged": ("2026-10-25T11:40:00Z", "2026-10-25T11:50:00Z"),
            "expired": ("2026-10-25T11:30:00Z", "2026-10-25T11:35:00Z"),
        }
        for name, (generated, expires) in cases.items():
            with self.subTest(state=name):
                document = valid_document()
                document["generated_at"] = generated
                document["expires_at"] = expires
                self.assertEqual(
                    VALIDATOR.validate_document(document, now=fixed),
                    [],
                    f"{name}: a stale document is valid, the page renders it as unknown",
                )

    # Snapshot contract -------------------------------------------------

    def test_snapshot_matches_the_document(self) -> None:
        cases = {
            "fresh": (
                valid_document(),
                datetime(2026, 10, 24, 12, 1, tzinfo=timezone.utc),
            ),
            "stale": (
                valid_document(),
                datetime(2026, 10, 24, 12, 30, tzinfo=timezone.utc),
            ),
            "null": (
                {
                    **valid_document(),
                    "generated_at": None,
                    "expires_at": None,
                    "overall": "unknown",
                    "components": [
                        {
                            **valid_document()["components"][0],
                            "status": "unknown",
                        }
                    ],
                },
                datetime(2026, 10, 24, 12, 1, tzinfo=timezone.utc),
            ),
            "incident": (
                {
                    **valid_document(),
                    "incidents": [
                        {
                            "id": "inc_1",
                            "status": "monitoring",
                            "title_fr": "Latence élevée",
                            "title_en": "Elevated latency",
                            "started_at": "2026-10-24T11:30:00Z",
                            "updated_at": "2026-10-24T11:50:00Z",
                            "summary_fr": "Nous surveillons.",
                            "summary_en": "We are monitoring.",
                        }
                    ],
                },
                datetime(2026, 10, 24, 12, 1, tzinfo=timezone.utc),
            ),
        }
        for name, (document, now) in cases.items():
            with self.subTest(case=name):
                page, status_bytes = page_for(document, now)
                self.assertEqual(
                    VALIDATOR.validate_snapshot(
                        "index.html", page, document, status_bytes
                    ),
                    [],
                )

    def test_snapshot_rejects_a_claim_beyond_the_heartbeat(self) -> None:
        document = valid_document()
        now = datetime(2026, 10, 24, 12, 1, tzinfo=timezone.utc)
        page, status_bytes = page_for(document, now)
        overstated = page.replace(
            'id="hero-status-text">opérationnel<',
            'id="hero-status-text">inconnu<',
            1,
        )
        self.assertIn(
            "#hero-status-text",
            " | ".join(
                VALIDATOR.validate_snapshot(
                    "index.html", overstated, document, status_bytes
                )
            ),
        )
        row = page.replace(
            '<li class="component" data-status="degraded">',
            '<li class="component" data-status="operational">',
            1,
        )
        self.assertIn(
            "component row 2 data-status",
            " | ".join(
                VALIDATOR.validate_snapshot("index.html", row, document, status_bytes)
            ),
        )

    def test_stale_snapshot_cannot_keep_the_publisher_summary(self) -> None:
        document = valid_document()
        now = datetime(2026, 10, 24, 12, 30, tzinfo=timezone.utc)
        page, status_bytes = page_for(document, now)
        self.assertIn(
            "Le dernier signal est trop ancien",
            page,
        )
        self.assertNotIn("Tout va bien.", page)
        forged = page.replace(
            'id="hero-status-text">inconnu<',
            'id="hero-status-text">opérationnel<',
            1,
        )
        errors = " | ".join(
            VALIDATOR.validate_snapshot("index.html", forged, document, status_bytes)
        )
        self.assertIn("#hero-status-text", errors)

    def test_snapshot_digest_and_instant_are_bound(self) -> None:
        document = valid_document()
        now = datetime(2026, 10, 24, 12, 1, tzinfo=timezone.utc)
        page, status_bytes = page_for(document, now)
        wrong_digest = re.sub(
            r'data-status-digest="[0-9a-f]{64}"',
            'data-status-digest="' + "0" * 64 + '"',
            page,
            count=1,
        )
        self.assertIn(
            "does not match the exact status.json bytes",
            " | ".join(
                VALIDATOR.validate_snapshot(
                    "index.html", wrong_digest, document, status_bytes
                )
            ),
        )
        wrong_time = re.sub(
            r'data-snapshot-at="[^"]*"',
            'data-snapshot-at="2026-10-24T12:30:00Z"',
            page,
            count=1,
        )
        # The digest still matches, but the page now shows confirmed rows at an
        # instant when the same document is stale.
        self.assertIn(
            "#hero-status-text",
            " | ".join(
                VALIDATOR.validate_snapshot(
                    "index.html", wrong_time, document, status_bytes
                )
            ),
        )

    def test_render_state_contract_covers_loading_fresh_stale_and_error(self) -> None:
        result = subprocess.run(
            ["node", str(SCRIPTS / "status_model_test.mjs")],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"status model contract failed:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("status model ok", result.stdout)

    def test_snapshot_contract_matches_the_browser_model(self) -> None:
        result = subprocess.run(
            ["node", str(SCRIPTS / "snapshot_contract_test.mjs")],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"snapshot contract failed:\n{result.stdout}\n{result.stderr}",
        )
        fixtures = json.loads(
            (SCRIPTS / "snapshot_fixtures.json").read_text(encoding="utf-8")
        )["fixtures"]
        browser = json.loads(result.stdout)
        for fixture, current in zip(fixtures, browser):
            with self.subTest(fixture=fixture["name"]):
                now = status_snapshot.parse_timestamp(fixture["now"])
                self.assertIsNotNone(now)
                expected = status_snapshot.snapshot_state(
                    fixture["document"], now
                )
                self.assertEqual(expected["reason"], current["reason"])
                self.assertEqual(expected["headline"], current["headline"])
                self.assertEqual(expected["summary_key"], current["summary"])
                self.assertEqual(expected["note_key"], current["note"])
                self.assertEqual(
                    "stale" if expected["banner"] else None, current["banner"]
                )
                self.assertEqual(expected["snapshot_kind"], current["snapshot"])
                self.assertEqual(expected["signal"], current["signal"])
                self.assertEqual(expected["fresh"], current["confirmed"])
                self.assertEqual(not expected["fresh"], current["forceUnknown"])
                generated = expected["generated"]
                self.assertEqual(
                    int(generated.timestamp() * 1000)
                    if generated is not None
                    else None,
                    current["generated"],
                )
                self.assertEqual(
                    "fresh" if expected["fresh"] else "stale", current["mode"]
                )

    def test_js_disabled_page_shows_status_rows_and_marker(self) -> None:
        page = repository_page("index.html")
        root = VALIDATOR.parse_page(page)
        html = next(root.find_all("html"), None)
        self.assertIn("no-js", VALIDATOR.classes(html))
        components = next(root.find_all("ul", element_id="components-list"), None)
        rows = [
            child
            for child in components.children
            if isinstance(child, VALIDATOR.Element) and child.tag == "li"
        ]
        self.assertGreater(len(rows), 0, "a no-JS visitor must see component rows")
        for row in rows:
            self.assertNotIn("hidden", row.attrs)
        hero = next(root.find_all(element_id="hero"), None)
        self.assertNotIn("hidden", hero.attrs)
        word = next(root.find_all(element_id="hero-status-text"), None)
        self.assertTrue(word.text().strip())
        marker = next(root.find_all(element_id="snapshot-marker"), None)
        self.assertTrue(marker.text().strip())
        noscript = next(root.find_all("noscript"), None)
        self.assertIn("status.json", noscript.text())
        self.assertNotIn("ne lit pas", noscript.text().lower())
        # app.js only enhances; the snapshot is already in the HTML.
        self.assertIn('<script src="/assets/status-model.', page)
        self.assertIn('<script src="/assets/app.', page)

    # Page and brand contract -------------------------------------------

    def test_accepts_repository_pages(self) -> None:
        for name in ("index.html", "404.html"):
            with self.subTest(page=name):
                self.assertEqual(
                    VALIDATOR.validate_page(
                        name,
                        repository_page(name),
                        require_status=(name == "index.html"),
                        theme_color="#080A0F",
                        csp=repository_csp(),
                    ),
                    [],
                )

    def test_rejects_page_regressions(self) -> None:
        page = repository_page("index.html")
        reverse = next(
            asset["file"]
            for asset in repository_manifest()["assets"]
            if asset["name"] == VALIDATOR.REVERSE_LOCKUP
        )

        def run(mutate) -> str:
            return " | ".join(
                VALIDATOR.validate_page(
                    "index.html",
                    mutate(page),
                    require_status=True,
                    theme_color="#080A0F",
                    csp=repository_csp(),
                )
            )

        cases = {
            "missing language": (
                lambda p: p.replace(' lang="fr-CA"', ""),
                "lang attribute",
            ),
            "non-french language": (
                lambda p: p.replace("fr-CA", "en-CA", 1),
                "French",
            ),
            "two h1": (
                lambda p: p.replace(
                    '<h1 class="hero-statement"', '<h1>Autre</h1><h1 class="hero-statement"', 1
                ),
                "exactly one <h1>",
            ),
            "missing main": (
                lambda p: p.replace(
                    '<main id="contenu" class="status-main" tabindex="-1">',
                    '<div id="contenu">',
                ).replace("</main>", "</div>"),
                "<main> landmark",
            ),
            "missing footer": (
                lambda p: re.sub(r"<footer.*?</footer>", "", p, flags=re.S),
                "contentinfo landmark",
            ),
            "missing skip link": (
                lambda p: re.sub(
                    r'<a class="sbk-skip".*?</a>', "", p, flags=re.S
                ),
                "skip link",
            ),
            "time without datetime": (
                lambda p: re.sub(
                    r' datetime="[^"]*"', "", p, count=1
                ),
                "missing its datetime",
            ),
            "external script": (
                lambda p: p.replace(
                    "</body>",
                    '<script src="https://cdn.example/app.js"></script></body>',
                ),
                "third-party origin",
            ),
            "inline handler": (
                lambda p: p.replace("<h1", '<h1 onclick="alert(1)"', 1),
                "inline event handler",
            ),
            "inline script": (
                lambda p: p.replace("</body>", "<script>alert(1)</script></body>"),
                "inline <script>",
            ),
            "inline style element": (
                lambda p: p.replace("</body>", "<style>body{color:red}</style></body>"),
                "inline <style>",
            ),
            "javascript url": (
                lambda p: p.replace(
                    "</body>", '<a href="javascript:alert(1)">x</a></body>'
                ),
                "javascript: URL",
            ),
            "missing viewport": (
                lambda p: p.replace(
                    '<meta name="viewport" content="width=device-width, initial-scale=1">',
                    "",
                ),
                "viewport",
            ),
            "light theme": (
                lambda p: p.replace('data-theme="dark"', 'data-theme="light"'),
                "graphite surface",
            ),
            "missing no-js": (
                lambda p: p.replace(' class="no-js"', "", 1),
                "no-js",
            ),
            "missing color scheme": (
                lambda p: p.replace('<meta name="color-scheme" content="dark">', ""),
                "color-scheme meta",
            ),
            "wrong theme color": (
                lambda p: p.replace(
                    '<meta name="theme-color" content="#080A0F">',
                    '<meta name="theme-color" content="#F7F5EF">',
                ),
                "must equal the dark",
            ),
            "missing brand mark": (
                lambda p: p.replace(
                    f'<img class="status-brand__mark" src="/{reverse}" '
                    'alt="SBK Techno" width="284" height="118">',
                    "",
                    1,
                ),
                "header mark",
            ),
            "missing retry": (
                lambda p: p.replace(
                    '<button class="btn" id="retry-button" type="button" data-i18n="retry">Réessayer</button>',
                    "",
                    1,
                ),
                "one retry button",
            ),
            "missing snapshot marker": (
                lambda p: re.sub(
                    r'<span id="snapshot-marker".*?</span>\s*</p>',
                    "</p>",
                    p,
                    count=1,
                    flags=re.S,
                ),
                "#snapshot-marker",
            ),
            "vitals visible": (
                lambda p: p.replace(
                    '<div class="vitals" id="vitals" aria-hidden="true" data-total="0" hidden>',
                    '<div class="vitals" id="vitals" aria-hidden="true" data-total="0">',
                    1,
                ),
                "must stay hidden",
            ),
            "duplicate csp": (
                lambda p: p.replace(
                    "</head>",
                    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'"></head>',
                ),
                "exactly one Content-Security-Policy",
            ),
            "frame ancestors": (
                lambda p: p.replace(
                    "form-action 'none'",
                    "form-action 'none'; frame-ancestors 'none'",
                    1,
                ),
                "frame-ancestors",
            ),
        }
        for name, (mutate, expected) in cases.items():
            with self.subTest(name=name):
                errors = run(mutate)
                self.assertNotEqual(errors, "")
                self.assertIn(expected, errors)

    def test_single_brand_link_and_manifest_digest(self) -> None:
        manifest = repository_manifest()
        page = repository_page("index.html")
        brand = next(
            asset for asset in manifest["assets"] if asset["name"] == "brand.css"
        )
        self.assertEqual(
            VALIDATOR.validate_brand_links(
                ROOT, {"index.html": page}, manifest
            ),
            [],
        )
        duplicate = page.replace(
            "</head>",
            f'<link rel="stylesheet" href="/{brand["file"]}"></head>',
            1,
        )
        self.assertIn(
            "exactly one brand",
            " | ".join(
                VALIDATOR.validate_brand_links(
                    ROOT, {"index.html": duplicate}, manifest
                )
            ),
        )
        wrong = page.replace(f'/{brand["file"]}', "/brand.000000000000.css", 1)
        self.assertIn(
            "must be the manifest entry",
            " | ".join(
                VALIDATOR.validate_brand_links(ROOT, {"index.html": wrong}, manifest)
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            isolated = Path(temporary)
            shutil.copyfile(ROOT / brand["file"], isolated / brand["file"])
            (isolated / brand["file"]).write_text("body {}", encoding="utf-8")
            self.assertIn(
                "SHA-256",
                " | ".join(
                    VALIDATOR.validate_brand_links(
                        isolated, {"index.html": page}, manifest
                    )
                ),
            )

    def test_old_brand_stylesheets_are_not_published(self) -> None:
        manifest = repository_manifest()
        published = {
            asset["file"] for asset in manifest["assets"] if asset.get("publish")
        }
        current = {path.name for path in ROOT.glob("brand.*.css")}
        self.assertEqual(current, {asset for asset in published if asset.endswith(".css")})
        self.assertEqual(prune_assets.retention_errors(ROOT), [])

    def test_unmanifested_brand_sheet_fails_retention(self) -> None:
        with temp_copy() as temporary:
            root = Path(temporary)
            extra = root / "brand.000000000000.css"
            extra.write_text("body {}", encoding="utf-8")
            errors = prune_assets.retention_errors(
                root, datetime.now(timezone.utc)
            )
            self.assertIn("no retention record", " | ".join(errors))
            ledger = json.loads((root / "asset-retention.json").read_text())
            ledger["retired"].append(
                {
                    "file": extra.name,
                    "retired_at": "2020-01-01T00:00:00Z",
                }
            )
            (root / "asset-retention.json").write_text(json.dumps(ledger))
            errors = prune_assets.retention_errors(
                root, datetime.now(timezone.utc)
            )
            self.assertIn("retention window", " | ".join(errors))
            prune_assets.prune(root, datetime.now(timezone.utc))
            self.assertFalse(extra.exists())

    def test_site_manifest_digest_drift_is_rejected(self) -> None:
        with temp_copy() as temporary:
            root = Path(temporary)
            site = json.loads((root / "site.manifest.json").read_text())
            app = next(asset for asset in site["assets"] if asset["name"] == "app.js")
            (root / app["file"]).write_text("// changed\n", encoding="utf-8")
            errors = " | ".join(
                VALIDATOR.validate_site_manifest(
                    root,
                    {
                        "index.html": (root / "index.html").read_text(),
                        "404.html": (root / "404.html").read_text(),
                    },
                    repository_manifest(),
                )
            )
            self.assertIn("SHA-256", errors)

    # Print contract ----------------------------------------------------

    def test_print_block_and_palette_meet_contrast(self) -> None:
        manifest = repository_manifest()
        pages = {
            name: repository_page(name) for name in ("index.html", "404.html")
        }
        self.assertEqual(VALIDATOR.validate_print(ROOT, pages, manifest), [])
        styles = (ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertIn("@media print", styles)
        self.assertNotRegex(styles, r"(?i)print-color-adjust\s*:")
        brand = next(asset for asset in manifest["assets"] if asset["name"] == "brand.css")
        brand_css = (ROOT / brand["file"]).read_text(encoding="utf-8")
        site = json.loads((ROOT / "site.manifest.json").read_text())
        print_asset = next(
            asset for asset in site["assets"] if asset["name"] == "print.css"
        )
        print_css = (ROOT / print_asset["file"]).read_text(encoding="utf-8")
        light = VALIDATOR.brand_properties(brand_css, dark=False)
        self.assertGreaterEqual(
            VALIDATOR.contrast_ratio(light["--sbk-text"], light["--sbk-canvas"]),
            4.5,
        )
        self.assertGreaterEqual(
            VALIDATOR.contrast_ratio(light["--sbk-text"], "#FFFFFF"), 4.5
        )
        self.assertIn("@media print", print_css)

    def test_print_block_is_required(self) -> None:
        manifest = repository_manifest()
        pages = {
            name: repository_page(name) for name in ("index.html", "404.html")
        }
        with temp_copy() as temporary:
            root = Path(temporary)
            styles = (root / "styles.css").read_text(encoding="utf-8")
            without = styles.replace("@media print", "@media screen", 1)
            (root / "styles.css").write_text(without, encoding="utf-8")
            self.assertIn(
                "missing an @media print block",
                " | ".join(VALIDATOR.validate_print(root, pages, manifest)),
            )

    def test_print_contrast_is_measured_with_backgrounds_dropped(self) -> None:
        manifest = repository_manifest()
        pages = {
            name: repository_page(name) for name in ("index.html", "404.html")
        }
        with temp_copy() as temporary:
            root = Path(temporary)
            site = json.loads((root / "site.manifest.json").read_text())
            asset = next(
                item for item in site["assets"] if item["name"] == "print.css"
            )
            css = (root / asset["file"]).read_text(encoding="utf-8")
            css = css.replace("--sbk-text: #0A0D13;", "--sbk-text: #F7F5EF;")
            css = css.replace("--sbk-canvas: #F7F5EF;", "--sbk-canvas: #F7F5EF;")
            (root / asset["file"]).write_text(css, encoding="utf-8")
            errors = " | ".join(VALIDATOR.validate_print(root, pages, manifest))
            self.assertIn("below the 4.5:1 minimum", errors)

    def test_print_safe_mark_is_required(self) -> None:
        manifest = repository_manifest()
        pages = {
            "index.html": re.sub(
                r'<img class="status-brand__print-mark".*?>',
                "",
                repository_page("index.html"),
                count=1,
            ),
            "404.html": repository_page("404.html"),
        }
        self.assertIn(
            "missing the print-safe mark",
            " | ".join(VALIDATOR.validate_print(ROOT, pages, manifest)),
        )

    # Repository gates --------------------------------------------------

    def test_repository_site_is_valid(self) -> None:
        self.assertEqual(VALIDATOR.validate_site(ROOT), [])

    def test_main_fails_on_invalid_page(self) -> None:
        with temp_copy() as temporary:
            root = Path(temporary)
            page = (root / "index.html").read_text(encoding="utf-8")
            (root / "index.html").write_text(
                page.replace(' lang="fr-CA"', "", 1), encoding="utf-8"
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = VALIDATOR.main(["--root", str(root)])
            self.assertEqual(code, 1)
            output = stderr.getvalue()
            self.assertIn("status site validation failed", output)
            self.assertIn("lang attribute", output)

    def test_rejects_cookie_access(self) -> None:
        with temp_copy() as temporary:
            root = Path(temporary)
            with (root / "app.js").open("a", encoding="utf-8") as handle:
                handle.write("\ndocument.cookie = 'session=1';\n")
            errors = " | ".join(VALIDATOR.validate_site(root))
            self.assertIn("document.cookie is not allowed", errors)

    def test_rejects_forbidden_storage_apis(self) -> None:
        cases = {
            "localStorage": 'window.localStorage.setItem("x", "y");',
            "sessionStorage": 'window.sessionStorage.getItem("x");',
            "document.cookie": 'document.cookie = "x=1";',
            "IndexedDB": 'window.indexedDB.open("x");',
            "Cache Storage": 'caches.open("x");',
            "service workers": 'navigator.serviceWorker.register("x.js");',
            "beacons": 'navigator.sendBeacon("/x");',
        }
        for label, source in cases.items():
            with self.subTest(api=label):
                errors = VALIDATOR.validate_no_storage({"app.js": source})
                self.assertIn(label, " | ".join(errors))

    def test_repository_has_no_storage_apis(self) -> None:
        sources = {
            name: (ROOT / name).read_text(encoding="utf-8")
            for name in (
                "index.html",
                "404.html",
                "app.js",
                "status-model.js",
                "styles.css",
                "templates/index.html",
                "templates/404.html",
            )
        }
        self.assertEqual(VALIDATOR.validate_no_storage(sources), [])

    def test_pages_allow_self_hosted_fonts(self) -> None:
        for name in ("index.html", "404.html"):
            with self.subTest(page=name):
                self.assertIn("font-src 'self'", repository_page(name))

    # Brand mark and contrast ------------------------------------------

    def test_repository_brand_marks_are_the_approved_graphite_assets(self) -> None:
        manifest = repository_manifest()
        assets = {asset["name"]: asset for asset in manifest["assets"]}
        reverse = assets[VALIDATOR.REVERSE_LOCKUP]
        footer = assets[VALIDATOR.FOOTER_ICON]
        self.assertRegex(reverse["file"], r"^logo-reverse\.[0-9a-f]{12}\.svg$")
        self.assertRegex(footer["file"], r"^icon-color\.[0-9a-f]{12}\.svg$")
        self.assertEqual(
            VALIDATOR.svg_dimensions(ROOT / reverse["file"]), (284.0, 118.0)
        )
        self.assertEqual(VALIDATOR.svg_fills(ROOT / footer["file"]), {"#2255BC"})
        self.assertAlmostEqual(
            VALIDATOR.contrast_ratio("#2255BC", "#EDF1F7"), 5.99, places=2
        )
        self.assertAlmostEqual(
            VALIDATOR.contrast_ratio("#EDF1F7", "#080A0F"), 17.47, places=2
        )
        for name in ("index.html", "404.html"):
            with self.subTest(page=name):
                text = repository_page(name)
                self.assertIn(f'src="/{reverse["file"]}"', text)
                self.assertIn(f'src="/{footer["file"]}"', text)
                self.assertNotIn("logo-color.", text)
                self.assertIn('alt="SBK Techno"', text)
                self.assertIn('class="status-footer__mark"', text)

    def test_brand_marks_reject_a_colour_lockup_or_distortion(self) -> None:
        manifest = repository_manifest()
        page = repository_page("index.html")
        brand_css = next(
            (ROOT / asset["file"]).read_text(encoding="utf-8")
            for asset in manifest["assets"]
            if asset["name"] == "brand.css"
        )
        site_css = (ROOT / "styles.css").read_text(encoding="utf-8")
        assets = {asset["name"]: asset for asset in manifest["assets"]}
        colour = "logo-color.000000000000.svg"
        reverse = assets[VALIDATOR.REVERSE_LOCKUP]["file"]
        cases = {
            "colour lockup": (
                page.replace(f"/{reverse}", f"/{colour}", 1),
                "reverse lockup",
            ),
            "distorted": (
                page.replace('width="284" height="118"', 'width="96" height="40"', 1),
                "distorts the reverse lockup aspect ratio",
            ),
        }
        for name, (mutated, expected) in cases.items():
            with self.subTest(case=name):
                errors = VALIDATOR.validate_brand_marks(
                    ROOT,
                    {"index.html": mutated},
                    manifest,
                    site_css,
                    brand_css,
                    "#080A0F",
                    "#EDF1F7",
                )
                self.assertIn(expected, " | ".join(errors))

    def test_brand_marks_reject_recolouring_and_fading(self) -> None:
        manifest = repository_manifest()
        page = repository_page("index.html")
        site_css = (ROOT / "styles.css").read_text(encoding="utf-8")
        brand_css = next(
            (ROOT / asset["file"]).read_text(encoding="utf-8")
            for asset in manifest["assets"]
            if asset["name"] == "brand.css"
        )
        cases = {
            "filter": (
                site_css
                + "\n.status-footer__mark { filter: brightness(0) invert(1); }\n",
                "must not be recoloured",
            ),
            "opacity": (
                site_css + "\n.status-footer__mark { opacity: 0.75; }\n",
                "must not be faded",
            ),
        }
        for name, (mutated, expected) in cases.items():
            with self.subTest(name=name):
                errors = VALIDATOR.validate_brand_marks(
                    ROOT,
                    {"index.html": page},
                    manifest,
                    mutated,
                    brand_css,
                    "#080A0F",
                    "#EDF1F7",
                )
                self.assertIn(expected, " | ".join(errors))

    def test_header_lockup_contrast_uses_the_rendered_asset_and_surface(self) -> None:
        manifest = repository_manifest()
        assets = {asset["name"]: asset for asset in manifest["assets"]}
        reverse = assets[VALIDATOR.REVERSE_LOCKUP]["file"]
        footer = assets[VALIDATOR.FOOTER_ICON]["file"]
        page = repository_page("index.html")
        site_css = (ROOT / "styles.css").read_text(encoding="utf-8")
        brand_css = next(
            (ROOT / asset["file"]).read_text(encoding="utf-8")
            for asset in manifest["assets"]
            if asset["name"] == "brand.css"
        )
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary)
            shutil.copyfile(ROOT / reverse, copy_root / reverse)
            shutil.copyfile(ROOT / footer, copy_root / footer)
            approved = VALIDATOR.validate_brand_marks(
                copy_root,
                {"index.html": page},
                manifest,
                site_css,
                brand_css,
                "#080A0F",
                "#EDF1F7",
            )
            self.assertEqual(" | ".join(approved), "")
            recoloured = (ROOT / reverse).read_text(encoding="utf-8").replace(
                "#2255BC", "#111111"
            )
            (copy_root / reverse).write_text(recoloured, encoding="utf-8")
            errors = " | ".join(
                VALIDATOR.validate_brand_marks(
                    copy_root,
                    {"index.html": page},
                    manifest,
                    site_css,
                    brand_css,
                    "#080A0F",
                    "#EDF1F7",
                )
            )
            self.assertIn("approved fills", errors)
            self.assertIn("no contrast decision", errors)

    def test_header_surface_change_invalidates_the_exemption(self) -> None:
        manifest = repository_manifest()
        page = repository_page("index.html")
        brand_css = next(
            (ROOT / asset["file"]).read_text(encoding="utf-8")
            for asset in manifest["assets"]
            if asset["name"] == "brand.css"
        )
        site_css = (ROOT / "styles.css").read_text(encoding="utf-8").replace(
            "background: var(--sbk-canvas);",
            "background: var(--sbk-surface);",
            1,
        )
        errors = " | ".join(
            VALIDATOR.validate_brand_marks(
                ROOT,
                {"index.html": page},
                manifest,
                site_css,
                brand_css,
                "#080A0F",
                "#EDF1F7",
            )
        )
        self.assertIn("recorded logotype exemption", errors)

    def test_header_mark_cannot_override_the_brand_token(self) -> None:
        manifest = repository_manifest()
        page = repository_page("index.html")
        brand_css = next(
            (ROOT / asset["file"]).read_text(encoding="utf-8")
            for asset in manifest["assets"]
            if asset["name"] == "brand.css"
        )
        site_css = (ROOT / "styles.css").read_text(encoding="utf-8") + (
            "\n.status-brand .status-brand__mark { width: 96px; height: 40px; }\n"
        )
        errors = " | ".join(
            VALIDATOR.validate_brand_marks(
                ROOT,
                {"index.html": page},
                manifest,
                site_css,
                brand_css,
                "#080A0F",
                "#EDF1F7",
            )
        )
        self.assertIn("must not override width", errors)
        self.assertIn("must not override height", errors)

    def test_manifest_digest_drift_is_rejected(self) -> None:
        manifest = repository_manifest()
        reverse = next(
            asset
            for asset in manifest["assets"]
            if asset["name"] == VALIDATOR.REVERSE_LOCKUP
        )
        filename = reverse["file"]
        isolated = {
            "version": 1,
            "generatedBy": "validate_status_test",
            "assets": [reverse],
        }
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary)
            shutil.copyfile(ROOT / filename, copy_root / filename)
            references = {"index.html": filename}
            self.assertEqual(
                VALIDATOR.validate_manifest_assets(
                    copy_root, isolated, references, {}
                ),
                [],
            )
            (copy_root / filename).write_text("<svg/>", encoding="utf-8")
            errors = " | ".join(
                VALIDATOR.validate_manifest_assets(
                    copy_root, isolated, references, {}
                )
            )
            self.assertIn("SHA-256", errors)

    # Palette and document details -------------------------------------

    def test_status_text_palette_meets_contrast_minimum(self) -> None:
        manifest = repository_manifest()
        brand_css = next(
            asset["file"] for asset in manifest["assets"] if asset["name"] == "brand.css"
        )
        stylesheet = (ROOT / brand_css).read_text(encoding="utf-8")
        pairs = {
            "ink on paper": ("--sbk-ink", "--sbk-paper"),
            "paper on ink": ("--sbk-paper", "--sbk-ink"),
            "muted on paper": ("--sbk-muted", "--sbk-paper"),
            "muted on surface": ("--sbk-muted", "--sbk-surface"),
            "ink on surface": ("--sbk-ink", "--sbk-surface"),
            "ink on surface strong": ("--sbk-ink", "--sbk-surface-strong"),
            "accent on paper": ("--sbk-accent", "--sbk-paper"),
            "paper on accent": ("--sbk-paper", "--sbk-accent"),
            "muted on warning background": ("--sbk-muted", "--sbk-warning-bg"),
            "muted on danger background": ("--sbk-muted", "--sbk-danger-bg"),
            "warning on warning background": ("--sbk-warning", "--sbk-warning-bg"),
            "warning on paper": ("--sbk-warning", "--sbk-paper"),
            "danger on danger background": ("--sbk-danger", "--sbk-danger-bg"),
            "success on success background": ("--sbk-success", "--sbk-success-bg"),
            "maintenance on maintenance background": (
                "--sbk-maintenance",
                "--sbk-maintenance-bg",
            ),
            "unknown on unknown background": ("--sbk-unknown", "--sbk-unknown-bg"),
        }
        for mode in ("light", "dark"):
            variables = VALIDATOR.brand_properties(stylesheet, dark=(mode == "dark"))
            for name, (foreground, background) in pairs.items():
                with self.subTest(mode=mode, pair=name):
                    measured = VALIDATOR.contrast_ratio(
                        variables[foreground], variables[background]
                    )
                    self.assertGreaterEqual(
                        measured,
                        4.5,
                        f"{mode} {name} measured {measured:.2f}:1",
                    )

    def test_graphite_text_pairs_meet_contrast_minimum(self) -> None:
        manifest = repository_manifest()
        brand_css = next(
            asset["file"] for asset in manifest["assets"] if asset["name"] == "brand.css"
        )
        variables = VALIDATOR.brand_properties(
            (ROOT / brand_css).read_text(encoding="utf-8"), dark=True
        )
        pairs = {
            "text on canvas": ("--sbk-text", "--sbk-canvas"),
            "text on surface": ("--sbk-text", "--sbk-surface"),
            "text on raised": ("--sbk-text", "--sbk-raised"),
            "muted on canvas": ("--sbk-text-muted", "--sbk-canvas"),
            "muted on surface": ("--sbk-text-muted", "--sbk-surface"),
            "muted on raised": ("--sbk-text-muted", "--sbk-raised"),
            "text on sunken": ("--sbk-text", "--sbk-sunken"),
            "muted on sunken": ("--sbk-text-muted", "--sbk-sunken"),
            "faint on sunken": ("--sbk-text-faint", "--sbk-sunken"),
            "faint on canvas": ("--sbk-text-faint", "--sbk-canvas"),
            "faint on surface": ("--sbk-text-faint", "--sbk-surface"),
            "faint on raised": ("--sbk-text-faint", "--sbk-raised"),
            "signal on canvas": ("--sbk-signal", "--sbk-canvas"),
            "signal on sunken": ("--sbk-signal", "--sbk-sunken"),
            "signal on signal soft": ("--sbk-signal", "--sbk-signal-soft"),
            "text on signal soft": ("--sbk-text", "--sbk-signal-soft"),
            "muted on signal soft": ("--sbk-text-muted", "--sbk-signal-soft"),
            "on-signal on signal": ("--sbk-on-signal", "--sbk-signal"),
            "text on danger soft": ("--sbk-text", "--sbk-danger-soft"),
            "muted on danger soft": ("--sbk-text-muted", "--sbk-danger-soft"),
            "muted on warning soft": ("--sbk-text-muted", "--sbk-warning-soft"),
            "live on live soft": ("--sbk-live", "--sbk-live-soft"),
            "warning on warning soft": ("--sbk-warning", "--sbk-warning-soft"),
            "danger on danger soft": ("--sbk-danger", "--sbk-danger-soft"),
            "maintenance on maintenance soft": (
                "--sbk-maintenance",
                "--sbk-maintenance-soft",
            ),
            "unknown on unknown soft": ("--sbk-unknown", "--sbk-unknown-soft"),
            "live status word on canvas": ("--sbk-live", "--sbk-canvas"),
            "warning status word on canvas": ("--sbk-warning", "--sbk-canvas"),
            "danger status word on canvas": ("--sbk-danger", "--sbk-canvas"),
            "maintenance status word on canvas": (
                "--sbk-maintenance",
                "--sbk-canvas",
            ),
            "unknown status word on canvas": ("--sbk-unknown", "--sbk-canvas"),
        }
        for name, (foreground, background) in pairs.items():
            with self.subTest(pair=name):
                measured = VALIDATOR.contrast_ratio(
                    variables[foreground], variables[background]
                )
                self.assertGreaterEqual(measured, 4.5, f"{name} measured {measured:.2f}:1")

    def test_graphite_ui_boundaries_meet_contrast_minimum(self) -> None:
        manifest = repository_manifest()
        brand_css = next(
            asset["file"] for asset in manifest["assets"] if asset["name"] == "brand.css"
        )
        variables = VALIDATOR.brand_properties(
            (ROOT / brand_css).read_text(encoding="utf-8"), dark=True
        )
        pairs = {
            "focus on canvas": ("--sbk-focus", "--sbk-canvas"),
            "focus on danger soft": ("--sbk-focus", "--sbk-danger-soft"),
            "focus on warning soft": ("--sbk-focus", "--sbk-warning-soft"),
            "signal on danger soft": ("--sbk-signal", "--sbk-danger-soft"),
            "live on canvas": ("--sbk-live", "--sbk-canvas"),
            "warning on canvas": ("--sbk-warning", "--sbk-canvas"),
            "danger on canvas": ("--sbk-danger", "--sbk-canvas"),
            "maintenance on canvas": ("--sbk-maintenance", "--sbk-canvas"),
            "unknown on canvas": ("--sbk-unknown", "--sbk-canvas"),
            "live node on sunken": ("--sbk-live", "--sbk-sunken"),
            "warning node on sunken": ("--sbk-warning", "--sbk-sunken"),
            "danger node on sunken": ("--sbk-danger", "--sbk-sunken"),
            "maintenance node on sunken": ("--sbk-maintenance", "--sbk-sunken"),
            "unknown node on sunken": ("--sbk-unknown", "--sbk-sunken"),
        }
        for name, (foreground, background) in pairs.items():
            with self.subTest(pair=name):
                measured = VALIDATOR.contrast_ratio(
                    variables[foreground], variables[background]
                )
                self.assertGreaterEqual(measured, 3.0, f"{name} measured {measured:.2f}:1")

    def test_status_surface_is_graphite(self) -> None:
        manifest = repository_manifest()
        brand_css = next(
            asset["file"] for asset in manifest["assets"] if asset["name"] == "brand.css"
        )
        dark = VALIDATOR.brand_properties(
            (ROOT / brand_css).read_text(encoding="utf-8"), dark=True
        )
        self.assertEqual(dark.get("--sbk-canvas"), "#080A0F")
        for name in ("index.html", "404.html"):
            with self.subTest(page=name):
                text = repository_page(name)
                self.assertIn('data-theme="dark"', text)
                self.assertIn('<meta name="color-scheme" content="dark">', text)
                self.assertIn(
                    f'<meta name="theme-color" content="{dark["--sbk-canvas"]}">',
                    text,
                )

    def test_parse_utc_rejects_fractional_seconds(self) -> None:
        self.assertIsNone(VALIDATOR.parse_utc("2026-10-24T12:00:00.000Z"))
        parsed = VALIDATOR.parse_utc("2026-10-24T12:00:00Z")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo, timezone.utc)
        self.assertEqual(parsed.year, 2026)


if __name__ == "__main__":
    unittest.main()
