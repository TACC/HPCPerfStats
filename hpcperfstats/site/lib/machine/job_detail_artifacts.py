"""
Persist and load gzip-compressed derived payloads for job_detail/type_detail.

Attributes:
  APP_DETAIL_ARTIFACT_SCHEMA_VERSION: Attribute.
  ARTIFACT_KIND_JOB_DETAIL: Attribute.
  ARTIFACT_KIND_MULTIPRECISION_MIX: Attribute.
  ARTIFACT_KIND_TYPE_DETAIL: Attribute.
  PAYLOAD_ENCODING_GZIP_JSON: Attribute.
  _CPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON: Attribute.
  _CPU_PRECISION_LABEL_ORDER: Attribute.
  _CPU_PRECISION_METRIC_TO_LABEL: Attribute.
  _FSIO_FINGERPRINT_METRIC_NAMES: Attribute.
  _GPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON: Attribute.
  _GPU_PRECISION_LABEL_ORDER: Attribute.
  _GPU_PRECISION_METRIC_TO_LABEL: Attribute.
  _GPU_TENSOR_SPLIT_METRICS: Attribute.
  _MULTIPRECISION_PIE_RADIUS: Attribute.
  _MULTIPRECISION_PLOT_RANGE: Attribute.
  logger: Attribute.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from math import pi
from datetime import date, datetime
from typing import Any, Dict, Optional

from django.db import connection
from django.db.models.base import ModelState
from bokeh.embed import json_item
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.palettes import d3
from bokeh.plotting import figure
from bokeh.transform import factor_cmap

from hpcperfstats.site.lib.machine.job_plot_artifacts import _utc_iso_for_plot_fingerprint
from hpcperfstats.analysis.metrics.lib.plot.bokeh_job_detail_help_marker import (
    add_job_detail_bokeh_help_marker,
)
from hpcperfstats.analysis.metrics.lib.plot.job_detail_bokeh_plot_descriptions import (
    description_for_job_detail_bokeh_plot,
    researcher_use_for_job_detail_bokeh_plot,
)
from hpcperfstats.analysis.metrics.lib.job_detail_fsio import (
    extend_fsio_payload_lists_with_peaks,
    fsio_job_detail_catalog,
)
from hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary import (
    gpu_agg_rows_for_job_window,
    gpu_count_total_for_job_window,
    gpu_inventory_for_job_window,
    reduce_gpu_agg_to_util_stats,
)
import hpcperfstats.analysis.metrics.lib.gen.jid_table as jid_table
import hpcperfstats.analysis.metrics.lib.plot as plots

from .models import job_data, job_detail_artifact

logger = logging.getLogger(__name__)

PAYLOAD_ENCODING_GZIP_JSON = "gzip_json"
ARTIFACT_KIND_JOB_DETAIL = "job_detail"
ARTIFACT_KIND_TYPE_DETAIL = "type_detail"
ARTIFACT_KIND_MULTIPRECISION_MIX = "multiprecision_mix"

# Staff-visible unavailable reasons (API detail); align phrasing with summary/roofline plot builders.
_CPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON = (
    "Missing CPU busy-ops mix metrics in job metrics "
    "(need positive avg_flops64b / avg_flops32b / avg_arm_int16_ops / "
    "avg_arm_int8_ops shares)."
)
_GPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON = (
    "Missing GPU precision-width mix metrics in job metrics "
    "(need positive avg_*_active shares)."
)
APP_DETAIL_ARTIFACT_SCHEMA_VERSION = 11

_FSIO_FINGERPRINT_METRIC_NAMES = tuple(
    sorted(name for name, _t, _u in fsio_job_detail_catalog())
)


def _fsio_metrics_fingerprint_map(job: job_data) -> Dict[str, str]:
  """
  Stable text map of detail_fsio_* values for detail artifact fingerprints.
  
  Args:
    job (job_data): Job.
  
  Returns:
    Dict[str, str]: Dict[str, str] produced by this call.
  
  Examples:
    >>> _fsio_metrics_fingerprint_map(None)  # doctest: +SKIP
  """
  by_m: Dict[str, Any] = {}
  try:
    for row in getattr(job, "metrics_data_set").all():
      by_m[str(row.metric)] = row.value
  except Exception:
    by_m = {}
  out: Dict[str, str] = {}
  for name in _FSIO_FINGERPRINT_METRIC_NAMES:
    v = by_m.get(name)
    if v is None:
      out[name] = ""
    else:
      try:
        out[name] = f"{float(v):.6f}"
      except (TypeError, ValueError):
        out[name] = ""
  return out


def _compress_payload(payload: Dict[str, Any]) -> tuple[bytes, str]:
  """
  Internal helper to handle compress payload.
  
  Args:
    payload (Dict[str, Any]): Mapping for payload.
  
  Returns:
    tuple[bytes, str]: tuple[bytes, str] produced by this call.
  
  Examples:
    >>> _compress_payload({})  # doctest: +SKIP
  """
  raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
  return gzip.compress(raw, compresslevel=6), PAYLOAD_ENCODING_GZIP_JSON


def _decompress_payload(
  payload_compressed: bytes,
  payload_encoding: str,
) -> Dict[str, Any]:
  """
  Internal helper to handle decompress payload.
  
  Args:
    payload_compressed (bytes): Payload compressed.
    payload_encoding (str): String for payload encoding.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Raises:
    ValueError: Raised when ``_decompress_payload`` hits a ``ValueError``
    failure path.
  
  Examples:
    >>> _decompress_payload(None, "x")  # doctest: +SKIP
  """
  if payload_encoding != PAYLOAD_ENCODING_GZIP_JSON:
    raise ValueError("Unsupported detail payload encoding: {!r}".format(payload_encoding))
  return json.loads(gzip.decompress(payload_compressed).decode("utf-8"))


def compute_detail_input_fingerprint(job: job_data) -> str:
  """
  Compute the detail input fingerprint.
  
  Args:
    job (job_data): Job.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> compute_detail_input_fingerprint(None)  # doctest: +SKIP
  """
  job_state = getattr(job, "_state", None)
  if (
      connection.vendor == "postgresql"
      and isinstance(job_state, ModelState)
      and not job_state.adding
      and job.pk is not None
  ):
    from hpcperfstats.site.lib.machine.artifact_readiness_expressions import (
        DetailArtifactInputFingerprintHex,
    )

    sql_fp = (
        job_data.objects.filter(pk=job.pk)
        .annotate(fp=DetailArtifactInputFingerprintHex())
        .values_list("fp", flat=True)
        .first()
    )
    if sql_fp is not None:
      return sql_fp

  def _safe_text(v: Any) -> str:
    """
    Internal helper to handle safe text.
    
    Args:
      v (Any): V passed to this helper.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> _safe_text(None)  # doctest: +SKIP
    """
    try:
      if v is None:
        return ""
      if isinstance(v, (str, int, float, bool)):
        return str(v)
      if isinstance(v, datetime):
        return _utc_iso_for_plot_fingerprint(v)
      if isinstance(v, date):
        return v.isoformat()
      if hasattr(v, "isoformat"):
        maybe = v.isoformat()
        return maybe if isinstance(maybe, str) else str(maybe)
      return str(v)
    except Exception:
      return ""

  payload = {
      "artifact_schema": APP_DETAIL_ARTIFACT_SCHEMA_VERSION,
      "end_time": _safe_text(getattr(job, "end_time", "")),
      "fsio_metrics": _fsio_metrics_fingerprint_map(job),
      "jid": _safe_text(getattr(job, "jid", "")),
      "metrics_distinct_time_count": _safe_text(getattr(job, "metrics_distinct_time_count", "")),
      "start_time": _safe_text(getattr(job, "start_time", "")),
  }
  canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upsert_job_detail_artifact(
  jid: str,
  artifact_kind: str,
  artifact_scope: str,
  input_fingerprint: str,
  payload: Dict[str, Any],
) -> None:
  """
  Upsert job detail artifact.
  
  Args:
    jid (str): String for jid.
    artifact_kind (str): String for artifact kind.
    artifact_scope (str): String for artifact scope.
    input_fingerprint (str): String for input fingerprint.
    payload (Dict[str, Any]): Mapping for payload.
  
  Returns:
    None
  
  Examples:
    >>> upsert_job_detail_artifact("x", "x", "x", "x", {})  # doctest: +SKIP
  """
  compressed, encoding = _compress_payload(payload)
  job_detail_artifact.objects.bulk_create(
      [
          job_detail_artifact(
              jid_id=jid,
              artifact_kind=artifact_kind,
              artifact_scope=artifact_scope,
              payload_compressed=compressed,
              payload_encoding=encoding,
              input_fingerprint=input_fingerprint,
              artifact_schema=APP_DETAIL_ARTIFACT_SCHEMA_VERSION,
          )
      ],
      update_conflicts=True,
      update_fields=[
          "payload_compressed",
          "payload_encoding",
          "input_fingerprint",
          "artifact_schema",
      ],
      unique_fields=["jid", "artifact_kind", "artifact_scope"],
  )


def load_job_detail_artifact(
  jid: str,
  artifact_kind: str,
  artifact_scope: str,
  input_fingerprint: str,
) -> Optional[Dict[str, Any]]:
  """
  Load the job detail artifact.
  
  Args:
    jid (str): String for jid.
    artifact_kind (str): String for artifact kind.
    artifact_scope (str): String for artifact scope.
    input_fingerprint (str): String for input fingerprint.
  
  Returns:
    Optional[Dict[str, Any]]: Optional[Dict[str, Any]] — the result, or None
    when unavailable.
  
  Examples:
    >>> load_job_detail_artifact("x", "x", "x", "x")  # doctest: +SKIP
  """
  row = (
      job_detail_artifact.objects.filter(
          jid_id=jid,
          artifact_kind=artifact_kind,
          artifact_scope=artifact_scope,
      )
      .only("payload_compressed", "payload_encoding", "input_fingerprint")
      .first()
  )
  if not row or row.input_fingerprint != input_fingerprint:
    return None
  try:
    return _decompress_payload(bytes(row.payload_compressed), row.payload_encoding)
  except Exception:
    logger.warning(
        "Failed to decode job_detail_artifact jid=%s kind=%s scope=%s",
        jid,
        artifact_kind,
        artifact_scope,
        exc_info=True,
    )
    return None


def _gpu_detail_from_jid_table(jt: Any) -> Dict[str, Any]:
  """
  Internal helper to handle gpu detail from job id table.
  
  Args:
    jt (Any): Jt passed to this helper.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> _gpu_detail_from_jid_table(None)  # doctest: +SKIP
  """
  rows = gpu_agg_rows_for_job_window(jt)
  inventory = gpu_inventory_for_job_window(jt)
  if not rows and not inventory:
    return {
        "gpu_active": None,
        "gpu_utilization_max": None,
        "gpu_utilization_mean": None,
        "gpu_count": None,
        "gpu_inventory": [],
    }
  active, vmax, vmean = reduce_gpu_agg_to_util_stats(rows)
  return {
      "gpu_active": active,
      "gpu_utilization_max": vmax,
      "gpu_utilization_mean": vmean,
      "gpu_count": gpu_count_total_for_job_window(jt),
      "gpu_inventory": inventory,
  }


def _metric_value_map(job: job_data) -> Dict[str, Optional[float]]:
  """
  Internal helper to handle metric value map.
  
  Args:
    job (job_data): Job.
  
  Returns:
    Dict[str, Optional[float]]: Dict[str, Optional[float]] produced by this
    call.
  
  Examples:
    >>> _metric_value_map(None)  # doctest: +SKIP
  """
  out: Dict[str, Optional[float]] = {}
  for row in getattr(job, "metrics_data_set").all():
    out[row.metric] = None if row.value is None else float(row.value)
  return out


def _load_existing_type_detail_scope_set(
  jid: str,
  input_fingerprint: str,
  type_names: list[str],
) -> set[str]:
  """
  Internal helper to load the existing type detail scope set.
  
  Args:
    jid (str): String for jid.
    input_fingerprint (str): String for input fingerprint.
    type_names (list[str]): Sequence for type names.
  
  Returns:
    set[str]: set[str] produced by this call.
  
  Examples:
    >>> _load_existing_type_detail_scope_set("x", "x", [])  # doctest: +SKIP
  """
  if not type_names:
    return set()
  rows = (
      job_detail_artifact.objects.filter(
          jid_id=jid,
          artifact_kind=ARTIFACT_KIND_TYPE_DETAIL,
          artifact_scope__in=type_names,
          input_fingerprint=input_fingerprint,
      )
      .values_list("artifact_scope", "payload_compressed")
  )
  return {scope for scope, payload in rows if payload}


def _gpu_detail_from_metric_values(
  metric_values: Dict[str, Optional[float]],
) -> Optional[Dict[str, Any]]:
  """
  Internal helper to handle gpu detail from metric values.
  
  Args:
    metric_values (Dict[str, Optional[float]]): Mapping for metric values.
  
  Returns:
    Optional[Dict[str, Any]]: Optional[Dict[str, Any]] — the result, or None
    when unavailable.
  
  Examples:
    >>> _gpu_detail_from_metric_values({})  # doctest: +SKIP
  """
  required = (
      "detail_gpu_active",
      "detail_gpu_util_max",
      "detail_gpu_util_mean",
      "detail_gpu_count",
  )
  if not all(k in metric_values for k in required):
    return None
  active = metric_values.get("detail_gpu_active")
  util_max = metric_values.get("detail_gpu_util_max")
  util_mean = metric_values.get("detail_gpu_util_mean")
  count = metric_values.get("detail_gpu_count")
  # All-null metric dict must not block host_data fallback (Resources visibility).
  if active is None and util_max is None and util_mean is None and count is None:
    return None
  return {
      "gpu_active": None if active is None else int(active),
      "gpu_utilization_max": util_max,
      "gpu_utilization_mean": util_mean,
      "gpu_count": None if count is None else int(count),
      # Inventory always comes from host_data (per-device); filled at assemble time.
      "gpu_inventory": [],
  }


_CPU_PRECISION_METRIC_TO_LABEL = {
    "avg_flops64b": "FP64",
    "avg_flops32b": "FP32",
    "avg_arm_int16_ops": "INT16",
    "avg_arm_int8_ops": "INT8",
}

_GPU_TENSOR_SPLIT_METRICS = (
    "avg_tensor_imma_active",
    "avg_tensor_hmma_active",
    "avg_tensor_dfma_active",
)

_GPU_PRECISION_METRIC_TO_LABEL = {
    "avg_tensor_imma_active": "Tensor IMMA (INT8/INT4)",
    "avg_tensor_hmma_active": "Tensor HMMA (FP16/BF16)",
    "avg_tensor_dfma_active": "Tensor DFMA (FP64)",
    "avg_tensor_active": "Tensor",
    "avg_fp16_active": "FP16",
    "avg_fp32_active": "FP32",
    "avg_fp64_active": "FP64",
}

_CPU_PRECISION_LABEL_ORDER = ("FP64", "FP32", "INT16", "INT8")
_GPU_PRECISION_LABEL_ORDER = (
    "Tensor IMMA (INT8/INT4)",
    "Tensor HMMA (FP16/BF16)",
    "Tensor DFMA (FP64)",
    "Tensor",
    "FP16",
    "FP32",
    "FP64",
)

# Inset pie; legend is laid out *below* the figure (not inside the plot frame).
_MULTIPRECISION_PIE_RADIUS = 0.78
_MULTIPRECISION_PLOT_RANGE = 1.05


def _ordered_precision_labels(
  labels: list[str],
  label_order: tuple[str, ...],
) -> list[str]:
  """
  Internal helper to handle ordered precision labels.
  
  Args:
    labels (list[str]): Sequence for labels.
    label_order (tuple[str, ...]): Sequence for label order.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> _ordered_precision_labels([], [])  # doctest: +SKIP
  """
  order_idx = {name: idx for idx, name in enumerate(label_order)}
  return sorted(labels, key=lambda name: order_idx.get(name, len(label_order)))


def _category10_palette_for_factors(factors: list[str]) -> list[str]:
  """
  Internal helper to handle category10 palette for factors.
  
  Args:
    factors (list[str]): Sequence for factors.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> _category10_palette_for_factors([])  # doctest: +SKIP
  """
  base = d3["Category10"][10]
  return [base[i % len(base)] for i in range(len(factors))]


def _precision_mix_from_metric_values(
  metric_values: Dict[str, Optional[float]],
  metric_to_label: Dict[str, str],
  *,
  skip_metrics: Optional[set[str]] = None,
) -> Dict[str, float]:
  """
  Internal helper to handle precision mix from metric values.
  
  Args:
    metric_values (Dict[str, Optional[float]]): Mapping for metric values.
    metric_to_label (Dict[str, str]): Mapping for metric to label.
    skip_metrics (Optional[set[str]]): Skip metrics, or None when absent.
  
  Returns:
    Dict[str, float]: Dict[str, float] produced by this call.
  
  Examples:
    >>> _precision_mix_from_metric_values({}, {}, None)  # doctest: +SKIP
  """
  skip = skip_metrics or set()
  out: Dict[str, float] = {}
  for metric_name, label in metric_to_label.items():
    if metric_name in skip or metric_name not in metric_values:
      continue
    try:
      value = float(metric_values.get(metric_name))
    except (TypeError, ValueError):
      continue
    if value > 0.0:
      out[label] = value
  return out


def _gpu_precision_mix_from_metric_values(
  metric_values: Dict[str, Optional[float]],
) -> Dict[str, float]:
  """
  Prefer tensor IMMA/HMMA/DFMA splits over lumped ``avg_tensor_active``.
  
  Args:
    metric_values (Dict[str, Optional[float]]): Mapping for metric values.
  
  Returns:
    Dict[str, float]: Dict[str, float] produced by this call.
  
  Examples:
    >>> _gpu_precision_mix_from_metric_values({})  # doctest: +SKIP
  """
  skip: set[str] = set()
  for split_metric in _GPU_TENSOR_SPLIT_METRICS:
    try:
      if float(metric_values.get(split_metric) or 0.0) > 0.0:
        skip.add("avg_tensor_active")
        break
    except (TypeError, ValueError):
      continue
  return _precision_mix_from_metric_values(
      metric_values, _GPU_PRECISION_METRIC_TO_LABEL, skip_metrics=skip
  )


def _pie_item_from_precision_mix(
  *,
  precision_mix: Dict[str, float],
  title: str,
  empty_reason: str,
  help_plot_key: str,
  label_order: tuple[str, ...],
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
  """
  Internal helper to handle pie item from precision mix.
  
  Args:
    precision_mix (Dict[str, float]): Mapping for precision mix.
    title (str): String for title.
    empty_reason (str): String for empty reason.
    help_plot_key (str): String for help plot key.
    label_order (tuple[str, ...]): Sequence for label order.
  
  Returns:
    tuple[Optional[Dict[str, Any]], Optional[str]]: tuple[Optional[Dict[str,
    Any]], Optional[str]] produced by this call.
  
  Examples:
    >>> _pie_item_from_precision_mix({}, "x", "x", "x", [])  # doctest: +SKIP
  """
  if not precision_mix:
    return None, empty_reason
  total = sum(float(v) for v in precision_mix.values() if float(v) > 0.0)
  if total <= 0.0:
    return None, empty_reason
  labels = _ordered_precision_labels(list(precision_mix.keys()), label_order)
  values = [float(precision_mix[label]) for label in labels]
  shares = [100.0 * value / total for value in values]
  palette = _category10_palette_for_factors(labels)
  starts = []
  ends = []
  theta = 0.0
  for value in values:
    frac = value / total
    starts.append(theta)
    theta += (2.0 * pi) * frac
    ends.append(theta)
  source = ColumnDataSource(
      data={
          "label": labels,
          "value": values,
          "share": shares,
          "start": starts,
          "end": ends,
      }
  )
  plot_span = _MULTIPRECISION_PLOT_RANGE
  del title  # SPA section h3 is the user-facing title; Bokeh title clipped wedges.
  # Title cleared: SPA renders the section h3; Bokeh title duplicated and clipped wedges.
  p = figure(
      title="",
      height=360,
      width=360,
      toolbar_location=None,
      tools="hover",
      x_range=(-plot_span, plot_span),
      y_range=(-plot_span, plot_span),
      min_border_top=16,
      min_border_bottom=16,
      min_border_left=16,
      min_border_right=16,
      match_aspect=True,
      sizing_mode="fixed",
  )
  p.axis.visible = False
  p.grid.visible = False
  p.outline_line_color = None
  p.wedge(
      x=0,
      y=0,
      radius=_MULTIPRECISION_PIE_RADIUS,
      start_angle="start",
      end_angle="end",
      source=source,
      legend_field="label",
      line_color="white",
      fill_alpha=0.9,
      color=factor_cmap("label", palette=palette, factors=labels),
  )
  hover = p.select_one(HoverTool)
  if hover is not None:
    hover.tooltips = [
        ("Width", "@label"),
        ("Share of busy (%)", "@share{0.00}"),
    ]
  # Legend outside the plot frame so long GPU labels are not clipped by wedges.
  if p.legend:
    leg = p.legend[0]
    leg.orientation = "horizontal"
    leg.location = "center"
    leg.label_text_font_size = "8pt"
    p.add_layout(leg, "below")
  add_job_detail_bokeh_help_marker(
      p,
      description_for_job_detail_bokeh_plot(help_plot_key),
      researcher_use_for_job_detail_bokeh_plot(help_plot_key),
  )
  return json_item(p), None


def _multiprecision_mix_payload(
  metric_values: Dict[str, Optional[float]],
) -> Dict[str, Any]:
  """
  Internal helper to handle multiprecision mix payload.
  
  Args:
    metric_values (Dict[str, Optional[float]]): Mapping for metric values.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> _multiprecision_mix_payload({})  # doctest: +SKIP
  """
  cpu_mix = _precision_mix_from_metric_values(
      metric_values, _CPU_PRECISION_METRIC_TO_LABEL
  )
  gpu_mix = _gpu_precision_mix_from_metric_values(metric_values)
  cpu_plot_item, cpu_reason = _pie_item_from_precision_mix(
      precision_mix=cpu_mix,
      title="CPU Multiprecision Mix",
      empty_reason=_CPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON,
      help_plot_key="jobDetailPlot_multiprecision_cpu",
      label_order=_CPU_PRECISION_LABEL_ORDER,
  )
  gpu_plot_item, gpu_reason = _pie_item_from_precision_mix(
      precision_mix=gpu_mix,
      title="GPU Multiprecision Mix",
      empty_reason=_GPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON,
      help_plot_key="jobDetailPlot_multiprecision_gpu",
      label_order=_GPU_PRECISION_LABEL_ORDER,
  )
  return {
      "cpu_plot_item": cpu_plot_item,
      "cpu_unavailable_reason": cpu_reason,
      "gpu_plot_item": gpu_plot_item,
      "gpu_unavailable_reason": gpu_reason,
  }


def _fsio_from_metric_values(
  metric_values: Dict[str, Optional[float]],
) -> tuple[Optional[Dict[str, Any]], bool]:
  """
  Build dual NFS+Lustre fsio dict from metrics.
  
  Returns ``({}, False)`` when catalog keys exist but all values are null so
  host_data fallback can still run. Returns ``(None, False)`` when no FSIO
  catalog keys are present in ``metric_values``.
  
  Args:
    metric_values (Dict[str, Optional[float]]): Mapping for metric values.
  
  Returns:
    tuple[Optional[Dict[str, Any]], bool]: tuple[Optional[Dict[str, Any]],
    bool] produced by this call.
  
  Examples:
    >>> _fsio_from_metric_values({})  # doctest: +SKIP
  """
  fsio_metrics = {name for name, _t, _u in fsio_job_detail_catalog()}
  if not fsio_metrics.intersection(metric_values.keys()):
    return None, False
  out: Dict[str, Any] = {}
  llite_read = metric_values.get("detail_fsio_llite_read_mb")
  llite_write = metric_values.get("detail_fsio_llite_write_mb")
  nfs_read = metric_values.get("detail_fsio_nfs_read_mb")
  nfs_write = metric_values.get("detail_fsio_nfs_write_mb")
  llite_peak_mb = metric_values.get("detail_fsio_llite_peak_mb_s")
  llite_peak_iops = metric_values.get("detail_fsio_llite_peak_iops")
  nfs_peak_mb = metric_values.get("detail_fsio_nfs_peak_mb_s")
  nfs_peak_iops = metric_values.get("detail_fsio_nfs_peak_iops")
  if llite_read is not None or llite_write is not None:
    out["llite"] = [
        float(llite_read or 0.0),
        float(llite_write or 0.0),
        llite_peak_mb,
        llite_peak_iops,
    ]
  if nfs_read is not None or nfs_write is not None:
    out["nfs"] = [
        float(nfs_read or 0.0),
        float(nfs_write or 0.0),
        nfs_peak_mb,
        nfs_peak_iops,
    ]
  if not out:
    # Catalog rows present but all null — allow host_data fallback.
    return {}, False
  return out, True


def persist_job_detail_artifacts_for_jid(
  jid: str,
  context: Optional[Dict[str, Any]] = None,
) -> None:
  """
  Prewarm derived payloads for user-facing API paths.
  
  Args:
    jid (str): String for jid.
    context (Optional[Dict[str, Any]]): Context, or None when absent.
  
  Returns:
    None
  
  Examples:
    >>> persist_job_detail_artifacts_for_jid("x", None)  # doctest: +SKIP
  """
  shared = context if isinstance(context, dict) else {}
  telemetry = shared.get("_telemetry") if isinstance(shared.get("_telemetry"), dict) else None
  job = shared.get("job")
  if job is None:
    job = job_data.objects.filter(jid=jid).prefetch_related("metrics_data_set").first()
    shared["job"] = job
  if not job:
    return
  jt = shared.get("jt")
  if jt is None:
    jt = jid_table.jid_table(jid)
    shared["jt"] = jt
  fingerprint = shared.get("detail_fingerprint")
  if fingerprint is None:
    fingerprint = compute_detail_input_fingerprint(job)
    shared["detail_fingerprint"] = fingerprint

  schema = getattr(job, "host_data_schema_json", None)
  if not isinstance(schema, dict) or not schema:
    schema = jt.schema or {}
  existing_type_detail_scopes = _load_existing_type_detail_scope_set(
      jid,
      fingerprint,
      sorted((schema or {}).keys()),
  )

  metric_values = _metric_value_map(job)
  fsio, fsio_from_metrics = _fsio_from_metric_values(metric_values)
  if fsio is None:
    fsio = {}
  elif telemetry is not None and fsio_from_metrics:
    telemetry["detail_fsio_metrics_reused"] = int(
        telemetry.get("detail_fsio_metrics_reused", 0)
    ) + 1
  try:
    if (not fsio_from_metrics) and ("llite" not in fsio):
      if telemetry is not None:
        telemetry["detail_fsio_fallback_queries"] = int(
            telemetry.get("detail_fsio_fallback_queries", 0)
        ) + 1
      llite_df = jt.get_llite_delta_by_event()
      if not llite_df.empty and "delta_sum" in llite_df.columns:
        llite_df = llite_df.copy()
        llite_df["delta_mb"] = llite_df["delta_sum"].fillna(0) / (1024 * 1024)
        read_row = llite_df[llite_df["event"] == "read_bytes"]
        write_row = llite_df[llite_df["event"] == "write_bytes"]
        fsio["llite"] = [
            float(read_row["delta_mb"].iloc[0]) if len(read_row) else 0.0,
            float(write_row["delta_mb"].iloc[0]) if len(write_row) else 0.0,
        ]
  except Exception:
    pass

  if (not fsio_from_metrics) and ("nfs" not in fsio):
    try:
      if telemetry is not None and "llite" in fsio:
        telemetry["detail_fsio_fallback_queries"] = int(
            telemetry.get("detail_fsio_fallback_queries", 0)
        ) + 1
      nfs = jt.get_nfs_delta_totals_mb()
      if nfs is not None:
        fsio["nfs"] = nfs
    except Exception:
      pass

  extend_fsio_payload_lists_with_peaks(fsio, jt)

  gpu_detail_from_metrics = _gpu_detail_from_metric_values(metric_values)
  gpu_detail = gpu_detail_from_metrics or _gpu_detail_from_jid_table(jt)
  # Per-device inventory always from host_data (metrics_data has aggregates only).
  if not gpu_detail.get("gpu_inventory"):
    try:
      gpu_detail["gpu_inventory"] = gpu_inventory_for_job_window(jt)
    except Exception:
      gpu_detail["gpu_inventory"] = []
  if telemetry is not None:
    if gpu_detail_from_metrics is not None:
      telemetry["detail_gpu_metrics_reused"] = int(
          telemetry.get("detail_gpu_metrics_reused", 0)
      ) + 1
    else:
      telemetry["detail_gpu_fallback_queries"] = int(
          telemetry.get("detail_gpu_fallback_queries", 0)
      ) + 1
  upsert_job_detail_artifact(
      jid=jid,
      artifact_kind=ARTIFACT_KIND_JOB_DETAIL,
      artifact_scope="",
      input_fingerprint=fingerprint,
      payload={
          "host_list": jt.acct_host_list,
          "schema": schema,
          "fsio": fsio,
          **gpu_detail,
      },
  )
  upsert_job_detail_artifact(
      jid=jid,
      artifact_kind=ARTIFACT_KIND_MULTIPRECISION_MIX,
      artifact_scope="",
      input_fingerprint=fingerprint,
      payload=_multiprecision_mix_payload(metric_values),
  )

  for type_name in sorted((schema or {}).keys()):
    if type_name in existing_type_detail_scopes:
      continue
    try:
      provider = jid_table.TypeDetailDataProvider(
          jid,
          type_name,
          jt.start_time,
          jt.end_time,
          jt.acct_host_list,
      )
      sp = plots.DevPlot(provider, jt.acct_host_list)
      df, plot_comp = sp.plot()
      tplot_item = json_item(plot_comp) if plot_comp is not None else None
      schema_cols = [
          c for c in df.columns if c not in ("host", "time", "index")
      ] if not df.empty else []
      stats_data = []
      if not df.empty and "time" in df.columns and schema_cols:
        df = df.copy()
        df["dt"] = df["time"].sub(df["time"].iloc[0]).astype("timedelta64[s]")
        df1 = df.groupby("dt")[schema_cols].mean().reset_index()
        for t in range(len(df1)):
          vals = df1.loc[df1.index[t], schema_cols].values.flatten().tolist()
          vals = [float(x) if hasattr(x, "__float__") else x for x in vals]
          stats_data.append([str(df1["dt"].iloc[t]), vals])
      upsert_job_detail_artifact(
          jid=jid,
          artifact_kind=ARTIFACT_KIND_TYPE_DETAIL,
          artifact_scope=type_name,
          input_fingerprint=fingerprint,
          payload={
              "type_name": type_name,
              "jobid": jid,
              "tplot_item": tplot_item,
              "stats_data": stats_data,
              "schema": schema_cols,
              "tplot_unavailable_reason": None if tplot_item is not None else "Type detail plot generation returned no data.",
          },
      )
    except Exception:
      logger.warning(
          "type_detail artifact prewarm failed jid=%s type=%s",
          jid,
          type_name,
          exc_info=True,
      )
      upsert_job_detail_artifact(
          jid=jid,
          artifact_kind=ARTIFACT_KIND_TYPE_DETAIL,
          artifact_scope=type_name,
          input_fingerprint=fingerprint,
          payload={
              "type_name": type_name,
              "jobid": jid,
              "tplot_item": None,
              "stats_data": [],
              "schema": [],
              "tplot_unavailable_reason": (
                  "Type detail plot generation failed during artifact prewarm."
              ),
          },
      )
