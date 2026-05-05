"""Persist and load gzip-compressed derived payloads for job_detail/type_detail."""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from math import pi
from datetime import date, datetime
from typing import Any, Dict, Optional

from bokeh.embed import json_item
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.plotting import figure

import hpcperfstats.analysis.gen.jid_table as jid_table
import hpcperfstats.analysis.plot as plots
from hpcperfstats.analysis.metrics.job_detail_fsio import fsio_job_detail_catalog
from hpcperfstats.analysis.metrics.gpu_job_detail_summary import (
    gpu_agg_rows_for_job_window,
    gpu_count_total_for_job_window,
    gpu_precision_mix_rows_for_job_window,
    reduce_gpu_precision_mix,
)

from .models import job_data, job_detail_artifact

logger = logging.getLogger(__name__)

PAYLOAD_ENCODING_GZIP_JSON = "gzip_json"
ARTIFACT_KIND_JOB_DETAIL = "job_detail"
ARTIFACT_KIND_TYPE_DETAIL = "type_detail"
ARTIFACT_KIND_MULTIPRECISION_MIX = "multiprecision_mix"
APP_DETAIL_ARTIFACT_SCHEMA_VERSION = 1


def _compress_payload(payload: Dict[str, Any]) -> tuple[bytes, str]:
  raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
  return gzip.compress(raw, compresslevel=6), PAYLOAD_ENCODING_GZIP_JSON


def _decompress_payload(payload_compressed: bytes, payload_encoding: str) -> Dict[str, Any]:
  if payload_encoding != PAYLOAD_ENCODING_GZIP_JSON:
    raise ValueError("Unsupported detail payload encoding: {!r}".format(payload_encoding))
  return json.loads(gzip.decompress(payload_compressed).decode("utf-8"))


def compute_detail_input_fingerprint(job: job_data) -> str:
  def _safe_text(v: Any) -> str:
    try:
      if v is None:
        return ""
      if isinstance(v, (str, int, float, bool)):
        return str(v)
      if isinstance(v, (datetime, date)):
        return v.isoformat()
      if hasattr(v, "isoformat"):
        maybe = v.isoformat()
        return maybe if isinstance(maybe, str) else str(maybe)
      return str(v)
    except Exception:
      return ""

  payload = {
      "artifact_schema": APP_DETAIL_ARTIFACT_SCHEMA_VERSION,
      "jid": _safe_text(getattr(job, "jid", "")),
      "metrics_distinct_time_count": _safe_text(getattr(job, "metrics_distinct_time_count", "")),
      "start_time": _safe_text(getattr(job, "start_time", "")),
      "end_time": _safe_text(getattr(job, "end_time", "")),
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
          )
      ],
      update_conflicts=True,
      update_fields=["payload_compressed", "payload_encoding", "input_fingerprint"],
      unique_fields=["jid", "artifact_kind", "artifact_scope"],
  )


def load_job_detail_artifact(
    jid: str,
    artifact_kind: str,
    artifact_scope: str,
    input_fingerprint: str,
) -> Optional[Dict[str, Any]]:
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
  rows = gpu_agg_rows_for_job_window(jt)
  if not rows:
    return {
        "gpu_active": None,
        "gpu_utilization_max": None,
        "gpu_utilization_mean": None,
        "gpu_count": None,
    }
  vmax = sum(max(0.0, float(r.get("vmax") or 0.0)) for r in rows)
  vmean = sum(max(0.0, float(r.get("vmean") or 0.0)) for r in rows)
  active = sum(
      1
      for r in rows
      if (float(r.get("vmax") or 0.0) > 0.0 and float(r.get("cnt") or 0.0) > 2.0)
  )
  return {
      "gpu_active": int(active),
      "gpu_utilization_max": float(vmax),
      "gpu_utilization_mean": float(vmean),
      "gpu_count": gpu_count_total_for_job_window(jt),
  }


def _metric_value_map(job: job_data) -> Dict[str, Optional[float]]:
  out: Dict[str, Optional[float]] = {}
  for row in getattr(job, "metrics_data_set").all():
    out[row.metric] = None if row.value is None else float(row.value)
  return out


def _load_existing_type_detail_scope_set(
    jid: str,
    input_fingerprint: str,
    type_names: list[str],
) -> set[str]:
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


def _gpu_detail_from_metric_values(metric_values: Dict[str, Optional[float]]) -> Optional[Dict[str, Any]]:
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
  return {
      "gpu_active": None if active is None else int(active),
      "gpu_utilization_max": util_max,
      "gpu_utilization_mean": util_mean,
      "gpu_count": None if count is None else int(count),
  }


def _cpu_precision_mix_from_metric_values(
    metric_values: Dict[str, Optional[float]]
) -> Dict[str, float]:
  out: Dict[str, float] = {}
  metric_to_label = {
      "vecpercent_64b": "FP64",
      "vecpercent_32b": "FP32",
      # Presently unavailable in the CPU metrics catalog; omitted when missing.
      "vecpercent_16b": "FP16",
      "vecpercent_8b": "FP8",
  }
  for metric_name, label in metric_to_label.items():
    if metric_name not in metric_values:
      continue
    try:
      value = float(metric_values.get(metric_name))
    except (TypeError, ValueError):
      continue
    if value > 0.0:
      out[label] = value
  return out


def _pie_item_from_precision_mix(
    *,
    precision_mix: Dict[str, float],
    title: str,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
  if not precision_mix:
    return None, "No usable precision-width telemetry is available for this chart."
  total = sum(float(v) for v in precision_mix.values() if float(v) > 0.0)
  if total <= 0.0:
    return None, "No usable precision-width telemetry is available for this chart."
  labels = list(precision_mix.keys())
  values = [float(precision_mix[label]) for label in labels]
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
          "start": starts,
          "end": ends,
      }
  )
  p = figure(
      title=title,
      height=320,
      width=320,
      toolbar_location=None,
      tools="hover",
      x_range=(-1.3, 1.3),
      y_range=(-1.3, 1.3),
  )
  p.axis.visible = False
  p.grid.visible = False
  p.outline_line_color = None
  p.wedge(
      x=0,
      y=0,
      radius=1.0,
      start_angle="start",
      end_angle="end",
      source=source,
      legend_field="label",
      line_color="white",
      fill_alpha=0.9,
  )
  hover = p.select_one(HoverTool)
  if hover is not None:
    hover.tooltips = [
        ("Width", "@label"),
        ("Share (%)", "@value{0.00}"),
    ]
  p.legend.location = "bottom_center"
  p.legend.orientation = "horizontal"
  return json_item(p), None


def _multiprecision_mix_payload(
    metric_values: Dict[str, Optional[float]],
    jt: Any,
) -> Dict[str, Any]:
  cpu_mix = _cpu_precision_mix_from_metric_values(metric_values)
  gpu_mix = reduce_gpu_precision_mix(gpu_precision_mix_rows_for_job_window(jt))
  cpu_plot_item, cpu_reason = _pie_item_from_precision_mix(
      precision_mix=cpu_mix,
      title="CPU Multiprecision Mix",
  )
  gpu_plot_item, gpu_reason = _pie_item_from_precision_mix(
      precision_mix=gpu_mix,
      title="GPU Multiprecision Mix",
  )
  return {
      "cpu_plot_item": cpu_plot_item,
      "cpu_unavailable_reason": cpu_reason,
      "gpu_plot_item": gpu_plot_item,
      "gpu_unavailable_reason": gpu_reason,
  }


def _fsio_from_metric_values(
    metric_values: Dict[str, Optional[float]]
) -> tuple[Optional[Dict[str, Any]], bool]:
  fsio_metrics = {name for name, _t, _u in fsio_job_detail_catalog()}
  if not fsio_metrics.intersection(metric_values.keys()):
    return None, False
  out = {}
  llite_read = metric_values.get("detail_fsio_llite_read_mb")
  llite_write = metric_values.get("detail_fsio_llite_write_mb")
  nfs_read = metric_values.get("detail_fsio_nfs_read_mb")
  nfs_write = metric_values.get("detail_fsio_nfs_write_mb")
  if llite_read is not None or llite_write is not None:
    out["llite"] = [float(llite_read or 0.0), float(llite_write or 0.0)]
    return out, True
  if nfs_read is not None or nfs_write is not None:
    out["nfs"] = [float(nfs_read or 0.0), float(nfs_write or 0.0)]
    return out, True
  return out, True


def persist_job_detail_artifacts_for_jid(jid: str, context: Optional[Dict[str, Any]] = None) -> None:
  """Prewarm derived payloads for user-facing API paths."""
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

  metric_values = _metric_value_map(job)
  fsio, fsio_from_metrics = _fsio_from_metric_values(metric_values)
  if fsio is None:
    fsio = {}
  elif telemetry is not None and fsio_from_metrics:
    telemetry["detail_fsio_metrics_reused"] = int(
        telemetry.get("detail_fsio_metrics_reused", 0)
    ) + 1
  try:
    if (not fsio) and (not fsio_from_metrics):
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
    fsio = {}

  if ("llite" not in fsio) and (not fsio_from_metrics):
    try:
      nfs = jt.get_nfs_delta_totals_mb()
      if nfs is not None:
        fsio["nfs"] = nfs
    except Exception:
      pass

  gpu_detail_from_metrics = _gpu_detail_from_metric_values(metric_values)
  gpu_detail = gpu_detail_from_metrics or _gpu_detail_from_jid_table(jt)
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
      payload=_multiprecision_mix_payload(metric_values, jt),
  )

  for type_name in sorted((schema or {}).keys()):
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
