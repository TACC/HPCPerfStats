"""Playwright + real BokehJS: job-list json_item fixtures must not log known failure strings.

Loads committed fixtures from ``site/frontend/test/fixtures/`` (generated from
Django ``json_item`` after range fixes). Uses the public Bokeh 3.9.1 CDN so
Chromium has a real canvas (jsdom/Vitest cannot run ``embed_item``).

A second test serves the **Next static export** (``npm run build`` →
``hpcperfstats_site/static/frontend/``) via ``python3 -m http.server`` and embeds
the same fixtures through ``/static/frontend/bokeh-playwright-smoke/`` (bundled
``@bokeh/bokehjs`` + ``patch-resize-observer-for-bokeh``), matching production chunk
graph without jsDelivr.

See also: ``test_job_hist_bokeh_ranges.py`` (Python y-range contracts).

Requires network for the CDN on the first load. If Playwright is missing, the
module is skipped (same pattern as ``test_web_pages_browser_e2e.py``).
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    pytest.skip("playwright is required for Bokeh browser embed tests", allow_module_level=True)

# Pin to the same major.minor as package.json @bokeh/bokehjs.
_BOKEH_CDN_JS = "https://cdn.jsdelivr.net/npm/@bokeh/bokehjs@3.9.1/build/js/bokeh.min.js"

_FAILURE_SUBSTRINGS = (
    "could not set initial ranges",
    "wasn't built properly",
    'can\'t access property "is_valid"',
)

# hpcperfstats/site/lib/machine/tests -> site
_SITE_DIR = Path(__file__).resolve().parents[3]
_FIXTURE_DIR = _SITE_DIR / "frontend" / "test" / "fixtures"
_STATIC_FRONTEND_DIR = _SITE_DIR / "hpcperfstats_site" / "static" / "frontend"
_SITE_ROOT_DIR = _SITE_DIR / "hpcperfstats_site"


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        _host, port = sock.getsockname()
        return int(port)


def _wait_http_ok(url: str, *, timeout_s: float = 45.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                resp.read(16)
            return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_err = exc
            time.sleep(0.12)
    raise TimeoutError(f"URL did not become reachable within {timeout_s}s: {url!r} ({last_err!r})")


def _html_page_for_item(payload_json: str) -> str:
    # application/json script body must not contain "</script>" — Bokeh ids only use p+digits.
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <script src="{_BOKEH_CDN_JS}"></script>
</head>
<body>
  <div id="plot" style="width:280px;height:200px"></div>
  <script type="application/json" id="payload">{payload_json}</script>
  <script>
    const item = JSON.parse(document.getElementById("payload").textContent);
    Bokeh.embed.embed_item(item, "plot");
  </script>
</body>
</html>"""


def _html_page_two_items_sequential_scroll(payload_a_json: str, payload_b_json: str) -> str:
    """Two plot targets: embed first, scroll second into view, embed second (layout stress)."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <script src="{_BOKEH_CDN_JS}"></script>
</head>
<body>
  <div id="plot-a" style="width:280px;height:200px"></div>
  <div id="spacer" style="height:2400px;width:1px"></div>
  <div id="plot-b" style="width:280px;height:200px"></div>
  <script type="application/json" id="payload-a">{payload_a_json}</script>
  <script type="application/json" id="payload-b">{payload_b_json}</script>
  <script>
    const itemA = JSON.parse(document.getElementById("payload-a").textContent);
    const itemB = JSON.parse(document.getElementById("payload-b").textContent);
    Bokeh.embed.embed_item(itemA, "plot-a");
    requestAnimationFrame(() => {{
      document.getElementById("plot-b").scrollIntoView({{ block: "end" }});
      requestAnimationFrame(() => {{
        Bokeh.embed.embed_item(itemB, "plot-b");
      }});
    }});
  </script>
</body>
</html>"""


@pytest.mark.django_db(databases=[])
def test_bokeh_embed_job_list_fixtures_no_histogram_failure_console_messages():
    """Embed each fixture; fail if Bokeh logs the same substrings as production."""
    paths = [
        _FIXTURE_DIR / "bokeh-job-hist-single-value.json",
        _FIXTURE_DIR / "bokeh-queue-bars-all-zero.json",
    ]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        pytest.skip(
            "Missing json fixtures under site/frontend/test/fixtures/ "
            "(generate via Django json_item; see module docstring). Missing: "
            + ", ".join(str(p) for p in missing)
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        violations: list[tuple[str, str]] = []

        def on_console(msg) -> None:
            text = msg.text
            for sub in _FAILURE_SUBSTRINGS:
                if sub in text:
                    violations.append((sub, text))

        def on_page_error(exc) -> None:
            msg = str(exc)
            for sub in _FAILURE_SUBSTRINGS:
                if sub in msg:
                    violations.append((sub, msg))

        page.on("console", on_console)
        page.on("pageerror", on_page_error)

        try:
            for path in paths:
                violations.clear()
                item = json.loads(path.read_text(encoding="utf-8"))
                payload = json.dumps(item, separators=(",", ":"))
                page.set_content(_html_page_for_item(payload), wait_until="load")
                time.sleep(0.8)
                assert not violations, (
                    f"Bokeh console/pageerror while embedding {path.name}: {violations!r}"
                )
        finally:
            browser.close()


@pytest.mark.django_db(databases=[])
def test_bokeh_embed_job_list_fixtures_next_built_bundle_no_histogram_failure_console_messages():
    """Same fixtures as CDN test, but Bokeh loads from the Next-built smoke route (no jsDelivr)."""
    smoke_index = _STATIC_FRONTEND_DIR / "bokeh-playwright-smoke" / "index.html"
    if not smoke_index.is_file():
        pytest.skip(
            "Built frontend missing bokeh-playwright-smoke/index.html; from "
            "hpcperfstats/site/frontend run `npm run build` (see docs/TESTING.md)."
        )

    paths = [
        _FIXTURE_DIR / "bokeh-job-hist-single-value.json",
        _FIXTURE_DIR / "bokeh-queue-bars-all-zero.json",
    ]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        pytest.skip(
            "Missing json fixtures under site/frontend/test/fixtures/. Missing: "
            + ", ".join(str(p) for p in missing)
        )

    port = _pick_free_port()
    proc = subprocess.Popen(
        [
            "python3",
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
            "--directory",
            str(_SITE_ROOT_DIR),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}/static/frontend/bokeh-playwright-smoke/"
    try:
        try:
            _wait_http_ok(base)
        except TimeoutError:
            pytest.fail(
                "http.server did not start in time for Bokeh bundle smoke test "
                f"(port {port}). From hpcperfstats/site/frontend run `npm run build` and retry."
            )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            violations: list[tuple[str, str]] = []

            def on_console(msg) -> None:
                text = msg.text
                for sub in _FAILURE_SUBSTRINGS:
                    if sub in text:
                        violations.append((sub, text))

            def on_page_error(exc) -> None:
                msg = str(exc)
                for sub in _FAILURE_SUBSTRINGS:
                    if sub in msg:
                        violations.append((sub, msg))

            page.on("console", on_console)
            page.on("pageerror", on_page_error)

            try:
                for path in paths:
                    violations.clear()
                    item = json.loads(path.read_text(encoding="utf-8"))
                    page.goto(base, wait_until="load", timeout=60_000)
                    page.wait_for_function(
                        "() => window.__HPCPERFSTATS_BOKEH_SMOKE_READY__ === true",
                        timeout=60_000,
                    )
                    page.evaluate(
                        """(plotItem) => {
                          window.__HPCPERFSTATS_NEXT_BOKEH__.embed.embed_item(plotItem, "plot");
                        }""",
                        item,
                    )
                    time.sleep(0.8)
                    assert not violations, (
                        f"Bokeh console/pageerror (Next bundle) while embedding {path.name}: "
                        f"{violations!r}"
                    )
            finally:
                browser.close()
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.django_db(databases=[])
def test_bokeh_embed_two_job_list_fixtures_sequential_scroll_no_console_failures():
    """Two different json_items after scroll: catches regressions in multi-embed + viewport layout."""
    hist = _FIXTURE_DIR / "bokeh-job-hist-single-value.json"
    queue = _FIXTURE_DIR / "bokeh-queue-bars-all-zero.json"
    missing = [p for p in (hist, queue) if not p.is_file()]
    if missing:
        pytest.skip(
            "Missing json fixtures under site/frontend/test/fixtures/. Missing: "
            + ", ".join(str(p) for p in missing)
        )

    payload_a = json.dumps(json.loads(hist.read_text(encoding="utf-8")), separators=(",", ":"))
    payload_b = json.dumps(json.loads(queue.read_text(encoding="utf-8")), separators=(",", ":"))
    html = _html_page_two_items_sequential_scroll(payload_a, payload_b)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        violations: list[tuple[str, str]] = []

        def on_console(msg) -> None:
            text = msg.text
            for sub in _FAILURE_SUBSTRINGS:
                if sub in text:
                    violations.append((sub, text))

        def on_page_error(exc) -> None:
            msg = str(exc)
            for sub in _FAILURE_SUBSTRINGS:
                if sub in msg:
                    violations.append((sub, msg))

        page.on("console", on_console)
        page.on("pageerror", on_page_error)

        try:
            page.set_content(html, wait_until="load")
            time.sleep(1.2)
            assert not violations, f"Bokeh console/pageerror (two-plot scroll page): {violations!r}"
        finally:
            browser.close()
