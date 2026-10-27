#!/usr/bin/env python3
"""Retain retired generated assets for a bounded window, then prune them.

A content-hashed asset may still be named by an HTML document a browser cached
before the last deploy. GitHub Pages serves every file with a ten-minute
cache, so a retired asset only has to survive that window. The repository
keeps a seven-day safety window instead of deleting in the same commit.

This script owns the ledger. ``--write`` records newly unreferenced files and
deletes files whose retirement is older than the window. ``--check`` fails when
an unmanifested generated file has no record, or when its window has expired
and nobody pruned it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import site_build

RETENTION_DAYS = 7
LEDGER = "asset-retention.json"
GENERATED_ASSET = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[0-9a-f]{12}\.[a-z0-9]+$")
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RetentionError(Exception):
    pass


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def current_assets(root: Path) -> set[str]:
    names: set[str] = set()
    brand = site_build.load_brand_manifest(root)
    for asset in site_build.published_brand_assets(brand).values():
        file = asset.get("file")
        if isinstance(file, str):
            names.add(file)
    try:
        site = site_build.load_site_manifest(root)
    except site_build.BuildError:
        site = {"assets": {}}
    for asset in site["assets"].values():
        file = asset.get("file")
        if isinstance(file, str):
            names.add(file)
    return names


def generated_files(root: Path) -> set[str]:
    found: set[str] = set()
    for path in root.iterdir():
        if path.is_file() and GENERATED_ASSET.fullmatch(path.name):
            found.add(path.name)
    assets = root / "assets"
    if assets.is_dir():
        for path in assets.iterdir():
            if path.is_file() and GENERATED_ASSET.fullmatch(path.name):
                found.add(path.relative_to(root).as_posix())
    return found


def load_ledger(root: Path) -> dict:
    path = root / LEDGER
    if not path.is_file():
        return {
            "version": 1,
            "retentionDays": RETENTION_DAYS,
            "retired": [],
        }
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetentionError(f"{LEDGER} cannot be read: {exc}")
    if not isinstance(document, dict) or document.get("version") != 1:
        raise RetentionError(f"{LEDGER} must declare version 1")
    if document.get("retentionDays") != RETENTION_DAYS:
        raise RetentionError(
            f"{LEDGER}: retentionDays must be {RETENTION_DAYS}; "
            "change the owner and the ledger together"
        )
    retired = document.get("retired")
    if not isinstance(retired, list):
        raise RetentionError(f"{LEDGER}: retired must be a list")
    for entry in retired:
        if not isinstance(entry, dict):
            raise RetentionError(f"{LEDGER}: each retired entry must be an object")
        if not isinstance(entry.get("file"), str) or not entry["file"]:
            raise RetentionError(f"{LEDGER}: retired entry has no file")
        if parse_timestamp(entry.get("retired_at")) is None:
            raise RetentionError(
                f"{LEDGER}: {entry['file']}: retired_at must be UTC ISO 8601"
            )
    return document


def retention_errors(root: Path, now: datetime | None = None) -> list[str]:
    instant = now or datetime.now(timezone.utc)
    try:
        ledger = load_ledger(root)
    except RetentionError as exc:
        return [str(exc)]
    current = current_assets(root)
    present = generated_files(root)
    recorded = {entry["file"]: entry for entry in ledger["retired"]}
    errors: list[str] = []
    window = timedelta(days=RETENTION_DAYS)

    for file in sorted(present - current):
        entry = recorded.get(file)
        if entry is None:
            errors.append(
                f"{file}: unmanifested generated asset has no retention record; "
                "run make brand"
            )
            continue
        retired = parse_timestamp(entry["retired_at"])
        if retired is None:
            continue
        if instant - retired > window:
            errors.append(
                f"{file}: retention window of {RETENTION_DAYS} days has expired; "
                "run make brand to prune it"
            )
    for file in sorted(set(recorded) - present):
        errors.append(f"{LEDGER}: records {file}, which no longer exists")
    return errors


def prune(root: Path, now: datetime | None = None) -> list[str]:
    instant = now or datetime.now(timezone.utc)
    try:
        ledger = load_ledger(root)
    except RetentionError as exc:
        return [str(exc)]
    current = current_assets(root)
    present = generated_files(root)
    window = timedelta(days=RETENTION_DAYS)
    retired = {
        entry["file"]: entry
        for entry in ledger["retired"]
        if entry["file"] in present
    }
    for file in sorted(present - current):
        if file not in retired:
            retired[file] = {
                "file": file,
                "retired_at": instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
    keep: list[dict] = []
    for file, entry in sorted(retired.items()):
        retired_at = parse_timestamp(entry["retired_at"])
        if retired_at is not None and instant - retired_at > window:
            path = root / file
            if path.is_file():
                path.unlink()
            continue
        keep.append(entry)
    document = {
        "version": 1,
        "retentionDays": RETENTION_DAYS,
        "retired": keep,
    }
    data = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    path = root / LEDGER
    if not path.is_file() or path.read_text(encoding="utf-8") != data:
        path.write_text(data, encoding="utf-8")
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not args.write and not args.check:
        parser.error("one of --write or --check is required")
    root = args.root.resolve()
    errors = prune(root) if args.write else retention_errors(root)
    if errors:
        print("asset retention check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("asset retention valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
