"""Dynamic exclude-type list: Codon kernel/pyext/bridge parity with CPython.

The caller's ``exclude_types_list`` used to force every ingest off the Codon
path (production always passes ``exclude_types``); these tests lock the
dynamic exclusion ABI (``feed_lines_full``/``feed_delta_collapse_arc``
``exclude_types``, ``codon_exclude_set_c``, ``parse_stats_lines_codon(exclude=)``,
``parse_stats_file_codon_open(exclude=)``, bridge + IPC plumbing) against
``IncrementalStatsParser(0, exclude)``.

Real-pyext cases skip unless ``HPCPERFSTATS_CODON_PYEXT_DIR`` holds a bake
that exports ``codon_exclude`` (built from this tree).
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

FEED_COLLAPSE = (
  Path(__file__).resolve().parents[2]
  / "hpcperfstats"
  / "dbload"
  / "codon"
  / "feed_collapse.py"
)
EXCLUDE = ["host_vfs", "intel_x86_uncore_cha_skx"]


def _load_feed_collapse():
  spec = importlib.util.spec_from_file_location(
    "feed_collapse_exclude_under_test", FEED_COLLAPSE
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _corpus() -> list[str]:
  """Kept types interleaved with excluded types, incl. a mid-file schema."""
  return [
    "!cpu user,W=64,U=1",
    "!host_vfs read,W=64,U=1",
    "!intel_x86_uncore_cha_skx llc,W=64,U=1",
    "!host_proc uid,W=32 vm_peak,W=64",
    "1000.0 J1 hostA",
    "host_vfs 0 @full 5.0",
    "cpu 0 @full 10.0",
    "intel_x86_uncore_cha_skx 0 @full 7.0",
    "cpu 1 @full 20.0",
    "1001.0 J1 hostA",
    "host_vfs 0 @full 9.0",
    "cpu 0 @full 15.0",
    "intel_x86_uncore_cha_skx 0 @full 8.0",
    "cpu 1 @full 25.0",
  ]


def _cpython_rows(
  lines: list[str], exclude: list[str] | None
) -> dict[tuple, tuple]:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
    compute_deltas_and_arc,
    stats_payload_to_records,
  )

  parser = IncrementalStatsParser(0, frozenset(exclude or ()))
  parser.feed_lines(list(lines))
  stats, _procs = parser.finish()
  cols = [
    "time", "host", "jid", "type", "dev", "event", "value", "wid", "mult",
    "unit",
  ]  # fmt: skip
  records = stats_payload_to_records(stats)
  df = (
    pd.DataFrame(records, columns=cols)
    if records
    else pd.DataFrame(columns=cols)
  )
  out = compute_deltas_and_arc(df)
  rows: dict[tuple, tuple] = {}
  for _, row in out.iterrows():
    key = (
      row["host"],
      row["type"],
      str(row["dev"] or ""),
      row["event"],
      float(row["time"]),
    )
    rows[key] = (float(row["value"]), float(row["delta"]), float(row["arc"]))
  return rows


def _kernel_rows(
  mod: Any, lines: list[str], exclude: Any
) -> dict[tuple, tuple]:
  if exclude is None:
    cols, *_ = mod.feed_delta_collapse_arc(lines, {}, {})
  else:
    cols, *_ = mod.feed_delta_collapse_arc(lines, {}, {}, 0, exclude)
  times, hosts, _j, types, devs, events, _u, values, deltas, arcs = cols
  return {
    (hosts[i], types[i], devs[i], events[i], times[i]): (
      values[i],
      deltas[i],
      arcs[i],
    )
    for i in range(len(times))
  }


def _assert_same(got: dict[tuple, tuple], want: dict[tuple, tuple]) -> None:
  assert set(got) == set(want), (
    f"only_got={set(got) - set(want)} only_want={set(want) - set(got)}"
  )
  for key, trip in got.items():
    for g, w in zip(trip, want[key], strict=True):
      if w is None or (isinstance(w, float) and math.isnan(w)):
        assert g is None or (isinstance(g, float) and math.isnan(g)), key
      else:
        assert math.isclose(g, w, rel_tol=1e-9, abs_tol=1e-6), key


def test_kernel_exclude_matches_cpython_parser() -> None:
  mod = _load_feed_collapse()
  got = _kernel_rows(mod, _corpus(), EXCLUDE)
  want = _cpython_rows(_corpus(), EXCLUDE)
  _assert_same(got, want)
  assert {k[1] for k in got} == {"cpu"}


def test_kernel_empty_exclude_is_identity() -> None:
  mod = _load_feed_collapse()
  base = _kernel_rows(mod, _corpus(), None)
  assert _kernel_rows(mod, _corpus(), []) == base
  assert {"host_vfs", "intel_x86_uncore_cha_skx"} <= {k[1] for k in base}
  _assert_same(base, _cpython_rows(_corpus(), []))


def test_kernel_all_types_excluded_yields_no_rows() -> None:
  mod = _load_feed_collapse()
  assert _kernel_rows(mod, _corpus(), ["cpu", *EXCLUDE]) == {}


def test_kernel_excluded_host_proc_is_dropped_before_proc_branch() -> None:
  mod = _load_feed_collapse()
  lines = [
    "!host_proc uid,W=32 vm_peak,W=64",
    "1000.0 J1 hostA",
    "host_proc 12/a/0/0 @full 7 100",
  ]
  _c, proc, *_ = mod.feed_delta_collapse_arc(lines, {}, {})
  assert len(proc[0]) == 1
  _c, proc, *_ = mod.feed_delta_collapse_arc(lines, {}, {}, 0, ["host_proc"])
  assert proc[0] == []


def test_kernel_exclude_carry_continues_across_flushes() -> None:
  """Excluded lines never enter the carry, so split == one-shot."""
  mod = _load_feed_collapse()
  lines = _corpus()
  split = lines.index("1001.0 J1 hostA")
  _c1, _p1, craw, _carc, _m = mod.feed_delta_collapse_arc(
    lines[:split], {}, {}, 0, EXCLUDE
  )
  assert all("host_vfs" not in k for k in craw)
  assert all("intel_x86_uncore_cha_skx" not in k for k in craw)
  assert any("cpu" in k for k in craw)


def test_parse_stats_lines_gate_passes_exclude_to_codon(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Production passes a non-None list; it must still reach the Codon path."""
  from hpcperfstats.dbload.lib import (
    sync_timedb_codon_bridge as bridge,
    sync_timedb_parsing as parsing,
  )

  seen: dict[str, Any] = {}

  def fake(lines, carry=None, exclude=None):
    seen["exclude"] = exclude
    return ("stats", "procs")

  monkeypatch.setattr(bridge, "try_codon_parse_collapse", fake)
  out = parsing.parse_stats_lines(["x"], 0, None, frozenset(EXCLUDE))
  assert out == ("stats", "procs")
  assert seen["exclude"] == sorted(EXCLUDE)


def test_codon_exclude_list_helper() -> None:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    _codon_exclude_list,
    exclude_types,
  )

  # None == IncrementalStatsParser's default list; empty == exclude nothing.
  assert _codon_exclude_list(None) == sorted(exclude_types)
  assert _codon_exclude_list(frozenset()) is None
  assert _codon_exclude_list(frozenset({"b", "a"})) == ["a", "b"]


def test_invoke_parse_mod_fn_passes_exclude_only_when_non_empty() -> None:
  from hpcperfstats.dbload.lib.sync_timedb_codon_bridge import (
    _invoke_parse_mod_fn,
  )

  calls: list[dict[str, Any]] = []

  class _Mod:
    @staticmethod
    def parse_stats_lines_codon(
      lines, slot=0, carry_raw=None, carry_arc=None, exclude=None
    ):
      calls.append({"exclude": exclude})
      return {"stats": [], "procs": []}

  _invoke_parse_mod_fn(_Mod, ["x"], 0, {}, {}, ["a"])
  _invoke_parse_mod_fn(_Mod, ["x"], 0, {}, {}, None)
  _invoke_parse_mod_fn(_Mod, ["x"], 0, {}, {}, [])
  assert [c["exclude"] for c in calls] == [["a"], None, None]


def test_ipc_envelopes_carry_exclude_only_when_set() -> None:
  from hpcperfstats.dbload.lib import codon_process_worker as w

  _k, env = w.decode_request(w.encode_request("parse", ["x"], None, None))
  assert "exclude" not in env
  _k, env = w.decode_request(
    w.encode_request("parse", ["x"], None, None, ["a", "b"])
  )
  assert env["exclude"] == ["a", "b"]
  kind, fenv = w.decode_request(w.encode_parse_file_request("/x", 1024))
  assert kind == "parse_file" and "exclude" not in fenv
  _k, fenv = w.decode_request(w.encode_parse_file_request("/x", 1024, 0, ["a"]))
  assert fenv["exclude"] == ["a"]


# ---- real baked pyext (skipped unless a bake from this tree is mounted) ----

_PYEXT_DIR = os.environ.get("HPCPERFSTATS_CODON_PYEXT_DIR", "").strip()


def _pyext_ready() -> bool:
  return (
    bool(_PYEXT_DIR)
    and (Path(_PYEXT_DIR) / "hpcperfstats_codon_parse.so").is_file()
  )


def _host_types(result: dict[str, Any]) -> set[str]:
  stats = result["stats"]
  return set(stats["type"]) if isinstance(stats, dict) else set()


@pytest.fixture
def pyext():
  if not _pyext_ready():
    pytest.skip("HPCPERFSTATS_CODON_PYEXT_DIR bake required")
  sys.path.insert(0, _PYEXT_DIR)
  try:
    import hpcperfstats_codon_parse as mod
  except ImportError as exc:  # pragma: no cover - env specific
    pytest.skip(f"pyext import failed: {exc}")
  yield mod
  sys.path.remove(_PYEXT_DIR)


def test_pyext_exclude_drops_types_and_does_not_leak(pyext) -> None:
  lines = _corpus()
  with_ex = pyext.parse_stats_lines_codon(lines, 0, exclude=EXCLUDE)
  assert _host_types(with_ex) == {"cpu"}
  # Same sticky slot, no exclude arg: previous list must not leak.
  plain = pyext.parse_stats_lines_codon(lines, 0)
  assert set(EXCLUDE) <= _host_types(plain)
  # Explicit empty list clears too.
  cleared = pyext.parse_stats_lines_codon(lines, 0, exclude=[])
  assert _host_types(cleared) == _host_types(plain)


def test_pyext_exclude_rejects_bad_arguments(pyext) -> None:
  with pytest.raises(TypeError):
    pyext.parse_stats_lines_codon(_corpus(), 0, exclude="host_vfs")
  with pytest.raises(TypeError):
    pyext.parse_stats_lines_codon(_corpus(), 0, exclude=[1])


def test_pyext_file_session_exclude(pyext, tmp_path: Path) -> None:
  path = tmp_path / "stats.txt"
  path.write_text("\n".join(_corpus()) + "\n")

  def run(**kw) -> dict[tuple, tuple]:
    assert pyext.parse_stats_file_codon_open(str(path), 1 << 20, 0, 0, **kw)
    out: dict[tuple, tuple] = {}
    while True:
      frame = pyext.parse_stats_file_codon_next(0, 0)
      if frame is None:
        break
      for ln in frame["stats"]["host_copy"].decode().splitlines():
        c = ln.split("\t")
        out[(c[1], c[3], c[4], c[5], c[0])] = tuple(c[7:])
    return out

  with_ex = run(exclude=EXCLUDE)
  plain = run()
  assert {k[1] for k in with_ex} == {"cpu"}
  assert set(EXCLUDE) <= {k[1] for k in plain}
  assert with_ex == {k: v for k, v in plain.items() if k[1] == "cpu"}
