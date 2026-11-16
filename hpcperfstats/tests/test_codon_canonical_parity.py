"""Codon feed kernel vs CPython IncrementalStatsParser on the formerly CPython-only paths.

Covers legacy CTL/CTR decode, the resume emission gate, ``%jobs`` shared state
across file-session windows, and ``proc``/``host_proc`` field rules. The kernel
source (``dbload/codon/feed_collapse.py``) runs under CPython here; the baked
``.so`` runs the same corpora in the codon-build bake smoke.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from hpcperfstats.tests.test_codon_feed_delta_collapse_arc import (
  _assert_rows_match,
  _load_feed_collapse,
)

_COLS = [
  "time",
  "host",
  "jid",
  "type",
  "dev",
  "event",
  "value",
  "wid",
  "mult",
  "unit",
]


def _cpython_rows(lines: list[str], start_idx: int = 0) -> dict[tuple, tuple]:
  """Rows from the CPython parser + ``compute_deltas_and_arc`` (the oracle)."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
    compute_deltas_and_arc,
    stats_payload_to_records,
  )

  parser = IncrementalStatsParser(start_idx)
  parser.feed_lines(list(lines))
  records = stats_payload_to_records(parser.finish()[0])
  df = (
    pd.DataFrame(records, columns=_COLS)
    if records
    else pd.DataFrame(columns=_COLS)
  )
  out = compute_deltas_and_arc(df)
  return {
    (r["host"], r["type"], str(r["dev"] or ""), r["event"], float(r["time"])): (
      float(r["value"]),
      float(r["delta"]),
      float(r["arc"]),
    )
    for _, r in out.iterrows()
  }


def _rows_from_cols(cols: Any) -> dict[tuple, tuple]:
  times, hosts, _j, types, devs, events, _u, values, deltas, arcs = cols
  return {
    (hosts[i], types[i], devs[i], events[i], times[i]): (
      values[i],
      deltas[i],
      arcs[i],
    )
    for i in range(len(times))
  }


def _codon_rows(
  mod: Any, lines: list[str], start_idx: int = 0
) -> dict[tuple, tuple]:
  cols = mod.feed_delta_collapse_arc(lines, {}, {}, start_idx)[0]
  return _rows_from_cols(cols)


def _legacy_corpus() -> list[str]:
  from hpcperfstats.dbload.lib.sync_timedb_parsing_legacy import (
    EVENTMAPS_BY_TYPE,
  )

  pmc = next(k for k in EVENTMAPS_BY_TYPE["intel_8pmc3"] if isinstance(k, int))
  knl = next(iter(EVENTMAPS_BY_TYPE["intel_knl_mc"]))
  amd_a, amd_b = list(EVENTMAPS_BY_TYPE["amd64_pmc"])[:2]
  lines = [
    "!intel_8pmc3 CTL0 CTL1 CTR0 CTR1 "
    "FIXED_CTR0,W=48 FIXED_CTR1,W=48 FIXED_CTR2,W=48",
    "!intel_knl_mc CTL0 CTR0,W=48",
    "!amd64_pmc CTL0 CTL1 CTR0,W=48 CTR1,W=48",
    "!cpu user,W=64,U=1 sys,W=64",
  ]
  for step in range(3):
    t = 1710000000 + 10 * step
    lines += [
      f"{t}.0 9 hosta",
      # CTL1 = 7 is not in the eventmap, so CPython names it OTHER.
      f"intel_8pmc3 0 {pmc} 7 {1000 + 50 * step} {2000 + 5 * step} "
      f"{10 + step} {20 + 2 * step} {30 + 3 * step}",
      f"intel_knl_mc 1 {knl} {500 + 9 * step}",
      f"amd64_pmc 0 {amd_a} {amd_b} {70 + step} {80 + 4 * step}",
      # Both CTLs select one name: the later CTR value wins (dict last-write).
      f"amd64_pmc 1 {amd_b} {amd_b} {90 + step} {95 + 2 * step}",
      # Legacy schemas drop @fast lines.
      "intel_8pmc3 1 @fast 1 2 3",
      f"cpu 0 {100 + step} {5 + step}",
    ]
  return lines


def test_legacy_ctl_ctr_decode_matches_cpython() -> None:
  mod = _load_feed_collapse()
  lines = _legacy_corpus()
  got = _codon_rows(mod, lines)
  types = {k[1] for k in got}
  assert {"intel_8pmc3", "intel_knl_mc_dclk", "amd64_pmc", "cpu"} <= types
  assert "intel_knl_mc" not in types
  assert any(k[3] == "OTHER" for k in got)
  _assert_rows_match(got, _cpython_rows(lines))


def test_legacy_literal_tables_match_cpython() -> None:
  from hpcperfstats.dbload.lib import sync_timedb_parsing_legacy as lp
  from hpcperfstats.dbload.lib.monitor_naming import legacy as leg

  mod = _load_feed_collapse()
  maps = lp.EVENTMAPS_BY_TYPE
  want_int = {
    t: {k: v for k, v in m.items() if isinstance(k, int)}
    for t, m in maps.items()
  }
  want_str = {
    t: {k: v for k, v in m.items() if isinstance(k, str)}
    for t, m in maps.items()
    if any(isinstance(k, str) for k in m)
  }
  assert want_int == mod.LEGACY_EVENTMAP_INT
  assert want_str == mod.LEGACY_EVENTMAP_STR
  assert mod.LEGACY_TYPE_NORMALIZE == lp._LEGACY_KNL_TYPE_NORMALIZE
  assert set(mod.LEGACY_REMOVED_SYMBOLS) == set(leg.REMOVED_LEGACY_SYMBOLS)
  assert set(mod.LEGACY_HARDWARE_DECODE_TYPES) == set(
    leg.LEGACY_HARDWARE_DECODE_TYPES
  )


def test_legacy_fixed_ctr_without_mapping_fails_like_cpython() -> None:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
  )

  lines = [
    "!amd64_pmc FIXED_CTR0,W=48",
    "1710000000.0 9 hosta",
    "amd64_pmc 0 5",
  ]
  with pytest.raises(KeyError):
    IncrementalStatsParser(0).feed_lines(lines)
  with pytest.raises(KeyError):
    _load_feed_collapse().feed_delta_collapse_arc(lines, {}, {})


def _shared_corpus(base: int = 1710000000) -> list[str]:
  lines = ["!cpu user,W=64,U=1", "!host_mem_job MemUsed,U=1"]
  for step in range(6):
    t = base + 10 * step
    if step % 2 == 0:
      lines.append("%jobs jids=9,10")
      lines.append("%affinity jid=9 cpus=0-1 gpus=- cg=sched")
    lines += [
      f"{t}.0 9 hosta",
      f"cpu 0 {100 + 7 * step}",
      f"host_mem_job 0 {50 + step}",
    ]
  return lines


@pytest.mark.parametrize("start_idx", [0, 3, 9, 14, 20, 40])
def test_resume_emission_gate_rows_match_cpython(start_idx: int) -> None:
  mod = _load_feed_collapse()
  lines = _legacy_corpus() + _shared_corpus(1710000100)[1:]
  _assert_rows_match(
    _codon_rows(mod, lines, start_idx), _cpython_rows(lines, start_idx)
  )


@pytest.mark.parametrize("start_idx", [0, 2, 4, 6, 10, 13])
def test_resume_emission_gate_marks_and_stamps_match_cpython(
  start_idx: int,
) -> None:
  """Marks bound to a header below the gate are dropped; arm state survives."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
  )

  mod = _load_feed_collapse()
  lines = _shared_corpus()
  parser = IncrementalStatsParser(start_idx)
  parser.feed_lines(list(lines))
  want = sorted(
    (m["kind"], float(m["time"]), m["host"]) for m in parser.take_ready_marks()
  )
  marks = mod.feed_delta_collapse_arc(lines, {}, {}, start_idx)[4]
  got = sorted((m["kind"], float(m["time"]), m["host"]) for m in marks)
  assert got == want
  emitted = [
    float(s.split()[0])
    for i, s in enumerate(lines)
    if i >= start_idx and s[:1].isdigit()
  ]
  assert all(t >= emitted[0] for _k, t, _h in got)
  _assert_rows_match(
    _codon_rows(mod, lines, start_idx), _cpython_rows(lines, start_idx)
  )


def test_window_split_keeps_shared_stamp_across_boundary() -> None:
  """``%jobs`` before a window's first header still stamps that window ``j:``."""
  mod = _load_feed_collapse()
  lines = _shared_corpus()
  whole = _codon_rows(mod, lines)
  assert any(k[2].startswith("j:") for k in whole)
  # The file session flushes on the step-2 header, so its %jobs stays behind.
  cut = next(i for i, s in enumerate(lines) if s.startswith("1710000020.0"))
  win1, win2 = lines[:cut], lines[:2] + lines[cut:]
  out1 = mod.feed_delta_collapse_arc(win1, {}, {})
  arm = mod.trailing_jobs_arm(win1)
  assert arm is True
  out2 = mod.feed_delta_collapse_arc(win2, out1[2], out1[3], 0, [], arm)
  split = _rows_from_cols(out1[0]) | _rows_from_cols(out2[0])
  _assert_rows_match(split, whole)
  assert mod.trailing_jobs_arm(lines[: cut - 2]) is False


def _proc_corpus(typ: str) -> list[str]:
  return [
    f"!{typ} uid vm_peak vm_size vm_hwm threads bogus",
    "1710000000.0 9 hosta",
    f"{typ} a.out/11/0x1/0x1 1000 4096 8192 2048 4 77",
    "1710000010.0 9 hosta",
    f"{typ} a.out/11/0x1/0x1 1000 nan 9000 x 5 78",
    "1710000020.0 9 hosta",
    f"{typ} a.out/11/0x1/0x1 1000 5000 abc 1024 6 79",
  ]


@pytest.mark.parametrize("typ", ["proc", "host_proc"])
def test_proc_rows_match_cpython(typ: str) -> None:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
  )

  mod = _load_feed_collapse()
  lines = _proc_corpus(typ)
  parser = IncrementalStatsParser(0)
  parser.feed_lines(list(lines))
  procs = parser.finish()[1]
  ident = {"time", "host", "jid", "proc", "device"}
  want = [
    {k: v for k, v in p.items() if k not in ident and v is not None}
    for p in procs
  ]
  assert want and "vm_size" not in want[0], "non-numeric last write is NULL"
  cols, proc = mod.feed_delta_collapse_arc(lines, {}, {})[:2]
  assert not cols[0], "proc lines must not become hardware stats rows"
  pj, ph, pp, pf, _pd = proc
  assert (pj, ph, pp) == (["9"], ["hosta"], ["a.out"])
  assert [dict(f) for f in pf] == want


def test_leading_whitespace_and_non_alpha_types_match_cpython() -> None:
  mod = _load_feed_collapse()
  lines = [
    "!cpu user,W=64,U=1",
    "!_x a,W=64",
    "1710000000.0 9 hosta",
    "   cpu 0 1",
    "_x 0 5",
    "1710000010.0 9 hosta",
    "\tcpu 0 4",
    "_x 0 9",
  ]
  _assert_rows_match(_codon_rows(mod, lines), _cpython_rows(lines))


# ---- real baked pyext (skipped unless a bake from this tree is mounted) ----

_PYEXT_DIR = os.environ.get("HPCPERFSTATS_CODON_PYEXT_DIR", "").strip()


@pytest.fixture
def pyext() -> Any:
  if not (
    _PYEXT_DIR and (Path(_PYEXT_DIR) / "hpcperfstats_codon_parse.so").is_file()
  ):
    pytest.skip("HPCPERFSTATS_CODON_PYEXT_DIR bake required")
  sys.path.insert(0, _PYEXT_DIR)
  try:
    import hpcperfstats_codon_parse as mod
  except ImportError as exc:  # pragma: no cover - env specific
    pytest.skip(f"pyext import failed: {exc}")
  yield mod
  sys.path.remove(_PYEXT_DIR)


def _source_shape(lines: list[str], start_idx: int) -> Counter:
  cols = _load_feed_collapse().feed_delta_collapse_arc(
    lines, {}, {}, start_idx
  )[0]
  return Counter(zip(cols[3], cols[4], cols[5], strict=True))


def _all_corpus() -> list[str]:
  return _legacy_corpus() + _shared_corpus(1710000100)[1:]


@pytest.mark.parametrize("start_idx", [0, 9, 20, 40])
def test_pyext_line_batch_matches_source(pyext: Any, start_idx: int) -> None:
  lines = _all_corpus()
  stats = pyext.parse_stats_lines_codon(lines, 0, start_idx=start_idx)["stats"]
  got = Counter(zip(stats["type"], stats["dev"], stats["event"], strict=True))
  assert got == _source_shape(lines, start_idx)
  # The gate is reset when absent, so a sticky slot never keeps it.
  plain = pyext.parse_stats_lines_codon(lines, 0)["stats"]
  assert len(plain["type"]) == sum(_source_shape(lines, 0).values())


@pytest.mark.parametrize("flush_bytes", [64, 1 << 20])
@pytest.mark.parametrize("start_idx", [0, 9, 20, 40])
def test_pyext_file_session_gate_and_window_arm_match_source(
  pyext: Any, tmp_path: Path, flush_bytes: int, start_idx: int
) -> None:
  lines = _all_corpus()
  path = tmp_path / "stats.txt"
  path.write_text("\n".join(lines) + "\n")
  assert pyext.parse_stats_file_codon_open(str(path), flush_bytes, start_idx, 0)
  got: Counter = Counter()
  while (frame := pyext.parse_stats_file_codon_next(0, 0)) is not None:
    for ln in frame["stats"]["host_copy"].decode().splitlines():
      c = ln.split("\t")
      got[(c[3], c[4], c[5])] += 1
  assert got == _source_shape(lines, start_idx)
