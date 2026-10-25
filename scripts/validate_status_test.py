#!/usr/bin/env python3
"""Tests for the shared status document validator."""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("validate_status.py")
SPEC = importlib.util.spec_from_file_location("validate_status", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def valid_page() -> str:
    return """<!doctype html>
<html lang="fr-CA" class="no-js" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#080A0F">
<title>État du service</title>
<link rel="stylesheet" href="styles.css">
</head>
<body>
<a class="sbk-skip" href="#contenu">Aller au contenu</a>
<header class="sbk-header">
<a class="sbk-brand" href="/"><img src="/logo.svg" alt="SBK Techno" width="96" height="40"></a>
</header>
<main id="contenu" tabindex="-1">
<section id="hero" data-status="unknown">
<h1>État du service</h1>
<p id="hero-status-text">État inconnu</p>
<p id="overall-summary">Aucun signal de vie reçu.</p>
<div id="vitals" aria-hidden="true" data-total="1">
<svg role="presentation" focusable="false"></svg>
</div>
</section>
<h2>Composantes</h2>
<p><span id="components-count">01</span> suivies</p>
<ul id="components-list">
<li class="component">
<h3>Portail</h3>
<p class="component-detail">Aucun signal de vie reçu pour le portail.</p>
<span class="pill" data-status="unknown">Inconnu</span>
<p class="component-meta"><time datetime="2026-10-25T13:42:57Z">25 octobre 2026 à 13 h 42 UTC</time></p>
</li>
</ul>
<p id="components-empty" hidden>Aucune composante déclarée.</p>
<h2>Incidents</h2>
<ol id="incidents-list"></ol>
<p id="incidents-empty">Aucun incident déclaré.</p>
</main>
<footer class="sbk-footer"><p>Exploité par SBK Techno</p></footer>
<noscript><p>Instantané statique du <time datetime="2026-10-25T13:42:57Z">25 octobre 2026</time>.</p></noscript>
</body>
</html>"""


def ratio(foreground: str, background: str) -> float:
    return VALIDATOR.contrast_ratio(foreground, background)


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

    def test_render_state_contract_covers_null_and_failed_fetch(self) -> None:
        node = subprocess.run(
            ["node", str(Path(__file__).with_name("status_model_test.mjs"))],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            node.returncode,
            0,
            f"status model contract failed:\n{node.stdout}\n{node.stderr}",
        )
        self.assertIn("status model ok", node.stdout)

    def test_accepts_valid_page(self) -> None:
        self.assertEqual(
            VALIDATOR.validate_page(
                "index.html",
                valid_page(),
                require_status=True,
                theme_color="#080A0F",
            ),
            [],
        )
        self.assertEqual(
            VALIDATOR.validate_page("404.html", valid_page(), theme_color="#080A0F"),
            [],
        )

    def test_rejects_page_regressions(self) -> None:
        def run(mutate) -> str:
            return " | ".join(
                VALIDATOR.validate_page(
                    "index.html",
                    mutate(valid_page()),
                    require_status=True,
                    theme_color="#080A0F",
                )
            )

        cases = {
            "missing language": (
                lambda p: p.replace(' lang="fr-CA"', ""),
                "lang attribute",
            ),
            "non-french language": (
                lambda p: p.replace("fr-CA", "en-CA"),
                "French",
            ),
            "two h1": (
                lambda p: p.replace(
                    "<h1>État du service</h1>", "<h1>Un</h1><h1>Deux</h1>"
                ),
                "exactly one <h1>",
            ),
            "first heading not h1": (
                lambda p: p.replace(
                    "<h1>État du service</h1>",
                    "<h2>État du service</h2><h1>Autre</h1>",
                ),
                "first heading",
            ),
            "heading skip": (
                lambda p: p.replace("<h3>Portail</h3>", "<h4>Portail</h4>"),
                "heading order skips",
            ),
            "missing main": (
                lambda p: p.replace(
                    '<main id="contenu" tabindex="-1">', '<div id="contenu">'
                ).replace("</main>", "</div>"),
                "<main> landmark",
            ),
            "missing footer": (
                lambda p: p.replace(
                    '<footer class="sbk-footer"><p>Exploité par SBK Techno</p></footer>',
                    "",
                ),
                "contentinfo landmark",
            ),
            "missing skip link": (
                lambda p: p.replace(
                    '<a class="sbk-skip" href="#contenu">Aller au contenu</a>', ""
                ),
                "skip link",
            ),
            "missing skip target": (
                lambda p: p.replace('href="#contenu"', 'href="#absent"'),
                "does not exist",
            ),
            "skip target not focusable": (
                lambda p: p.replace(' tabindex="-1"', ""),
                "tabindex='-1'",
            ),
            "time without datetime": (
                lambda p: p.replace(' datetime="2026-10-25T13:42:57Z"', "", 1),
                "missing its datetime",
            ),
            "time with invalid datetime": (
                lambda p: p.replace(
                    'datetime="2026-10-25T13:42:57Z"', 'datetime="hier"', 1
                ),
                "not a valid ISO 8601",
            ),
            "time without text": (
                lambda p: p.replace(
                    ">25 octobre 2026 à 13 h 42 UTC</time>", "></time>", 1
                ),
                "human-readable text",
            ),
            "external script": (
                lambda p: p.replace(
                    "</body>",
                    '<script src="https://cdn.example/app.js"></script></body>',
                ),
                "third-party origin",
            ),
            "external image": (
                lambda p: p.replace(
                    "</body>",
                    '<img src="https://cdn.example/logo.png" alt=""></body>',
                ),
                "third-party origin",
            ),
            "external stylesheet": (
                lambda p: p.replace(
                    "</head>",
                    '<link rel="stylesheet" href="https://cdn.example/style.css"></head>',
                ),
                "third-party origin",
            ),
            "external srcset": (
                lambda p: p.replace(
                    "</body>",
                    '<img src="/logo.svg" srcset="https://cdn.example/logo.png 1x" alt=""></body>',
                ),
                "third-party origin",
            ),
            "inline handler": (
                lambda p: p.replace('<h1>', '<h1 onclick="alert(1)">', 1),
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
            "component without snapshot note": (
                lambda p: p.replace(
                    '<p class="component-meta"><time datetime="2026-10-25T13:42:57Z">25 octobre 2026 à 13 h 42 UTC</time></p>',
                    "",
                    1,
                ),
                "component row 1 needs a snapshot note",
            ),
            "component without name": (
                lambda p: p.replace("<h3>Portail</h3>", "", 1),
                "component name",
            ),
            "component without detail": (
                lambda p: p.replace(
                    '<p class="component-detail">Aucun signal de vie reçu pour le portail.</p>',
                    "",
                    1,
                ),
                "component detail",
            ),
            "component without label": (
                lambda p: p.replace(
                    '<span class="pill" data-status="unknown">Inconnu</span>', "", 1
                ),
                "text status label",
            ),
            "component invalid status": (
                lambda p: p.replace(
                    '<span class="pill" data-status="unknown">',
                    '<span class="pill" data-status="green">',
                    1,
                ),
                "invalid status",
            ),
            "components without rows": (
                lambda p: re.sub(
                    r'<ul id="components-list">.*?</ul>',
                    '<ul id="components-list"></ul>',
                    p,
                    flags=re.S,
                ).replace(
                    '<span id="components-count">01</span>',
                    '<span id="components-count">01</span>',
                ),
                "#components-count must not claim a count",
            ),
            "components empty visible": (
                lambda p: p.replace(
                    '<p id="components-empty" hidden>', '<p id="components-empty">'
                ),
                "must be hidden",
            ),
            "incidents empty hidden": (
                lambda p: p.replace(
                    '<p id="incidents-empty">Aucun incident déclaré.</p>',
                    '<p id="incidents-empty" hidden>Aucun incident déclaré.</p>',
                ),
                "must be visible",
            ),
            "missing noscript": (
                lambda p: p.replace(
                    '<noscript><p>Instantané statique du '
                    '<time datetime="2026-10-25T13:42:57Z">25 octobre 2026</time>.'
                    "</p></noscript>",
                    "",
                ),
                "noscript",
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
                lambda p: p.replace(' class="no-js"', ""),
                "no-js",
            ),
            "external preconnect": (
                lambda p: p.replace(
                    "</head>",
                    '<link rel="preconnect" href="https://cdn.example"></head>',
                ),
                "third-party origin",
            ),
            "missing color scheme": (
                lambda p: p.replace('<meta name="color-scheme" content="dark">', ""),
                "color-scheme meta",
            ),
            "light color scheme": (
                lambda p: p.replace(
                    '<meta name="color-scheme" content="dark">',
                    '<meta name="color-scheme" content="light">',
                ),
                "must include dark",
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
                    '<a class="sbk-brand" href="/"><img src="/logo.svg" '
                    'alt="SBK Techno" width="96" height="40"></a>',
                    "",
                ),
                "header mark",
            ),
            "mark non-numeric": (
                lambda p: p.replace('width="96" height="40"', 'width="wide" height="40"', 1),
                "numeric width and height",
            ),
            "missing hero": (
                lambda p: p.replace(
                    '<section id="hero" data-status="unknown">', "<section>"
                ),
                "missing #hero",
            ),
            "invalid hero status": (
                lambda p: p.replace(
                    '<section id="hero" data-status="unknown">',
                    '<section id="hero" data-status="green">',
                ),
                "valid data-status",
            ),
            "hidden status word": (
                lambda p: p.replace(
                    '<p id="hero-status-text">',
                    '<p id="hero-status-text" aria-hidden="true">',
                ),
                "not hide it",
            ),
            "icon status word": (
                lambda p: p.replace(
                    '<p id="hero-status-text">État inconnu</p>',
                    '<p id="hero-status-text">État inconnu<svg></svg></p>',
                ),
                "not an icon",
            ),
            "missing vitals": (
                lambda p: re.sub(
                    r'<div id="vitals".*?</div>', "", p, flags=re.S
                ),
                "missing #vitals",
            ),
            "vitals not hidden": (
                lambda p: p.replace(
                    '<div id="vitals" aria-hidden="true" data-total="1">',
                    '<div id="vitals" data-total="1">',
                ),
                "must be aria-hidden",
            ),
            "vitals total mismatch": (
                lambda p: p.replace('data-total="1"', 'data-total="2"'),
                "must match the 1 static component rows",
            ),
            "missing component count": (
                lambda p: p.replace(
                    '<span id="components-count">01</span>',
                    '<span id="components-count"></span>',
                ),
                "#components-count",
            ),
        }
        for name, (mutate, expected) in cases.items():
            with self.subTest(name=name):
                errors = run(mutate)
                self.assertNotEqual(errors, "")
                self.assertIn(expected, errors)

    def test_rejects_third_party_stylesheet_resources(self) -> None:
        self.assertNotEqual(
            VALIDATOR.validate_css(
                "styles.css", "a { background: url(https://cdn.example/x.png); }"
            ),
            "",
        )
        self.assertNotEqual(
            VALIDATOR.validate_css(
                "styles.css", '@import "https://cdn.example/x.css";'
            ),
            "",
        )
        self.assertEqual(
            VALIDATOR.validate_css("styles.css", "a { background: url(/x.png); }"),
            [],
        )

    def test_repository_site_is_valid(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.assertEqual(VALIDATOR.validate_site(root), [])

    def test_repository_brand_marks_are_the_approved_graphite_assets(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (root / "brand.manifest.json").read_text(encoding="utf-8")
        )
        assets = {asset["name"]: asset for asset in manifest["assets"]}
        reverse = assets[VALIDATOR.REVERSE_LOCKUP]
        footer = assets[VALIDATOR.FOOTER_ICON]
        self.assertRegex(reverse["file"], r"^logo-reverse\.[0-9a-f]{12}\.svg$")
        self.assertRegex(footer["file"], r"^icon-color\.[0-9a-f]{12}\.svg$")
        self.assertEqual(
            VALIDATOR.svg_dimensions(root / reverse["file"]), (284.0, 118.0)
        )
        self.assertEqual(VALIDATOR.svg_fills(root / footer["file"]), {"#2255BC"})
        self.assertAlmostEqual(
            VALIDATOR.contrast_ratio("#2255BC", "#EDF1F7"), 5.99, places=2
        )
        self.assertAlmostEqual(
            VALIDATOR.contrast_ratio("#EDF1F7", "#080A0F"), 17.47, places=2
        )
        for name in ("index.html", "404.html"):
            with self.subTest(page=name):
                text = (root / name).read_text(encoding="utf-8")
                self.assertIn(f'src="/{reverse["file"]}"', text)
                self.assertIn(f'src="/{footer["file"]}"', text)
                self.assertNotIn("logo-color.", text)
                self.assertIn('alt="SBK Techno"', text)
                self.assertIn('class="status-footer__mark"', text)

    def test_brand_marks_reject_a_colour_lockup_or_distortion(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (root / "brand.manifest.json").read_text(encoding="utf-8")
        )
        stylesheets = {
            "styles.css": (root / "styles.css").read_text(encoding="utf-8")
        }
        page = (root / "index.html").read_text(encoding="utf-8")
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
                    root,
                    {"index.html": mutated},
                    manifest,
                    stylesheets,
                    "#080A0F",
                    "#EDF1F7",
                )
                self.assertIn(expected, " | ".join(errors))

    def test_brand_marks_reject_recolouring_and_fading(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (root / "brand.manifest.json").read_text(encoding="utf-8")
        )
        page = (root / "index.html").read_text(encoding="utf-8")
        css = (root / "styles.css").read_text(encoding="utf-8")
        cases = {
            "filter": (
                css + "\n.status-footer__mark { filter: brightness(0) invert(1); }\n",
                "must not be recoloured",
            ),
            "opacity": (
                css + "\n.status-footer__mark { opacity: 0.75; }\n",
                "must not be faded",
            ),
        }
        for name, (mutated, expected) in cases.items():
            with self.subTest(case=name):
                errors = VALIDATOR.validate_brand_marks(
                    root,
                    {"index.html": page},
                    manifest,
                    {"styles.css": mutated},
                    "#080A0F",
                    "#EDF1F7",
                )
                self.assertIn(expected, " | ".join(errors))

    def test_rejects_cookie_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "status.json").write_text(
                json.dumps(valid_document()), encoding="utf-8"
            )
            (root / "CNAME").write_bytes(VALIDATOR.CNAME)
            (root / ".nojekyll").write_text("", encoding="utf-8")
            (root / "styles.css").write_text("body { color: #000; }", encoding="utf-8")
            (root / "app.js").write_text(
                "document.cookie = 'session=1';", encoding="utf-8"
            )
            (root / "index.html").write_text(valid_page(), encoding="utf-8")
            (root / "404.html").write_text(valid_page(), encoding="utf-8")

            errors = VALIDATOR.validate_site(root)
            self.assertIn("document.cookie is not allowed", " | ".join(errors))

    def test_rejects_unreferenced_published_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "orphan.svg").write_text("<svg/>", encoding="utf-8")
            manifest = {
                "assets": [
                    {"name": "orphan.svg", "file": "orphan.svg", "publish": True}
                ]
            }
            errors = VALIDATOR.validate_manifest_assets(root, manifest, {}, {})
            self.assertIn("not referenced", " | ".join(errors))

    def test_footer_icon_contrast_on_graphite(self) -> None:
        self.assertAlmostEqual(
            VALIDATOR.contrast_ratio("#2255BC", "#080A0F"), 2.92, places=2
        )
        self.assertAlmostEqual(
            VALIDATOR.contrast_ratio("#2255BC", "#EDF1F7"), 5.99, places=2
        )
        self.assertAlmostEqual(
            VALIDATOR.contrast_ratio("#EDF1F7", "#080A0F"), 17.47, places=2
        )

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
        root = Path(__file__).resolve().parent.parent
        sources = {
            name: (root / name).read_text(encoding="utf-8")
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

    def test_no_js_snapshot_is_honest_for_operational_and_null_heartbeat(self) -> None:
        root = Path(__file__).resolve().parent.parent
        text = (root / "index.html").read_text(encoding="utf-8")
        operational = valid_document()
        operational.update(
            overall="operational",
            summary_fr="Tout va bien.",
            summary_en="All good.",
            generated_at="2026-10-24T12:00:00Z",
            expires_at="2026-10-24T12:05:00Z",
        )
        null_heartbeat = valid_document()
        null_heartbeat.update(
            generated_at=None,
            expires_at=None,
            overall="unknown",
            summary_fr="Aucun signal de vie reçu.",
            summary_en="No heartbeat received.",
        )
        for name, document in {
            "operational": operational,
            "null heartbeat": null_heartbeat,
        }.items():
            with self.subTest(fixture=name):
                self.assertEqual(
                    VALIDATOR.validate_no_js_snapshot("index.html", text), []
                )
                self.assertNotIn(document["summary_fr"], text)
                if document["generated_at"] is not None:
                    self.assertNotIn(document["generated_at"], text)
                self.assertIn(VALIDATOR.NO_JS_PHRASE, text.lower())

    def test_no_js_snapshot_rejects_a_hardcoded_status_or_time(self) -> None:
        root = Path(__file__).resolve().parent.parent
        text = (root / "index.html").read_text(encoding="utf-8")
        broken = text.replace(
            '<span id="hero-status-text">non affiché</span>',
            '<span id="hero-status-text">opérationnel</span>',
            1,
        ).replace(
            "</body>",
            '<time datetime="2026-10-25T13:56:03Z">25 octobre 2026</time></body>',
            1,
        )
        errors = " | ".join(VALIDATOR.validate_no_js_snapshot("index.html", broken))
        self.assertIn("must not hard-code <time>", errors)
        self.assertIn("must say the published state is not displayed", errors)

    def test_no_js_snapshot_rejects_a_fabricated_count_or_row(self) -> None:
        root = Path(__file__).resolve().parent.parent
        text = (root / "index.html").read_text(encoding="utf-8")
        numeric = text.replace(
            '<p class="section-count" id="components-count">Non affiché</p>',
            '<p class="section-count" id="components-count">01</p>',
            1,
        )
        self.assertIn(
            "#components-count must not claim a count",
            " | ".join(VALIDATOR.validate_no_js_snapshot("index.html", numeric)),
        )

        row = text.replace(
            '<ul class="components" id="components-list"></ul>',
            '<ul class="components" id="components-list">'
            '<li class="component" data-status="unknown">'
            "<h3>Instantané non rendu</h3></li></ul>",
            1,
        )
        self.assertIn(
            "the no-JS component list must be empty",
            " | ".join(VALIDATOR.validate_no_js_snapshot("index.html", row)),
        )

    def test_header_lockup_contrast_uses_the_rendered_asset_and_surface(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (root / "brand.manifest.json").read_text(encoding="utf-8")
        )
        assets = {asset["name"]: asset for asset in manifest["assets"]}
        reverse = assets[VALIDATOR.REVERSE_LOCKUP]["file"]
        footer = assets[VALIDATOR.FOOTER_ICON]["file"]
        page = (root / "index.html").read_text(encoding="utf-8")
        stylesheet = (root / "styles.css").read_text(encoding="utf-8")
        brand_stylesheet = next(
            (root / asset["file"]).read_text(encoding="utf-8")
            for asset in manifest["assets"]
            if asset["name"] == "brand.css"
        )
        stylesheets = {
            "styles.css": stylesheet,
            "brand.c0268e99a0fe.css": brand_stylesheet,
        }

        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary)
            shutil.copyfile(root / reverse, copy_root / reverse)
            shutil.copyfile(root / footer, copy_root / footer)

            approved = VALIDATOR.validate_brand_marks(
                copy_root,
                {"index.html": page},
                manifest,
                stylesheets,
                "#080A0F",
                "#EDF1F7",
            )
            self.assertEqual(" | ".join(approved), "")

            recoloured = (root / reverse).read_text(encoding="utf-8").replace(
                "#2255BC", "#111111"
            )
            (copy_root / reverse).write_text(recoloured, encoding="utf-8")
            errors = " | ".join(
                VALIDATOR.validate_brand_marks(
                    copy_root,
                    {"index.html": page},
                    manifest,
                    stylesheets,
                    "#080A0F",
                    "#EDF1F7",
                )
            )
            self.assertIn("approved fills", errors)
            self.assertIn("no contrast decision", errors)

    def test_header_surface_change_invalidates_the_exemption(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (root / "brand.manifest.json").read_text(encoding="utf-8")
        )
        page = (root / "index.html").read_text(encoding="utf-8")
        stylesheet = (root / "styles.css").read_text(encoding="utf-8")
        brand_stylesheet = next(
            (root / asset["file"]).read_text(encoding="utf-8")
            for asset in manifest["assets"]
            if asset["name"] == "brand.css"
        )
        surface = stylesheet.replace(
            "background: var(--sbk-canvas);",
            "background: var(--sbk-surface);",
            1,
        )
        errors = " | ".join(
            VALIDATOR.validate_brand_marks(
                root,
                {"index.html": page},
                manifest,
                {"styles.css": surface, "brand.c0268e99a0fe.css": brand_stylesheet},
                "#080A0F",
                "#EDF1F7",
            )
        )
        self.assertIn("recorded logotype exemption", errors)

    def test_header_mark_cannot_override_the_brand_token(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (root / "brand.manifest.json").read_text(encoding="utf-8")
        )
        page = (root / "index.html").read_text(encoding="utf-8")
        stylesheet = (root / "styles.css").read_text(encoding="utf-8")
        brand_stylesheet = next(
            (root / asset["file"]).read_text(encoding="utf-8")
            for asset in manifest["assets"]
            if asset["name"] == "brand.css"
        )
        overridden = stylesheet + (
            "\n.status-brand .status-brand__mark { width: 96px; height: 40px; }\n"
        )
        errors = " | ".join(
            VALIDATOR.validate_brand_marks(
                root,
                {"index.html": page},
                manifest,
                {
                    "styles.css": overridden,
                    "brand.c0268e99a0fe.css": brand_stylesheet,
                },
                "#080A0F",
                "#EDF1F7",
            )
        )
        self.assertIn("must not override width", errors)
        self.assertIn("must not override height", errors)

    def test_manifest_digest_drift_is_rejected(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (root / "brand.manifest.json").read_text(encoding="utf-8")
        )
        reverse = next(
            asset for asset in manifest["assets"]
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
            shutil.copyfile(root / filename, copy_root / filename)
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

    def test_pages_allow_self_hosted_fonts(self) -> None:
        root = Path(__file__).resolve().parent.parent
        for name in ("index.html", "404.html"):
            with self.subTest(page=name):
                text = (root / name).read_text(encoding="utf-8")
                self.assertIn(
                    "font-src 'self'",
                    text,
                    f"{name}: the CSP must allow the self-hosted Archivo fonts",
                )

    def test_main_fails_on_invalid_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "status.json").write_text(
                json.dumps(valid_document()), encoding="utf-8"
            )
            (root / "CNAME").write_bytes(VALIDATOR.CNAME)
            (root / ".nojekyll").write_text("", encoding="utf-8")
            (root / "styles.css").write_text("body { color: #000; }", encoding="utf-8")
            (root / "app.js").write_text("'use strict';", encoding="utf-8")
            (root / "index.html").write_text(
                valid_page().replace(' lang="fr-CA"', ""), encoding="utf-8"
            )
            (root / "404.html").write_text(valid_page(), encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = VALIDATOR.main(["--root", str(root)])

            self.assertEqual(code, 1)
            output = stderr.getvalue()
            self.assertIn("status site validation failed", output)
            self.assertIn("lang attribute", output)

    def test_status_text_palette_meets_contrast_minimum(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "brand.manifest.json").read_text(encoding="utf-8"))
        brand_css = next(
            asset["file"] for asset in manifest["assets"] if asset["name"] == "brand.css"
        )
        stylesheet = (root / brand_css).read_text(encoding="utf-8")

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
                    self.assertIn(foreground, variables)
                    self.assertIn(background, variables)
                    measured = ratio(variables[foreground], variables[background])
                    self.assertGreaterEqual(
                        measured,
                        4.5,
                        f"{mode} {name} measured {measured:.2f}:1, "
                        "below the 4.5:1 body-text minimum",
                    )

    def test_graphite_text_pairs_meet_contrast_minimum(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "brand.manifest.json").read_text(encoding="utf-8"))
        brand_css = next(
            asset["file"] for asset in manifest["assets"] if asset["name"] == "brand.css"
        )
        stylesheet = (root / brand_css).read_text(encoding="utf-8")
        variables = VALIDATOR.brand_properties(stylesheet, dark=True)

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
            "maintenance status word on canvas": ("--sbk-maintenance", "--sbk-canvas"),
            "unknown status word on canvas": ("--sbk-unknown", "--sbk-canvas"),
        }
        for name, (foreground, background) in pairs.items():
            with self.subTest(pair=name):
                self.assertIn(foreground, variables)
                self.assertIn(background, variables)
                measured = ratio(variables[foreground], variables[background])
                self.assertGreaterEqual(
                    measured,
                    4.5,
                    f"{name} measured {measured:.2f}:1, below the 4.5:1 body-text minimum",
                )

    def test_graphite_ui_boundaries_meet_contrast_minimum(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "brand.manifest.json").read_text(encoding="utf-8"))
        brand_css = next(
            asset["file"] for asset in manifest["assets"] if asset["name"] == "brand.css"
        )
        stylesheet = (root / brand_css).read_text(encoding="utf-8")
        variables = VALIDATOR.brand_properties(stylesheet, dark=True)

        # Interactive boundaries and non-text status indicators. Decorative
        # hairlines are not controls and are not measured here.
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
                self.assertIn(foreground, variables)
                self.assertIn(background, variables)
                measured = ratio(variables[foreground], variables[background])
                self.assertGreaterEqual(
                    measured,
                    3.0,
                    f"{name} measured {measured:.2f}:1, below the 3:1 UI minimum",
                )

    def test_status_surface_is_graphite(self) -> None:
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "brand.manifest.json").read_text(encoding="utf-8"))
        brand_css = next(
            asset["file"] for asset in manifest["assets"] if asset["name"] == "brand.css"
        )
        stylesheet = (root / brand_css).read_text(encoding="utf-8")
        dark = VALIDATOR.brand_properties(stylesheet, dark=True)

        self.assertEqual(dark.get("--sbk-canvas"), "#080A0F")
        for name in ("index.html", "404.html"):
            with self.subTest(page=name):
                text = (root / name).read_text(encoding="utf-8")
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
