#!/usr/bin/env python3
"""The one HMAC-SHA256 contract for the published status heartbeat.

The publish workflow verifies the base64 payload with this module before it
writes ``status.json``. ``scripts/verify_published.py`` uses the same primitive
for direct post-publish verification, so the publish-time and post-publish
checks cannot drift apart.

HMAC is symmetric: there is no public key and no published signature artifact.
A direct verification therefore needs the key and the exact signature from the
publish run. Without them, the published check binds the live bytes to the
committed document that the publish workflow already verified.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from pathlib import Path

MAX_PAYLOAD_BYTES = 65536
SIGNATURE_HEX_LENGTH = 64
HEX_DIGITS = frozenset("0123456789abcdef")


class SignatureError(Exception):
    """A heartbeat payload, signature, or key is malformed."""


def sign(payload: bytes, key: bytes) -> str:
    """Return the lowercase hex HMAC-SHA256 of the exact payload bytes."""
    if not key:
        raise SignatureError("HMAC key is empty")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify(payload: bytes, signature: str, key: bytes) -> None:
    """Raise SignatureError unless signature is the HMAC of payload."""
    if not key:
        raise SignatureError("HMAC key is empty")
    if (
        not isinstance(signature, str)
        or len(signature) != SIGNATURE_HEX_LENGTH
        or any(character not in HEX_DIGITS for character in signature)
    ):
        raise SignatureError("signature must be 64 lowercase hex characters")
    expected = sign(payload, key)
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("signature mismatch")


def decode_payload(encoded: str) -> bytes:
    """Decode one workflow payload and enforce the bounded wire contract."""
    if not isinstance(encoded, str) or not encoded:
        raise SignatureError("payload is empty")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise SignatureError("payload is not valid base64") from None
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise SignatureError(f"payload exceeds {MAX_PAYLOAD_BYTES} bytes")
    if not payload.endswith(b"\n"):
        raise SignatureError("payload must end with a newline")
    return payload


def load_key(path: Path) -> bytes:
    """Read a secret file the way the publisher does: trim, then require bytes."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SignatureError(f"cannot read the HMAC key file: {exc}") from None
    if not value:
        raise SignatureError("HMAC key file is empty")
    return value.encode("utf-8")
