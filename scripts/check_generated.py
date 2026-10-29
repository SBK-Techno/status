#!/usr/bin/env python3
"""Run the platform brand generator's ``-check`` against the committed pages.

The site build adds one layer after ``genstatus``: content-hashed app assets,
the shared CSP, and the publish-time snapshot. This check reverses only that
layer in a temporary checkout, then asks ``genstatus -check`` to compare the
remaining brand-resolved markup, ``brand.manifest.json``, and every published
brand asset. The final page is separately reproduced by
``scripts/build_site.py --check``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import platform_contract
import site_build


def run_genstatus(
    platform: Path, brand: Path, root: Path
) -> subprocess.CompletedProcess[str]:
    # Run the source, not a stale build artifact that may sit in a developer
    # checkout. CI checks out the platform source and uses the same command.
    command = ["go", "run", "./cmd/genstatus"]
    return subprocess.run(
        command + ["-root", str(root), "-brand", str(brand), "-check"],
        cwd=platform,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--platform", type=Path, required=True)
    parser.add_argument("--brand", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    platform = args.platform.resolve()
    brand = args.brand.resolve()
    if not platform.is_dir():
        print(f"platform checkout is missing: {platform}", file=sys.stderr)
        return 1
    if not brand.is_dir():
        print(f"brand checkout is missing: {brand}", file=sys.stderr)
        return 1
    try:
        brand_manifest = site_build.load_brand_manifest(root)
        site_manifest = site_build.load_site_manifest(root)
        csp = site_build.read_csp(root)
        host = platform_contract.status_host(platform)
    except (site_build.BuildError, OSError, platform_contract.ContractError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="status-genstatus-") as temporary:
        staging = Path(temporary)
        (staging / "templates").mkdir()
        for name in ("index.html", "404.html"):
            shutil.copyfile(root / "templates" / name, staging / "templates" / name)
        shutil.copyfile(root / "brand.manifest.json", staging / "brand.manifest.json")
        for asset in site_build.published_brand_assets(brand_manifest).values():
            file = asset.get("file")
            if not isinstance(file, str):
                continue
            source = root / file
            if not source.is_file():
                print(f"{file}: published brand asset is missing", file=sys.stderr)
                return 1
            shutil.copyfile(source, staging / file)
        for name in ("index.html", "404.html"):
            page = (root / name).read_text(encoding="utf-8")
            template = (root / "templates" / name).read_text(encoding="utf-8")
            normalized = site_build.normalize_page(
                page, template, site_manifest["assets"], csp, host
            )
            (staging / name).write_text(normalized, encoding="utf-8")
        result = run_genstatus(platform, brand, staging)
    if result.returncode != 0:
        print("genstatus -check failed:", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return 1
    print(result.stdout.strip() or "genstatus check valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
