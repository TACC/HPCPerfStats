"""Oracle parity: bounded FeedKernel vs IncrementalStatsParser (feed-only)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CODON_EXP = _REPO / "tests" / "experiments" / "codon"
if str(_CODON_EXP) not in sys.path:
  sys.path.insert(0, str(_CODON_EXP))

from e6_corpus import (  # noqa: E402
  HOST_PROC_AT_FULL_FIXTURE,
  build_e6_payload_lines,
)
from parse_kernel import feed_fingerprint  # noqa: E402


def _oracle_fingerprint(lines: list[str]) -> dict[str, int | float]:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
    stats_payload_to_records,
  )

  parser = IncrementalStatsParser(0)
  parser.feed_lines(lines)
  stats, procs = parser.finish()
  stats = stats_payload_to_records(stats)
  value_sum = sum(float(r.get("value") or 0) for r in stats)
  vm_peak_sum = sum(
    int(r.get("vm_peak") or 0) for r in procs if r.get("vm_peak") is not None
  )
  return {
    "stats_rows": len(stats),
    "proc_rows": len(procs),
    "stats_value_sum": value_sum,
    "proc_vm_peak_sum": vm_peak_sum,
  }


@pytest.mark.parametrize(
  "lines",
  [
    build_e6_payload_lines(n_samples=50),
    build_e6_payload_lines(n_samples=400),
    HOST_PROC_AT_FULL_FIXTURE,
  ],
  ids=["e6_n50", "e6_n400", "host_proc_at_full"],
)
def test_feed_kernel_matches_incremental_parser(lines: list[str]) -> None:
  oracle = _oracle_fingerprint(lines)
  kernel = feed_fingerprint(lines)
  assert kernel == oracle
