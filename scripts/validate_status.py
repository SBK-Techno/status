#!/usr/bin/env python3
"""Validate the public status document and the static site contract.

The same validator runs on every push and on a dispatched heartbeat, so the
seed, the published document, and the page cannot drift apart.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

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
CNAME = b"status.ia.sbktechno.com\n"
REQUIRED_FILES = ("index.html", "404.html", "styles.css", "app.js")
SITE_HOST = "status.ia.sbktechno.com"
DARK_SELECTOR = ':root[data-theme="dark"]'
BRAND_PROPERTY = re.compile(r"(--sbk-[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6});")
REVERSE_LOCKUP = "logo-reverse.svg"
FOOTER_ICON = "icon-color.svg"
SVG_TAG = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
SVG_DIMENSION = re.compile(r'\b(width|height)="([0-9]+(?:\.[0-9]+)?)"')
SVG_FILL = re.compile(r'fill="(#[0-9A-Fa-f]{6})"', re.IGNORECASE)
CSS_BLOCK = re.compile(r"(?s)([^{}]+)\{([^{}]*)\}")
HARDCODED_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
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


def validate_document(status: object) -> list[str]:
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
        if (parsed.hostname or "").lower() != SITE_HOST:
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


def css_declarations(stylesheet: str, selector: str) -> dict[str, str]:
    """Return the declarations of one selector, merged across duplicate blocks."""
    declarations: dict[str, str] = {}
    for match in CSS_BLOCK.finditer(stylesheet):
        if selector not in match.group(1):
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
        if (parsed.hostname or "").lower() != SITE_HOST:
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


def validate_no_js_snapshot(name: str, text: str) -> list[str]:
    """The static page must not present a status or time it cannot have read."""
    errors: list[str] = []
    page = parse_page(text)
    if next(page.find_all("time"), None) is not None:
        errors.append(
            f"{name}: the static snapshot must not hard-code <time> elements; "
            "times come from status.json only after JavaScript reads it"
        )
    if HARDCODED_TIMESTAMP.search(text):
        errors.append(
            f"{name}: the static snapshot must not hard-code a timestamp"
        )

    status_word = next(page.find_all(element_id="hero-status-text"), None)
    if status_word is not None:
        word = status_word.text().strip().lower()
        if "non disponible" not in word and "non rendu" not in word:
            errors.append(
                f"{name}: the no-JS hero must say the snapshot is not rendered, "
                "not present a status word"
            )

    summary = next(page.find_all(element_id="overall-summary"), None)
    if summary is not None and "javascript" not in summary.text().lower():
        errors.append(
            f"{name}: the no-JS summary must say the published data is not "
            "rendered without JavaScript"
        )

    components = next(page.find_all("ul", element_id="components-list"), None)
    rows = [
        child
        for child in (components.children if components is not None else [])
        if isinstance(child, Element) and child.tag == "li"
    ]
    for index, row in enumerate(rows, 1):
        detail = next(row.find_all("p", class_name="component-detail"), None)
        if detail is not None and "javascript" not in detail.text().lower():
            errors.append(
                f"{name}: component row {index} must say its state is not "
                "rendered without JavaScript"
            )

    incidents = next(page.find_all(element_id="incidents-empty"), None)
    if incidents is not None and "javascript" not in incidents.text().lower():
        errors.append(
            f"{name}: the no-JS incident note must say incidents are not "
            "rendered without JavaScript"
        )
    return errors


def validate_page(
    name: str,
    text: str,
    *,
    require_status: bool = False,
    theme_color: str | None = None,
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

    brand_links = [
        node for node in root.find_all("a") if "sbk-brand" in classes(node)
    ]
    if not brand_links:
        errors.append(f"{name}: missing the .sbk-brand header mark link")
    else:
        mark = next(brand_links[0].find_all("img"), None)
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
            if not rows:
                errors.append(
                    f"{name}: #components-list must contain static <li> rows "
                    "so the page works without JavaScript"
                )
            for index, row in enumerate(rows, 1):
                if "component" not in classes(row):
                    errors.append(f"{name}: component row {index} must use class 'component'")
                heading = next(row.find_all("h3"), None)
                if heading is None or not heading.text().strip():
                    errors.append(f"{name}: component row {index} needs a component name")
                detail = next(row.find_all("p", class_name="component-detail"), None)
                if detail is None or not detail.text().strip():
                    errors.append(f"{name}: component row {index} needs a component detail")
                pill = next(row.find_all("span", class_name="pill"), None)
                if pill is None or not pill.text().strip():
                    errors.append(f"{name}: component row {index} needs a text status label")
                elif pill.attrs.get("data-status", "") not in STATUSES:
                    errors.append(
                        f"{name}: component row {index} has invalid status "
                        f"{pill.attrs.get('data-status')!r}"
                    )
                meta = next(row.find_all("p", class_name="component-meta"), None)
                if meta is None or not meta.text().strip():
                    errors.append(
                        f"{name}: component row {index} needs a snapshot note"
                    )

        component_count = next(root.find_all(element_id="components-count"), None)
        if component_count is None or not component_count.text().strip():
            errors.append(
                f"{name}: #components-count must expose the component count as text"
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
            elif rows and int(total) != len(rows):
                errors.append(
                    f"{name}: #vitals data-total {total!r} must match the "
                    f"{len(rows)} static component rows"
                )

        empty = next(root.find_all(element_id="components-empty"), None)
        if empty is None:
            errors.append(f"{name}: missing #components-empty")
        elif "hidden" not in empty.attrs:
            errors.append(
                f"{name}: #components-empty must be hidden when static rows are rendered"
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
    stylesheets: dict[str, str],
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
    if isinstance(reverse_file, str) and reverse_file.strip():
        fills = svg_fills(root / reverse_file)
        if "#FFFFFF" not in fills:
            errors.append(
                f"{reverse_file}: reverse lockup must keep its white wordmark"
            )
        else:
            measured = contrast_ratio("#FFFFFF", canvas)
            if measured < 4.5:
                errors.append(
                    f"{reverse_file}: white wordmark measures {measured:.2f}:1 on "
                    f"{canvas}, below the 4.5:1 minimum"
                )

    stylesheet = stylesheets.get("styles.css", "")
    header = css_declarations(stylesheet, ".status-brand .status-brand__mark")
    if not header:
        errors.append(
            "styles.css: .status-brand .status-brand__mark must size the header mark"
        )
    else:
        if header.get("width") != "auto":
            errors.append(
                "styles.css: the header mark must keep width: auto so the "
                "reverse lockup retains its intrinsic aspect ratio"
            )
        if header.get("height") != "var(--status-mark-height)":
            errors.append(
                "styles.css: the header mark height must come from "
                "var(--status-mark-height)"
            )
        if "filter" in header:
            errors.append(
                "styles.css: the header mark must not be recoloured with a filter; "
                "render the published reverse lockup unchanged"
            )
    footer_rule = css_declarations(stylesheet, ".status-footer__mark")
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
    mark_height = css_declarations(stylesheet, ":root").get(
        "--status-mark-height", ""
    )
    height_match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)rem", mark_height)
    if height_match is None or not 2 <= float(height_match.group(1)) <= 3:
        errors.append(
            "styles.css: --status-mark-height must be between 2rem and 3rem"
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
        mark = next(brand_link.find_all("img"), None)
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
        if not any(filename in text for text in references):
            errors.append(
                f"{filename}: published but not referenced by a page or stylesheet"
            )
    return errors


def validate_site(root: Path) -> list[str]:
    errors = validate_document(load_document(root / "status.json"))
    manifest, manifest_errors = load_brand_manifest(root)
    errors.extend(manifest_errors)
    brand_colours: dict[str, str] = {}
    if manifest is not None:
        brand_colours, brand_errors = brand_variables(root, manifest)
        errors.extend(brand_errors)
    theme_color = brand_colours.get("--sbk-canvas")
    if manifest is not None and theme_color is None:
        errors.append("brand.css: must declare --sbk-canvas in the dark theme")
    cname = root / "CNAME"
    if not cname.is_file():
        errors.append("CNAME is missing")
    elif cname.read_bytes() != CNAME:
        errors.append("CNAME must contain exactly 'status.ia.sbktechno.com' with a final newline")
    if not (root / ".nojekyll").is_file():
        errors.append(".nojekyll is missing")
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
            )
        )

    stylesheets: dict[str, str] = {}
    for name, text in pages.items():
        page_root = parse_page(text)
        for link in page_root.find_all("link"):
            rel = link.attrs.get("rel", "").lower().split()
            if "stylesheet" not in rel:
                continue
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

    if manifest is not None and theme_color is not None:
        errors.extend(
            validate_brand_marks(
                root,
                pages,
                manifest,
                stylesheets,
                theme_color,
                brand_colours.get("--sbk-text"),
            )
        )
        errors.extend(validate_manifest_assets(root, manifest, pages, stylesheets))

    if "index.html" in pages:
        errors.extend(validate_no_js_snapshot("index.html", pages["index.html"]))
    sources: dict[str, str] = dict(pages)
    sources.update(stylesheets)
    for name in ("app.js", "templates/index.html", "templates/404.html"):
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
    except (OSError, json.JSONDecodeError) as exc:
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
