"""Playwright: load vendored axe-core; assert no serious/critical violations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

AXE_MIN_JS = (
    Path(__file__).resolve().parent / "fixtures" / "axe-core" / "axe.min.js"
)

# JSON-serializable options for axe.run (WCAG 2.x + 2.1 AA tag set).
_AXE_RUN_OPTIONS: dict[str, Any] = {
    "runOnly": {
        "type": "tag",
        "values": ["wcag2a", "wcag2aa", "wcag21aa"],
    },
}

_SERIOUS_IMPACTS = frozenset({"critical", "serious"})


def inject_axe(page) -> None:
  """Ensure axe is defined on the page (loads vendored script once).

  ``add_script_tag(path=...)`` injects an inline ``<script>`` body. Pages that
  already carry a strict CSP without ``unsafe-inline`` (for example Django HTML
  404 responses) will block that injection. Callers probing synthetic documents
  should navigate to ``about:blank`` (no CSP) before ``set_content`` / inject.
  """
  if not AXE_MIN_JS.is_file():
    raise FileNotFoundError("Missing vendored axe-core: {}".format(AXE_MIN_JS))
  has_axe = page.evaluate("() => typeof window.axe !== 'undefined'")
  if has_axe:
    return
  page.add_script_tag(path=str(AXE_MIN_JS))


def assert_no_serious_axe_violations(
    page,
    *,
    wait_ms: int = 0,
    run_options: dict[str, Any] | None = None,
) -> None:
  """
  Run axe against document and fail pytest if any violation has impact
  serious or critical.
  """
  if wait_ms:
    page.wait_for_timeout(wait_ms)
  inject_axe(page)
  opts = run_options if run_options is not None else _AXE_RUN_OPTIONS
  result = page.evaluate(
      """async (opts) => {
        return await axe.run(document, opts);
      }""",
      opts,
  )
  violations = result.get("violations") or []
  bad = [
      v
      for v in violations
      if (v.get("impact") or "").lower() in _SERIOUS_IMPACTS
  ]
  if not bad:
    return
  lines = []
  for v in bad:
    rule = v.get("id", "?")
    impact = v.get("impact", "?")
    help_txt = (v.get("help") or "").replace("\n", " ")
    nodes = v.get("nodes") or []
    targets = []
    for n in nodes[:3]:
      t = n.get("target")
      if isinstance(t, list):
        targets.append(", ".join(str(x) for x in t))
      elif t is not None:
        targets.append(str(t))
    tail = "; ".join(targets) if targets else "(no targets)"
    lines.append("  [{}] {} — {}".format(impact, rule, help_txt))
    lines.append("    {}".format(tail))
  msg = "axe found {} serious/critical violation(s):\n{}".format(
      len(bad),
      "\n".join(lines),
  )
  raise AssertionError(msg)
