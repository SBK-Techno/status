#!/usr/bin/env python3
"""The one owner of the status site's cache policy.

``deploy/cache-policy.json`` declares the headers. The edge renderer writes
``deploy/Caddyfile`` from it, the browser check serves the same policy from a
loopback origin, and the published-site verifier reads the same declarations
instead of keeping a second copy of the numbers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

POLICY_FILE = Path("deploy/cache-policy.json")
DIRECTIVE = re.compile(r"([a-z-]+)(?:=([^,\s]+))?")


class PolicyError(Exception):
    """The cache policy is missing or violates the site contract."""


@dataclass(frozen=True)
class CachePolicy:
    html: str
    status: str
    hashed: str
    hashed_pattern: re.Pattern[str]

    @property
    def html_max_age(self) -> int:
        return self._max_age(self.html, "html")

    @property
    def immutable_min_max_age(self) -> int:
        return self._max_age(self.hashed, "hashed")

    @staticmethod
    def _max_age(value: str, label: str) -> int:
        directives = cache_directives(value)
        raw = directives.get("max-age")
        if raw is None:
            raise PolicyError(f"{label} Cache-Control must declare max-age")
        try:
            return int(raw)
        except ValueError as exc:
            raise PolicyError(f"{label} max-age must be an integer") from exc

    def cache_control(self, path: str) -> str:
        """Return the header for one same-origin path."""
        if path == "/status.json":
            return self.status
        if self.hashed_pattern.search(path):
            return self.hashed
        return self.html


def cache_directives(value: str) -> dict[str, str]:
    directives: dict[str, str] = {}
    for part in value.split(","):
        match = DIRECTIVE.fullmatch(part.strip().lower())
        if match is not None:
            directives[match.group(1)] = match.group(2) or ""
    return directives


def _required(document: dict, key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip() or "\n" in value:
        raise PolicyError(f"{POLICY_FILE}: {key} must be a single header value")
    return value.strip()


def load(root: Path) -> CachePolicy:
    path = Path(root) / POLICY_FILE
    if not path.is_file():
        raise PolicyError(f"{POLICY_FILE} is missing")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"{POLICY_FILE} cannot be read: {exc}") from None
    if not isinstance(document, dict) or document.get("version") != 1:
        raise PolicyError(f"{POLICY_FILE} must declare version 1")
    pattern = document.get("hashedPattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise PolicyError(f"{POLICY_FILE}: hashedPattern must be a regular expression")
    if any(character.isspace() for character in pattern):
        raise PolicyError(f"{POLICY_FILE}: hashedPattern must not contain whitespace")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise PolicyError(f"{POLICY_FILE}: hashedPattern is invalid: {exc}") from None
    policy = CachePolicy(
        html=_required(document, "html"),
        status=_required(document, "status"),
        hashed=_required(document, "hashed"),
        hashed_pattern=compiled,
    )
    html = cache_directives(policy.html)
    status = cache_directives(policy.status)
    hashed = cache_directives(policy.hashed)
    if "immutable" in html or policy.html_max_age > 600:
        raise PolicyError(
            f"{POLICY_FILE}: HTML must revalidate within 600 seconds and "
            "must never be immutable"
        )
    if "no-store" not in status:
        raise PolicyError(f"{POLICY_FILE}: status.json must stay no-store")
    if "immutable" not in hashed or policy.immutable_min_max_age < 31536000:
        raise PolicyError(
            f"{POLICY_FILE}: hashed assets must be immutable for at least "
            "one year"
        )
    return policy
