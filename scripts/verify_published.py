#!/usr/bin/env python3
"""Verify that the published status site is the artifact this repository built.

The command is deliberately outside ``make validate``: every check below needs
the deployed origin. It is bounded, read-only, and never prints a key or a
signature. The published contract is:

* ``/status.json`` is live, is ``application/json``, has schema version 1, and
  passes the same document validator as the repository. Its bytes are
  identical to the committed ``status.json``, which the publish workflow
  accepted only after ``scripts/heartbeat_signature.py`` verified its
  HMAC-SHA256 signature. When the publish-run signature and key are supplied,
  the live bytes are verified directly instead.
* ``/`` and ``/404.html`` are live HTML and match the built pages byte for
  byte. The page's ``data-status-digest`` is the SHA-256 of the live
  ``status.json`` and its ``data-snapshot-at`` is the built snapshot instant.
* Every resource the pages reference is a manifest-owned content-hashed asset
  recorded by ``site.manifest.json`` or ``brand.manifest.json``, is live, and
  matches its recorded SHA-256, size, and media type. Fonts referenced by the
  brand stylesheet are checked the same way. Asset names come from the build
  output, never from a list in this module.
* HTML revalidates within the limit declared by ``deploy/cache-policy.json``
  with a validator, a conditional request returns 304 with no body,
  content-hashed assets are immutable by URL (and by header on a controllable
  edge), no response sets a cookie, and a directory path is not a listing.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import posixpath
import re
import ssl
import sys
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import heartbeat_signature
import site_build
import site_cache
import validate_status
from status_snapshot import document_digest, parse_timestamp

STATUS_PATH = "/status.json"
PAGES = ("index.html", "404.html")
PAGE_PATHS = {"index.html": "/", "404.html": "/404.html"}
STATUS_MAX_BYTES = 256 * 1024
HTML_MAX_BYTES = 4 * 1024 * 1024
ASSET_MAX_BYTES = 8 * 1024 * 1024
LISTING_MAX_BYTES = 64 * 1024
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT = 10.0
MAX_REDIRECTS = 3
USER_AGENT = "sbk-status-verify/1"
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
CONTENT_HASH_SEGMENT = re.compile(r"(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])")
CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
LISTING_MARKERS = ("index of /", "directory listing for")
MEDIA_ALIASES = {
    "text/javascript": {"application/javascript", "application/x-javascript"},
    "application/javascript": {"text/javascript", "application/x-javascript"},
    "image/x-icon": {"image/vnd.microsoft.icon"},
    "image/vnd.microsoft.icon": {"image/x-icon"},
}
RESOURCE_ATTRIBUTES = {
    ("script", "src"),
    ("img", "src"),
    ("img", "srcset"),
    ("source", "src"),
    ("source", "srcset"),
    ("video", "src"),
    ("video", "poster"),
    ("audio", "src"),
    ("track", "src"),
    ("object", "data"),
    ("embed", "src"),
    ("use", "href"),
    ("image", "href"),
    ("input", "src"),
}
LINK_RESOURCE_RELS = {
    "stylesheet",
    "icon",
    "apple-touch-icon",
    "mask-icon",
    "manifest",
    "preload",
    "modulepreload",
    "prefetch",
}


class PlanError(Exception):
    """The local artifact cannot be turned into a published plan."""


class UsageError(Exception):
    """The command line cannot describe a verifiable origin."""


class FetchError(Exception):
    """One bounded HTTP request failed."""


@dataclass(frozen=True)
class Asset:
    path: str
    file: str
    sha256: str
    size: int
    media_type: str
    transitive: bool = False


@dataclass(frozen=True)
class Page:
    name: str
    data: bytes
    refs: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    root: Path
    pages: dict[str, Page]
    status_bytes: bytes
    assets: dict[str, Asset]
    snapshot_at: str
    digest: str
    cache_policy: site_cache.CachePolicy


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def header(self, name: str) -> str | None:
        values = self.header_values(name)
        return values[0] if values else None

    def header_values(self, name: str) -> list[str]:
        wanted = name.lower()
        return [value for key, value in self.headers if key.lower() == wanted]


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    skipped: bool = False


@dataclass(frozen=True)
class Report:
    base: str
    root: Path
    results: tuple[CheckResult, ...]

    @property
    def failures(self) -> list[CheckResult]:
        return [
            result for result in self.results if not result.ok and not result.skipped
        ]

    @property
    def passes(self) -> list[CheckResult]:
        return [result for result in self.results if result.ok]

    @property
    def skips(self) -> list[CheckResult]:
        return [result for result in self.results if result.skipped]

    @property
    def ok(self) -> bool:
        return not self.failures


class _ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.lower()
        attributes = {name.lower(): value or "" for name, value in attrs}
        if lowered == "link":
            rels = set(attributes.get("rel", "").lower().split())
            if not rels & LINK_RESOURCE_RELS:
                return
            href = attributes.get("href")
            if href:
                self.values.append((lowered, "href", href))
            return
        for name, value in attributes.items():
            if (lowered, name) in RESOURCE_ATTRIBUTES and value:
                self.values.append((lowered, name, value))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)


def _srcset_urls(value: str) -> list[str]:
    urls: list[str] = []
    for candidate in value.split(","):
        tokens = candidate.strip().split()
        if tokens:
            urls.append(tokens[0])
    return urls


def _normalize_reference(value: str, page_url: str, where: str) -> str | None:
    value = value.strip()
    if not value or value.startswith("#"):
        return None
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc:
        raise PlanError(f"{where}: external resource {value!r} is not allowed")
    if "\\" in parsed.path:
        raise PlanError(f"{where}: resource {value!r} contains a backslash")
    if not parsed.path:
        return None
    resolved = urllib.parse.urlsplit(urllib.parse.urljoin(page_url, parsed.path)).path
    normalized = posixpath.normpath(urllib.parse.unquote(resolved))
    if not normalized.startswith("/") or ".." in normalized.split("/"):
        raise PlanError(f"{where}: resource {value!r} escapes the site root")
    if normalized == "/":
        raise PlanError(f"{where}: resource {value!r} points at a page")
    return normalized


def page_references(text: str, page_name: str) -> tuple[str, ...]:
    """Return the same-origin resource paths a built page references."""
    parser = _ReferenceParser()
    parser.feed(text)
    parser.close()
    page_url = PAGE_PATHS[page_name]
    seen: dict[str, None] = {}
    for _tag, attribute, value in parser.values:
        values = _srcset_urls(value) if attribute == "srcset" else [value]
        for candidate in values:
            path = _normalize_reference(candidate, page_url, page_name)
            if path is not None:
                seen.setdefault(path, None)
    return tuple(seen)


def css_references(text: str, css_path: str) -> tuple[str, ...]:
    """Return the resources one stylesheet references."""
    seen: dict[str, None] = {}
    for match in CSS_URL.finditer(text):
        value = match.group(2).strip()
        if not value or value.startswith(("#", "data:")):
            continue
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme or parsed.netloc:
            raise PlanError(f"{css_path}: external resource {value!r} is not allowed")
        if not parsed.path:
            continue
        resolved = urllib.parse.urlsplit(
            urllib.parse.urljoin(css_path, parsed.path)
        ).path
        normalized = posixpath.normpath(urllib.parse.unquote(resolved))
        if not normalized.startswith("/") or ".." in normalized.split("/"):
            raise PlanError(f"{css_path}: resource {value!r} escapes the site root")
        seen.setdefault(normalized, None)
    return tuple(seen)


def base_media_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def media_type_matches(expected: str, actual: str | None) -> bool:
    expected_base = base_media_type(expected)
    actual_base = base_media_type(actual)
    if actual_base == expected_base:
        return True
    return actual_base in MEDIA_ALIASES.get(expected_base, set())


def cache_directives(value: str) -> dict[str, str | None]:
    directives: dict[str, str | None] = {}
    for part in value.split(","):
        name, separator, raw = part.strip().partition("=")
        key = name.strip().lower()
        if not key:
            continue
        directives[key] = raw.strip().strip('"') if separator else None
    return directives


def content_hash_matches(file: str, sha256: str) -> bool:
    return sha256[:12] in CONTENT_HASH_SEGMENT.findall(file)


def _manifest_entry(
    file: str,
    site_assets: dict[str, dict],
    brand_assets: dict[str, dict],
) -> dict:
    owners: list[dict] = []
    if file in site_assets:
        owners.append(site_assets[file])
    if file in brand_assets:
        owners.append(brand_assets[file])
    if not owners:
        raise PlanError(
            f"{file}: not recorded in site.manifest.json or brand.manifest.json"
        )
    if len(owners) > 1:
        raise PlanError(f"{file}: owned by both generated manifests")
    return owners[0]


def _asset_from_manifest(
    root: Path,
    path: str,
    site_assets: dict[str, dict],
    brand_assets: dict[str, dict],
    *,
    transitive: bool,
) -> Asset:
    file = path.lstrip("/")
    entry = _manifest_entry(file, site_assets, brand_assets)
    sha256 = entry.get("sha256")
    size = entry.get("bytes")
    media_type = entry.get("mediaType")
    if not isinstance(sha256, str) or not SHA256_HEX.fullmatch(sha256):
        raise PlanError(f"{file}: manifest sha256 is missing or malformed")
    if type(size) is not int or size < 0:
        raise PlanError(f"{file}: manifest byte size is missing or malformed")
    if not isinstance(media_type, str) or not media_type:
        raise PlanError(f"{file}: manifest media type is missing")
    if not content_hash_matches(file, sha256):
        raise PlanError(f"{file}: filename does not carry its content hash")
    local = root / file
    if not local.is_file():
        raise PlanError(f"{file}: built file is missing")
    data = local.read_bytes()
    if len(data) != size:
        raise PlanError(
            f"{file}: built file is {len(data)} bytes, manifest says {size}"
        )
    if hashlib.sha256(data).hexdigest() != sha256:
        raise PlanError(f"{file}: built file does not match the manifest digest")
    return Asset(
        path=path,
        file=file,
        sha256=sha256,
        size=size,
        media_type=media_type,
        transitive=transitive,
    )


def build_plan(root: Path) -> Plan:
    """Derive every published expectation from the build and its manifests."""
    root = root.resolve()
    try:
        errors = site_build.check_site(root)
    except (OSError, site_build.BuildError, json.JSONDecodeError) as exc:
        raise PlanError(str(exc)) from None
    if errors:
        raise PlanError("; ".join(errors[:5]))
    try:
        site_manifest = site_build.load_site_manifest(root)
        brand_manifest = site_build.load_brand_manifest(root)
        status_bytes = (root / "status.json").read_bytes()
        cache_policy = site_cache.load(root)
    except (
        site_build.BuildError,
        site_cache.PolicyError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise PlanError(str(exc)) from None

    site_assets = {entry["file"]: entry for entry in site_manifest["assets"].values()}
    brand_assets = {
        entry["file"]: entry
        for entry in site_build.published_brand_assets(brand_manifest).values()
    }

    pages: dict[str, Page] = {}
    assets: dict[str, Asset] = {}
    for name in PAGES:
        try:
            data = (root / name).read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise PlanError(f"{name}: cannot be read: {exc}") from None
        refs = page_references(text, name)
        for path in refs:
            if path not in assets:
                assets[path] = _asset_from_manifest(
                    root,
                    path,
                    site_assets,
                    brand_assets,
                    transitive=False,
                )
        pages[name] = Page(name=name, data=data, refs=refs)

    if not any(not asset.transitive for asset in assets.values()):
        raise PlanError("the built pages reference no manifest-owned assets")

    pending = list(assets.values())
    while pending:
        asset = pending.pop()
        if base_media_type(asset.media_type) != "text/css":
            continue
        try:
            text = (root / asset.file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise PlanError(f"{asset.file}: cannot be read: {exc}") from None
        for path in css_references(text, asset.path):
            if path in assets:
                continue
            dependency = _asset_from_manifest(
                root,
                path,
                site_assets,
                brand_assets,
                transitive=True,
            )
            assets[path] = dependency
            pending.append(dependency)

    index = pages["index.html"]
    attributes = site_build.body_attributes(index.data.decode("utf-8"))
    digest = attributes.get("data-status-digest", "")
    snapshot_at = attributes.get("data-snapshot-at", "")
    if not SHA256_HEX.fullmatch(digest):
        raise PlanError("index.html: missing or invalid data-status-digest")
    if parse_timestamp(snapshot_at) is None:
        raise PlanError("index.html: missing or invalid data-snapshot-at")
    if digest != document_digest(status_bytes):
        raise PlanError(
            "index.html: snapshot is bound to a different status.json; run make brand"
        )
    return Plan(
        root=root,
        pages=pages,
        status_bytes=status_bytes,
        assets=assets,
        snapshot_at=snapshot_at,
        digest=digest,
        cache_policy=cache_policy,
    )


class HttpClient:
    """Small bounded HTTP client: one request at a time, no implicit retries."""

    def __init__(self, *, timeout: float, max_bytes: int) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.spent = 0

    def get(
        self,
        url: str,
        *,
        limit: int,
        conditional: dict[str, str] | None = None,
    ) -> Response:
        current = url
        for redirect in range(MAX_REDIRECTS + 1):
            response = self._one(current, limit=limit, conditional=conditional)
            if response.status in (301, 302, 303, 307, 308):
                location = response.header("location")
                if not location:
                    raise FetchError(f"{current}: redirect without Location")
                if redirect >= MAX_REDIRECTS:
                    raise FetchError(f"{current}: too many redirects")
                target = urllib.parse.urljoin(current, location)
                if not _same_origin(current, target):
                    raise FetchError(f"{current}: redirect leaves the verified origin")
                current = target
                continue
            return response
        raise FetchError(f"{url}: too many redirects")

    def _one(
        self,
        url: str,
        *,
        limit: int,
        conditional: dict[str, str] | None,
    ) -> Response:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise FetchError(f"{url}: unsupported URL")
        if parsed.username or parsed.password:
            raise FetchError(f"{url}: URL must not carry credentials")
        try:
            port = parsed.port
        except ValueError:
            raise FetchError(f"{url}: invalid port") from None
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        if self.spent >= self.max_bytes:
            raise FetchError("response budget exhausted")
        body_limit = min(limit, self.max_bytes - self.spent)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        if conditional:
            headers.update(conditional)
        if parsed.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                parsed.hostname,
                port,
                timeout=self.timeout,
                context=ssl.create_default_context(),
            )
        else:
            connection = http.client.HTTPConnection(
                parsed.hostname, port, timeout=self.timeout
            )
        try:
            connection.request("GET", target, headers=headers)
            raw = connection.getresponse()
            status = raw.status
            header_items = tuple(raw.getheaders())
            response = Response(
                url=url,
                status=status,
                headers=header_items,
                body=b"",
            )
            declared = response.header("content-length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError:
                    raise FetchError(f"{url}: malformed Content-Length") from None
                if declared_size < 0 or declared_size > body_limit:
                    raise FetchError(
                        f"{url}: response declares {declared_size} bytes, "
                        f"over the {body_limit}-byte limit"
                    )
            encoding = response.header("content-encoding")
            if encoding and encoding.strip().lower() != "identity":
                raise FetchError(f"{url}: unsupported Content-Encoding")
            body = raw.read(body_limit + 1)
            if len(body) > body_limit:
                raise FetchError(f"{url}: response exceeds the byte budget")
        except FetchError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise FetchError(f"{url}: {type(exc).__name__}") from None
        finally:
            connection.close()
        self.spent += len(body)
        return Response(
            url=url,
            status=status,
            headers=header_items,
            body=body,
        )


def _same_origin(left: str, right: str) -> bool:
    first = urllib.parse.urlsplit(left)
    second = urllib.parse.urlsplit(right)

    def origin(parsed: urllib.parse.SplitResult) -> tuple[str, str, int]:
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        return (parsed.scheme.lower(), (parsed.hostname or "").lower(), port)

    try:
        return origin(first) == origin(second)
    except ValueError:
        return False


def normalize_base(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        raise UsageError("URL must use http or https")
    if not parsed.hostname:
        raise UsageError("URL must include a host")
    if parsed.username or parsed.password:
        raise UsageError("URL must not include credentials")
    if parsed.query or parsed.fragment:
        raise UsageError("URL must not include a query or fragment")
    if parsed.path not in ("", "/"):
        raise UsageError("URL must be the site root")
    try:
        parsed.port
    except ValueError:
        raise UsageError("URL has an invalid port") from None
    if parsed.scheme == "http" and parsed.hostname not in (
        "127.0.0.1",
        "::1",
        "localhost",
    ):
        raise UsageError("use https for a non-loopback origin")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


class Verifier:
    def __init__(
        self,
        *,
        root: Path,
        base: str,
        signature: str | None = None,
        hmac_key: bytes | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_BYTES,
        host_policy: str = "auto",
    ) -> None:
        self.root = root
        self.base = base
        self.signature = signature
        self.hmac_key = hmac_key
        self.host_policy = host_policy
        self.client = HttpClient(timeout=timeout, max_bytes=max_bytes)
        self.results: list[CheckResult] = []
        self.cache: dict[tuple, Response] = {}
        self.fetched: list[Response] = []
        self.plan: Plan | None = None

    def run(self) -> Report:
        try:
            self.plan = build_plan(self.root)
        except PlanError as exc:
            self._fail("local-build", str(exc))
            return self._report()
        self._pass(
            "local-build",
            f"{len(self.plan.assets)} manifest-owned assets; pages reproduce",
        )

        status = self._status_live()
        self._status_schema(status)
        self._status_artifact(status)
        self._status_signature(status)
        pages = self._pages_live()
        self._page_artifact(pages)
        self._page_assets(pages)
        self._asset_integrity()
        self._asset_transitive()
        self._snapshot(status, pages)
        self._html_cache(pages)
        self._conditional(pages)
        self._asset_cache()
        self._no_listing()
        self._no_cookie()
        return self._report()

    def _report(self) -> Report:
        return Report(
            base=self.base,
            root=self.root,
            results=tuple(self.results),
        )

    def _add(self, name: str, ok: bool, detail: str, *, skipped: bool = False) -> None:
        self.results.append(
            CheckResult(name=name, ok=ok, detail=detail, skipped=skipped)
        )

    def _pass(self, name: str, detail: str) -> None:
        self._add(name, True, detail)

    def _fail(self, name: str, detail: str) -> None:
        self._add(name, False, detail)

    def _skip(self, name: str, detail: str) -> None:
        self._add(name, False, detail, skipped=True)

    def _fetch(
        self,
        path: str,
        *,
        limit: int,
        conditional: dict[str, str] | None = None,
    ) -> Response:
        url = urllib.parse.urljoin(self.base, path.lstrip("/"))
        key = (
            url,
            limit,
            tuple(sorted((conditional or {}).items())),
        )
        if key in self.cache:
            return self.cache[key]
        response = self.client.get(url, limit=limit, conditional=conditional)
        self.cache[key] = response
        self.fetched.append(response)
        return response

    def _local(self, path: str) -> bytes:
        assert self.plan is not None
        return (self.plan.root / path.lstrip("/")).read_bytes()

    def _fetch_asset(self, asset: Asset) -> Response:
        return self._fetch(
            asset.path,
            limit=min(asset.size + 1, ASSET_MAX_BYTES),
        )

    @staticmethod
    def _asset_mismatch(asset: Asset, response: Response) -> str | None:
        if response.status != 200:
            return f"{asset.path} returned {response.status}"
        if len(response.body) != asset.size:
            return (
                f"{asset.path}: {len(response.body)} bytes, manifest says {asset.size}"
            )
        if hashlib.sha256(response.body).hexdigest() != asset.sha256:
            return f"{asset.path}: content digest mismatch"
        if not media_type_matches(asset.media_type, response.header("content-type")):
            return (
                f"{asset.path}: media type "
                f"{response.header('content-type') or 'missing'}, "
                f"manifest says {asset.media_type}"
            )
        return None

    def _status_live(self) -> Response | None:
        try:
            response = self._fetch(STATUS_PATH, limit=STATUS_MAX_BYTES)
        except FetchError as exc:
            self._fail("status-live", str(exc))
            return None
        if response.status != 200:
            self._fail("status-live", f"/status.json returned {response.status}")
            return None
        if not media_type_matches("application/json", response.header("content-type")):
            self._fail(
                "status-live",
                f"/status.json has media type "
                f"{response.header('content-type') or 'missing'}",
            )
            return None
        self._pass(
            "status-live",
            f"200, application/json, {len(response.body)} bytes",
        )
        return response

    def _status_schema(self, status: Response | None) -> None:
        if status is None:
            self._skip("status-schema", "status.json was not fetched")
            return
        try:
            document = json.loads(status.body)
        except json.JSONDecodeError as exc:
            self._fail("status-schema", f"status.json is not valid JSON: {exc}")
            return
        errors = validate_status.validate_document(document)
        if errors:
            shown = "; ".join(errors[:3])
            if len(errors) > 3:
                shown += f"; and {len(errors) - 3} more"
            self._fail("status-schema", shown)
            return
        assert isinstance(document, dict)
        self._pass(
            "status-schema",
            f"version 1, {len(document['components'])} components, "
            f"{len(document['incidents'])} incidents",
        )

    def _status_artifact(self, status: Response | None) -> None:
        if status is None or self.plan is None:
            self._skip("status-artifact", "status.json was not fetched")
            return
        live = document_digest(status.body)
        built = document_digest(self.plan.status_bytes)
        if status.body == self.plan.status_bytes:
            self._pass(
                "status-artifact",
                f"{len(status.body)} bytes match the committed status.json",
            )
            return
        self._fail(
            "status-artifact",
            f"live sha256 {live} differs from the committed artifact {built}",
        )

    def _status_signature(self, status: Response | None) -> None:
        if status is None or self.plan is None:
            self._skip("status-signature", "status.json was not fetched")
            return
        if self.signature is not None:
            try:
                heartbeat_signature.verify(
                    status.body, self.signature, self.hmac_key or b""
                )
            except heartbeat_signature.SignatureError as exc:
                self._fail("status-signature", str(exc))
                return
            self._pass(
                "status-signature",
                "HMAC-SHA256 verified directly over the live bytes",
            )
            return
        if status.body == self.plan.status_bytes:
            self._pass(
                "status-signature",
                "live bytes match the committed status.json "
                "(direct HMAC needs the publish-run signature)",
            )
            return
        self._fail(
            "status-signature",
            "live status.json differs from the committed signed document; "
            "no publish-run signature was supplied for direct verification",
        )

    def _pages_live(self) -> dict[str, Response]:
        pages: dict[str, Response] = {}
        failures: list[str] = []
        for name in PAGES:
            path = PAGE_PATHS[name]
            try:
                response = self._fetch(path, limit=HTML_MAX_BYTES)
            except FetchError as exc:
                failures.append(str(exc))
                continue
            if response.status != 200:
                failures.append(f"{path} returned {response.status}")
            elif not media_type_matches("text/html", response.header("content-type")):
                failures.append(
                    f"{path} has media type "
                    f"{response.header('content-type') or 'missing'}"
                )
            else:
                pages[name] = response
        if failures:
            self._fail("pages-live", "; ".join(failures))
        else:
            self._pass("pages-live", "/ and /404.html are 200 text/html")
        return pages

    def _page_artifact(self, pages: dict[str, Response]) -> None:
        if self.plan is None:
            self._skip("page-artifact", "the local build is unavailable")
            return
        failures: list[str] = []
        for name in PAGES:
            response = pages.get(name)
            if response is None:
                failures.append(f"{name} was not fetched")
                continue
            built = self.plan.pages[name].data
            if response.body != built:
                failures.append(
                    f"{name} differs from the built artifact "
                    f"({len(response.body)} live bytes, {len(built)} built)"
                )
        if failures:
            self._fail("page-artifact", "; ".join(failures))
        else:
            self._pass(
                "page-artifact",
                "index.html and 404.html match the build byte for byte",
            )

    def _page_assets(self, pages: dict[str, Response]) -> None:
        if self.plan is None:
            self._skip("page-assets", "the local build is unavailable")
            return
        local: set[str] = set()
        for page in self.plan.pages.values():
            local.update(page.refs)
        live: set[str] = set()
        reference_errors: list[str] = []
        for name, response in pages.items():
            try:
                live.update(page_references(response.body.decode("utf-8"), name))
            except (UnicodeDecodeError, PlanError) as exc:
                reference_errors.append(str(exc))
        missing = sorted(local - live)
        extra = sorted(live - local)
        if missing:
            reference_errors.append(
                "live pages are missing references: " + ", ".join(missing)
            )
        if extra:
            reference_errors.append(
                "live pages reference assets outside the build: " + ", ".join(extra)
            )
        direct = [asset for asset in self.plan.assets.values() if not asset.transitive]
        for asset in direct:
            try:
                response = self._fetch_asset(asset)
            except FetchError as exc:
                reference_errors.append(str(exc))
                continue
            if response.status != 200:
                reference_errors.append(f"{asset.path} returned {response.status}")
        if reference_errors:
            self._fail("page-assets", "; ".join(reference_errors[:5]))
        else:
            self._pass(
                "page-assets",
                f"{len(direct)} referenced assets are live and match the build",
            )

    def _asset_integrity(self) -> None:
        if self.plan is None:
            self._skip("asset-integrity", "the local build is unavailable")
            return
        direct = [asset for asset in self.plan.assets.values() if not asset.transitive]
        failures: list[str] = []
        for asset in direct:
            try:
                response = self._fetch_asset(asset)
            except FetchError:
                continue
            mismatch = self._asset_mismatch(asset, response)
            if mismatch is not None:
                failures.append(mismatch)
        if failures:
            self._fail("asset-integrity", "; ".join(failures[:5]))
        else:
            self._pass(
                "asset-integrity",
                f"{len(direct)} assets match their manifest digest, size, and type",
            )

    def _asset_transitive(self) -> None:
        if self.plan is None:
            self._skip("asset-transitive", "the local build is unavailable")
            return
        transitive = [asset for asset in self.plan.assets.values() if asset.transitive]
        if not transitive:
            self._skip(
                "asset-transitive",
                "the built stylesheets reference no transitive assets",
            )
            return
        failures: list[str] = []
        for asset in transitive:
            try:
                response = self._fetch_asset(asset)
            except FetchError as exc:
                failures.append(str(exc))
                continue
            mismatch = self._asset_mismatch(asset, response)
            if mismatch is not None:
                failures.append(mismatch)
        for asset in self.plan.assets.values():
            if base_media_type(asset.media_type) != "text/css":
                continue
            try:
                response = self._fetch_asset(asset)
            except FetchError:
                continue
            if response.status != 200:
                continue
            try:
                expected = set(
                    css_references(self._local(asset.file).decode("utf-8"), asset.path)
                )
                actual = set(css_references(response.body.decode("utf-8"), asset.path))
            except (UnicodeDecodeError, PlanError) as exc:
                failures.append(str(exc))
                continue
            unknown = sorted(actual - set(self.plan.assets))
            if unknown:
                failures.append(
                    f"{asset.path}: live stylesheet references unbuilt assets: "
                    + ", ".join(unknown)
                )
            if actual != expected:
                failures.append(
                    f"{asset.path}: live stylesheet references differ from the build"
                )
        if failures:
            self._fail("asset-transitive", "; ".join(failures[:5]))
        else:
            self._pass(
                "asset-transitive",
                f"{len(transitive)} stylesheet dependencies are live and match",
            )

    def _snapshot(self, status: Response | None, pages: dict[str, Response]) -> None:
        if self.plan is None:
            self._skip("snapshot", "the local build is unavailable")
            return
        if status is None or "index.html" not in pages:
            self._skip("snapshot", "the live page or status.json was not fetched")
            return
        try:
            live_attributes = site_build.body_attributes(
                pages["index.html"].body.decode("utf-8")
            )
        except UnicodeDecodeError:
            self._fail("snapshot", "index.html is not valid UTF-8")
            return
        live_digest = live_attributes.get("data-status-digest", "")
        live_at = live_attributes.get("data-snapshot-at", "")
        failures: list[str] = []
        if live_digest != document_digest(status.body):
            failures.append("page digest does not match the live status.json digest")
        if live_digest != self.plan.digest:
            failures.append("page digest does not match the built artifact")
        if live_at != self.plan.snapshot_at:
            failures.append(
                f"snapshot instant {live_at!r} does not match the built "
                f"instant {self.plan.snapshot_at!r}"
            )
        if parse_timestamp(live_at) is None:
            failures.append("snapshot instant is not a UTC timestamp")
        if failures:
            self._fail("snapshot", "; ".join(failures))
        else:
            self._pass(
                "snapshot",
                f"digest {live_digest[:12]} and instant {live_at} agree",
            )

    def _html_cache(self, pages: dict[str, Response]) -> None:
        if not pages:
            self._skip("cache-html", "the live pages were not fetched")
            return
        assert self.plan is not None
        policy = self.plan.cache_policy
        failures: list[str] = []
        for name, response in pages.items():
            path = PAGE_PATHS[name]
            value = response.header("cache-control")
            if not value:
                failures.append(f"{path}: missing Cache-Control")
                continue
            directives = cache_directives(value)
            if "no-store" in directives or "private" in directives:
                failures.append(f"{path}: Cache-Control must be publicly cacheable")
            if "immutable" in directives:
                failures.append(f"{path}: HTML must not be immutable")
            raw_age = directives.get("max-age")
            if raw_age is None:
                failures.append(f"{path}: Cache-Control has no max-age")
            else:
                try:
                    age = int(raw_age)
                except ValueError:
                    failures.append(f"{path}: max-age is not an integer")
                else:
                    if age < 0 or age > policy.html_max_age:
                        failures.append(
                            f"{path}: max-age {age} exceeds {policy.html_max_age}"
                        )
            if not (response.header("etag") or response.header("last-modified")):
                failures.append(f"{path}: no ETag or Last-Modified validator")
        if failures:
            self._fail("cache-html", "; ".join(failures))
        else:
            self._pass(
                "cache-html",
                f"max-age <= {policy.html_max_age} and a validator on both pages",
            )

    def _conditional(self, pages: dict[str, Response]) -> None:
        response = pages.get("index.html")
        if response is None:
            self._skip("cache-conditional", "index.html was not fetched")
            return
        conditional: dict[str, str] = {}
        etag = response.header("etag")
        modified = response.header("last-modified")
        if etag:
            conditional["If-None-Match"] = etag
        elif modified:
            conditional["If-Modified-Since"] = modified
        else:
            self._fail(
                "cache-conditional",
                "index.html has no ETag or Last-Modified to revalidate",
            )
            return
        try:
            revalidated = self._fetch(
                "/", limit=HTML_MAX_BYTES, conditional=conditional
            )
        except FetchError as exc:
            self._fail("cache-conditional", str(exc))
            return
        if revalidated.status != 304:
            self._fail(
                "cache-conditional",
                f"conditional request returned {revalidated.status}, expected 304",
            )
            return
        declared = revalidated.header("content-length")
        if revalidated.body:
            self._fail(
                "cache-conditional",
                f"304 carried {len(revalidated.body)} body bytes",
            )
        elif declared is not None and declared.strip() not in ("", "0"):
            self._fail(
                "cache-conditional",
                f"304 declared Content-Length {declared}",
            )
        else:
            self._pass("cache-conditional", "304 with an empty body")

    def _asset_cache(self) -> None:
        if self.plan is None:
            self._skip("cache-assets", "the local build is unavailable")
            return
        policy = self.plan.cache_policy
        github_pages = self.host_policy == "github-pages" or (
            self.host_policy == "auto"
            and any(self._is_github_pages(response) for response in self.fetched)
        )
        failures: list[str] = []
        for asset in self.plan.assets.values():
            try:
                response = self._fetch_asset(asset)
            except FetchError:
                continue
            if response.status != 200:
                continue
            value = response.header("cache-control")
            if not value:
                failures.append(f"{asset.path}: missing Cache-Control")
                continue
            directives = cache_directives(value)
            if (
                "no-store" in directives
                or "private" in directives
                or "no-cache" in directives
            ):
                failures.append(
                    f"{asset.path}: content-hashed asset must not be private "
                    "or revalidated on every request"
                )
                continue
            raw_age = directives.get("max-age")
            age: int | None = None
            if raw_age is not None:
                try:
                    age = int(raw_age)
                except ValueError:
                    failures.append(f"{asset.path}: max-age is not an integer")
                    continue
            if "immutable" in directives:
                if age is None or age < policy.immutable_min_max_age:
                    failures.append(
                        f"{asset.path}: immutable requires max-age >= "
                        f"{policy.immutable_min_max_age}"
                    )
                continue
            if github_pages:
                if age is None or age < 0 or age > policy.html_max_age:
                    failures.append(
                        f"{asset.path}: GitHub Pages max-age must be between "
                        f"0 and {policy.html_max_age}"
                    )
                continue
            failures.append(
                f"{asset.path}: expected immutable with max-age >= "
                f"{policy.immutable_min_max_age} on this origin"
            )
        if failures:
            self._fail("cache-assets", "; ".join(failures[:5]))
        elif github_pages:
            self._pass(
                "cache-assets",
                f"{len(self.plan.assets)} content-hashed URLs; GitHub Pages "
                f"caps max-age at {policy.html_max_age} (immutable header unavailable)",
            )
        else:
            self._pass(
                "cache-assets",
                f"{len(self.plan.assets)} content-hashed assets are immutable "
                f"for at least {policy.immutable_min_max_age} seconds",
            )

    @staticmethod
    def _is_github_pages(response: Response) -> bool:
        server = (response.header("server") or "").lower()
        return (
            server.startswith("github.com")
            or response.header("x-github-request-id") is not None
        )

    def _no_listing(self) -> None:
        paths = ("/assets/", "/assets", "/.git/")
        failures: list[str] = []
        for path in paths:
            try:
                response = self._fetch(path, limit=LISTING_MAX_BYTES)
            except FetchError as exc:
                failures.append(str(exc))
                continue
            if response.status not in (403, 404):
                failures.append(f"{path}: {response.status}, expected 403 or 404")
                continue
            body = response.body.decode("utf-8", errors="replace").lower()
            if any(marker in body for marker in LISTING_MARKERS):
                failures.append(f"{path}: body looks like a directory listing")
        if failures:
            self._fail("no-listing", "; ".join(failures))
        else:
            self._pass(
                "no-listing",
                "/assets/ and /.git/ are absent, not directory listings",
            )

    def _no_cookie(self) -> None:
        failures = sorted(
            {
                response.url
                for response in self.fetched
                if response.header_values("set-cookie")
            }
        )
        if failures:
            self._fail(
                "no-cookie",
                "Set-Cookie on: " + ", ".join(failures[:5]),
            )
        else:
            self._pass(
                "no-cookie",
                f"no Set-Cookie on {len(self.fetched)} responses",
            )


def verify(
    root: Path,
    url: str,
    *,
    signature: str | None = None,
    hmac_key: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    host_policy: str = "auto",
) -> Report:
    base = normalize_base(url)
    return Verifier(
        root=root,
        base=base,
        signature=signature,
        hmac_key=hmac_key,
        timeout=timeout,
        max_bytes=max_bytes,
        host_policy=host_policy,
    ).run()


def render(report: Report) -> str:
    lines = [
        f"verify published {report.base}",
        f"  local artifact: {report.root}",
    ]
    for result in report.results:
        if result.skipped:
            state = "SKIP"
        elif result.ok:
            state = "PASS"
        else:
            state = "FAIL"
        lines.append(f"  {state} {result.name}: {result.detail}")
    if report.failures:
        lines.append(
            f"published verification failed: {len(report.failures)} check(s) failed"
        )
    else:
        suffix = f" ({len(report.skips)} skipped)" if report.skips else ""
        lines.append(
            f"published verification passed: {len(report.passes)} check(s){suffix}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--url", required=True, help="published site root URL")
    parser.add_argument(
        "--signature",
        help="publish-run HMAC signature for direct verification",
    )
    parser.add_argument(
        "--hmac-key-file",
        type=Path,
        help="file holding the HMAC key (default: STATUS_HEARTBEAT_HMAC)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"seconds per request (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"total response budget (default: {DEFAULT_MAX_BYTES})",
    )
    parser.add_argument(
        "--host-policy",
        choices=("auto", "github-pages", "edge"),
        default="auto",
        help="cache policy to require (default: detect GitHub Pages)",
    )
    args = parser.parse_args(argv)

    try:
        base = normalize_base(args.url)
    except UsageError as exc:
        parser.error(str(exc))
        return 2

    key: bytes | None = None
    if args.signature:
        if args.hmac_key_file is not None:
            try:
                key = heartbeat_signature.load_key(args.hmac_key_file)
            except heartbeat_signature.SignatureError as exc:
                parser.error(str(exc))
                return 2
        else:
            encoded = os.environ.get("STATUS_HEARTBEAT_HMAC", "")
            if not encoded:
                parser.error(
                    "--signature requires --hmac-key-file or STATUS_HEARTBEAT_HMAC"
                )
                return 2
            key = encoded.encode("utf-8")
    elif args.hmac_key_file is not None:
        parser.error("--hmac-key-file requires --signature")
        return 2

    try:
        report = verify(
            args.root,
            base,
            signature=args.signature,
            hmac_key=key,
            timeout=args.timeout,
            max_bytes=args.max_bytes,
            host_policy=args.host_policy,
        )
    except UsageError as exc:
        parser.error(str(exc))
        return 2
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
