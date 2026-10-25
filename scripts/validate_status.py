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


def validate_page(name: str, text: str, *, require_status: bool = False) -> list[str]:
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

    mains = list(root.find_all("main"))
    if len(mains) != 1:
        errors.append(f"{name}: must contain exactly one <main> landmark (found {len(mains)})")
    if not any(is_landmark(node) for node in root.find_all("header")):
        errors.append(f"{name}: missing top-level <header> banner landmark")
    if not any(is_landmark(node) for node in root.find_all("footer")):
        errors.append(f"{name}: missing top-level <footer> contentinfo landmark")

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
        for element_id in ("hero-status-text", "overall-summary"):
            node = next(root.find_all(element_id=element_id), None)
            if node is None or not node.text().strip():
                errors.append(f"{name}: #{element_id} must contain static status text")

        components = next(root.find_all("ul", element_id="components-list"), None)
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
                if next(row.find_all("time"), None) is None:
                    errors.append(f"{name}: component row {index} needs a <time> timestamp")

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


def validate_site(root: Path) -> list[str]:
    errors = validate_document(load_document(root / "status.json"))
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
        errors.extend(validate_page(name, text, require_status=(name == "index.html")))

    seen_stylesheets: set[str] = set()
    for name, text in pages.items():
        page_root = parse_page(text)
        for link in page_root.find_all("link"):
            rel = link.attrs.get("rel", "").lower().split()
            if "stylesheet" not in rel:
                continue
            href = link.attrs.get("href", "").strip()
            local = local_site_path(href)
            if local is None or local in seen_stylesheets:
                continue
            seen_stylesheets.add(local)
            path = root / local
            if not path.is_file():
                errors.append(f"{name}: linked stylesheet {href!r} is missing")
                continue
            try:
                css_text = path.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(f"{local}: cannot be read: {exc}")
                continue
            errors.extend(validate_css(local, css_text))

    app = root / "app.js"
    if app.is_file() and "document.cookie" in app.read_text(encoding="utf-8"):
        errors.append("app.js must not read or write cookies")
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
