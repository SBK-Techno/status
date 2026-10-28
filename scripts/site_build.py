#!/usr/bin/env python3
"""Build the public status site from the templates and the exact document.

One module owns the composition so the publisher, the local build, the
validator, and the CI reproduction check cannot disagree:

* brand placeholders resolve through ``brand.manifest.json``;
* site assets are copied to content-hashed names recorded in
  ``site.manifest.json``;
* the shared CSP comes from ``templates/csp.txt``;
* the static snapshot is rendered from the exact ``status.json`` bytes and
  records their digest and the instant the freshness rules were applied.

The build is deterministic when the document is unchanged: the previous
snapshot instant is reused while the digest matches.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from status_snapshot import (
    document_digest,
    format_stamp,
    iso,
    parse_timestamp,
    render_snapshot_region,
)

SNAPSHOT_START = "<!-- snapshot:start -->"
SNAPSHOT_END = "<!-- snapshot:end -->"
SITE_MANIFEST = "site.manifest.json"
CSP_FILE = "templates/csp.txt"
DARK_SELECTOR = ':root[data-theme="dark"]'
BRAND_PLACEHOLDER = re.compile(r"\{\{asset:([a-z0-9][a-z0-9.-]*)\}\}")
SITE_PLACEHOLDER = re.compile(r"\{\{site:([a-z0-9][a-z0-9.-]*)\}\}")
ANY_PLACEHOLDER = re.compile(r"\{\{[^}]+\}\}")
BRAND_LINK = re.compile(r"^brand\.[0-9a-f]{12}\.css$")
HEX_TOKEN = re.compile(r"(--sbk-[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6});")
SNAPSHOT_ATTR = re.compile(r'data-snapshot-at="[^"]*"')
SNAPSHOT_AT_VALUE = re.compile(r'data-snapshot-at="([^"]+)"')
DIGEST_ATTR = re.compile(r'data-status-digest="[^"]*"')
SNAPSHOT_FOOTER = re.compile(
    r'(<span id="snapshot-time">).*?(</span>)', re.DOTALL
)
CSP_META = re.compile(
    r'(<meta\s+http-equiv="Content-Security-Policy"\s+content=")[^"]*(")',
    re.IGNORECASE,
)

SITE_ASSETS = {
    "styles.css": ("styles.css", "css", "text/css; charset=utf-8"),
    "app.js": ("app.js", "js", "text/javascript; charset=utf-8"),
    "status-model.js": (
        "status-model.js",
        "js",
        "text/javascript; charset=utf-8",
    ),
    "print.css": (None, "css", "text/css; charset=utf-8"),
}


class BuildError(Exception):
    pass


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_brand_manifest(root: Path) -> dict:
    manifest = load_json(root / "brand.manifest.json")
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise BuildError("brand.manifest.json must declare version 1")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise BuildError("brand.manifest.json assets must be a list")
    return manifest


def published_brand_assets(manifest: dict) -> dict[str, dict]:
    assets: dict[str, dict] = {}
    for asset in manifest.get("assets", []):
        if isinstance(asset, dict) and asset.get("publish"):
            name = asset.get("name")
            if isinstance(name, str):
                assets[name] = asset
    return assets


def brand_stylesheet(root: Path, manifest: dict) -> tuple[str, str]:
    entry = published_brand_assets(manifest).get("brand.css")
    if entry is None:
        raise BuildError("brand.manifest.json must publish brand.css")
    filename = entry.get("file")
    if not isinstance(filename, str) or not BRAND_LINK.fullmatch(filename):
        raise BuildError("brand.css entry must use brand.<12 hex>.css")
    return filename, (root / filename).read_text(encoding="utf-8")


def read_csp(root: Path) -> str:
    path = root / CSP_FILE
    if not path.is_file():
        raise BuildError(f"{CSP_FILE} is missing")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise BuildError(f"{CSP_FILE} is empty")
    if "\n" in value:
        raise BuildError(f"{CSP_FILE} must contain exactly one policy line")
    if "frame-ancestors" in value:
        raise BuildError(
            f"{CSP_FILE} must not carry frame-ancestors in a meta policy"
        )
    return value


def render_print_palette(brand_css: str, source_name: str) -> str:
    """Copy the brand's light palette into a print-only token block.

    The page declares the dark theme, so the brand stylesheet resolves the
    dark block. Print must use the light block, and the brand stylesheet is the
    only owner of those values; this function copies them instead of keeping a
    second palette. The gate re-renders this file from ``brand.css`` and fails
    when the committed copy drifts.
    """
    index = brand_css.find(DARK_SELECTOR)
    light = brand_css[:index] if index >= 0 else brand_css
    tokens = HEX_TOKEN.findall(light)
    if not tokens:
        raise BuildError(f"{source_name}: no light colour tokens found")
    values = dict(tokens)
    for required in ("--sbk-canvas", "--sbk-text"):
        if required not in values:
            raise BuildError(f"{source_name}: light block is missing {required}")
    lines = [
        "/*",
        " * Generated by scripts/build_site.py from " + source_name + ".",
        " * Do not edit. The brand stylesheet owns this palette.",
        " *",
        " * Print resolves the brand light block; the page declares the dark",
        " * theme, so these values are rebound for print only. Text and mark",
        " * contrast is measured with backgrounds present and dropped.",
        " */",
        "@media print {",
        '  :root[data-theme="dark"] {',
        "    color-scheme: light;",
    ]
    for name, value in tokens:
        lines.append(f"    {name}: {value};")
    lines.extend(["  }", "}", ""])
    return "\n".join(lines)


def digest12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def site_asset_outputs(root: Path, brand_manifest: dict) -> dict[str, dict]:
    """Return the four generated site assets keyed by logical name."""
    brand_file, brand_css = brand_stylesheet(root, brand_manifest)
    outputs: dict[str, dict] = {}
    for name, (source_name, extension, media_type) in SITE_ASSETS.items():
        if source_name is None:
            data = render_print_palette(brand_css, brand_file).encode("utf-8")
        else:
            path = root / source_name
            if not path.is_file():
                raise BuildError(f"{source_name} is missing")
            data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        stem = name.rsplit(".", 1)[0]
        file = f"assets/{stem}.{digest[:12]}.{extension}"
        outputs[name] = {
            "name": name,
            "source": source_name,
            "file": file,
            "mediaType": media_type,
            "sha256": digest,
            "bytes": len(data),
            "data": data,
        }
    return outputs


def site_manifest_document(assets: dict[str, dict]) -> str:
    document = {
        "version": 1,
        "generatedBy": "scripts/build_site.py",
        "assets": [
            {
                "name": asset["name"],
                "source": asset["source"],
                "file": asset["file"],
                "mediaType": asset["mediaType"],
                "sha256": asset["sha256"],
                "bytes": asset["bytes"],
            }
            for asset in assets.values()
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def resolve_brand_placeholders(text: str, manifest: dict) -> str:
    assets = published_brand_assets(manifest)

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        asset = assets.get(name)
        if asset is None:
            raise BuildError(f"template references unpublished brand asset {name!r}")
        file = asset.get("file")
        if not isinstance(file, str) or not file:
            raise BuildError(f"brand asset {name!r} has no file")
        return "/" + file

    return BRAND_PLACEHOLDER.sub(replace, text)


def resolve_site_placeholders(text: str, assets: dict[str, dict]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        asset = assets.get(name)
        if asset is None:
            raise BuildError(f"template references unknown site asset {name!r}")
        return "/" + asset["file"]

    return SITE_PLACEHOLDER.sub(replace, text)


def extract_snapshot_region(page: str) -> str:
    start = page.find(SNAPSHOT_START)
    end = page.find(SNAPSHOT_END)
    if start < 0 or end < 0 or end < start:
        raise BuildError("page is missing the snapshot markers")
    if page.find(SNAPSHOT_START, start + 1) >= 0 or page.find(SNAPSHOT_END, end + 1) >= 0:
        raise BuildError("page has duplicate snapshot markers")
    return page[start + len(SNAPSHOT_START) : end]


def replace_snapshot_region(page: str, region: str) -> str:
    """Replace the region verbatim so normalization can restore the template."""
    extract_snapshot_region(page)
    start = page.find(SNAPSHOT_START)
    end = page.find(SNAPSHOT_END)
    return page[: start + len(SNAPSHOT_START)] + region + page[end:]


def _select_snapshot_at(
    root: Path, status_bytes: bytes, requested: str | None
) -> str:
    if requested is not None:
        if parse_timestamp(requested) is None:
            raise BuildError("--snapshot-at must be a UTC timestamp ending in Z")
        return requested
    page_path = root / "index.html"
    if page_path.is_file():
        page = page_path.read_text(encoding="utf-8")
        digest_match = re.search(r'data-status-digest="([0-9a-f]{64})"', page)
        snapshot_at = snapshot_at_from_page(page)
        if (
            digest_match is not None
            and digest_match.group(1) == document_digest(status_bytes)
            and snapshot_at is not None
        ):
            return snapshot_at
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_pages(
    root: Path,
    status_bytes: bytes,
    status_document: dict,
    site_assets: dict[str, dict],
    snapshot_at: str,
) -> dict[str, str]:
    manifest = load_brand_manifest(root)
    csp = read_csp(root)
    digest = document_digest(status_bytes)

    def common(text: str) -> str:
        text = resolve_brand_placeholders(text, manifest)
        text = resolve_site_placeholders(text, site_assets)
        text = text.replace("{{csp}}", csp)
        text = text.replace("{{snapshot:at}}", snapshot_at)
        text = text.replace("{{snapshot:digest}}", digest)
        return text

    index_template = (root / "templates/index.html").read_text(encoding="utf-8")
    error_template = (root / "templates/404.html").read_text(encoding="utf-8")
    index = common(index_template)
    error = common(error_template)
    snapshot = render_snapshot_region(
        status_document, parse_timestamp(snapshot_at)
    )
    index = replace_snapshot_region(
        index, "\n" + snapshot.strip("\n") + "\n    "
    )
    generated = parse_timestamp(status_document.get("generated_at"))
    footer = (
        f'<time datetime="{iso(generated)}">{format_stamp(generated)}</time>'
        if generated is not None
        else "Aucun"
    )
    index = index.replace("{{snapshot:footer}}", footer)

    for name, text in (("index.html", index), ("404.html", error)):
        unresolved = ANY_PLACEHOLDER.search(text)
        if unresolved is not None:
            raise BuildError(
                f"{name}: unresolved placeholder {unresolved.group(0)!r}"
            )
    return {"index.html": index, "404.html": error}


def expected_site_outputs(
    root: Path, snapshot_at: str | None = None
) -> dict[str, bytes]:
    status_path = root / "status.json"
    if not status_path.is_file():
        raise BuildError("status.json is missing")
    status_bytes = status_path.read_bytes()
    status_document = json.loads(status_bytes)
    if not isinstance(status_document, dict):
        raise BuildError("status.json must be a JSON object")
    manifest = load_brand_manifest(root)
    assets = site_asset_outputs(root, manifest)
    at = _select_snapshot_at(root, status_bytes, snapshot_at)
    pages = render_pages(root, status_bytes, status_document, assets, at)
    outputs: dict[str, bytes] = {
        "index.html": pages["index.html"].encode("utf-8"),
        "404.html": pages["404.html"].encode("utf-8"),
        SITE_MANIFEST: site_manifest_document(assets).encode("utf-8"),
    }
    for asset in assets.values():
        outputs[asset["file"]] = asset["data"]
    return outputs


def snapshot_at_from_page(page: str) -> str | None:
    """Return the snapshot instant recorded in one rendered page."""
    match = SNAPSHOT_AT_VALUE.search(page)
    if match is None or parse_timestamp(match.group(1)) is None:
        return None
    return match.group(1)


def current_snapshot_at(root: Path) -> str | None:
    page_path = root / "index.html"
    if not page_path.is_file():
        return None
    return snapshot_at_from_page(page_path.read_text(encoding="utf-8"))


def check_site(root: Path) -> list[str]:
    status_path = root / "status.json"
    if not status_path.is_file():
        return ["status.json is missing"]
    status_bytes = status_path.read_bytes()
    digest = document_digest(status_bytes)
    page_path = root / "index.html"
    if not page_path.is_file():
        return ["index.html is missing"]
    page = page_path.read_text(encoding="utf-8")
    digest_match = re.search(r'data-status-digest="([0-9a-f]{64})"', page)
    if digest_match is None:
        return ["index.html: missing data-status-digest"]
    if digest_match.group(1) != digest:
        return [
            "index.html: snapshot is bound to a different status.json; "
            "run make brand"
        ]
    snapshot_at = current_snapshot_at(root)
    if snapshot_at is None:
        return ["index.html: missing or invalid data-snapshot-at"]
    try:
        expected = expected_site_outputs(root, snapshot_at=snapshot_at)
    except (BuildError, OSError, json.JSONDecodeError) as exc:
        return [str(exc)]
    errors: list[str] = []
    for name, data in sorted(expected.items()):
        path = root / name
        if not path.is_file():
            errors.append(f"{name}: generated output is missing")
        elif path.read_bytes() != data:
            errors.append(f"{name}: generated output is stale; run make brand")
    return errors


def write_site(root: Path, snapshot_at: str | None = None) -> list[str]:
    try:
        expected = expected_site_outputs(root, snapshot_at=snapshot_at)
    except (BuildError, OSError, json.JSONDecodeError) as exc:
        return [str(exc)]
    for name, data in sorted(expected.items()):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.read_bytes() == data:
            continue
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
    return []


def normalize_page(
    page: str, template: str, site_assets: dict[str, dict], csp: str
) -> str:
    """Reverse the site transforms so genstatus can compare the brand stage."""
    normalized = page
    if SNAPSHOT_START in page and SNAPSHOT_START in template:
        template_region = extract_snapshot_region(template)
        normalized = replace_snapshot_region(normalized, template_region)
    normalized = SNAPSHOT_ATTR.sub(
        'data-snapshot-at="{{snapshot:at}}"', normalized, count=1
    )
    normalized = DIGEST_ATTR.sub(
        'data-status-digest="{{snapshot:digest}}"', normalized, count=1
    )
    normalized = SNAPSHOT_FOOTER.sub(
        r'\1{{snapshot:footer}}\2', normalized, count=1
    )
    normalized = CSP_META.sub(rf'\1{{{{csp}}}}\2', normalized, count=1)
    for asset in site_assets.values():
        normalized = normalized.replace("/" + asset["file"], "{{site:" + asset["name"] + "}}")
    return normalized


def load_site_manifest(root: Path) -> dict:
    path = root / SITE_MANIFEST
    if not path.is_file():
        raise BuildError(f"{SITE_MANIFEST} is missing")
    document = load_json(path)
    if not isinstance(document, dict) or document.get("version") != 1:
        raise BuildError(f"{SITE_MANIFEST} must declare version 1")
    if document.get("generatedBy") != "scripts/build_site.py":
        raise BuildError(
            f"{SITE_MANIFEST} must declare generatedBy scripts/build_site.py"
        )
    assets = document.get("assets")
    if not isinstance(assets, list) or not assets:
        raise BuildError(f"{SITE_MANIFEST} must declare assets")
    result: dict[str, dict] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise BuildError(f"{SITE_MANIFEST}: each asset must be an object")
        name = asset.get("name")
        file = asset.get("file")
        if not isinstance(name, str) or name not in SITE_ASSETS:
            raise BuildError(f"{SITE_MANIFEST}: unknown asset name {name!r}")
        if not isinstance(file, str) or not file.startswith("assets/"):
            raise BuildError(f"{SITE_MANIFEST}: {name!r} must live under assets/")
        result[name] = asset
    missing = set(SITE_ASSETS) - set(result)
    if missing:
        raise BuildError(
            f"{SITE_MANIFEST}: missing assets {', '.join(sorted(missing))}"
        )
    return {"document": document, "assets": result}


class _BodyParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attributes: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "body" and self.attributes is None:
            self.attributes = {name.lower(): value or "" for name, value in attrs}


def body_attributes(page: str) -> dict[str, str]:
    parser = _BodyParser()
    parser.feed(page)
    parser.close()
    return parser.attributes or {}
