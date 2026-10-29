#!/usr/bin/env python3
"""Browser check for layout stability on the built status site.

The check serves the built site from a loopback origin, holds ``status.json``
until the first paint is measured, then releases it. It proves three things at
1440x900 and 390x844:

* the server-rendered snapshot already occupies the vitals space, so the hero
  and board do not move when the first read resolves;
* cumulative layout shift through that read stays below 0.05;
* every hashed asset the page requests carries the platform contract's
  content-hash shape.

Response headers and cache policy belong to the platform edge. This local
origin is not the edge and asserts no header policy.

Run ``make cls-check``. Playwright is a local browser dependency, not part of
the CI gate: install ``playwright`` and its Chromium build first.
"""

from __future__ import annotations

import argparse
import mimetypes
import posixpath
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import platform_contract

VIEWPORTS = ((1440, 900), (390, 844))
CLS_LIMIT = 0.05
POSITION_TOLERANCE = 0.5
STATUS_PATH = "/status.json"

CLS_SCRIPT = """
window.__cls = 0;
window.__shifts = [];
new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) {
    if (entry.hadRecentInput) continue;
    window.__cls += entry.value;
    window.__shifts.push({
      value: entry.value,
      startTime: entry.startTime,
      sources: entry.sources.map((source) =>
        source.node
          ? source.node.id || source.node.className || source.node.tagName
          : ""
      ),
    });
  }
}).observe({ type: "layout-shift", buffered: true });
"""

POSITION_SCRIPT = """
() => {
  const box = (node) => {
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    return {
      top: Math.round((rect.top + window.scrollY) * 100) / 100,
      height: Math.round(rect.height * 100) / 100,
    };
  };
  const vitals = document.querySelector("#vitals");
  const banner = document.querySelector("#stale-banner");
  return {
    hero: box(document.querySelector("#hero")),
    heroTitle: box(document.querySelector("#hero-title")),
    board: box(document.querySelector(".board")),
    boardTitle: box(document.querySelector("#components-title")),
    vitals: box(vitals),
    vitalsHidden: vitals ? vitals.hidden : true,
    banner: box(banner),
    bannerHidden: banner ? banner.hidden : true,
    documentHeight: document.documentElement.scrollHeight,
  };
}
"""


class SiteState:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.status_gate = threading.Event()
        self.lock = threading.Lock()
        self.status_requests = 0
        self.requests: list[str] = []

    def reset(self) -> None:
        self.status_gate.clear()
        with self.lock:
            self.status_requests = 0
            self.requests = []

    def release(self) -> None:
        self.status_gate.set()

    def record(self, path: str) -> None:
        with self.lock:
            self.requests.append(path)
            if path == STATUS_PATH:
                self.status_requests += 1


class SiteHandler(BaseHTTPRequestHandler):
    server_version = "sbk-status-check/1"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> SiteState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        self._serve(body=True)

    def do_HEAD(self) -> None:
        self._serve(body=False)

    def _resolve(self) -> str | None:
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        path = posixpath.normpath(path)
        if not path.startswith("/") or ".." in path.split("/"):
            return None
        if path == "/":
            return "/index.html"
        return path

    def _serve(self, body: bool) -> None:
        path = self._resolve()
        if path is None:
            self.send_error(400)
            return
        self.state.record(path)
        if path == STATUS_PATH and body:
            if not self.state.status_gate.wait(timeout=30):
                self.send_error(504)
                return
        file = self.state.root / path.lstrip("/")
        if path.endswith("/") or not file.is_file():
            self.send_error(404)
            return
        try:
            data = file.read_bytes()
        except OSError:
            self.send_error(500)
            return
        self.send_response(200)
        media_type, _ = mimetypes.guess_type(file.name)
        if file.suffix == ".js":
            media_type = "text/javascript"
        elif file.suffix == ".json":
            media_type = "application/json"
        elif file.suffix == ".woff2":
            media_type = "font/woff2"
        self.send_header("Content-Type", media_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if body:
            self.wfile.write(data)


class SiteServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, root: Path) -> None:
        super().__init__(("127.0.0.1", 0), SiteHandler)
        self.state = SiteState(root)

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


def positions(page) -> dict:
    return page.evaluate(POSITION_SCRIPT)


def measure_viewport(
    browser, base_url: str, width: int, height: int, hold_ms: int, release
) -> tuple[dict, dict, list[str], list[dict]]:
    context = browser.new_context(
        viewport={"width": width, "height": height},
        reduced_motion="reduce",
        service_workers="block",
    )
    page = context.new_page()
    try:
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.clearBrowserCache")
        page.add_init_script(CLS_SCRIPT)
        page.goto(base_url + "/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector("#hero", timeout=5000)
        page.wait_for_selector(".board", timeout=5000)
        page.wait_for_selector("#vitals", state="attached", timeout=5000)
        page.wait_for_timeout(100)
        before = positions(page)
        time.sleep(hold_ms / 1000)
        # The server holds the response until this point, so `before` is the
        # painted server snapshot, never a race with the first read.
        release()
        page.wait_for_function(
            "() => { const hero = document.querySelector('#hero'); "
            "return Boolean(hero && hero.dataset.mode); }",
            timeout=10000,
        )
        page.wait_for_timeout(250)
        after = positions(page)
        cls = page.evaluate("window.__cls")
        shifts = page.evaluate("window.__shifts")
        resources = page.evaluate(
            "() => [...performance.getEntriesByType('resource')]"
            ".map((entry) => new URL(entry.name).pathname)"
        )
        return before, after, resources, {"cls": cls, "shifts": shifts}
    finally:
        context.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--hold-ms",
        type=int,
        default=150,
        help="time between the painted snapshot and releasing status.json",
    )
    args = parser.parse_args(argv)
    if args.hold_ms < 0 or args.hold_ms >= 3000:
        parser.error("--hold-ms must be between 0 and 2999")
    root = args.root.resolve()
    try:
        contract = platform_contract.artifact_contract()
    except platform_contract.ContractError as exc:
        print(f"cls-check: {exc}", file=sys.stderr)
        return 1
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "cls-check: Playwright is not installed. Run\n"
            "  python3 -m pip install --user playwright\n"
            "  python3 -m playwright install chromium",
            file=sys.stderr,
        )
        return 1

    server = SiteServer(root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    failures: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for width, height in VIEWPORTS:
                    label = f"{width}x{height}"
                    server.state.reset()
                    try:
                        before, after, resources, cls = measure_viewport(
                            browser,
                            server.base_url,
                            width,
                            height,
                            args.hold_ms,
                            server.state.release,
                        )
                    except Exception as exc:  # noqa: BLE001 - report browser faults
                        failures.append(f"{label}: browser check failed: {exc}")
                        continue
                    if before["vitalsHidden"] or before["vitals"]["height"] <= 0:
                        failures.append(
                            f"{label}: #vitals must be visible in the server-rendered "
                            "HTML and reserve its space"
                        )
                    for key in ("hero", "heroTitle", "board", "boardTitle"):
                        moved = abs(after[key]["top"] - before[key]["top"])
                        if moved > POSITION_TOLERANCE:
                            failures.append(
                                f"{label}: #{key} moved {moved:.2f}px after "
                                "the first status.json read"
                            )
                    height_delta = abs(
                        after["documentHeight"] - before["documentHeight"]
                    )
                    if height_delta > POSITION_TOLERANCE:
                        failures.append(
                            f"{label}: document height moved {height_delta:.2f}px "
                            "after the first status.json read"
                        )
                    if cls["cls"] >= CLS_LIMIT:
                        failures.append(
                            f"{label}: CLS {cls['cls']:.4f} is not below {CLS_LIMIT}"
                        )
                    if server.state.status_requests < 1:
                        failures.append(
                            f"{label}: status.json was not read from the network"
                        )
                    hashed = [
                        path
                        for path in resources
                        if contract.is_content_hashed(path)
                    ]
                    if not hashed:
                        failures.append(
                            f"{label}: no platform-contract content-hashed asset "
                            "was requested"
                        )
                    print(
                        f"{label}: CLS {cls['cls']:.4f}; "
                        f"hero {before['hero']['top']} -> {after['hero']['top']}, "
                        f"hero title {before['heroTitle']['top']} -> "
                        f"{after['heroTitle']['top']}, "
                        f"board {before['board']['top']} -> {after['board']['top']}, "
                        f"vitals {'visible' if not before['vitalsHidden'] else 'hidden'}, "
                        f"banner {'visible' if not before['bannerHidden'] else 'hidden'}, "
                        f"{len(set(hashed))} content-hashed assets"
                    )
                    if cls["shifts"]:
                        print(
                            "  shifts: "
                            + ", ".join(
                                f"{shift['value']:.4f} at {shift['startTime']:.0f}ms "
                                f"({', '.join(source for source in shift['sources'] if source)})"
                                for shift in cls["shifts"]
                            )
                        )
            finally:
                browser.close()
    finally:
        server.state.release()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    if failures:
        print("cls-check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("cls-check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
