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
from pathlib import Path

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
REQUIRED_FILES = ("index.html", "styles.css", "app.js")


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
