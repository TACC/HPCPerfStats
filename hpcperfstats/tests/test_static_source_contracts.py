"""Source-level contracts (no DB).

Ruff F821/F524 regressions and related invariants.
"""

from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1]

_ARTIFACT_NEEDLE = 'f"\'{{\\"artifact_schema\\":\' || %s::text || "'

_BAD_PLOT_INNER_FORMAT = (
  ").format(jt=jt, st=st, et=et, jcol=jcol, mdc=mdc, hosts=hosts_json, "
  "live=live_sql)"
)


@pytest.mark.parametrize(
  "rel,needles,forbidden",
  [
    (
      "site/lib/machine/artifact_readiness_expressions.py",
      [_ARTIFACT_NEEDLE],
      [_BAD_PLOT_INNER_FORMAT],
    ),
  ],
)
def test_expected_source_fragments(rel, needles, forbidden):
  text = (_PKG / rel).read_text(encoding="utf-8")
  for n in needles:
    assert n in text, (rel, n[:60])
  for bad in forbidden:
    assert bad not in text, (rel, bad)
