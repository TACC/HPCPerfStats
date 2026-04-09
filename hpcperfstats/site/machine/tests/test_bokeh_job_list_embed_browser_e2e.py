"""Playwright + real BokehJS: job-list json_item fixtures must not log known failure strings.

Loads committed fixtures from ``site/frontend/src/test-fixtures/`` (generated from
Django ``json_item`` after range fixes). Uses the public Bokeh 3.9.0 CDN so
Chromium has a real canvas (jsdom/Vitest cannot run ``embed_item``).

See also: ``test_job_hist_bokeh_ranges.py`` (Python y-range contracts).

Requires network for the CDN on the first load. If Playwright is missing, the
module is skipped (same pattern as ``test_web_pages_browser_e2e.py``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    pytest.skip("playwright is required for Bokeh browser embed tests", allow_module_level=True)

# Pin to the same major.minor as package.json @bokeh/bokehjs.
_BOKEH_CDN_JS = "https://cdn.jsdelivr.net/npm/@bokeh/bokehjs@3.9.0/build/js/bokeh.min.js"

_FAILURE_SUBSTRINGS = (
    "could not set initial ranges",
    "wasn't built properly",
    'can\'t access property "is_valid"',
)

# hpcperfstats/site/machine/tests -> site
_SITE_DIR = Path(__file__).resolve().parent.parent.parent
_FIXTURE_DIR = _SITE_DIR / "frontend" / "src" / "test-fixtures"


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
            "Missing json fixtures under site/frontend/src/test-fixtures/ "
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
