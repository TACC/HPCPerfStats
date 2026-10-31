"""
Golden-corpus parity: columnar kernels vs the pandas path on real jobs.

Fixtures live outside git (``HPCPERFSTATS_METRICS_GOLDEN_DIR``, default
``/data/user/sharrell/hpcperfstats-metrics-golden``): ``jobs/<jid>.frame.npz``
(the decoded JobFrame) and ``jobs/<jid>.golden.json.gz`` (pandas-path
metric rows, detail payloads, plot JSON and pre-Bokeh series). Tests skip
with a reason when the corpus is absent unless
``HPCPERFSTATS_METRICS_GOLDEN_REQUIRED=1``.

Tolerances: float64-only quantities must match at ``rtol=1e-9, atol=0``.
Quantities the pandas path obtained from PostgreSQL ``sum(real)`` carry
float4 accumulation noise (plan decision D34); their measured deviation is
written to ``test_runs/columnar_golden_deviation.json`` and checked against
``SQL_SUM_RTOL``.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.columnar import summary_series as ss
from hpcperfstats.analysis.metrics.lib.columnar.job_table import (
  ColumnarJobTable,
)

RTOL_EXACT = 1e-9
# Per-sample sums come from PostgreSQL ``sum(real)`` (float4 accumulation in
# scan order); 1e-5 relative bounds the observed noise on the corpus (D34).
SQL_SUM_RTOL = 1e-5
# Documented pandas-path defects the golden carries (plan D36 / D37):
# the Summary ``nv_gpu_link_gbs`` column was served the GiB/s aggregate of the
# roofline prefetch because the aggregate cache key omits ``conv``.
GOLDEN_SCALE_DEFECTS = {
  "nv_gpu_link_gbs": 1e9 / 1024**3,
  "nv_gpu_mem_bw_gbs": 1e9 / 1024**3,
}

_REPO = Path(__file__).resolve().parents[4]
_DEVIATION_REPORT = _REPO / "test_runs" / "columnar_golden_deviation.json"


def _corpus_dir() -> Path | None:
  raw = os.environ.get("HPCPERFSTATS_METRICS_GOLDEN_DIR") or (
    "/data/user/sharrell/hpcperfstats-metrics-golden"
  )
  path = Path(raw)
  if (path / "jobs").is_dir() and any((path / "jobs").glob("*.golden.json.gz")):
    return path
  if os.environ.get("HPCPERFSTATS_METRICS_GOLDEN_REQUIRED") == "1":
    pytest.fail(f"golden corpus required but absent at {path}")
  return None


def _load_corpus_module():
  spec = importlib.util.spec_from_file_location(
    "metrics_golden_corpus_golden_t",
    _REPO / "scripts" / "metrics_golden_corpus.py",
  )
  mod = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = mod
  spec.loader.exec_module(mod)
  return mod


def _jids() -> list[str]:
  d = _corpus_dir()
  if d is None:
    return []
  return sorted(
    p.name[: -len(".golden.json.gz")]
    for p in (d / "jobs").glob("*.golden.json.gz")
  )


_JIDS = _jids()
_DEVIATIONS: dict[str, dict[str, float]] = {}


def _record_deviation(section: str, column: str, rel: float) -> None:
  slot = _DEVIATIONS.setdefault(section, {})
  slot[column] = max(slot.get(column, 0.0), float(rel))


@pytest.fixture(scope="module")
def corpus_mod():
  return _load_corpus_module()


@pytest.fixture(scope="module")
def golden_jobs(corpus_mod):
  d = _corpus_dir()
  if d is None:
    pytest.skip("golden corpus absent (HPCPERFSTATS_METRICS_GOLDEN_DIR)")
  out = {}
  for jid in _JIDS:
    with gzip.open(
      d / "jobs" / f"{jid}.golden.json.gz", "rt", encoding="utf-8"
    ) as fh:
      golden = json.load(fh)
    frame = corpus_mod.load_frame_npz(d / "jobs" / f"{jid}.frame.npz")
    frame.check_contract()
    out[jid] = (
      ColumnarJobTable.from_golden(jid, frame, golden["jid_table"]),
      golden,
    )
  return out


def _rel_dev(a: np.ndarray, b: np.ndarray) -> float:
  """Max relative deviation over finite pairs; NaN patterns must match."""
  a_nan, b_nan = np.isnan(a), np.isnan(b)
  if not np.array_equal(a_nan, b_nan):
    return math.inf
  fin = ~a_nan
  if not fin.any():
    return 0.0
  denom = np.maximum(np.abs(b[fin]), np.finfo(float).tiny)
  return float(np.max(np.abs(a[fin] - b[fin]) / denom))


def _golden_frame_rows(columns: dict) -> dict[tuple[str, int], int]:
  hosts = columns["host"]
  times = columns["time"]
  return {
    (str(h), int(t)): i
    for i, (h, t) in enumerate(zip(hosts, times, strict=True))
  }


@pytest.mark.parametrize("jid", _JIDS or ["__no_corpus__"])
def test_summary_series_matches_golden(jid, golden_jobs):
  if jid == "__no_corpus__":
    pytest.skip("golden corpus absent")
  table, golden = golden_jobs[jid]
  gs = golden["series"]["summary"]
  try:
    built = ss.build_summary_series(table)
  except (ValueError, KeyError) as exc:
    assert gs["reason"] is not None, (
      f"columnar raised {exc!r} but pandas built a frame"
    )
    assert str(exc) == gs["reason"] or str(exc) in gs["reason"]
    return
  assert gs["reason"] is None, (
    f"pandas failed ({gs['reason']}) but columnar built a frame"
  )
  gcols = gs["columns"]
  metric_cols_g = sorted(c for c in gcols if c not in ("index", "host", "time"))
  built_cols = sorted(built.frame.columns)
  missing = sorted(set(metric_cols_g) - set(built_cols))
  # D36: with full-coverage cycle counters the frequency cleanup deletes the
  # instruction column; pandas kept it only because its SQL chunk dropped
  # the cycle counters (NaN at an all-NULL sample).
  for col in list(missing):
    cycles = (
      ("amd_acycles", "amd_mcycles")
      if col == "amd_instr"
      else ("acycles", "mcycles")
    )
    if col in ("instr", "amd_instr") and not any(c in gcols for c in cycles):
      missing.remove(col)
      _record_deviation("summary_missing_columns", col, 1.0)
  assert not missing, f"jid={jid} columns missing in columnar build: {missing}"
  # Rows keyed by (host, time): same key set.
  built_keys = list(
    zip(built.frame.host_names(), built.frame.time_us.tolist(), strict=True)
  )
  g_index = _golden_frame_rows(gcols)
  assert set(built_keys) == set(g_index), (
    "summary base (host, time) rows differ"
  )
  order = np.array([g_index[k] for k in built_keys])
  # D36: a column the pandas path dropped (all-NaN at an all-NULL sample in
  # its SQL chunk) may exist here with ``0`` at that sample.
  for col in sorted(set(built_cols) - set(metric_cols_g)):
    b = built.frame.columns[col]
    # ``freq`` is derived from such columns and is all-NaN when the cycle
    # counters are zero (0 / 0); it is never rendered in that state.
    explained = bool((b == 0.0).any()) or (
      col == "freq" and ("acycles" not in gcols or "mcycles" not in gcols)
    )
    assert explained, f"jid={jid} unexplained extra column {col}"
    _record_deviation("summary_extra_columns", col, 1.0)
  for col in metric_cols_g:
    if col not in built.frame.columns:
      continue  # explained missing column (recorded above)
    g = np.array(
      [np.nan if v is None else float(v) for v in gcols[col]], dtype=float
    )[order]
    b = built.frame.columns[col].copy()
    if col in GOLDEN_SCALE_DEFECTS:
      b = b * GOLDEN_SCALE_DEFECTS[col]
    # D36: pandas NaN where the SQL chunk dropped an all-NULL group; the
    # columnar rule yields 0 there.
    coalesce_rows = np.isnan(g) & ~np.isnan(b) & (b == 0.0)
    if coalesce_rows.any():
      _record_deviation(
        "summary_coalesce_zero_rows", col, float(coalesce_rows.sum())
      )
      g = g[~coalesce_rows]
      b = b[~coalesce_rows]
    rel = _rel_dev(b, g)
    if col == "ibbw" and rel > SQL_SUM_RTOL:
      # D36 cascade: pandas dropped the InfiniBand column (NaN at an
      # all-NULL sample) and filled ``ibbw`` from Omni-Path instead.
      opa = ss.agg_if_feasible(
        table, "opa", "arc", ["PortXmitData", "PortRcvData"], 1.0 / 125000.0
      )
      alt = (
        built.frame.join_values(opa)[~coalesce_rows] if not opa.empty else None
      )
      if alt is not None and _rel_dev(alt, g) <= SQL_SUM_RTOL:
        _record_deviation("summary_ibbw_source_switch", col, 1.0)
        continue
    _record_deviation("summary", col, rel)
    assert rel <= SQL_SUM_RTOL, f"jid={jid} column {col} rel dev {rel:.3e}"
  g_specs = [tuple(r) for r in gs["render_specs"]]
  # Subplots of D36 extra / explained-missing columns follow from the
  # deterministic aggregate rule; compare the render list on common columns.
  common = set(built_cols) & set(metric_cols_g)
  b_specs = [
    (n, lab, None if y is None else float(y))
    for n, lab, y in built.render_specs
    if n in common
  ]
  g_specs = [
    (n, lab, None if y is None else float(y))
    for n, lab, y in g_specs
    if n in common
  ]
  assert [s[:2] for s in b_specs] == [s[:2] for s in g_specs]
  for (n, _l, by), (_n, _gl, gy) in zip(b_specs, g_specs, strict=True):
    if by is None or gy is None:
      assert by is gy, f"y_top mismatch for {n}: {by} vs {gy}"
    else:
      assert math.isclose(by, gy, rel_tol=SQL_SUM_RTOL), (
        f"y_top {n}: {by} vs {gy}"
      )


def test_write_deviation_report():
  if not _DEVIATIONS:
    pytest.skip("no deviations recorded (corpus absent)")
  _DEVIATION_REPORT.parent.mkdir(parents=True, exist_ok=True)
  _DEVIATION_REPORT.write_text(
    json.dumps(_DEVIATIONS, indent=1, sort_keys=True)
  )


# -- K1: metrics rows ---------------------------------------------------------


def _golden_rows_by_key(rows):
  return {(str(r["type"]), str(r["metric"])): r for r in rows}


@pytest.mark.parametrize("jid", _JIDS or ["__no_corpus__"])
def test_metrics_rows_match_golden(jid, golden_jobs, monkeypatch):
  if jid == "__no_corpus__":
    pytest.skip("golden corpus absent")
  from types import SimpleNamespace

  from hpcperfstats.analysis.metrics.lib import metrics as metrics_mod

  table, golden = golden_jobs[jid]
  gm = golden["metrics"]
  monkeypatch.setattr(metrics_mod, "open_job_table", lambda job, **_kw: table)
  monkeypatch.setattr(
    metrics_mod,
    "_in_window_telemetry_bounds_for_job",
    lambda job: (gm["telemetry_first_time"], gm["telemetry_last_time"]),
  )
  job = SimpleNamespace(
    jid=jid, nhosts=golden["job"].get("nhosts"), artifact_only=False
  )
  payload = metrics_mod.Metrics().compute_metrics(job)
  rows = payload["rows"]
  got = _golden_rows_by_key(rows)
  exp = _golden_rows_by_key(gm["rows"])
  rounded_rows: dict | None = None

  def _rounded_value(key):
    """Metric value recomputed from text-rounded float4 inputs (D38)."""
    nonlocal rounded_rows
    if rounded_rows is None:
      monkeypatch.setattr(
        metrics_mod,
        "open_job_table",
        lambda job, **_kw: _text_rounded_table(table, golden),
      )
      rounded_rows = _golden_rows_by_key(
        metrics_mod.Metrics().compute_metrics(job)["rows"]
      )
    return rounded_rows[key]["value"]

  assert sorted(got) == sorted(exp), f"jid={jid} metric row keys differ"
  assert int(payload["distinct_time_count"]) == int(
    gm["distinct_time_count"] or 0
  )
  assert payload["host_data_schema"] == gm["host_data_schema"]
  for key in sorted(exp):
    g, b = exp[key], got[key]
    assert str(b["units"]) == str(g["units"]), f"jid={jid} {key} units"
    assert b.get("no_data_reason") == g.get("no_data_reason"), (
      f"jid={jid} {key} reason"
    )
    gv, bv = g["value"], b["value"]
    if gv is None or bv is None:
      assert gv is None and bv is None, f"jid={jid} {key}: {bv!r} vs {gv!r}"
      continue
    gv, bv = float(gv), float(bv)
    if math.isnan(gv) or math.isnan(bv):
      assert math.isnan(gv) and math.isnan(bv), f"jid={jid} {key}: {bv} vs {gv}"
      continue
    rel = abs(bv - gv) / max(abs(gv), np.finfo(float).tiny)
    if rel > SQL_SUM_RTOL:
      # D38: the pandas path saw text-rounded float4 values; prove the
      # kernel logic agrees when fed the same rounded inputs.
      rv = _rounded_value(key)
      assert rv is not None and not math.isnan(float(rv)), (
        f"jid={jid} {key} rounded None"
      )
      rel_r = abs(float(rv) - gv) / max(abs(gv), np.finfo(float).tiny)
      _record_deviation("metrics_float4_text_rounding", key[1], rel)
      assert rel_r <= SQL_SUM_RTOL, (
        f"jid={jid} {key}: exact {bv} rounded {rv} vs golden {gv} rel {rel_r:.3e}"
      )
      continue
    _record_deviation("metrics", key[1], rel)


def _text_rounded_table(
  table: ColumnarJobTable, golden: dict
) -> ColumnarJobTable:
  """
  Same job with ``value`` / ``arc`` / ``delta`` replaced by the shortest
  decimal text of each float4, as the pandas path received them from
  PostgreSQL's text protocol (plan D38).
  """
  import dataclasses

  f = table.frame

  def _pg_float4_text(v: float) -> float:
    """PostgreSQL ``float4out`` (Ryu shortest round-trip, ties excluded)."""
    v32 = np.float32(v)
    lo = float(np.nextafter(v32, np.float32(-np.inf)))
    hi = float(np.nextafter(v32, np.float32(np.inf)))
    for prec in range(1, 10):
      d = float(f"{float(v32):.{prec}g}")
      if np.float32(d) != v32:
        continue
      if d in ((float(v32) + lo) / 2, (float(v32) + hi) / 2):
        continue
      return d
    return float(v32)

  def _round(col: np.ndarray) -> np.ndarray:
    finite = np.isfinite(col)
    uniq, inverse = np.unique(col[finite], return_inverse=True)
    mapped = np.array(
      [_pg_float4_text(float(u)) for u in uniq], dtype=np.float64
    )
    out = col.copy()
    out[finite] = mapped[inverse]
    return out

  frame = dataclasses.replace(
    f, value=_round(f.value), arc=_round(f.arc), delta=_round(f.delta)
  )
  return ColumnarJobTable.from_golden(table.jid, frame, golden["jid_table"])


# -- K2 / K3: detail payloads, type detail, roofline --------------------------


def _vals(seq):
  return np.array([np.nan if v is None else float(v) for v in seq], dtype=float)


@pytest.mark.parametrize("jid", _JIDS or ["__no_corpus__"])
def test_job_detail_fsio_gpu_match_golden(jid, golden_jobs):
  if jid == "__no_corpus__":
    pytest.skip("golden corpus absent")
  from hpcperfstats.analysis.metrics.lib.columnar import detail_series as ds
  from hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary import (
    reduce_gpu_agg_to_util_stats,
  )

  table, golden = golden_jobs[jid]
  payload = golden["detail"]["job_detail"][""]["payload"]
  # FSIO: totals come from persisted metrics in the pandas path; peaks too.
  g_fsio = payload.get("fsio") or {}
  totals = ds.fsio_totals(table)
  assert sorted(totals) == sorted(g_fsio), (
    f"jid={jid} fsio families {sorted(totals)} vs {sorted(g_fsio)}"
  )
  for key, (r_mb, w_mb) in totals.items():
    g_r, g_w, g_pm, g_pi = (list(g_fsio[key]) + [None] * 4)[:4]
    for name, mine, theirs in (("read", r_mb, g_r), ("write", w_mb, g_w)):
      assert math.isclose(
        mine, float(theirs or 0.0), rel_tol=SQL_SUM_RTOL, abs_tol=0.0
      ), f"jid={jid} fsio {key} {name}: {mine} vs {theirs}"
    pm, pi = ds.fsio_peaks(table, key)
    for name, mine, theirs in (
      ("peak_mb_s", pm, g_pm),
      ("peak_iops", pi, g_pi),
    ):
      if theirs is None or mine is None:
        assert theirs is None and mine is None, (
          f"jid={jid} fsio {key} {name}: {mine} vs {theirs}"
        )
      else:
        rel = abs(mine - float(theirs)) / max(
          abs(float(theirs)), np.finfo(float).tiny
        )
        _record_deviation("detail_fsio", f"{key}_{name}", rel)
        assert rel <= SQL_SUM_RTOL, (
          f"jid={jid} fsio {key} {name}: {mine} vs {theirs}"
        )
  # GPU summary and inventory.
  active, vmax, vmean = reduce_gpu_agg_to_util_stats(ds.gpu_agg_rows(table))
  count = ds.gpu_count_total(table)
  for name, mine in (
    ("gpu_active", active),
    ("gpu_utilization_max", vmax),
    ("gpu_utilization_mean", vmean),
    ("gpu_count", count),
  ):
    theirs = payload.get(name)
    if theirs is None or mine is None:
      assert theirs is None and mine is None, (
        f"jid={jid} {name}: {mine} vs {theirs}"
      )
    else:
      assert math.isclose(float(mine), float(theirs), rel_tol=RTOL_EXACT), (
        f"jid={jid} {name}"
      )
  inv = ds.gpu_inventory(table)
  g_inv = payload.get("gpu_inventory") or []
  assert len(inv) == len(g_inv), (
    f"jid={jid} inventory length {len(inv)} vs {len(g_inv)}"
  )
  for mine, theirs in zip(inv, g_inv, strict=True):
    assert (mine["host"], mine["dev"], mine["type"]) == (
      theirs["host"],
      theirs["dev"],
      theirs["type"],
    )
    assert mine["sample_count"] == theirs["sample_count"]
    for k in ("util_max", "util_mean", "power_max_w"):
      a, b = mine.get(k), theirs.get(k)
      if a is None or b is None:
        assert a is None and b is None, f"jid={jid} inventory {k}: {a} vs {b}"
      else:
        assert math.isclose(float(a), float(b), rel_tol=RTOL_EXACT), (
          f"jid={jid} inventory {k}"
        )


@pytest.mark.parametrize("jid", _JIDS or ["__no_corpus__"])
def test_type_detail_series_match_golden(jid, golden_jobs):
  if jid == "__no_corpus__":
    pytest.skip("golden corpus absent")
  from hpcperfstats.analysis.metrics.lib.columnar import (
    type_detail_series as tds,
  )

  table, golden = golden_jobs[jid]
  g_types = golden["series"].get("type_detail") or {}
  g_payloads = golden["detail"].get("type_detail") or {}
  for type_name in sorted(table.schema):
    g = g_types.get(type_name)
    assert g is not None, (
      f"jid={jid} type {type_name} absent from golden series"
    )
    if g.get("error"):
      continue
    events_units = [tuple(x) for x in g["events_units"]]
    # Event (unit) pairs: same set (the ORM returned them in database order).
    mine_eu = tds.events_units_for_first_host(table, type_name, None)
    assert sorted(e for e, _u in mine_eu) == sorted(
      e for e, _u in events_units
    ), f"jid={jid} type {type_name} events differ"
    assert tds.type_list_for_first_host(table, type_name) == list(
      g["type_list"]
    )
    series = tds.build_type_detail_series(
      table, type_name, events_units, g["type_list"]
    )
    gcols = g["columns"]
    g_metric_cols = sorted(
      c for c in gcols if c not in ("index", "host", "time")
    )
    assert sorted(series.frame.columns) == g_metric_cols, (
      f"jid={jid} type {type_name} columns"
    )
    if not gcols:
      assert series.frame.empty
      continue
    g_index = _golden_frame_rows(gcols)
    keys = list(
      zip(series.frame.host_names(), series.frame.time_us.tolist(), strict=True)
    )
    assert set(keys) == set(g_index), (
      f"jid={jid} type {type_name} base rows differ"
    )
    order = np.array([g_index[k] for k in keys])
    for col in g_metric_cols:
      rel = _rel_dev(series.frame.columns[col], _vals(gcols[col])[order])
      _record_deviation("type_detail", f"{type_name}.{col}", rel)
      assert rel <= SQL_SUM_RTOL, (
        f"jid={jid} type {type_name} {col} rel {rel:.3e}"
      )
    # stats_data of the persisted artifact (per-dt means, pandas order).
    g_payload = g_payloads.get(type_name, {}).get("payload") or {}
    rows, cols = tds.stats_data_rows(series)
    assert cols == list(g_payload.get("schema") or []), (
      f"jid={jid} type {type_name} schema order"
    )
    g_rows = g_payload.get("stats_data") or []
    assert len(rows) == len(g_rows), f"jid={jid} type {type_name} stats rows"
    for (dt, vals), (g_dt, g_vals) in zip(rows, g_rows, strict=True):
      assert dt == g_dt, f"jid={jid} type {type_name} dt {dt} vs {g_dt}"
      rel = _rel_dev(np.array(vals, dtype=float), _vals(g_vals))
      assert rel <= SQL_SUM_RTOL, (
        f"jid={jid} type {type_name} stats {dt} rel {rel:.3e}"
      )


def _roofline_rows(points):
  if points is None:
    return {}
  devs = points.dev_names()
  return {
    (h, d, int(t)): (float(f), float(b))
    for h, d, t, f, b in zip(
      points.host_names(),
      devs,
      points.time_us.tolist(),
      points.flops_gf,
      points.bw_gb,
      strict=True,
    )
  }


def _golden_roofline_rows(cols):
  if not cols:
    return {}
  devs = cols.get("dev") or [""] * len(cols["host"])
  return {
    (h, "" if d is None else str(d), int(t)): (float(f), float(b))
    for h, d, t, f, b in zip(
      cols["host"],
      devs,
      cols["time"],
      cols["flops_gf"],
      cols["bw_gb"],
      strict=True,
    )
  }


@pytest.mark.parametrize("jid", _JIDS or ["__no_corpus__"])
def test_roofline_points_match_golden(jid, golden_jobs):
  if jid == "__no_corpus__":
    pytest.skip("golden corpus absent")
  from hpcperfstats.analysis.metrics.lib.columnar import roofline_series as rs

  table, golden = golden_jobs[jid]
  for section, fn in (
    ("roofline", rs.cpu_roofline_points),
    ("gpu_roofline", rs.gpu_roofline_points),
  ):
    g = golden["series"][section]
    out = fn(table)
    points, reason = out[0], out[1]
    if section == "gpu_roofline":
      assert out[2] == g.get("bw_axis"), (
        f"jid={jid} gpu bw axis {out[2]} vs {g.get('bw_axis')}"
      )
    if g["reason"] is not None:
      assert points is None, (
        f"jid={jid} {section}: pandas had no points ({g['reason']})"
      )
      assert reason == g["reason"], (
        f"jid={jid} {section} reason differs:\n{reason}\n{g['reason']}"
      )
      continue
    assert points is not None and reason is None, (
      f"jid={jid} {section}: columnar reason {reason}"
    )
    mine = _roofline_rows(points)
    theirs = _golden_roofline_rows(g["columns"])
    # D36: the pandas inner join lacked all-NULL samples that the columnar
    # rule keeps as zero rows; the figure drops zero FLOPS / BW points anyway.
    extra = set(mine) - set(theirs)
    for key in extra:
      flops, bw = mine[key]
      assert flops == 0.0 or bw == 0.0, (
        f"jid={jid} {section} unexplained extra point {key}"
      )
      _record_deviation(f"{section}_zero_points", "rows", 1.0)
    assert set(theirs) <= set(mine), f"jid={jid} {section} point keys missing"
    for key in theirs:
      for idx, name in ((0, "flops_gf"), (1, "bw_gb")):
        a, b = mine[key][idx], theirs[key][idx]
        rel = (
          abs(a - b) / max(abs(b), np.finfo(float).tiny)
          if not (math.isnan(a) and math.isnan(b))
          else 0.0
        )
        _record_deviation(section, name, rel)
        assert rel <= SQL_SUM_RTOL, (
          f"jid={jid} {section} {key} {name}: {a} vs {b}"
        )
    peaks = (
      rs.max_converted_sum_val(table, "cpu_peak_fp64_flops_per_s", 1e-9)
      if section == "roofline"
      else None
    )
    del peaks  # peak inference is covered by test_roofline_peaks_match_golden


@pytest.mark.parametrize("jid", _JIDS or ["__no_corpus__"])
def test_roofline_peaks_match_golden(jid, golden_jobs):
  if jid == "__no_corpus__":
    pytest.skip("golden corpus absent")
  from hpcperfstats.analysis.metrics.lib.plot import roofline_peaks as rp

  table, golden = golden_jobs[jid]
  cpu = rp.infer_cpu_roofline_peak_flops_and_bw_gbps(table)
  gpu = rp.infer_gpu_roofline_peak_flops_and_bw_gbps(
    table, bw_axis=golden["series"]["gpu_roofline"].get("bw_axis")
  )
  for name, mine, theirs in (
    ("cpu", cpu, golden["series"]["roofline"]["peaks"]),
    ("gpu", gpu, golden["series"]["gpu_roofline"]["peaks"]),
  ):
    for a, b in zip(mine, theirs, strict=True):
      if a is None or b is None:
        assert a is None and b is None, (
          f"jid={jid} {name} peaks {mine} vs {theirs}"
        )
      else:
        rel = abs(float(a) - float(b)) / max(
          abs(float(b)), np.finfo(float).tiny
        )
        _record_deviation("roofline_peaks", name, rel)
        assert rel <= SQL_SUM_RTOL, f"jid={jid} {name} peaks {mine} vs {theirs}"
