"""Source-level contracts (no DB).

Ruff F821/F524 regressions and related invariants.
"""

from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1]

_JID_AGG_TAIL = (
    "    key = make_cache_key_bounded(\n"
    "        KEY_AGG_DF, self.jid, typ, val_col, events_key,\n"
    "        self._large_job_plot_cache_token,\n"
    "    )\n"
    "    import pandas as pd\n\n"
    "    result = cached_orm(key, get_site_content_cache_timeout(), _fn)\n"
    "    if result is not None:\n"
    "      return result\n"
    '    return pd.DataFrame(columns=["host", "time", "sum_val"])'
)

_TYPE_DETAIL_HEAD = (
    "    key = make_cache_key(\n"
    "        KEY_TYPE_DETAIL_AGG, self.jid, self.type_name, event, metric, "
    "_st, _et\n"
    "    )\n"
    "    import pandas as pd\n\n"
    "    def _fn():"
)

_ARTIFACT_NEEDLE = 'f"\'{{\\"artifact_schema\\":\' || %s::text || "'

_BAD_PLOT_INNER_FORMAT = (
    ").format(jt=jt, st=st, et=et, jcol=jcol, mdc=mdc, hosts=hosts_json, "
    "live=live_sql)"
)


@pytest.mark.parametrize(
    "rel,needles,forbidden",
    [
      (
          "analysis/gen/jid_table.py",
          [_JID_AGG_TAIL, _TYPE_DETAIL_HEAD],
          [],
      ),
      (
          "site/machine/artifact_readiness_expressions.py",
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
