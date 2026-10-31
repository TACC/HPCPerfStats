"""
Bokeh builders consume array dicts and reproduce the golden plot JSON.

For every golden job the current builders (`compute_plot_item_for_kind`
over the columnar table) must yield, after Bokeh id normalisation, the same
document as the pandas-path capture: same object tree, same attribute
values, same ``ColumnDataSource`` columns, same unavailable reasons. Floats
compare at ``PLOT_JSON_RTOL`` (1e-5, NaN-equal): the pandas path fed Bokeh
values summed in PostgreSQL ``real`` and read through the float4 text
protocol (plan D34 / D38), so plot data carries float4 rounding that the
columnar float64 path does not reproduce; the pre-Bokeh series test holds
the 1e-9 base tolerance where the float4 oracle applies. Skips without the
corpus unless ``HPCPERFSTATS_METRICS_GOLDEN_REQUIRED=1``.
"""

from __future__ import annotations

import base64
import gzip
import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.columnar.job_table import (
  ColumnarJobTable,
)
from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
  build_summary_series,
)
from hpcperfstats.site.lib.machine.job_plot_artifacts import (
  compute_plot_item_for_kind,
)

_REPO = Path(__file__).resolve().parents[4]
PLOT_KINDS = ("summary_plot", "roofline", "gpu_roofline")
PLOT_JSON_RTOL = 1e-5
# ``ColumnDataSource(DataFrame)`` added the frame's RangeIndex as an
# ``index`` column; the array path has no index and no glyph or tooltip
# reads it (a tooltip reading ``@index`` would still differ in its string).
IGNORED_DATA_COLUMNS = frozenset({"index"})
# Summary panels whose pandas-path values carried the conv-less GPU cache
# scale (plan D37, ``GOLDEN_SCALE_DEFECTS`` in test_columnar_golden.py):
# numbers inside these figures may differ by exactly this factor.
SCALE_DEFECT_FACTOR = 1e9 / 1024**3
SCALE_DEFECT_AXIS_LABELS = frozenset({"GPU HBM BW [GB/s]", "GPU link [GB/s]"})
SCALE_DEFECT_COLUMNS = frozenset({"nv_gpu_mem_bw_gbs", "nv_gpu_link_gbs"})
# ``*_plain`` hover strings are 2-decimal renderings; float4 noise (D34/D38)
# can flip the last digit, so parsed decimals compare at one display unit.
PLAIN_DECIMAL_ATOL = 0.011
# Document fields that legitimately differ between captures.
VOLATILE_DOC_KEYS = frozenset({"version", "target_id", "root_id"})


def _corpus_dir() -> Path | None:
  raw = os.environ.get("HPCPERFSTATS_METRICS_GOLDEN_DIR") or (
    "/data/user/sharrell/hpcperfstats-metrics-golden"
  )
  d = Path(raw)
  if (d / "manifest.json").is_file():
    return d
  if os.environ.get("HPCPERFSTATS_METRICS_GOLDEN_REQUIRED") == "1":
    pytest.fail(f"golden corpus required but absent: {d}")
  return None


def _jids() -> list[str]:
  d = _corpus_dir()
  if d is None:
    return []
  manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
  jobs = manifest.get("jobs", [])
  return [str(j["jid"]) for j in jobs] if isinstance(jobs, list) else list(jobs)


def _load_corpus_module():
  spec = importlib.util.spec_from_file_location(
    "metrics_golden_corpus_bokeh_t",
    _REPO / "scripts" / "metrics_golden_corpus.py",
  )
  mod = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = mod
  spec.loader.exec_module(mod)
  return mod


# -- Bokeh JSON normalisation ------------------------------------------------


def decode_ndarray(node: dict[str, Any]) -> list[Any]:
  """Decode a Bokeh ``ndarray`` node (optionally gzip-wrapped) to a list."""
  array = node["array"]
  if isinstance(array, dict) and array.get("type") == "bytes":
    raw = base64.b64decode(array["data"])
    if raw[:2] == b"\x1f\x8b":
      raw = gzip.decompress(raw)
    arr = np.frombuffer(raw, dtype=np.dtype(node["dtype"]))
    if node.get("order") == "big" and sys.byteorder != "big":
      arr = arr.byteswap()
    return arr.reshape(node.get("shape") or (-1,)).tolist()
  return list(array)


def _index_defs(node: Any, defs: dict[str, Any]) -> None:
  if isinstance(node, dict):
    if node.get("type") == "object" and "id" in node:
      defs.setdefault(node["id"], node)
    for value in node.values():
      _index_defs(value, defs)
  elif isinstance(node, list):
    for value in node:
      _index_defs(value, defs)


def normalize_bokeh_doc(item: dict[str, Any]) -> Any:
  """
  Strip Bokeh ids and expand references so two documents compare by
  structure and values only.
  """
  defs: dict[str, Any] = {}
  _index_defs(item, defs)

  def axis_labels(fig: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for side in ("left", "right", "above", "below"):
      for axis in (fig.get("attributes") or {}).get(side) or []:
        if isinstance(axis, dict) and set(axis) == {"id"}:
          axis = defs.get(axis["id"], {})
        label = ((axis or {}).get("attributes") or {}).get("axis_label")
        if isinstance(label, str):
          labels.add(label)
    return labels

  def collect_ids(node: Any, seen: set[str]) -> None:
    if isinstance(node, dict):
      if set(node) == {"id"}:
        if node["id"] not in seen:
          seen.add(node["id"])
          collect_ids(defs.get(node["id"]), seen)
        return
      if "id" in node:
        seen.add(node["id"])
      for value in node.values():
        collect_ids(value, seen)
    elif isinstance(node, list):
      for value in node:
        collect_ids(value, seen)

  # Every object inside a scale-defect figure (its sources, ranges, help
  # markers) is allowed the D37 factor, also when the GridPlot toolbar
  # references it from outside the figure subtree.
  defect_ids: set[str] = set()
  for obj in defs.values():
    if obj.get("name") == "Figure" and (
      axis_labels(obj) & SCALE_DEFECT_AXIS_LABELS
    ):
      collect_ids(obj, defect_ids)

  def norm(node: Any, stack: tuple[str, ...]) -> Any:
    if isinstance(node, dict):
      keys = set(node)
      if (
        node.get("type") == "object"
        and node.get("id") in defect_ids
        and "$defect:figure" not in stack
      ):
        return {
          "$defect": SCALE_DEFECT_FACTOR,
          "node": norm(node, (*stack, "$defect:figure")),
        }
      if keys == {"id"}:
        ref = defs.get(node["id"])
        if ref is None:
          return {"$unresolved": True}
        if node["id"] in stack:
          return {"$cycle": ref.get("name")}
        return norm(ref, (*stack, node["id"]))
      if node.get("type") == "ndarray" and "array" in node:
        # A list and an ndarray of the same values are the same column to
        # BokehJS; compare them as plain lists.
        return decode_ndarray(node)
      if node.get("type") == "map" and "entries" in node:
        columns = {}
        for k, v in sorted(node["entries"], key=lambda kv: str(kv[0])):
          name = str(k)
          if name in IGNORED_DATA_COLUMNS:
            continue
          value = norm(v, stack)
          # Shared GridPlot hover tools reference the defect panels' sources
          # outside the figure subtree; the column name identifies them.
          if name.removesuffix("_plain") in SCALE_DEFECT_COLUMNS:
            value = {"$defect": SCALE_DEFECT_FACTOR, "node": value}
          columns[name] = value
        return {"$map": columns}
      out = {}
      for key, value in node.items():
        if key == "id" or key in VOLATILE_DOC_KEYS:
          continue
        out[key] = norm(value, stack)
      return out
    if isinstance(node, list):
      return [norm(v, stack) for v in node]
    return node

  return norm(item, ())


def _rel_close(fa: float, fb: float, rtol: float) -> bool:
  if math.isnan(fa) or math.isnan(fb):
    return math.isnan(fa) and math.isnan(fb)
  return fa == fb or abs(fa - fb) <= rtol * max(abs(fa), abs(fb))


def _plain_decimal(text: str) -> float | None:
  try:
    return float(text.replace(",", ""))
  except ValueError:
    return None


def _close(a: Any, b: Any, factor: float | None = None) -> bool:
  """Leaf equality; inside a scale-defect figure the D37 factor is allowed."""
  if isinstance(a, str) and isinstance(b, str):
    pa, pb = _plain_decimal(a), _plain_decimal(b)
    if pa is None or pb is None:
      return a == b
    factors = (1.0, factor, 1.0 / factor) if factor else (1.0,)
    return any(
      _rel_close(pa, pb * f, PLOT_JSON_RTOL)
      or abs(pa - pb * f) <= PLAIN_DECIMAL_ATOL
      for f in factors
    )
  if isinstance(a, float) or isinstance(b, float):
    try:
      fa, fb = float(a), float(b)
    except TypeError, ValueError:
      return False
    if factor:
      return any(
        _rel_close(fa, fb * f, PLOT_JSON_RTOL)
        for f in (1.0, factor, 1.0 / factor)
      )
    return _rel_close(fa, fb, PLOT_JSON_RTOL)
  return a == b


def diff_normalized(
  a: Any, b: Any, path: str = "", factor: float | None = None
) -> list[str]:
  """First differences between two normalised documents (bounded)."""
  out: list[str] = []
  if isinstance(a, dict) and isinstance(b, dict):
    if "$defect" in a and "$defect" in b:
      return diff_normalized(a["node"], b["node"], path, float(a["$defect"]))
    for key in sorted(set(a) | set(b)):
      if key not in a or key not in b:
        out.append(f"{path}/{key}: only in {'golden' if key in b else 'new'}")
        continue
      out.extend(diff_normalized(a[key], b[key], f"{path}/{key}", factor))
      if len(out) >= 20:
        return out
    return out
  if isinstance(a, list) and isinstance(b, list):
    if len(a) != len(b):
      return [f"{path}: list length {len(a)} != {len(b)}"]
    for i, (p, q) in enumerate(zip(a, b, strict=True)):
      out.extend(diff_normalized(p, q, f"{path}[{i}]", factor))
      if len(out) >= 20:
        return out
    return out
  if not _close(a, b, factor):
    out.append(f"{path}: {str(a)[:80]!r} != {str(b)[:80]!r}")
  return out


def test_normalizer_resolves_refs_and_arrays():
  arr = np.array([1.5, float("nan")])
  item = {
    "root_id": "p1",
    "doc": {
      "version": "x",
      "roots": [
        {
          "type": "object",
          "name": "Plot",
          "id": "p1",
          "attributes": {
            "source": {
              "type": "object",
              "name": "ColumnDataSource",
              "id": "s1",
              "attributes": {
                "data": {
                  "type": "map",
                  "entries": [
                    [
                      "y",
                      {
                        "type": "ndarray",
                        "array": {
                          "type": "bytes",
                          "data": base64.b64encode(arr.tobytes()).decode(),
                        },
                        "dtype": "float64",
                        "shape": [2],
                        "order": sys.byteorder,
                      },
                    ]
                  ],
                },
              },
            },
            "again": {"id": "s1"},
          },
        }
      ],
    },
  }
  norm = normalize_bokeh_doc(item)
  root = norm["doc"]["roots"][0]
  assert "id" not in root and "root_id" not in norm
  col = root["attributes"]["source"]["attributes"]["data"]["$map"]["y"]
  assert col[0] == 1.5 and math.isnan(col[1])
  assert (
    diff_normalized(root["attributes"]["again"], root["attributes"]["source"])
    == []
  )
  assert diff_normalized(norm, normalize_bokeh_doc(item)) == []
  entries = item["doc"]["roots"][0]["attributes"]["source"]["attributes"][
    "data"
  ]["entries"]
  entries.append(["index", [0, 1]])
  assert diff_normalized(norm, normalize_bokeh_doc(item)) == []


def test_normalizer_scale_defect_figure_allows_d37_factor_only():
  def fig(value: float, label: str) -> dict[str, Any]:
    return {
      "doc": {
        "roots": [
          {
            "type": "object",
            "name": "Figure",
            "id": "f",
            "attributes": {
              "left": [
                {
                  "type": "object",
                  "name": "LinearAxis",
                  "id": "ax",
                  "attributes": {"axis_label": label},
                }
              ],
              "y": value,
              "y_plain": f"{value:,.2f}",
            },
          }
        ]
      }
    }

  f = SCALE_DEFECT_FACTOR
  gpu_a = normalize_bokeh_doc(fig(10.0, "GPU link [GB/s]"))
  gpu_b = normalize_bokeh_doc(fig(10.0 / f, "GPU link [GB/s]"))
  assert "$defect" in gpu_a["doc"]["roots"][0]
  assert diff_normalized(gpu_a, gpu_b) == []
  gpu_c = normalize_bokeh_doc(fig(10.0 / 2.0, "GPU link [GB/s]"))
  assert diff_normalized(gpu_a, gpu_c) != []
  cpu_a = normalize_bokeh_doc(fig(10.0, "CPU Usage [#cores]"))
  cpu_b = normalize_bokeh_doc(fig(10.0 / f, "CPU Usage [#cores]"))
  assert "$defect" not in cpu_a["doc"]["roots"][0]
  assert diff_normalized(cpu_a, cpu_b) != []
  # Defect-named columns are allowed the factor anywhere in the document.
  src = lambda col, val: {  # noqa: E731
    "doc": {
      "roots": [
        {
          "type": "object",
          "name": "ColumnDataSource",
          "id": "s",
          "attributes": {
            "data": {"type": "map", "entries": [[col, [val]], ["x", [1.0]]]}
          },
        }
      ]
    }
  }
  assert (
    diff_normalized(
      normalize_bokeh_doc(src("nv_gpu_link_gbs", 10.0)),
      normalize_bokeh_doc(src("nv_gpu_link_gbs", 10.0 / f)),
    )
    == []
  )
  assert (
    diff_normalized(
      normalize_bokeh_doc(src("cpu", 10.0)),
      normalize_bokeh_doc(src("cpu", 10.0 / f)),
    )
    != []
  )
  # Plain-decimal hover strings tolerate one display unit of float4 noise.
  assert _close("4,646.97", "4,646.96")
  assert not _close("4,646.97", "4,647.10")


@pytest.fixture(scope="module")
def golden_tables():
  d = _corpus_dir()
  if d is None:
    pytest.skip("golden corpus absent (HPCPERFSTATS_METRICS_GOLDEN_DIR)")
  corpus = _load_corpus_module()
  out = {}
  for jid in _jids():
    with gzip.open(
      d / "jobs" / f"{jid}.golden.json.gz", "rt", encoding="utf-8"
    ) as fh:
      golden = json.load(fh)
    frame = corpus.load_frame_npz(d / "jobs" / f"{jid}.frame.npz")
    out[jid] = (
      ColumnarJobTable.from_golden(jid, frame, golden["jid_table"]),
      golden,
    )
  return out


def _unwrap(node: Any) -> Any:
  return node["node"] if isinstance(node, dict) and "$defect" in node else node


def panel_label(fig_norm: Any) -> str | None:
  """Y-axis label of a normalised Figure (the Summary subplot identity)."""
  fig = _unwrap(fig_norm)
  for axis in (fig.get("attributes") or {}).get("left") or []:
    label = (_unwrap(axis).get("attributes") or {}).get("axis_label")
    if isinstance(label, str):
      return label
  return None


def grid_panels(doc_norm: Any, names: list[str]) -> dict[str, Any]:
  """
  ``metric name -> normalised Figure`` for a Summary GridPlot document.

  Panels follow the render-spec order (one subplot per spec), so the spec
  names key them; labels alone repeat (Intel and AMD DRAM bandwidth share
  ``CPU DRAMBW[GB/s]``).
  """
  root = doc_norm["doc"]["roots"][0]
  children = root["attributes"]["children"]
  assert len(children) >= len(names), (len(children), names)
  # Hardware-error-rate figures follow the render specs; key them by
  # position so both documents pair them the same way.
  names = list(names) + [
    f"$hardware_error_{k}" for k in range(len(children) - len(names))
  ]
  out: dict[str, Any] = {}
  for entry, name in zip(children, names, strict=True):
    fig = entry[0] if isinstance(entry, list) else entry
    assert panel_label(fig) is not None, "summary panel without a y-axis label"
    assert name not in out, f"duplicate summary panel {name}"
    out[name] = fig
  return out


def d36_extra_panel_explained(
  metric: str, built_cols: dict[str, np.ndarray], golden_cols: set[str]
) -> bool:
  """
  Mirror of ``test_columnar_golden``'s D36 rule: a column the pandas path
  dropped (all-NaN at an all-NULL sample in its SQL chunk) exists here with
  ``0`` at that sample; ``freq`` exists when the cycle counters are absent.
  """
  if metric not in built_cols:
    return False
  b = built_cols[metric]
  return bool((b == 0.0).any()) or (
    metric == "freq"
    and ("acycles" not in golden_cols or "mcycles" not in golden_cols)
  )


def d36_missing_panel_explained(metric: str, golden_cols: set[str]) -> bool:
  cycles = (
    ("amd_acycles", "amd_mcycles")
    if metric == "amd_instr"
    else ("acycles", "mcycles")
  )
  return metric in ("instr", "amd_instr") and not any(
    c in golden_cols for c in cycles
  )


def d36_coalesce_metrics(
  summary: Any, golden_summary: dict[str, Any]
) -> set[str]:
  """
  Metrics with D36 coalesce-zero rows: pandas NaN where the SQL chunk
  dropped an all-NULL group while the columnar build keeps ``0`` there
  (same mask as ``test_columnar_golden``). Their panels differ in NaN
  gaps and in the hover rows Bokeh keeps, so only their structure is
  compared here; the series test compares their values with those rows
  removed.
  """
  gcols = golden_summary.get("columns") or {}
  if not gcols:
    return set()
  g_index = {
    (str(h), int(t)): i
    for i, (h, t) in enumerate(zip(gcols["host"], gcols["time"], strict=True))
  }
  keys = list(
    zip(summary.frame.host_names(), summary.frame.time_us.tolist(), strict=True)
  )
  if set(keys) != set(g_index):
    return set()
  order = np.array([g_index[k] for k in keys])
  out: set[str] = set()
  for col, b in summary.frame.columns.items():
    if col not in gcols:
      continue
    g = np.array(
      [np.nan if v is None else float(v) for v in gcols[col]], dtype=float
    )[order]
    if (np.isnan(g) & ~np.isnan(b) & (b == 0.0)).any():
      out.add(col)
  return out


def d36_ibbw_source_switched(
  jt: ColumnarJobTable, summary: Any, golden_summary: dict[str, Any]
) -> bool:
  """
  D36 cascade (same rule as the series test): the pandas path dropped the
  InfiniBand column and filled ``ibbw`` from Omni-Path; the columnar build
  keeps InfiniBand. True when the golden ``ibbw`` matches the OPA series.
  """
  from hpcperfstats.analysis.metrics.lib.columnar import summary_series as ss

  gcols = golden_summary.get("columns") or {}
  if "ibbw" not in gcols or "ibbw" not in summary.frame.columns:
    return False
  g_index = {
    (str(h), int(t)): i
    for i, (h, t) in enumerate(zip(gcols["host"], gcols["time"], strict=True))
  }
  keys = list(
    zip(summary.frame.host_names(), summary.frame.time_us.tolist(), strict=True)
  )
  if set(keys) != set(g_index):
    return False
  order = np.array([g_index[k] for k in keys])
  g = np.array(
    [np.nan if v is None else float(v) for v in gcols["ibbw"]], dtype=float
  )[order]
  b = summary.frame.columns["ibbw"]
  # Same row filter as the series test: coalesce-zero rows are judged by
  # the D36 rule, not by the source switch.
  keep = ~(np.isnan(g) & ~np.isnan(b) & (b == 0.0))
  if _max_rel_dev(b[keep], g[keep]) <= 1e-5:
    return False
  opa = ss.agg_if_feasible(
    jt, "opa", "arc", ["PortXmitData", "PortRcvData"], 1.0 / 125000.0
  )
  if opa.empty:
    return False
  return _max_rel_dev(summary.frame.join_values(opa)[keep], g[keep]) <= 1e-5


def _max_rel_dev(a: np.ndarray, b: np.ndarray) -> float:
  a_nan, b_nan = np.isnan(a), np.isnan(b)
  keep = ~(a_nan & b_nan)
  if not np.array_equal(a_nan[keep], b_nan[keep]):
    return math.inf
  fin = keep & ~a_nan
  if not fin.any():
    return 0.0
  denom = np.maximum(np.abs(a[fin]), np.abs(b[fin]))
  denom[denom == 0.0] = 1.0
  return float(np.max(np.abs(a[fin] - b[fin]) / denom))


def strip_data(node: Any) -> Any:
  """Drop ``ColumnDataSource.data`` maps so only structure is compared."""
  if isinstance(node, dict):
    return {
      k: (
        "$data"
        if k == "data" and isinstance(v, dict) and "$map" in v
        else strip_data(v)
      )
      for k, v in node.items()
    }
  if isinstance(node, list):
    return [strip_data(v) for v in node]
  return node


def compare_summary_grid(
  jt: ColumnarJobTable, golden: dict[str, Any], got: Any, want: Any
) -> list[str]:
  """
  Panel-wise comparison of two Summary GridPlot documents.

  Panels pair by y-axis label. A panel only in the new document must be a
  D36-explained extra column (plan decision D36, same rule as the series
  test); a panel only in the golden must be the D36 ``instr`` case. The
  GridPlot's own toolbar merges the children's tools and is not compared
  separately; the remaining root attributes are.
  """
  summary = build_summary_series(jt)
  golden_summary = golden["series"]["summary"]
  golden_cols = {
    c
    for c in (golden_summary.get("columns") or {})
    if c not in ("index", "host", "time")
  }
  got_panels = grid_panels(got, [name for name, _l, _t in summary.render_specs])
  want_panels = grid_panels(
    want, [str(spec[0]) for spec in golden_summary["render_specs"]]
  )
  coalesce = d36_coalesce_metrics(summary, golden_summary)
  if d36_ibbw_source_switched(jt, summary, golden_summary):
    coalesce.add("ibbw")
  problems: list[str] = []
  for metric in sorted(set(got_panels) - set(want_panels)):
    if not d36_extra_panel_explained(
      metric, summary.frame.columns, golden_cols
    ):
      problems.append(f"unexplained extra panel {metric}")
  for metric in sorted(set(want_panels) - set(got_panels)):
    if not d36_missing_panel_explained(metric, golden_cols):
      problems.append(f"panel missing in columnar build: {metric}")
  for metric in sorted(set(got_panels) & set(want_panels)):
    a, b = got_panels[metric], want_panels[metric]
    if metric in coalesce:
      a, b = strip_data(a), strip_data(b)
      # y_range and the help-marker Label position follow the data.
      for side in (a, b):
        attrs = _unwrap(side)["attributes"]
        attrs.pop("y_range", None)
        for item in attrs.get("center") or []:
          if _unwrap(item).get("name") == "Label":
            _unwrap(item)["attributes"].pop("y", None)
    problems.extend(diff_normalized(a, b, f"/panel[{metric}]"))
    if len(problems) >= 20:
      break
  root_got = {
    k: v
    for k, v in got["doc"]["roots"][0]["attributes"].items()
    if k not in ("children", "toolbar")
  }
  root_want = {
    k: v
    for k, v in want["doc"]["roots"][0]["attributes"].items()
    if k not in ("children", "toolbar")
  }
  problems.extend(diff_normalized(root_got, root_want, "/root"))
  return problems


@pytest.mark.parametrize("kind", PLOT_KINDS)
@pytest.mark.parametrize("jid", _jids() or ["__no_corpus__"])
def test_plot_json_matches_golden_after_id_normalization(
  golden_tables, jid, kind
):
  jt, golden = golden_tables[jid]
  stored = (golden.get("plots") or {}).get(kind, {}).get("normal")
  if stored is None:
    pytest.skip(f"golden has no {kind} artifact for {jid}")
  want_item = stored["item"].get("plot_item")
  want_reason = stored["item"].get("unavailable_reason")
  got_item, got_reason, _bw = compute_plot_item_for_kind(jt, kind, False)
  assert (got_item is None) == (want_item is None), (got_reason, want_reason)
  assert got_reason == want_reason
  if want_item is None:
    return
  got, want = normalize_bokeh_doc(got_item), normalize_bokeh_doc(want_item)
  if kind == "summary_plot":
    diffs = compare_summary_grid(jt, golden, got, want)
  else:
    diffs = diff_normalized(got, want)
  assert diffs == [], "\n".join(diffs[:20])
