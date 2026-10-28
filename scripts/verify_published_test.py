#!/usr/bin/env python3
"""Offline fixtures for the published-site verifier.

The fixture origin is a loopback HTTP server built from the real repository's
build output. Each test changes exactly one live response so the matching
check fails; no test reaches the network.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import heartbeat_signature
import site_build
import verify_published

ROOT = SCRIPTS.parent
NON_SITE = {".git", ".github", ".toolchain", "scripts", "__pycache__"}
REQUIRED_CHECKS = {
    "local-build",
    "status-live",
    "status-schema",
    "status-artifact",
    "status-signature",
    "pages-live",
    "page-artifact",
    "page-assets",
    "asset-integrity",
    "asset-transitive",
    "snapshot",
    "cache-html",
    "cache-conditional",
    "cache-assets",
    "no-listing",
    "no-cookie",
}


@dataclass
class Route:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    cache: str = "max-age=600"
    etag: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    allow_conditional: bool = True
    conditional_body: bytes = b""

    def __post_init__(self) -> None:
        if not self.etag:
            digest = hashlib.sha256(self.body).hexdigest()[:16]
            self.etag = f'"{digest}"'


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def version_string(self) -> str:
        return "GitHub.com"

    def do_GET(self) -> None:
        site: FixtureSite = self.server.site  # type: ignore[attr-defined]
        path = urllib.parse.urlsplit(self.path).path
        route = site.routes.get(path)
        if route is None:
            route = Route(
                status=404,
                body=b"<html><body>Not found</body></html>",
            )
        if route.allow_conditional:
            if self.headers.get("If-None-Match") == route.etag:
                self.send_response(304)
                for name, value in route.headers:
                    self.send_header(name, value)
                self.send_header("ETag", route.etag)
                self.send_header("Cache-Control", route.cache)
                self.send_header("Content-Length", str(len(route.conditional_body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if route.conditional_body:
                    self.wfile.write(route.conditional_body)
                self.close_connection = True
                return
        self.send_response(route.status)
        for name, value in route.headers:
            self.send_header(name, value)
        self.send_header("Content-Type", route.content_type)
        self.send_header("Cache-Control", route.cache)
        self.send_header("ETag", route.etag)
        self.send_header("Content-Length", str(len(route.body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if route.body:
            self.wfile.write(route.body)
        self.close_connection = True

    def log_message(self, *_args) -> None:
        return


class FixtureSite:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.routes: dict[str, Route] = {}
        self.reset()

    def reset(self) -> None:
        self.routes = {}
        plan = verify_published.build_plan(self.root)
        for page in plan.pages.values():
            self.add(
                verify_published.PAGE_PATHS[page.name],
                page.data,
                "text/html; charset=utf-8",
            )
        self.add(
            "/status.json",
            plan.status_bytes,
            "application/json; charset=utf-8",
        )
        for asset in plan.assets.values():
            self.add(
                asset.path,
                (self.root / asset.file).read_bytes(),
                asset.media_type,
            )
        for path in ("/assets/", "/assets", "/.git/"):
            self.add(
                path,
                b"<html><body>Not found</body></html>",
                "text/html; charset=utf-8",
                status=404,
            )

    def add(
        self,
        path: str,
        body: bytes,
        content_type: str,
        *,
        status: int = 200,
        cache: str = "max-age=600",
        headers: tuple[tuple[str, str], ...] = (),
    ) -> Route:
        route = Route(
            status=status,
            body=body,
            content_type=content_type,
            cache=cache,
            headers=headers,
        )
        self.routes[path] = route
        return route


class FixtureServer:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.httpd.daemon_threads = True
        self.site = FixtureSite(ROOT)
        self.httpd.site = self.site  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.base = f"http://{host}:{port}/"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


class VerifyPublishedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = FixtureServer()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.close()

    def setUp(self) -> None:
        self.server.site.reset()
        self.plan = verify_published.build_plan(ROOT)

    def verify(self, **kwargs) -> verify_published.Report:
        return verify_published.verify(
            ROOT,
            self.server.base,
            timeout=5,
            **kwargs,
        )

    @staticmethod
    def result(
        report: verify_published.Report, name: str
    ) -> verify_published.CheckResult:
        return next(result for result in report.results if result.name == name)

    def assert_check_fails(
        self,
        report: verify_published.Report,
        name: str,
        text: str | None = None,
    ) -> None:
        result = self.result(report, name)
        self.assertFalse(result.ok, f"{name} unexpectedly passed")
        self.assertFalse(result.skipped, f"{name} unexpectedly skipped")
        if text is not None:
            self.assertIn(text, result.detail)

    def assert_check_passes(self, report: verify_published.Report, name: str) -> None:
        result = self.result(report, name)
        self.assertTrue(result.ok, result.detail)
        self.assertFalse(result.skipped)

    def test_the_built_site_passes_every_published_check(self) -> None:
        report = self.verify()
        self.assertTrue(report.ok, verify_published.render(report))
        self.assertEqual({result.name for result in report.results}, REQUIRED_CHECKS)

    def test_missing_asset_fails_the_live_asset_check(self) -> None:
        asset = next(
            asset for asset in self.plan.assets.values() if not asset.transitive
        )
        del self.server.site.routes[asset.path]
        report = self.verify()
        self.assert_check_fails(report, "page-assets", "404")

    def test_stale_digest_fails_the_snapshot_and_binding(self) -> None:
        route = self.server.site.routes["/status.json"]
        route.body = route.body + b" "
        report = self.verify()
        self.assert_check_fails(report, "snapshot", "digest")
        self.assert_check_fails(report, "status-artifact", "differs")

    def test_schema_version_is_enforced(self) -> None:
        document = json.loads(self.plan.status_bytes)
        document["version"] = 2
        self.server.site.routes["/status.json"].body = (
            json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        report = self.verify()
        self.assert_check_fails(report, "status-schema", "version")

    def test_bad_signature_fails_and_no_key_is_printed(self) -> None:
        key = b"fixture-signing-key"
        good = heartbeat_signature.sign(self.plan.status_bytes, key)
        passed = self.verify(signature=good, hmac_key=key)
        self.assertTrue(passed.ok, verify_published.render(passed))
        self.assert_check_passes(passed, "status-signature")
        self.assertNotIn(key.decode("utf-8"), verify_published.render(passed))

        failed = self.verify(signature="0" * 64, hmac_key=key)
        self.assert_check_fails(failed, "status-signature", "mismatch")
        self.assertNotIn(key.decode("utf-8"), verify_published.render(failed))

    def test_wrong_html_cache_fails(self) -> None:
        self.server.site.routes["/"].cache = "max-age=31536000"
        report = self.verify()
        self.assert_check_fails(report, "cache-html", "exceeds")

    def test_wrong_asset_cache_fails(self) -> None:
        asset = next(
            asset for asset in self.plan.assets.values() if not asset.transitive
        )
        self.server.site.routes[asset.path].cache = "no-store"
        report = self.verify()
        self.assert_check_fails(report, "cache-assets", "must not be private")

    def test_immutable_without_a_long_max_age_fails(self) -> None:
        asset = next(
            asset for asset in self.plan.assets.values() if not asset.transitive
        )
        self.server.site.routes[asset.path].cache = "public, max-age=600, immutable"
        report = self.verify()
        self.assert_check_fails(report, "cache-assets", "immutable requires")

    def test_edge_policy_requires_immutable_assets(self) -> None:
        report = self.verify(host_policy="edge")
        self.assert_check_fails(report, "cache-assets", "expected immutable")

    def test_304_with_a_body_fails(self) -> None:
        self.server.site.routes["/"].conditional_body = b"oops"
        report = self.verify()
        self.assert_check_fails(report, "cache-conditional", "Content-Length")

    def test_missing_304_fails(self) -> None:
        self.server.site.routes["/"].allow_conditional = False
        report = self.verify()
        self.assert_check_fails(report, "cache-conditional", "expected 304")

    def test_directory_listing_fails(self) -> None:
        self.server.site.add(
            "/assets/",
            b"<title>Index of /assets</title>",
            "text/html; charset=utf-8",
        )
        report = self.verify()
        self.assert_check_fails(report, "no-listing", "expected 403 or 404")

    def test_set_cookie_fails_without_printing_the_value(self) -> None:
        self.server.site.routes["/"].headers = (
            ("Set-Cookie", "session=fixture-value; Path=/"),
        )
        report = self.verify()
        self.assert_check_fails(report, "no-cookie", "Set-Cookie")
        self.assertNotIn("fixture-value", verify_published.render(report))

    def test_wrong_content_type_fails(self) -> None:
        asset = next(
            asset for asset in self.plan.assets.values() if not asset.transitive
        )
        self.server.site.routes[asset.path].content_type = "text/plain"
        report = self.verify()
        self.assert_check_fails(report, "asset-integrity", "media type")

    def test_extra_reference_fails(self) -> None:
        route = self.server.site.routes["/"]
        page = route.body.decode("utf-8")
        route.body = page.replace(
            "</body>", '<script src="/assets/evil.js"></script></body>'
        ).encode("utf-8")
        report = self.verify()
        self.assert_check_fails(report, "page-assets", "outside the build")

    def test_asset_names_come_from_the_rebuilt_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="status-verify-") as temporary:
            root = Path(temporary)
            for item in ROOT.iterdir():
                if item.name in NON_SITE:
                    continue
                target = root / item.name
                if item.is_dir():
                    shutil.copytree(item, target)
                else:
                    shutil.copyfile(item, target)
            app = root / "app.js"
            app.write_text(
                app.read_text(encoding="utf-8") + "\n// verifier rebuild fixture\n",
                encoding="utf-8",
            )
            self.assertEqual(site_build.write_site(root), [])

            rebuilt = verify_published.build_plan(root)
            manifest = site_build.load_site_manifest(root)
            entry = next(
                asset
                for asset in manifest["assets"].values()
                if asset["name"] == "app.js"
            )
            old = next(
                asset
                for asset in self.plan.assets.values()
                if asset.file.startswith("assets/app.")
            )
            self.assertIn("/" + entry["file"], rebuilt.assets)
            self.assertNotEqual(entry["file"], old.file)

            report = verify_published.verify(root, self.server.base, timeout=5)
            self.assert_check_fails(report, "page-assets", "404")
            missing = self.result(report, "page-assets").detail
            self.assertIn(entry["file"], missing)


if __name__ == "__main__":
    unittest.main()
