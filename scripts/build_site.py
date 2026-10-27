#!/usr/bin/env python3
"""Build or check the public status site from templates and status.json."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import site_build


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--snapshot-at",
        help="UTC instant used for the snapshot freshness rules (YYYY-MM-DDTHH:MM:SSZ)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write the generated site")
    mode.add_argument(
        "--check",
        action="store_true",
        help="fail when the committed site differs from a fresh build",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not args.write and not args.check:
        parser.error("one of --write or --check is required")
    if args.write:
        errors = site_build.write_site(root, snapshot_at=args.snapshot_at)
    else:
        errors = site_build.check_site(root)
    if errors:
        print("site build check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("site build valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
