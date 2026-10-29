#!/usr/bin/env python3
"""Validate the public status document and the static site contract.

The same validator runs on every push and against the live document in the
post-deploy check, so the seed, the live document, and the page cannot drift
apart.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import platform_contract
import site_build
import status_snapshot
from status_snapshot import (
    STATUS_LABELS_FR,
    document_digest,
    format_stamp,
    parse_timestamp as parse_snapshot_timestamp,
    snapshot_state,
)

STATUSES = {
    "operational",
    "degraded",
    "partial_outage",
    "major_outage",
    "maintenance",
    "unknown",
}
INCIDENT_STATUSES = {"investigating", "identified", "monitoring", "resolved"}
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MAX_LIFETIME = timedelta(minutes=15)
FUTURE_SKEW = timedelta(minutes=5)
SNAPSHOT_ATTR = re.compile(r'data-snapshot-at="([^"]+)"')
DIGEST_ATTR = re.compile(r'data-status-digest="([0-9a-f]{64})"')
CSP_META = re.compile(
    r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"',
    re.IGNORECASE,
)
HASHED_SITE_ASSET = re.compile(r"^assets/[a-z0-9-]+\.[0-9a-f]{12}\.(?:css|js)$")
PRINT_TOKEN = re.compile(r"(--sbk-[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6});")
HEADER_LOGO_TOKEN = "--sbk-logo-header"
# WCAG 1.4.11 exempts logotypes. The brand-approved reverse lockup carries one
# #2255BC mark that cannot reach 3:1 on any uniform surface that also keeps its
# #FFFFFF wordmark legible, and the brand layer publishes no dark-surface
# variant. The exemption is tied to the exact published file and fill and to
# the measured ratio on the surface the stylesheet actually renders. A
# recolour, a different surface, or a different asset fails until the brand
# layer ships a variant and this record is replaced by a 3:1 threshold.
HEADER_LOGOTYPE_FILL = "#2255BC"
HEADER_LOGOTYPE_RATIO = 2.92
TOP_LEVEL = {
    "version",
    "generated_at",
    "expires_at",
    "overall",
    "summary_fr",
    "summary_en",
    "components",
    "incidents",
}
COMPONENT_FIELDS = {"id", "label_fr", "label_en", "status", "detail_fr", "detail_en"}
INCIDENT_FIELDS = {
    "id",
    "status",
    "title_fr",
    "title_en",
    "started_at",
    "updated_at",
    "summary_fr",
    "summary_en",
}
REQUIRED_FILES = (
    "index.html",
    "404.html",
    "styles.css",
    "app.js",
    "status-model.js",
    "site.manifest.json",
    "templates/csp.txt",
)
SITE_HOST = None  # resolved from the platform naming standard on first use


def status_host() -> str:
    """Return the one public status host without importing the platform early."""
    global SITE_HOST
    if SITE_HOST is None:
        SITE_HOST = platform_contract.status_host()
    return SITE_HOST
DARK_SELECTOR = ':root[data-theme="dark"]'
BRAND_PROPERTY = re.compile(r"(--sbk-[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6});")
REVERSE_LOCKUP = "logo-reverse.svg"
FOOTER_ICON = "icon-color.svg"
SVG_TAG = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
SVG_DIMENSION = re.compile(r'\b(width|height)="([0-9]+(?:\.[0-9]+)?)"')
SVG_FILL = re.compile(r'fill="(#[0-9A-Fa-f]{6})"', re.IGNORECASE)
CSS_BLOCK = re.compile(r"(?s)([^{}]+)\{([^{}]*)\}")
STORAGE_PATTERNS = (
    (re.compile(r"\blocalStorage\b"), "localStorage"),
    (re.compile(r"\bsessionStorage\b"), "sessionStorage"),
    (re.compile(r"\bdocument\s*\.\s*cookie\b"), "document.cookie"),
    (re.compile(r"\bindexedDB\b", re.IGNORECASE), "IndexedDB"),
    (re.compile(r"\bcaches\s*\."), "Cache Storage"),
    (re.compile(r"\bserviceWorker\b"), "service workers"),
    (re.compile(r"\bsendBeacon\b"), "beacons"),
    (re.compile(r"\bSet-Cookie\b", re.IGNORECASE), "Set-Cookie"),
)
FRENCH_LANG = re.compile(r"^fr(?:-[A-Za-z0-9]{1,8})*$")
EVENT_HANDLER = re.compile(r"^on[a-z]+$")
CSS_URL = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)
CSS_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?(['\"]?)([^'\")\s;]+)\1", re.IGNORECASE)
VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
RESOURCE_ATTRIBUTES = {"src", "srcset", "poster", "data", "action", "formaction", "manifest"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def non_empty_text(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def unknown_keys(value: dict, allowed: set[str], where: str) -> list[str]:
    return [f"{where} has unknown field {key!r}" for key in sorted(set(value) - allowed)]


def validate_document(
    status: object,
    *,
    now: datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(status, dict):
        return ["status document must be a JSON object"]
    errors.extend(unknown_keys(status, TOP_LEVEL, "status"))

    if type(status.get("version")) is not int or status.get("version") != 1:
        errors.append("version must be the integer 1")
    if status.get("overall") not in STATUSES:
        errors.append("overall must be one of: " + ", ".join(sorted(STATUSES)))
    for field in ("summary_fr", "summary_en"):
        if not non_empty_text(status.get(field)):
            errors.append(f"{field} must be a non-empty string")

    generated = None
    expires = None
    for field in ("generated_at", "expires_at"):
        if field not in status:
            errors.append(f"{field} is required")
            continue
        value = status[field]
        if value is None:
            continue
        parsed = parse_utc(value)
        if parsed is None:
            errors.append(f"{field} must be null or a UTC ISO 8601 timestamp ending in Z")
        elif field == "generated_at":
            generated = parsed
        else:
            expires = parsed
    if (generated is None) != (expires is None):
        errors.append("generated_at and expires_at must both be null or both be set")
    elif generated is not None and expires is not None:
        if expires <= generated:
            errors.append("expires_at must be after generated_at")
        elif expires - generated > MAX_LIFETIME:
            errors.append("expires_at must be at most 15 minutes after generated_at")
        instant = now or datetime.now(timezone.utc)
        if generated - instant > FUTURE_SKEW:
            errors.append(
                "generated_at must not be more than 5 minutes in the future"
            )

    components = status.get("components")
    if not isinstance(components, list) or not components:
        errors.append("components must be a non-empty list")
    else:
        component_ids: set[str] = set()
        for index, component in enumerate(components):
            where = f"components[{index}]"
            if not isinstance(component, dict):
                errors.append(f"{where} must be an object")
                continue
            errors.extend(unknown_keys(component, COMPONENT_FIELDS, where))
            component_id = component.get("id")
            if not non_empty_text(component_id):
                errors.append(f"{where}.id must be a non-empty string")
            elif component_id in component_ids:
                errors.append(f"{where}.id duplicates {component_id!r}")
            else:
                component_ids.add(component_id)
            for field in ("label_fr", "label_en", "detail_fr", "detail_en"):
                if not non_empty_text(component.get(field)):
                    errors.append(f"{where}.{field} must be a non-empty string")
            if component.get("status") not in STATUSES:
                errors.append(
                    f"{where}.status must be one of: " + ", ".join(sorted(STATUSES))
                )
        if status.get("generated_at") is None and status.get("expires_at") is None:
            # A document without a heartbeat has observed nothing. It may not
            # claim a healthy rollup or confirmed component states.
            if status.get("overall") != "unknown":
                errors.append(
                    "overall must be unknown when generated_at is null"
                )
            for index, component in enumerate(components):
                if isinstance(component, dict) and component.get("status") != "unknown":
                    errors.append(
                        f"components[{index}].status must be unknown when "
                        "generated_at is null"
                    )

    incidents = status.get("incidents")
    if not isinstance(incidents, list):
        errors.append("incidents must be a list")
    else:
        incident_ids: set[str] = set()
        for index, incident in enumerate(incidents):
            where = f"incidents[{index}]"
            if not isinstance(incident, dict):
                errors.append(f"{where} must be an object")
                continue
            errors.extend(unknown_keys(incident, INCIDENT_FIELDS, where))
            incident_id = incident.get("id")
            if not non_empty_text(incident_id):
                errors.append(f"{where}.id must be a non-empty string")
            elif incident_id in incident_ids:
                errors.append(f"{where}.id duplicates {incident_id!r}")
            else:
                incident_ids.add(incident_id)
            if incident.get("status") not in INCIDENT_STATUSES:
                errors.append(
                    f"{where}.status must be one of: "
                    + ", ".join(sorted(INCIDENT_STATUSES))
                )
            for field in ("title_fr", "title_en", "summary_fr", "summary_en"):
                if not non_empty_text(incident.get(field)):
                    errors.append(f"{where}.{field} must be a non-empty string")
            started = parse_utc(incident.get("started_at"))
            updated = parse_utc(incident.get("updated_at"))
            if started is None:
                errors.append(f"{where}.started_at must be a UTC ISO 8601 timestamp")
            if updated is None:
                errors.append(f"{where}.updated_at must be a UTC ISO 8601 timestamp")
            if started is not None and updated is not None and updated < started:
                errors.append(f"{where}.updated_at must not be before started_at")
    return errors


class Element:
    """A small DOM node used by the page contract parser."""

    __slots__ = ("tag", "attrs", "parent", "children")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "Element | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[Element | str] = []

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            if isinstance(child, Element):
                parts.append(child.text())
            else:
                parts.append(child)
        return "".join(parts)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.walk()

    def find_all(
        self,
        tag: str | None = None,
        *,
        class_name: str | None = None,
        element_id: str | None = None,
    ):
        for node in self.walk():
            if tag is not None and node.tag != tag:
                continue
            if class_name is not None and class_name not in classes(node):
                continue
            if element_id is not None and node.attrs.get("id") != element_id:
                continue
            yield node


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", {}, None)
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = Element(tag, {name.lower(): value or "" for name, value in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        node = Element(tag, {name.lower(): value or "" for name, value in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_ELEMENTS:
            return
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def classes(node: Element) -> set[str]:
    return set(node.attrs.get("class", "").split())


def aria_hidden(node: Element) -> bool:
    current: Element | None = node
    while current is not None:
        if current.attrs.get("aria-hidden", "").strip().lower() == "true":
            return True
        current = current.parent
    return False


def brand_properties(stylesheet: str, *, dark: bool = False) -> dict[str, str]:
    """Return the six-digit colour tokens for one mode of the brand sheet.

    The brand layer declares light tokens first and redefines them under the
    dark selector. Slicing at that selector keeps one mode from overwriting the
    other, so a dark-only pair cannot pass a light-mode test by accident.
    """
    index = stylesheet.find(DARK_SELECTOR)
    if dark:
        if index < 0:
            return {}
        stylesheet = stylesheet[index:]
    elif index >= 0:
        stylesheet = stylesheet[:index]
    return {
        match.group(1): match.group(2).upper()
        for match in BRAND_PROPERTY.finditer(stylesheet)
    }


def srgb_channel(value: int) -> float:
    value = value / 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(colour: str) -> float:
    red, green, blue = (int(colour[index : index + 2], 16) for index in (1, 3, 5))
    return (
        0.2126 * srgb_channel(red)
        + 0.7152 * srgb_channel(green)
        + 0.0722 * srgb_channel(blue)
    )


def contrast_ratio(foreground: str, background: str) -> float:
    first, second = sorted(
        (relative_luminance(foreground), relative_luminance(background)),
        reverse=True,
    )
    return (first + 0.05) / (second + 0.05)


def parse_page(text: str) -> Element:
    parser = PageParser()
    parser.feed(text)
    parser.close()
    return parser.root


def is_landmark(node: Element) -> bool:
    parent = node.parent
    while parent is not None and parent.tag != "#document":
        if parent.tag in {"article", "aside", "main", "nav", "section"}:
            return False
        parent = parent.parent
    return True


def is_focusable(node: Element) -> bool:
    if node.attrs.get("tabindex") == "-1":
        return False
    if node.tag == "a" and "href" in node.attrs:
        return True
    if node.tag in {"button", "input", "select", "textarea", "summary"}:
        return True
    return "tabindex" in node.attrs


def check_url(value: str, where: str) -> list[str]:
    value = value.strip()
    if not value or value.startswith("#") or value.lower().startswith("data:"):
        return []
    if value.startswith("//"):
        return [f"{where}: third-party origin {value!r} is not allowed"]
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        if (parsed.hostname or "").lower() != status_host():
            return [f"{where}: third-party origin {value!r} is not allowed"]
    elif parsed.scheme == "javascript":
        return [f"{where}: javascript: URL is not allowed"]
    return []


def check_srcset(value: str, where: str) -> list[str]:
    errors: list[str] = []
    for candidate in value.split(","):
        candidate = candidate.strip()
        if candidate:
            errors.extend(check_url(candidate.split()[0], where))
    return errors


def validate_css(name: str, text: str) -> list[str]:
    errors: list[str] = []
    for match in CSS_URL.finditer(text):
        errors.extend(check_url(match.group(2), f"{name} url()"))
    for match in CSS_IMPORT.finditer(text):
        errors.extend(check_url(match.group(2), f"{name} @import"))
    return errors


def css_rule(stylesheet: str, selector: str) -> dict[str, str]:
    """Return the declarations of the rule whose selector list contains one exact selector.

    A longer selector such as `.status-header__inner` cannot satisfy a request
    for `.status-header`, so a rule cannot leak into the measurement of another
    element.
    """
    declarations: dict[str, str] = {}
    for match in CSS_BLOCK.finditer(stylesheet):
        raw = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.DOTALL)
        selectors = [part.strip() for part in raw.split(",")]
        if selector not in selectors:
            continue
        for declaration in match.group(2).split(";"):
            name, separator, value = declaration.partition(":")
            if separator:
                declarations[name.strip()] = value.strip()
    return declarations


def svg_dimensions(path: Path) -> tuple[float, float] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    tag = SVG_TAG.search(text)
    if tag is None:
        return None
    values = dict(SVG_DIMENSION.findall(tag.group(0)))
    if "width" not in values or "height" not in values:
        return None
    width, height = float(values["width"]), float(values["height"])
    if width <= 0 or height <= 0:
        return None
    return width, height


def svg_fills(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {match.upper() for match in SVG_FILL.findall(text)}


def local_site_path(href: str) -> str | None:
    value = href.strip()
    if not value or value.startswith("#"):
        return None
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        if (parsed.hostname or "").lower() != status_host():
            return None
        path = parsed.path
    elif parsed.scheme or value.startswith("//"):
        return None
    else:
        path = value.split("?", 1)[0].split("#", 1)[0]
    path = path.lstrip("/")
    return path or None


def validate_no_storage(sources: dict[str, str]) -> list[str]:
    """Reject every browser storage, cookie, worker, and beacon API."""
    errors: list[str] = []
    for name, text in sources.items():
        for pattern, label in STORAGE_PATTERNS:
            if pattern.search(text):
                errors.append(
                    f"{name}: {label} is not allowed by the public no-storage contract"
                )
    return errors


def element_text(node: Element | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text()).strip()


def first_time(node: Element | None) -> Element | None:
    if node is None:
        return None
    return next(node.find_all("time"), None)


def ordered_rows(node: Element | None, tag: str = "li") -> list[Element]:
    if node is None:
        return []
    return [
        child
        for child in node.children
        if isinstance(child, Element) and child.tag == tag
    ]


VITAL_KEYS = ("operational", "degraded", "outage", "maintenance", "unknown")


def vital_key(status: str) -> str:
    if status in ("partial_outage", "major_outage"):
        return "outage"
    return status if status in VITAL_KEYS else "unknown"


def validate_vitals(name: str, vitals: Element, expected: dict) -> list[str]:
    """The static instrument must be visible and carry the snapshot values.

    It is no longer a JavaScript-only placeholder: hiding it until the first
    read was what moved the hero and the board after status.json arrived.
    """
    errors: list[str] = []
    if "hidden" in vitals.attrs:
        errors.append(
            f"{name}: #vitals must stay visible; its publish-time values are "
            "part of the static snapshot"
        )
    total = vitals.attrs.get("data-total", "").strip()
    if total != str(expected["total"]):
        errors.append(
            f"{name}: #vitals data-total must be {expected['total']!r}"
        )
    counts = {
        node.attrs.get("data-count", ""): element_text(node)
        for node in vitals.find_all("span", class_name="vitals__count")
    }
    for key in VITAL_KEYS:
        if counts.get(key) != str(expected["counts"][key]):
            errors.append(
                f"{name}: #vitals count for {key!r} must be "
                f"{expected['counts'][key]!r}"
            )
    if "segments" in expected:
        segments = {
            node.attrs.get("data-vital", ""): node
            for node in vitals.find_all("circle", class_name="vitals__segment")
        }
        for key in VITAL_KEYS:
            segment = segments.get(key)
            if segment is None:
                errors.append(f"{name}: #vitals is missing the {key!r} segment")
                continue
            want = expected["segments"][key]
            if segment.attrs.get("stroke-dasharray") != want["dasharray"]:
                errors.append(
                    f"{name}: #vitals {key!r} segment dash array must be "
                    f"{want['dasharray']!r}"
                )
            if segment.attrs.get("stroke-dashoffset") != want["dashoffset"]:
                errors.append(
                    f"{name}: #vitals {key!r} segment dash offset must be "
                    f"{want['dashoffset']!r}"
                )
    if "signals" in expected:
        signal_value = next(
            vitals.find_all("span", class_name="vitals__signal-value"), None
        )
        if expected["signals"] is None:
            if element_text(signal_value) != "Inconnu":
                errors.append(
                    f"{name}: #vitals must report the signal count as unknown"
                )
        else:
            signals = next(
                vitals.find_all("span", element_id="vitals-signals"), None
            )
            total_node = next(
                vitals.find_all("span", element_id="vitals-signal-total"), None
            )
            if element_text(signals) != str(expected["signals"]):
                errors.append(
                    f"{name}: #vitals signal count must be "
                    f"{expected['signals']!r}"
                )
            if element_text(total_node) != str(expected["total"]):
                errors.append(
                    f"{name}: #vitals signal total must be "
                    f"{expected['total']!r}"
                )
    return errors


def validate_snapshot(
    name: str,
    text: str,
    document: dict,
    status_bytes: bytes,
) -> list[str]:
    """Re-render the document and reject a page that claims more than it.

    The snapshot is tied to the exact status.json bytes by its digest and to
    the freshness instant it records. A stale or unconfirmed heartbeat may not
    leave a live-looking headline, a confirmed row, or the publisher summary on
    the page.
    """
    errors: list[str] = []
    page = parse_page(text)
    body = next(page.find_all("body"), None)
    if body is None:
        return [f"{name}: missing <body>"]

    snapshot_raw = body.attrs.get("data-snapshot-at", "").strip()
    digest = body.attrs.get("data-status-digest", "").strip()
    snapshot_at = parse_snapshot_timestamp(snapshot_raw)
    if snapshot_at is None:
        errors.append(
            f"{name}: body data-snapshot-at must be a UTC timestamp ending in Z"
        )
    if digest != document_digest(status_bytes):
        errors.append(
            f"{name}: data-status-digest does not match the exact status.json bytes"
        )
    if snapshot_at is None:
        return errors

    expected = snapshot_state(document, snapshot_at)
    hero = next(page.find_all(element_id="hero"), None)
    if hero is None:
        return errors + [f"{name}: missing #hero"]
    if "hidden" in hero.attrs:
        errors.append(f"{name}: #hero must be visible in the static snapshot")
    if hero.attrs.get("data-status") != expected["headline"]:
        errors.append(
            f"{name}: #hero data-status {hero.attrs.get('data-status')!r} "
            f"must be {expected['headline']!r} for this heartbeat"
        )

    status_word = next(page.find_all(element_id="hero-status-text"), None)
    if element_text(status_word) != expected["overall_word"]:
        errors.append(
            f"{name}: #hero-status-text {element_text(status_word)!r} "
            f"must be {expected['overall_word']!r} for this heartbeat"
        )
    elif aria_hidden(status_word):
        errors.append(
            f"{name}: #hero-status-text must expose the status as text"
        )

    summary = next(page.find_all(element_id="overall-summary"), None)
    if element_text(summary) != expected["summary"]:
        errors.append(
            f"{name}: #overall-summary does not match the snapshot summary; "
            "a stale document must use the stale copy, not the publisher claim"
        )

    stale = next(page.find_all(element_id="stale-banner"), None)
    if stale is None:
        errors.append(f"{name}: missing #stale-banner")
    else:
        visible = "hidden" not in stale.attrs
        if expected["banner"] is None and visible:
            errors.append(
                f"{name}: #stale-banner must stay hidden for a fresh heartbeat"
            )
        if expected["banner"] is not None:
            if not visible:
                errors.append(
                    f"{name}: #stale-banner must be visible for a {expected['reason']} snapshot"
                )
            if stale.attrs.get("role", "").strip().lower() != "status":
                errors.append(f"{name}: #stale-banner must use role='status'")
            if stale.attrs.get("data-reason") != expected["reason"]:
                errors.append(
                    f"{name}: #stale-banner data-reason must be "
                    f"{expected['reason']!r}"
                )
            title = next(stale.find_all(element_id="stale-title"), None)
            body_text = next(stale.find_all(element_id="stale-text"), None)
            if element_text(title) != expected["banner"]["title"]:
                errors.append(
                    f"{name}: #stale-title must be {expected['banner']['title']!r}"
                )
            if element_text(body_text) != expected["banner"]["text"]:
                errors.append(
                    f"{name}: #stale-text must be {expected['banner']['text']!r}"
                )

    error_banner = next(page.find_all(element_id="fetch-error"), None)
    if error_banner is None:
        errors.append(f"{name}: missing #fetch-error")
    elif "hidden" not in error_banner.attrs:
        errors.append(
            f"{name}: #fetch-error must stay hidden in the static snapshot"
        )
    if (
        stale is not None
        and error_banner is not None
        and "hidden" not in stale.attrs
        and "hidden" not in error_banner.attrs
    ):
        errors.append(
            f"{name}: the fetch error and the stale notice must never be visible together"
        )

    components = next(page.find_all("ul", element_id="components-list"), None)
    rows = ordered_rows(components)
    if len(rows) != len(expected["components"]):
        errors.append(
            f"{name}: #components-list has {len(rows)} rows, "
            f"the document has {len(expected['components'])}"
        )
    else:
        for index, (row, component) in enumerate(
            zip(rows, expected["components"]), 1
        ):
            heading = next(row.find_all("h3"), None)
            pill = next(row.find_all("span", class_name="pill"), None)
            if "component" not in classes(row):
                errors.append(f"{name}: component row {index} must use class 'component'")
            if "hidden" in row.attrs:
                errors.append(
                    f"{name}: component row {index} must be visible without JavaScript"
                )
            if row.attrs.get("data-status") != component["status"]:
                errors.append(
                    f"{name}: component row {index} data-status must be "
                    f"{component['status']!r}"
                )
            if element_text(heading) != component["label"]:
                errors.append(
                    f"{name}: component row {index} name must be {component['label']!r}"
                )
            if pill is None:
                errors.append(f"{name}: component row {index} needs a text status label")
            else:
                if pill.attrs.get("data-status") != component["status"]:
                    errors.append(
                        f"{name}: component row {index} pill status must be "
                        f"{component['status']!r}"
                    )
                if element_text(pill) != component["status_label"]:
                    errors.append(
                        f"{name}: component row {index} pill must read "
                        f"{component['status_label']!r}"
                    )
            if next(row.find_all("p"), None) is not None:
                errors.append(
                    f"{name}: component row {index} must stay compact; "
                    "move shared explanation to #components-note"
                )

    count = next(page.find_all(element_id="components-count"), None)
    if element_text(count) != expected["component_count_text"]:
        errors.append(
            f"{name}: #components-count must be "
            f"{expected['component_count_text']!r}"
        )

    note = next(page.find_all(element_id="components-note"), None)
    if note is None:
        errors.append(f"{name}: missing #components-note")
    else:
        if "hidden" in note.attrs:
            errors.append(f"{name}: #components-note must be visible with static rows")
        if element_text(note) != expected["note"]:
            errors.append(
                f"{name}: #components-note must be {expected['note']!r}"
            )

    empty = next(page.find_all(element_id="components-empty"), None)
    if empty is None:
        errors.append(f"{name}: missing #components-empty")
    elif expected["components"] and "hidden" not in empty.attrs:
        errors.append(
            f"{name}: #components-empty must be hidden when static rows are rendered"
        )
    elif not expected["components"] and "hidden" in empty.attrs:
        errors.append(
            f"{name}: #components-empty must be visible when no row is rendered"
        )

    vitals = next(page.find_all(element_id="vitals"), None)
    if vitals is None:
        errors.append(f"{name}: missing #vitals")
    else:
        errors.extend(
            validate_vitals(
                name,
                vitals,
                expected["vitals"],
            )
        )

    incidents = next(page.find_all("ol", element_id="incidents-list"), None)
    incident_rows = ordered_rows(incidents)
    if len(incident_rows) != len(expected["incidents"]):
        errors.append(
            f"{name}: #incidents-list has {len(incident_rows)} rows, "
            f"the document has {len(expected['incidents'])}"
        )
    else:
        for index, (row, incident) in enumerate(
            zip(incident_rows, expected["incidents"]), 1
        ):
            title = next(row.find_all("h3"), None)
            pill = next(row.find_all("span", class_name="pill"), None)
            if element_text(title) != incident["title"]:
                errors.append(
                    f"{name}: incident row {index} title must be {incident['title']!r}"
                )
            if pill is None or pill.attrs.get("data-status") != incident["status"]:
                errors.append(
                    f"{name}: incident row {index} pill status must be "
                    f"{incident['status']!r}"
                )
            elif element_text(pill) != incident["status_label"]:
                errors.append(
                    f"{name}: incident row {index} pill must read "
                    f"{incident['status_label']!r}"
                )
            started = parse_snapshot_timestamp(incident["started_at"])
            updated = parse_snapshot_timestamp(incident["updated_at"])
            datetimes = {
                node.attrs.get("datetime")
                for node in row.find_all("time")
            }
            if started is not None and status_snapshot.iso(started) not in datetimes:
                errors.append(
                    f"{name}: incident row {index} must show its started_at time"
                )
            if updated is not None and status_snapshot.iso(updated) not in datetimes:
                errors.append(
                    f"{name}: incident row {index} must show its updated_at time"
                )

    incident_count = next(page.find_all(element_id="incidents-count"), None)
    if element_text(incident_count) != expected["incidents_count_text"]:
        errors.append(
            f"{name}: #incidents-count must be "
            f"{expected['incidents_count_text']!r}"
        )
    incident_empty = next(page.find_all(element_id="incidents-empty"), None)
    if incident_empty is None:
        errors.append(f"{name}: missing #incidents-empty")
    elif expected["incidents"] and "hidden" not in incident_empty.attrs:
        errors.append(
            f"{name}: #incidents-empty must be hidden when incidents are shown"
        )
    elif not expected["incidents"] and "hidden" in incident_empty.attrs:
        errors.append(
            f"{name}: #incidents-empty must be visible when the journal is empty"
        )

    marker = next(page.find_all(element_id="snapshot-marker"), None)
    if marker is None:
        errors.append(f"{name}: missing #snapshot-marker")
    else:
        if marker.attrs.get("data-snapshot-state") != expected["reason"]:
            errors.append(
                f"{name}: #snapshot-marker data-snapshot-state must be "
                f"{expected['reason']!r}"
            )
        marker_time = first_time(marker)
        if expected["generated_iso"] is None:
            if marker_time is not None:
                errors.append(
                    f"{name}: #snapshot-marker must not show a time without a heartbeat"
                )
            if "aucun signal" not in element_text(marker).lower():
                errors.append(
                    f"{name}: #snapshot-marker must say no signal was received"
                )
        elif marker_time is None or marker_time.attrs.get("datetime") != expected["generated_iso"]:
            errors.append(
                f"{name}: #snapshot-marker must carry the generated_at time"
            )

    for element_id in ("hero-signal",):
        node = next(page.find_all(element_id=element_id), None)
        if expected["generated_iso"] is None:
            if element_text(node) != "Aucun":
                errors.append(f"{name}: #{element_id} must read 'Aucun'")
        else:
            stamp = first_time(node)
            if stamp is None or stamp.attrs.get("datetime") != expected["generated_iso"]:
                errors.append(
                    f"{name}: #{element_id} must carry generated_at as a <time>"
                )
            elif element_text(stamp) != format_stamp(expected["generated"]):
                errors.append(
                    f"{name}: #{element_id} must render the generated_at stamp"
                )

    checked = next(page.find_all(element_id="hero-checked"), None)
    checked_time = first_time(checked)
    if (
        checked_time is None
        or checked_time.attrs.get("datetime") != expected["snapshot_iso"]
    ):
        errors.append(
            f"{name}: #hero-checked must record the snapshot instant, not a live claim"
        )

    footer_time = next(page.find_all(element_id="snapshot-time"), None)
    if expected["generated_iso"] is None:
        if element_text(footer_time) != "Aucun":
            errors.append(f"{name}: #snapshot-time must read 'Aucun' without a heartbeat")
    else:
        stamp = first_time(footer_time)
        if stamp is None or stamp.attrs.get("datetime") != expected["generated_iso"]:
            errors.append(
                f"{name}: #snapshot-time must carry generated_at as a <time>"
            )

    noscript = next(page.find_all("noscript"), None)
    if noscript is None or not noscript.text().strip():
        errors.append(f"{name}: <noscript> must explain the static snapshot")
    elif "status.json" not in noscript.text():
        errors.append(f"{name}: <noscript> must link or name status.json")
    elif "ne lit pas" in noscript.text().lower():
        errors.append(
            f"{name}: <noscript> must not claim the page cannot read the status"
        )
    return errors


def validate_page(
    name: str,
    text: str,
    *,
    require_status: bool = False,
    theme_color: str | None = None,
    csp: str | None = None,
) -> list[str]:
    root = parse_page(text)
    errors: list[str] = []

    html = next(root.find_all("html"), None)
    if html is None:
        errors.append(f"{name}: missing <html> element")
    else:
        lang = html.attrs.get("lang", "").strip()
        if not lang:
            errors.append(f"{name}: <html> must declare a lang attribute")
        elif not FRENCH_LANG.fullmatch(lang):
            errors.append(
                f"{name}: <html lang={lang!r}> must declare French as the document language"
            )
        if html.attrs.get("data-theme", "").strip().lower() != "dark":
            errors.append(
                f'{name}: <html> must declare data-theme="dark" for the graphite surface'
            )
        if "no-js" not in classes(html):
            errors.append(
                f'{name}: <html> must declare class="no-js" so the static '
                "snapshot is the default and JavaScript only enhances it"
            )

    mains = list(root.find_all("main"))
    if len(mains) != 1:
        errors.append(f"{name}: must contain exactly one <main> landmark (found {len(mains)})")
    if not any(is_landmark(node) for node in root.find_all("header")):
        errors.append(f"{name}: missing top-level <header> banner landmark")
    if not any(is_landmark(node) for node in root.find_all("footer")):
        errors.append(f"{name}: missing top-level <footer> contentinfo landmark")

    color_schemes = [
        node
        for node in root.find_all("meta")
        if node.attrs.get("name", "").lower() == "color-scheme"
    ]
    if not color_schemes:
        errors.append(f"{name}: missing color-scheme meta tag")
    else:
        values = re.split(r"[\s,]+", color_schemes[0].attrs.get("content", "").lower())
        if "dark" not in values:
            errors.append(f"{name}: color-scheme meta must include dark")

    theme_metas = [
        node
        for node in root.find_all("meta")
        if node.attrs.get("name", "").lower() == "theme-color"
    ]
    if not theme_metas:
        errors.append(f"{name}: missing theme-color meta tag")
    elif len(theme_metas) > 1:
        errors.append(f"{name}: must declare exactly one theme-color meta tag")
    else:
        got = theme_metas[0].attrs.get("content", "").strip()
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", got):
            errors.append(f"{name}: theme-color must be a six-digit hex colour")
        elif theme_color is not None and got.lower() != theme_color.lower():
            errors.append(
                f"{name}: theme-color {got!r} must equal the dark --sbk-canvas "
                f"token {theme_color!r}"
            )

    csp_metas = [
        node
        for node in root.find_all("meta")
        if node.attrs.get("http-equiv", "").lower() == "content-security-policy"
    ]
    if len(csp_metas) != 1:
        errors.append(f"{name}: must declare exactly one Content-Security-Policy meta")
    else:
        policy = csp_metas[0].attrs.get("content", "").strip()
        if "frame-ancestors" in policy:
            errors.append(
                f"{name}: frame-ancestors is ignored in a meta CSP; remove it "
                "and set it at the edge"
            )
        if csp is not None and policy != csp:
            errors.append(
                f"{name}: Content-Security-Policy must equal templates/csp.txt"
            )

    brand_links = [
        node for node in root.find_all("a") if "sbk-brand" in classes(node)
    ]
    if not brand_links:
        errors.append(f"{name}: missing the .sbk-brand header mark link")
    else:
        mark = next(
            brand_links[0].find_all("img", class_name="status-brand__mark"), None
        )
        if mark is None:
            errors.append(f"{name}: the .sbk-brand link must contain the header mark")
        else:
            if not mark.attrs.get("alt", "").strip():
                errors.append(f"{name}: the header mark must have a text alternative")
            height = mark.attrs.get("height", "").strip()
            width = mark.attrs.get("width", "").strip()
            if not height.isdigit() or not width.isdigit():
                errors.append(
                    f"{name}: the header mark must declare numeric width and height"
                )
            elif int(height) <= 0 or int(width) <= 0:
                errors.append(f"{name}: the header mark dimensions must be positive")

    skip_links = [node for node in root.find_all("a") if classes(node) & {"sbk-skip", "skip"}]
    if not skip_links:
        errors.append(f"{name}: missing skip link to the main content")
    else:
        skip = skip_links[0]
        if not skip.text().strip():
            errors.append(f"{name}: skip link must have text")
        href = skip.attrs.get("href", "")
        if not href.startswith("#") or len(href) == 1:
            errors.append(f"{name}: skip link must target a fragment")
        else:
            target = next(root.find_all(element_id=href[1:]), None)
            if target is None:
                errors.append(f"{name}: skip link target {href!r} does not exist")
            elif target.tag != "main":
                errors.append(f"{name}: skip link must target <main>")
            elif target.attrs.get("tabindex") != "-1":
                errors.append(f"{name}: skip link target <main> must use tabindex='-1'")
        focusable = [node for node in root.walk() if is_focusable(node)]
        if focusable and focusable[0] is not skip:
            errors.append(f"{name}: skip link must be the first focusable element")

    headings = [node for node in root.walk() if node.tag in HEADING_TAGS]
    h1s = [node for node in headings if node.tag == "h1"]
    if len(h1s) != 1:
        errors.append(f"{name}: must contain exactly one <h1> (found {len(h1s)})")
    if headings and headings[0].tag != "h1":
        errors.append(f"{name}: first heading must be <h1>, found <{headings[0].tag}>")
    previous = 0
    for heading in headings:
        level = int(heading.tag[1])
        if previous and level > previous + 1:
            errors.append(f"{name}: heading order skips from h{previous} to h{level}")
        previous = level
        if not heading.text().strip():
            errors.append(f"{name}: <{heading.tag}> heading must not be empty")

    for node in root.find_all("time"):
        value = node.attrs.get("datetime", "").strip()
        if not value:
            errors.append(f"{name}: <time> is missing its datetime attribute")
        else:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(
                    f"{name}: <time datetime={value!r}> is not a valid ISO 8601 timestamp"
                )
            else:
                if parsed.tzinfo is None:
                    errors.append(
                        f"{name}: <time datetime={value!r}> must include a UTC offset"
                    )
        if not node.text().strip():
            errors.append(f"{name}: <time> must contain human-readable text")

    for node in root.walk():
        for attr in node.attrs:
            if EVENT_HANDLER.fullmatch(attr):
                errors.append(
                    f"{name}: inline event handler {attr!r} is not allowed on <{node.tag}>"
                )
        if node.tag == "script":
            if not node.attrs.get("src", "").strip() and node.text().strip():
                errors.append(f"{name}: inline <script> is not allowed")
        if node.tag == "style":
            errors.append(f"{name}: inline <style> is not allowed")
        if node.tag == "base":
            errors.append(f"{name}: <base> is not allowed")
        for attr in RESOURCE_ATTRIBUTES & node.attrs.keys():
            value = node.attrs[attr]
            if attr == "srcset":
                errors.extend(check_srcset(value, f"{name}: <{node.tag} srcset>"))
            else:
                errors.extend(check_url(value, f"{name}: <{node.tag} {attr}>"))
        if node.tag in {"link", "use", "image"} and "href" in node.attrs:
            errors.extend(check_url(node.attrs["href"], f"{name}: <{node.tag} href>"))
        if "style" in node.attrs:
            for match in CSS_URL.finditer(node.attrs["style"]):
                errors.extend(
                    check_url(match.group(2), f"{name}: <{node.tag} style> url()")
                )
        if node.attrs.get("href", "").strip().lower().startswith("javascript:"):
            errors.append(f"{name}: javascript: URL is not allowed on <{node.tag}>")
        if node.tag == "meta" and node.attrs.get("http-equiv", "").lower() == "refresh":
            match = re.search(r"url=([^;]+)", node.attrs.get("content", ""), re.IGNORECASE)
            if match:
                errors.extend(
                    check_url(match.group(1).strip("'\" \t"), f"{name}: <meta refresh>")
                )

    viewports = [
        node
        for node in root.find_all("meta")
        if node.attrs.get("name", "").lower() == "viewport"
    ]
    if not viewports:
        errors.append(f"{name}: missing viewport meta tag")
    elif "width=device-width" not in viewports[0].attrs.get("content", "").replace(" ", ""):
        errors.append(f"{name}: viewport meta must set width=device-width")

    if require_status:
        hero = next(root.find_all(element_id="hero"), None)
        if hero is None:
            errors.append(f"{name}: missing #hero")
        elif hero.attrs.get("data-status", "") not in STATUSES:
            errors.append(
                f"{name}: #hero must declare a valid data-status "
                f"(found {hero.attrs.get('data-status')!r})"
            )

        status_word = next(root.find_all(element_id="hero-status-text"), None)
        if status_word is None or not status_word.text().strip():
            errors.append(f"{name}: #hero-status-text must contain static status text")
        elif aria_hidden(status_word):
            errors.append(
                f"{name}: #hero-status-text must expose the status as text, "
                "not hide it from assistive technology"
            )
        elif next(status_word.find_all("svg"), None) is not None:
            errors.append(
                f"{name}: #hero-status-text must be a text status word, not an icon"
            )

        summary = next(root.find_all(element_id="overall-summary"), None)
        if summary is None or not summary.text().strip():
            errors.append(f"{name}: #overall-summary must contain static status text")

        marker = next(root.find_all(element_id="snapshot-marker"), None)
        if marker is None or not marker.text().strip():
            errors.append(
                f"{name}: #snapshot-marker must state the snapshot instant and "
                "that it may no longer be current"
            )

        components = next(root.find_all("ul", element_id="components-list"), None)
        rows: list[Element] = []
        if components is None:
            errors.append(f"{name}: missing #components-list")
        else:
            rows = [
                child
                for child in components.children
                if isinstance(child, Element) and child.tag == "li"
            ]
            for index, row in enumerate(rows, 1):
                if "component" not in classes(row):
                    errors.append(f"{name}: component row {index} must use class 'component'")
                heading = next(row.find_all("h3"), None)
                if heading is None or not heading.text().strip():
                    errors.append(f"{name}: component row {index} needs a component name")
                pill = next(row.find_all("span", class_name="pill"), None)
                if pill is None or not pill.text().strip():
                    errors.append(f"{name}: component row {index} needs a text status label")
                elif pill.attrs.get("data-status", "") not in STATUSES:
                    errors.append(
                        f"{name}: component row {index} has invalid status "
                        f"{pill.attrs.get('data-status')!r}"
                    )
                if next(row.find_all("p"), None) is not None:
                    errors.append(
                        f"{name}: component row {index} must stay compact; "
                        "move shared explanation to #components-note"
                    )
                if next(row.find_all("p", class_name="component-detail"), None) is not None:
                    errors.append(
                        f"{name}: component row {index} must not repeat the "
                        "unconfirmed explanation"
                    )

        components_note = next(root.find_all(element_id="components-note"), None)
        if components_note is None or not components_note.text().strip():
            errors.append(f"{name}: #components-note must carry the shared row explanation")
        if len(list(root.find_all(element_id="components-note"))) != 1:
            errors.append(f"{name}: #components-note must appear exactly once")

        error_banners = list(root.find_all(element_id="fetch-error"))
        stale_banners = list(root.find_all(element_id="stale-banner"))
        if len(error_banners) != 1:
            errors.append(f"{name}: #fetch-error must appear exactly once")
        if len(stale_banners) != 1:
            errors.append(f"{name}: #stale-banner must appear exactly once")
        error_banner = error_banners[0] if error_banners else None
        stale_banner = stale_banners[0] if stale_banners else None
        if error_banner is not None and stale_banner is not None:
            if "hidden" not in error_banner.attrs and "hidden" not in stale_banner.attrs:
                errors.append(
                    f"{name}: the fetch error and the stale notice must never "
                    "be visible together"
                )
        if error_banner is not None:
            if error_banner.attrs.get("role", "").strip().lower() != "alert":
                errors.append(f"{name}: #fetch-error must use role='alert'")
            retries = list(error_banner.find_all(element_id="retry-button"))
            if (
                len(retries) != 1
                or retries[0].tag != "button"
                or not retries[0].text().strip()
            ):
                errors.append(f"{name}: #fetch-error must contain exactly one retry button")
        if stale_banner is not None:
            if stale_banner.attrs.get("role", "").strip().lower() != "status":
                errors.append(f"{name}: #stale-banner must use role='status'")

        component_count = next(root.find_all(element_id="components-count"), None)
        if component_count is None or not component_count.text().strip():
            errors.append(
                f"{name}: #components-count must expose the component count as text"
            )
        elif not rows and re.search(r"\d", component_count.text()):
            errors.append(
                f"{name}: #components-count must not claim a count before "
                "status.json is read"
            )

        vitals = next(root.find_all(element_id="vitals"), None)
        if vitals is None:
            errors.append(f"{name}: missing #vitals")
        else:
            if vitals.attrs.get("aria-hidden", "").strip().lower() != "true":
                errors.append(
                    f"{name}: #vitals must be aria-hidden because it is a "
                    "decorative view of the component board"
                )
            svg = next(vitals.find_all("svg"), None)
            if svg is None:
                errors.append(f"{name}: #vitals must contain inline SVG")
            elif (
                svg.attrs.get("aria-hidden", "").strip().lower() != "true"
                and svg.attrs.get("role", "").strip().lower() != "presentation"
            ):
                errors.append(f"{name}: the #vitals SVG must be decorative")
            total = vitals.attrs.get("data-total", "").strip()
            if not total.isdigit():
                errors.append(f"{name}: #vitals must declare a numeric data-total")
            counts = {key: 0 for key in VITAL_KEYS}
            for row in rows:
                counts[vital_key(row.attrs.get("data-status", ""))] += 1
            errors.extend(
                validate_vitals(
                    name,
                    vitals,
                    {"total": len(rows), "counts": counts},
                )
            )
            segments = {
                node.attrs.get("data-vital", "")
                for node in vitals.find_all("circle", class_name="vitals__segment")
            }
            expected_segments = set(VITAL_KEYS)
            if segments != expected_segments:
                errors.append(
                    f"{name}: #vitals must declare all five status segments; "
                    f"found {sorted(segments)}"
                )

        empty = next(root.find_all(element_id="components-empty"), None)
        if empty is None:
            errors.append(f"{name}: missing #components-empty")
        elif rows and "hidden" not in empty.attrs:
            errors.append(
                f"{name}: #components-empty must be hidden when static rows are rendered"
            )
        elif not rows and "hidden" in empty.attrs:
            errors.append(
                f"{name}: #components-empty must be visible when no row is rendered"
            )

        incidents = next(root.find_all("ol", element_id="incidents-list"), None)
        if incidents is None:
            errors.append(f"{name}: missing #incidents-list")
        else:
            rows = [
                child
                for child in incidents.children
                if isinstance(child, Element) and child.tag == "li"
            ]
            for index, row in enumerate(rows, 1):
                if next(row.find_all("span", class_name="pill"), None) is None:
                    errors.append(f"{name}: incident row {index} needs a text status label")
                if next(row.find_all("time"), None) is None:
                    errors.append(f"{name}: incident row {index} needs a <time> timestamp")
            empty = next(root.find_all(element_id="incidents-empty"), None)
            if empty is None:
                errors.append(f"{name}: missing #incidents-empty")
            elif not rows and ("hidden" in empty.attrs or not empty.text().strip()):
                errors.append(
                    f"{name}: #incidents-empty must be visible without JavaScript "
                    "when there are no incidents"
                )

        if not any(node.text().strip() for node in root.find_all("noscript")):
            errors.append(
                f"{name}: <noscript> must explain the static snapshot and link to status.json"
            )

    return errors


def load_document(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_brand_manifest(root: Path) -> tuple[dict | None, list[str]]:
    path = root / "brand.manifest.json"
    if not path.is_file():
        return None, ["brand.manifest.json is missing"]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"brand.manifest.json cannot be read: {exc}"]
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        return None, ["brand.manifest.json must declare version 1"]
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        return None, ["brand.manifest.json assets must be a list"]
    return manifest, []


def brand_variables(root: Path, manifest: dict) -> tuple[dict[str, str], list[str]]:
    """Read the dark-mode tokens from the published brand stylesheet."""
    brand = next(
        (
            asset
            for asset in manifest["assets"]
            if isinstance(asset, dict)
            and asset.get("name") == "brand.css"
            and asset.get("publish")
        ),
        None,
    )
    if brand is None:
        return {}, ["brand.manifest.json must publish brand.css"]
    filename = brand.get("file")
    if not isinstance(filename, str) or not filename.strip():
        return {}, ["brand.manifest.json brand.css entry has no file"]
    stylesheet_path = root / filename
    if not stylesheet_path.is_file():
        return {}, [f"brand.manifest.json: brand.css file {filename!r} is missing"]
    try:
        stylesheet = stylesheet_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, [f"{filename}: cannot be read: {exc}"]
    return brand_properties(stylesheet, dark=True), []


def validate_brand_marks(
    root: Path,
    pages: dict[str, str],
    manifest: dict,
    site_css: str,
    brand_css: str,
    canvas: str,
    plate: str | None,
) -> list[str]:
    """Assert the graphite marks, their geometry, and their measured contrast."""
    errors: list[str] = []
    assets = {
        asset.get("name"): asset
        for asset in manifest["assets"]
        if isinstance(asset, dict)
    }
    reverse = assets.get(REVERSE_LOCKUP)
    footer = assets.get(FOOTER_ICON)
    if reverse is None or not reverse.get("publish"):
        errors.append(
            f"brand.manifest.json must publish {REVERSE_LOCKUP} for the graphite header"
        )
        reverse = None
    if footer is None or not footer.get("publish"):
        errors.append(
            f"brand.manifest.json must publish {FOOTER_ICON} for the graphite footer"
        )
        footer = None

    reverse_file = reverse.get("file") if reverse else None
    footer_file = footer.get("file") if footer else None
    reverse_dimensions: tuple[float, float] | None = None
    if isinstance(reverse_file, str) and reverse_file.strip():
        reverse_dimensions = svg_dimensions(root / reverse_file)
        if reverse_dimensions is None:
            errors.append(
                f"{reverse_file}: cannot read the reverse lockup dimensions"
            )
    if plate is None:
        errors.append(
            "brand.css: must declare --sbk-text in the dark theme for the "
            "footer mark plate"
        )
    else:
        plate_contrast = contrast_ratio(plate, canvas)
        if plate_contrast < 3.0:
            errors.append(
                f"brand.css: the footer mark plate {plate} measures "
                f"{plate_contrast:.2f}:1 on {canvas}, below the 3:1 separation minimum"
            )
    footer_surface = plate or canvas
    if isinstance(footer_file, str) and footer_file.strip():
        fills = svg_fills(root / footer_file)
        if not fills:
            errors.append(f"{footer_file}: footer icon declares no fill colour")
        for fill in sorted(fills):
            measured = contrast_ratio(fill, footer_surface)
            if measured < 4.5:
                errors.append(
                    f"{footer_file}: fill {fill} measures {measured:.2f}:1 on "
                    f"the {footer_surface} plate, below the 4.5:1 "
                    "meaningful-graphic minimum"
                )
    stylesheet = without_print_block(site_css)
    brand_stylesheet = brand_css

    # Measure the asset the page actually references against the surface the
    # stylesheet actually paints, not a colour copied into the validator.
    header_rule = css_rule(stylesheet, ".status-header")
    header_background = header_rule.get("background", "")
    header_surface: str | None = None
    token_match = re.fullmatch(r"var\((--sbk-[a-z0-9-]+)\)", header_background)
    if token_match is not None:
        header_surface = brand_properties(brand_stylesheet, dark=True).get(
            token_match.group(1)
        )
        if header_surface is None:
            errors.append(
                f"styles.css: .status-header uses {header_background}, which the "
                "brand stylesheet does not declare in the dark theme"
            )
    elif re.fullmatch(r"#[0-9A-Fa-f]{6}", header_background):
        header_surface = header_background.upper()
    else:
        errors.append(
            "styles.css: .status-header must paint a brand token or a "
            "six-digit hex colour so the header mark contrast is measurable"
        )

    if isinstance(reverse_file, str) and reverse_file.strip() and header_surface:
        fills = svg_fills(root / reverse_file)
        approved = {"#FFFFFF", HEADER_LOGOTYPE_FILL}
        if fills != approved:
            errors.append(
                f"{reverse_file}: the reverse lockup must keep exactly the "
                f"approved fills {sorted(approved)}, found {sorted(fills)}; "
                "do not recolour the published asset"
            )
        for fill in sorted(fills):
            measured = contrast_ratio(fill, header_surface)
            if fill == "#FFFFFF":
                if measured < 4.5:
                    errors.append(
                        f"{reverse_file}: white wordmark measures {measured:.2f}:1 "
                        f"on {header_surface}, below the 4.5:1 minimum"
                    )
            elif measured >= 3.0:
                continue
            elif fill == HEADER_LOGOTYPE_FILL:
                if abs(measured - HEADER_LOGOTYPE_RATIO) > 0.01:
                    errors.append(
                        f"{reverse_file}: {fill} now measures {measured:.2f}:1 on "
                        f"{header_surface}, below 3:1 and different from the "
                        f"recorded logotype exemption {HEADER_LOGOTYPE_RATIO:.2f}:1; "
                        "re-measure the rendered surface before changing the exemption"
                    )
            else:
                errors.append(
                    f"{reverse_file}: fill {fill} has no contrast decision on "
                    f"{header_surface}"
                )

    brand_header = css_rule(brand_stylesheet, ".sbk-brand img")
    token = re.search(
        rf"{HEADER_LOGO_TOKEN}\s*:\s*([0-9]+(?:\.[0-9]+)?)px", brand_stylesheet
    )
    if not brand_header:
        errors.append(
            "brand.css: .sbk-brand img must size the header mark from the "
            "brand token"
        )
    else:
        if brand_header.get("width") != f"var({HEADER_LOGO_TOKEN})":
            errors.append(
                f"brand.css: .sbk-brand img width must be var({HEADER_LOGO_TOKEN}), "
                f"found {brand_header.get('width')!r}"
            )
        if brand_header.get("height") != "auto":
            errors.append(
                "brand.css: .sbk-brand img must keep height: auto so the "
                "reverse lockup retains its intrinsic aspect ratio"
            )
    if token is None or float(token.group(1)) <= 0:
        errors.append(
            f"brand.css: must declare a positive {HEADER_LOGO_TOKEN} token"
        )

    header = css_rule(stylesheet, ".status-brand .status-brand__mark")
    if not header:
        errors.append(
            "styles.css: .status-brand .status-brand__mark must render the header mark"
        )
    for property_name in ("width", "height", "filter", "opacity", "transform"):
        if property_name in header:
            errors.append(
                f"styles.css: the header mark must not override {property_name}; "
                f"its size comes from var({HEADER_LOGO_TOKEN}) and the published "
                "asset is rendered unchanged"
            )
    footer_rule = css_rule(stylesheet, ".status-footer__mark")
    if not footer_rule:
        errors.append("styles.css: .status-footer__mark must size the footer mark")
    else:
        if "filter" in footer_rule:
            errors.append(
                "styles.css: the footer mark must not be recoloured with a filter"
            )
        if footer_rule.get("background") != "var(--sbk-text)":
            errors.append(
                "styles.css: the footer mark must sit on the var(--sbk-text) "
                "plate so the brand blue meets the contrast minimum"
            )
        if footer_rule.get("box-sizing") != "content-box":
            errors.append(
                "styles.css: the footer mark plate must use box-sizing: content-box "
                "so the 16px icon keeps its geometry"
            )
        try:
            opacity = float(footer_rule.get("opacity", "1"))
        except ValueError:
            opacity = 1
        if opacity < 1:
            errors.append(
                "styles.css: the footer mark must not be faded; its contrast is "
                "measured at full opacity"
            )

    for name, text in pages.items():
        page = parse_page(text)
        brand_link = next(
            (node for node in page.find_all("a") if "sbk-brand" in classes(node)),
            None,
        )
        if brand_link is None:
            continue
        if not brand_link.attrs.get("aria-label", "").strip():
            errors.append(f"{name}: the header brand link needs an accessible name")
        mark = next(
            brand_link.find_all("img", class_name="status-brand__mark"), None
        )
        if mark is None:
            continue
        if isinstance(reverse_file, str):
            expected = "/" + reverse_file
            if mark.attrs.get("src", "").strip() != expected:
                errors.append(
                    f"{name}: the header mark must reference the reverse lockup "
                    f"{expected!r}, not a colour or recoloured mark"
                )
        if not mark.attrs.get("alt", "").strip():
            errors.append(f"{name}: the header mark must preserve its text alternative")
        width = mark.attrs.get("width", "").strip()
        height = mark.attrs.get("height", "").strip()
        if width.isdigit() and height.isdigit() and reverse_dimensions is not None:
            declared = int(width) / int(height)
            intrinsic = reverse_dimensions[0] / reverse_dimensions[1]
            if abs(declared - intrinsic) > 0.001:
                errors.append(
                    f"{name}: the header mark declares {width}x{height}, which "
                    f"distorts the reverse lockup aspect ratio "
                    f"{reverse_dimensions[0]:g}x{reverse_dimensions[1]:g}"
                )
        footer_mark = next(
            page.find_all("img", class_name="status-footer__mark"), None
        )
        if footer_mark is None:
            errors.append(f"{name}: missing the footer mark")
        else:
            if isinstance(footer_file, str):
                expected = "/" + footer_file
                if footer_mark.attrs.get("src", "").strip() != expected:
                    errors.append(
                        f"{name}: the footer mark must reference the one-colour "
                        f"icon {expected!r}"
                    )
            if footer_mark.attrs.get("alt", "").strip():
                errors.append(
                    f'{name}: the footer mark must use alt="" because the '
                    "attribution text is the accessible source"
                )
    return errors


def validate_manifest_assets(
    root: Path,
    manifest: dict,
    pages: dict[str, str],
    stylesheets: dict[str, str],
) -> list[str]:
    """Every published asset must exist locally and be referenced locally."""
    errors: list[str] = []
    references = list(pages.values()) + list(stylesheets.values())
    for asset in manifest["assets"]:
        if not isinstance(asset, dict) or not asset.get("publish"):
            continue
        filename = asset.get("file")
        if not isinstance(filename, str) or not filename.strip():
            errors.append(
                f"brand.manifest.json: published asset {asset.get('name')!r} has no file"
            )
            continue
        if not (root / filename).is_file():
            errors.append(f"{filename}: published brand asset is missing")
            continue
        data = (root / filename).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != asset.get("sha256"):
            errors.append(
                f"{filename}: SHA-256 {digest} does not match the manifest"
            )
        if len(data) != asset.get("bytes"):
            errors.append(
                f"{filename}: {len(data)} bytes do not match the manifest"
            )
        if not any(filename in text for text in references):
            errors.append(
                f"{filename}: published but not referenced by a page or stylesheet"
            )
    return errors


def stylesheet_links(page: Element) -> list[Element]:
    return [
        node
        for node in page.find_all("link")
        if "stylesheet" in node.attrs.get("rel", "").lower().split()
    ]


def validate_csp(root: Path) -> tuple[str | None, list[str]]:
    try:
        csp = site_build.read_csp(root)
    except (site_build.BuildError, OSError) as exc:
        return None, [str(exc)]
    errors: list[str] = []
    for forbidden in ("'unsafe-inline'", "'unsafe-eval'", "*"):
        if forbidden in csp:
            errors.append(f"templates/csp.txt must not allow {forbidden}")
    required = {
        "default-src 'none'",
        "style-src 'self'",
        "script-src 'self'",
        "font-src 'self'",
        "img-src 'self' data:",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
    }
    directives = {part.strip() for part in csp.split(";") if part.strip()}
    for directive in sorted(required):
        if directive not in directives:
            errors.append(f"templates/csp.txt is missing {directive!r}")
    return csp, errors


def validate_brand_links(
    root: Path, pages: dict[str, str], manifest: dict
) -> list[str]:
    """Exactly one brand sheet per page, and it must be the manifest's sheet."""
    errors: list[str] = []
    entries = [
        asset
        for asset in manifest["assets"]
        if isinstance(asset, dict)
        and asset.get("name") == "brand.css"
        and asset.get("publish")
    ]
    if len(entries) != 1:
        return [
            "brand.manifest.json must publish exactly one brand.css "
            f"(found {len(entries)})"
        ]
    entry = entries[0]
    brand_file = entry.get("file")
    if not isinstance(brand_file, str) or not re.fullmatch(
        r"brand\.[0-9a-f]{12}\.css", brand_file
    ):
        return ["brand.manifest.json brand.css must use brand.<12 hex>.css"]

    path = root / brand_file
    if not path.is_file():
        errors.append(f"{brand_file}: brand stylesheet is missing")
    else:
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            errors.append(f"{brand_file}: SHA-256 does not match the manifest")
        if len(data) != entry.get("bytes"):
            errors.append(f"{brand_file}: byte size does not match the manifest")

    for name, text in pages.items():
        page = parse_page(text)
        links = [
            link
            for link in stylesheet_links(page)
            if re.fullmatch(r"brand\.[0-9a-f]{12}\.css", Path(link.attrs.get("href", "")).name)
        ]
        if len(links) != 1:
            errors.append(
                f"{name}: must link exactly one brand.<hash>.css stylesheet "
                f"(found {len(links)})"
            )
            continue
        href = links[0].attrs.get("href", "").strip()
        local = local_site_path(href)
        if local is None or local != brand_file:
            errors.append(
                f"{name}: brand stylesheet {href!r} must be the manifest entry "
                f"{brand_file!r}"
            )
    return errors


def validate_site_manifest(
    root: Path, pages: dict[str, str], manifest: dict
) -> list[str]:
    errors: list[str] = []
    try:
        site_manifest = site_build.load_site_manifest(root)
    except (site_build.BuildError, OSError, json.JSONDecodeError) as exc:
        return [str(exc)]
    assets = site_manifest["assets"]
    for name, asset in assets.items():
        file = asset.get("file")
        if not isinstance(file, str) or not HASHED_SITE_ASSET.fullmatch(file):
            errors.append(
                f"site.manifest.json: {name!r} must use assets/<name>.<12 hex>.<css|js>"
            )
            continue
        path = root / file
        if not path.is_file():
            errors.append(f"{file}: generated site asset is missing")
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != asset.get("sha256"):
            errors.append(f"{file}: SHA-256 does not match site.manifest.json")
        if len(data) != asset.get("bytes"):
            errors.append(f"{file}: byte size does not match site.manifest.json")
        hash_match = re.fullmatch(
            r"assets/[a-z0-9-]+\.([0-9a-f]{12})\.(?:css|js)", file
        )
        if hash_match is None or hash_match.group(1) != digest[:12]:
            errors.append(
                f"{file}: filename must carry the first 12 hex characters of "
                "the asset digest"
            )
        source = asset.get("source")
        if source is not None:
            source_path = root / source
            if not source_path.is_file():
                errors.append(f"{source}: site asset source is missing")
            elif source_path.read_bytes() != data:
                errors.append(f"{file}: must be an exact copy of {source}")

    expected_links = {
        "index.html": {
            "styles.css": "stylesheet",
            "print.css": "stylesheet",
            "app.js": "script",
            "status-model.js": "script",
        },
        "404.html": {
            "styles.css": "stylesheet",
            "print.css": "stylesheet",
        },
    }
    for name, text in pages.items():
        page = parse_page(text)
        linked = {
            Path(link.attrs.get("href", "")).name: link
            for link in stylesheet_links(page)
        }
        scripts = {
            Path(script.attrs.get("src", "")).name: script
            for script in page.find_all("script")
            if script.attrs.get("src")
        }
        for unhashed in ("styles.css", "app.js", "status-model.js"):
            if re.search(
                rf'(?:href|src)="(?:/)?{re.escape(unhashed)}(?:"|\?)',
                text,
            ):
                errors.append(
                    f"{name}: must not reference the unhashed {unhashed}; "
                    "use the content-hashed site asset"
                )
        for logical, kind in expected_links.get(name, {}).items():
            file = Path(assets[logical]["file"]).name if logical in assets else ""
            if not file:
                continue
            nodes = linked if kind == "stylesheet" else scripts
            if file not in nodes:
                errors.append(f"{name}: must reference the generated {logical}")
            elif kind == "script" and "defer" not in nodes[file].attrs:
                errors.append(f"{name}: {logical} must load with defer")
        for logical in ("app.js", "status-model.js"):
            if logical not in expected_links.get(name, {}):
                file = Path(assets[logical]["file"]).name
                if file in scripts:
                    errors.append(f"{name}: must not load {logical}")
        for logical in ("styles.css", "print.css"):
            if logical not in expected_links.get(name, {}):
                continue
            file = Path(assets[logical]["file"]).name
            media = linked.get(file)
            if media is not None:
                if logical == "print.css" and media.attrs.get("media", "").strip().lower() != "print":
                    errors.append(f"{name}: print.css must use media='print'")
                if logical == "styles.css" and media.attrs.get("media", "").strip().lower() == "print":
                    errors.append(f"{name}: styles.css must not be print-only")

    brand_entry = next(
        (
            asset
            for asset in manifest["assets"]
            if isinstance(asset, dict) and asset.get("name") == "brand.css"
        ),
        None,
    )
    if brand_entry is None:
        errors.append("brand.manifest.json must publish brand.css")
    return errors


def print_block(stylesheet: str) -> str:
    marker = "@media print"
    index = stylesheet.find(marker)
    if index < 0:
        return ""
    opening = stylesheet.find("{", index)
    if opening < 0:
        return ""
    depth = 0
    for position in range(opening, len(stylesheet)):
        character = stylesheet[position]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return stylesheet[opening + 1 : position]
    return ""


def without_print_block(stylesheet: str) -> str:
    """Drop the print block so screen measurements read the screen rules."""
    marker = "@media print"
    index = stylesheet.find(marker)
    if index < 0:
        return stylesheet
    opening = stylesheet.find("{", index)
    if opening < 0:
        return stylesheet
    depth = 0
    for position in range(opening, len(stylesheet)):
        character = stylesheet[position]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return stylesheet[:index] + stylesheet[position + 1 :]
    return stylesheet


def validate_print(
    root: Path,
    pages: dict[str, str],
    manifest: dict,
) -> list[str]:
    errors: list[str] = []
    try:
        site_manifest = site_build.load_site_manifest(root)["assets"]
        brand_file, brand_css = site_build.brand_stylesheet(root, manifest)
        print_css = (root / site_manifest["print.css"]["file"]).read_text(
            encoding="utf-8"
        )
    except (site_build.BuildError, OSError, KeyError) as exc:
        return [str(exc)]
    styles_css = (root / "styles.css").read_text(encoding="utf-8")
    expected_print = site_build.render_print_palette(brand_css, brand_file)
    if print_css != expected_print:
        errors.append(
            "print.css is not the generated light palette for brand.css; "
            "run make brand"
        )
    block = print_block(styles_css)
    if not block:
        return errors + ["styles.css: missing an @media print block"]
    declaration = re.compile(r"(?i)(?:^|[;{])\s*(?:-\w+-)?print-color-adjust\s*:")
    if declaration.search(styles_css) or declaration.search(print_css):
        errors.append(
            "print must not rely on print-color-adjust; its contrast is measured "
            "with backgrounds dropped"
        )
    for selector in (".status-brand .status-brand__mark",):
        rule = css_rule(block, selector)
        if rule.get("display") != "none":
            errors.append(
                f"styles.css: @media print must hide {selector} (the reverse "
                "lockup is invisible on a light sheet)"
            )
    rule = css_rule(block, ".status-brand .status-brand__print-mark")
    if rule.get("display") in (None, "", "none"):
        errors.append(
            "styles.css: @media print must show .status-brand__print-mark"
        )
    rule = css_rule(block, ".status-brand__print-wordmark")
    if rule.get("display") in (None, "", "none"):
        errors.append(
            "styles.css: @media print must show .status-brand__print-wordmark"
        )

    light = brand_properties(brand_css, dark=False)
    dark = brand_properties(brand_css, dark=True)
    print_tokens = {
        name: value.upper() for name, value in PRINT_TOKEN.findall(print_css)
    }
    if print_tokens != light:
        errors.append(
            "print.css must copy exactly the brand.css light colour tokens"
        )
    effective = {**dark, **print_tokens}
    text_tokens = (
        "--sbk-text",
        "--sbk-text-muted",
        "--sbk-text-faint",
        "--sbk-live",
        "--sbk-warning",
        "--sbk-danger",
        "--sbk-maintenance",
        "--sbk-unknown",
        "--sbk-signal",
    )
    for token in text_tokens:
        try:
            foreground = effective[token]
            canvas_contrast = contrast_ratio(foreground, effective["--sbk-canvas"])
            dropped_contrast = contrast_ratio(foreground, "#FFFFFF")
        except KeyError as exc:
            return errors + [f"print palette is missing {exc.args[0]}"]
        if canvas_contrast < 4.5:
            errors.append(
                f"print {token} measures {canvas_contrast:.2f}:1 on the print "
                "canvas, below the 4.5:1 minimum"
            )
        if dropped_contrast < 4.5:
            errors.append(
                f"print {token} measures {dropped_contrast:.2f}:1 when the "
                "browser drops backgrounds, below the 4.5:1 minimum"
            )

    assets = {
        asset.get("name"): asset
        for asset in manifest["assets"]
        if isinstance(asset, dict)
    }
    icon = assets.get(FOOTER_ICON)
    if icon is None:
        errors.append("brand.manifest.json must publish the print mark")
    else:
        fills = svg_fills(root / icon["file"])
        for fill in sorted(fills):
            for surface in (effective.get("--sbk-canvas"), "#FFFFFF"):
                measured = contrast_ratio(fill, surface)
                if measured < 4.5:
                    errors.append(
                        f"{icon['file']}: print mark fill {fill} measures "
                        f"{measured:.2f}:1 on {surface}, below the 4.5:1 minimum"
                    )
        for name, text in pages.items():
            page = parse_page(text)
            mark = next(
                page.find_all("img", class_name="status-brand__print-mark"), None
            )
            if mark is None:
                errors.append(f"{name}: missing the print-safe mark")
                continue
            if mark.attrs.get("src", "").strip() != "/" + icon["file"]:
                errors.append(
                    f"{name}: the print mark must reference {icon['file']!r}"
                )
    return errors


def validate_retention(root: Path) -> list[str]:
    try:
        import prune_assets
    except ImportError as exc:  # pragma: no cover - packaging fault
        return [f"cannot load the retention gate: {exc}"]
    return prune_assets.retention_errors(root)


def validate_site(root: Path) -> list[str]:
    errors: list[str] = []
    status_path = root / "status.json"
    status_bytes = b""
    document: object = None
    if not status_path.is_file():
        errors.append("status.json is missing")
    else:
        status_bytes = status_path.read_bytes()
        try:
            document = json.loads(status_bytes)
        except json.JSONDecodeError as exc:
            errors.append(f"status.json is not valid JSON: {exc}")
        if document is not None:
            errors.extend(validate_document(document))

    manifest, manifest_errors = load_brand_manifest(root)
    errors.extend(manifest_errors)
    brand_colours: dict[str, str] = {}
    if manifest is not None:
        brand_colours, brand_errors = brand_variables(root, manifest)
        errors.extend(brand_errors)
    theme_color = brand_colours.get("--sbk-canvas")
    if manifest is not None and theme_color is None:
        errors.append("brand.css: must declare --sbk-canvas in the dark theme")
    csp, csp_errors = validate_csp(root)
    errors.extend(csp_errors)
    for name in REQUIRED_FILES:
        path = root / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"{name} must exist and be non-empty")

    pages: dict[str, str] = {}
    for name in ("index.html", "404.html"):
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{name}: cannot be read: {exc}")
            continue
        pages[name] = text
        errors.extend(
            validate_page(
                name,
                text,
                require_status=(name == "index.html"),
                theme_color=theme_color,
                csp=csp,
            )
        )

    stylesheets: dict[str, str] = {}
    for name, text in pages.items():
        page_root = parse_page(text)
        for link in stylesheet_links(page_root):
            href = link.attrs.get("href", "").strip()
            local = local_site_path(href)
            if local is None or local in stylesheets:
                continue
            path = root / local
            if not path.is_file():
                errors.append(f"{name}: linked stylesheet {href!r} is missing")
                continue
            try:
                css_text = path.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(f"{local}: cannot be read: {exc}")
                continue
            stylesheets[local] = css_text
            errors.extend(validate_css(local, css_text))

    if manifest is not None:
        try:
            brand_file, brand_css = site_build.brand_stylesheet(root, manifest)
        except (site_build.BuildError, OSError) as exc:
            errors.append(str(exc))
            brand_file, brand_css = "", ""
        site_css_path = root / "styles.css"
        site_css = (
            site_css_path.read_text(encoding="utf-8")
            if site_css_path.is_file()
            else ""
        )
        if theme_color is not None and brand_file:
            errors.extend(
                validate_brand_marks(
                    root,
                    pages,
                    manifest,
                    site_css,
                    brand_css,
                    theme_color,
                    brand_colours.get("--sbk-text"),
                )
            )
        errors.extend(validate_manifest_assets(root, manifest, pages, stylesheets))
        errors.extend(validate_brand_links(root, pages, manifest))
        errors.extend(validate_site_manifest(root, pages, manifest))
        errors.extend(validate_print(root, pages, manifest))

    if isinstance(document, dict) and "index.html" in pages:
        errors.extend(
            validate_snapshot(
                "index.html", pages["index.html"], document, status_bytes
            )
        )
    errors.extend(validate_retention(root))

    sources: dict[str, str] = dict(pages)
    sources.update(stylesheets)
    for name in ("app.js", "status-model.js", "templates/index.html", "templates/404.html"):
        path = root / name
        if path.is_file():
            try:
                sources[name] = path.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(f"{name}: cannot be read: {exc}")
    errors.extend(validate_no_storage(sources))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", type=Path, help="validate one document only")
    parser.add_argument("--root", type=Path, default=Path("."), help="status site root")
    args = parser.parse_args(argv)
    try:
        if args.document is not None:
            errors = validate_document(load_document(args.document))
            label = str(args.document)
        else:
            errors = validate_site(args.root)
            label = "status site"
    except (OSError, json.JSONDecodeError, platform_contract.ContractError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    if errors:
        print(f"{label} validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"{label} valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
