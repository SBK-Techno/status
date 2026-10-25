#!/usr/bin/env python3
"""Tests for the shared status document validator."""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("validate_status.py")
SPEC = importlib.util.spec_from_file_location("validate_status", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def valid_page() -> str:
    return """<!doctype html>
<html lang="fr-CA" data-theme="dark">
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
<time datetime="2026-10-25T13:42:57Z">25 octobre 2026 à 13 h 42 UTC</time>
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


def channel(value: int) -> float:
    value = value / 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def ratio(foreground: str, background: str) -> float:
    first, second = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (first + 0.05) / (second + 0.05)


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
            "component without time": (
                lambda p: p.replace(
                    '<time datetime="2026-10-25T13:42:57Z">25 octobre 2026 à 13 h 42 UTC</time>',
                    "",
                    1,
                ),
                "component row 1 needs a <time>",
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
                ),
                "static <li> rows",
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
            "mark too tall": (
                lambda p: p.replace('width="96" height="40"', 'width="96" height="80"', 1),
                "must be between 32 and 48",
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
