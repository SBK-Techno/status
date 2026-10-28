#!/usr/bin/env python3
"""Tests for the one cache-policy owner and its generated edge config."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import render_edge_config
import site_cache

ROOT = SCRIPTS.parent


@contextmanager
def copied_policy():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        deploy = root / "deploy"
        deploy.mkdir()
        shutil.copy(ROOT / site_cache.POLICY_FILE, deploy / "cache-policy.json")
        yield root


class CachePolicyTest(unittest.TestCase):
    def test_repository_policy_maps_each_surface(self) -> None:
        policy = site_cache.load(ROOT)
        self.assertEqual(policy.cache_control("/"), policy.html)
        self.assertEqual(policy.cache_control("/404.html"), policy.html)
        self.assertEqual(policy.cache_control("/status.json"), policy.status)
        self.assertIn("no-store", policy.cache_control("/status.json"))
        site = json.loads((ROOT / "site.manifest.json").read_text())
        brand = json.loads((ROOT / "brand.manifest.json").read_text())
        paths = [asset["file"] for asset in site["assets"]]
        paths.extend(
            asset["file"]
            for asset in brand["assets"]
            if asset.get("publish")
        )
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(policy.cache_control("/" + path), policy.hashed)
                self.assertIn("immutable", policy.cache_control("/" + path))
                self.assertGreaterEqual(
                    policy.immutable_min_max_age, 31536000
                )
        self.assertEqual(policy.html_max_age, 600)

    def test_policy_rejects_html_that_cannot_revalidate(self) -> None:
        with copied_policy() as root:
            path = root / site_cache.POLICY_FILE
            document = json.loads(path.read_text())
            document["html"] = "public, max-age=31536000, immutable"
            path.write_text(json.dumps(document))
            with self.assertRaises(site_cache.PolicyError):
                site_cache.load(root)

    def test_policy_rejects_a_cacheable_heartbeat(self) -> None:
        with copied_policy() as root:
            path = root / site_cache.POLICY_FILE
            document = json.loads(path.read_text())
            document["status"] = "public, max-age=60"
            path.write_text(json.dumps(document))
            with self.assertRaises(site_cache.PolicyError):
                site_cache.load(root)

    def test_policy_rejects_short_immutable_assets(self) -> None:
        with copied_policy() as root:
            path = root / site_cache.POLICY_FILE
            document = json.loads(path.read_text())
            document["hashed"] = "public, max-age=600, immutable"
            path.write_text(json.dumps(document))
            with self.assertRaises(site_cache.PolicyError):
                site_cache.load(root)

    def test_edge_config_is_generated_from_the_policy(self) -> None:
        rendered = render_edge_config.render(ROOT)
        self.assertEqual(
            (ROOT / render_edge_config.OUTPUT).read_text(), rendered
        )
        policy = site_cache.load(ROOT)
        self.assertIn('header ?Cache-Control "', rendered)
        self.assertIn("header @hashed {", rendered)
        self.assertIn("match status 2xx 304", rendered)
        self.assertIn('header /status.json >Cache-Control "', rendered)
        self.assertIn(policy.html, rendered)
        self.assertIn(policy.hashed, rendered)
        self.assertIn(policy.status, rendered)
        self.assertIn("@hashed path_regexp", rendered)
        self.assertIn("frame-ancestors 'none'", rendered)
        self.assertIn("file_server", rendered)


if __name__ == "__main__":
    unittest.main()
