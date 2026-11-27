"""CPython oracle: Codon-native E6 kernel fingerprint matches IncrementalStatsParser."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CODON_EXP = _REPO / "tests" / "experiments" / "codon"
if str(_CODON_EXP) not in sys.path:
  sys.path.insert(0, str(_CODON_EXP))

import codon_native_e6_bench as codon_native  # noqa: E402

from hpcperfstats.dbload.lib.sync_timedb_parsing import (  # noqa: E402
  IncrementalStatsParser,
  stats_payload_to_records,
)


def test_codon_native_e6_fingerprint_matches_incremental_parser() -> None:
  lines = codon_native.build_e6_payload_lines(400)
  kernel = codon_native.FeedKernel(0)
  kernel.feed_lines(lines)
  stats_rows, proc_rows, value_sum, vm_peak_sum = kernel.fingerprint()

  parser = IncrementalStatsParser(0)
  parser.feed_lines(lines)
  stats, procs = parser.finish()
  stats = stats_payload_to_records(stats)
  oracle_value = sum(float(r.get("value") or 0) for r in stats)
  oracle_vm = sum(
    int(r.get("vm_peak") or 0) for r in procs if r.get("vm_peak") is not None
  )

  assert stats_rows == len(stats)
  assert proc_rows == len(procs)
  assert value_sum == oracle_value
  assert vm_peak_sum == oracle_vm
