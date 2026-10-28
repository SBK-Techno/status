#!/usr/bin/env python3
"""Tests for the shared heartbeat HMAC contract and the publish CLI."""

from __future__ import annotations

import base64
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import heartbeat_signature
import verify_heartbeat

KEY = b"fixture-key"
PAYLOAD = b'{"version":1,"generated_at":null}\n'


class HeartbeatSignatureTest(unittest.TestCase):
    def test_sign_and_verify_the_exact_bytes(self) -> None:
        signature = heartbeat_signature.sign(PAYLOAD, KEY)
        self.assertEqual(len(signature), 64)
        heartbeat_signature.verify(PAYLOAD, signature, KEY)

    def test_rejects_a_tampered_payload(self) -> None:
        signature = heartbeat_signature.sign(PAYLOAD, KEY)
        with self.assertRaisesRegex(
            heartbeat_signature.SignatureError, "signature mismatch"
        ):
            heartbeat_signature.verify(PAYLOAD + b" ", signature, KEY)

    def test_rejects_a_malformed_signature(self) -> None:
        for signature in ("", "abc", "A" * 64, "g" * 64):
            with self.subTest(signature=signature):
                with self.assertRaises(heartbeat_signature.SignatureError):
                    heartbeat_signature.verify(PAYLOAD, signature, KEY)

    def test_rejects_an_empty_key(self) -> None:
        with self.assertRaisesRegex(heartbeat_signature.SignatureError, "key is empty"):
            heartbeat_signature.sign(PAYLOAD, b"")
        with self.assertRaisesRegex(heartbeat_signature.SignatureError, "key is empty"):
            heartbeat_signature.verify(PAYLOAD, "0" * 64, b"")

    def test_decodes_a_bounded_payload_with_a_final_newline(self) -> None:
        encoded = base64.b64encode(PAYLOAD).decode("ascii")
        self.assertEqual(heartbeat_signature.decode_payload(encoded), PAYLOAD)

    def test_decode_rejects_the_wire_contract_violations(self) -> None:
        cases = {
            "empty": "",
            "bad base64": "not base64!",
            "no newline": base64.b64encode(b'{"version":1}').decode("ascii"),
            "too large": base64.b64encode(
                b"x" * (heartbeat_signature.MAX_PAYLOAD_BYTES + 1) + b"\n"
            ).decode("ascii"),
        }
        for label, encoded in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(heartbeat_signature.SignatureError):
                    heartbeat_signature.decode_payload(encoded)

    def test_load_key_trims_like_the_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "key"
            path.write_text("  fixture-key \n", encoding="utf-8")
            self.assertEqual(heartbeat_signature.load_key(path), KEY)
            path.write_text("\n", encoding="utf-8")
            with self.assertRaisesRegex(heartbeat_signature.SignatureError, "empty"):
                heartbeat_signature.load_key(path)

    def test_load_key_rejects_a_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                heartbeat_signature.SignatureError, "cannot read"
            ):
                heartbeat_signature.load_key(Path(temporary) / "missing")


class VerifyHeartbeatTest(unittest.TestCase):
    def run_cli(
        self,
        payload: bytes,
        signature: str,
        key: str,
        out: Path,
    ) -> tuple[int, str, str]:
        environment = {
            "PAYLOAD": base64.b64encode(payload).decode("ascii"),
            "SIGNATURE": signature,
            "STATUS_HEARTBEAT_HMAC": key,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = verify_heartbeat.main(["--out", str(out)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_writes_the_exact_verified_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "status.json"
            signature = heartbeat_signature.sign(PAYLOAD, KEY)
            code, stdout, stderr = self.run_cli(
                PAYLOAD, signature, KEY.decode("utf-8"), out
            )
            self.assertEqual(code, 0, stderr)
            self.assertEqual(out.read_bytes(), PAYLOAD)
            self.assertIn("wrote", stdout)

    def test_a_bad_signature_writes_nothing_and_hides_the_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "status.json"
            code, stdout, stderr = self.run_cli(
                PAYLOAD, "0" * 64, KEY.decode("utf-8"), out
            )
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertIn("signature mismatch", stderr)
            self.assertNotIn(KEY.decode("utf-8"), stdout + stderr)

    def test_rejects_a_missing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "status.json"
            signature = heartbeat_signature.sign(PAYLOAD, KEY)
            code, _stdout, stderr = self.run_cli(PAYLOAD, signature, "", out)
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertIn("not configured", stderr)

    def test_rejects_a_payload_without_a_newline(self) -> None:
        payload = b'{"version":1}'
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "status.json"
            signature = heartbeat_signature.sign(payload, KEY)
            code, _stdout, stderr = self.run_cli(
                payload, signature, KEY.decode("utf-8"), out
            )
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertIn("newline", stderr)

    def test_rejects_a_payload_over_the_size_cap(self) -> None:
        payload = b"x" * (heartbeat_signature.MAX_PAYLOAD_BYTES + 1) + b"\n"
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "status.json"
            signature = heartbeat_signature.sign(payload, KEY)
            code, _stdout, stderr = self.run_cli(
                payload, signature, KEY.decode("utf-8"), out
            )
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
            self.assertIn("exceeds", stderr)

    def test_reports_an_unwritable_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            signature = heartbeat_signature.sign(PAYLOAD, KEY)
            code, _stdout, stderr = self.run_cli(
                PAYLOAD, signature, KEY.decode("utf-8"), out
            )
            self.assertEqual(code, 1)
            self.assertIn("cannot write", stderr)


if __name__ == "__main__":
    unittest.main()
