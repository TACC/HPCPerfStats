"""Persist and load gzip-compressed Bokeh json_item rows for job_plots (+ update_metrics prewarm)."""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from typing import Any, Dict, NamedTuple, Optional, Sequence, Tuple

import bokeh
from bokeh.embed import json_item
from django.conf import settings
from django.db import connection

import hpcperfstats.analysis.gen.jid_table as jid_table
import hpcperfstats.analysis.plot as plots
import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics.live_host_sample_count import (
    live_distinct_host_time_count_expression,
)

from .bokeh_plot_layout import (
    _apply_zoom_layout_to_bokeh_model,
    _apply_zoom_layout_to_json_item,
)
from .models import job_data, job_plot_artifact

logger = logging.getLogger(__name__)

class JobPlotKindSpec(NamedTuple):
  plot_fn: Any
  empty_fallback: str
  json_item_key: str
  unavailable_reason_key: str
  log_fail_action: str
  wall_time: bool = False


JOB_PLOT_KIND_SPECS: Dict[str, JobPlotKindSpec] = {
    "summary_plot": JobPlotKindSpec(
        plot_fn=plots.plot_and_reason_summary_from_jid_table,
        empty_fallback=plots.MSG_NO_METRIC_DATA,
        json_item_key="mplot_item",
        unavailable_reason_key="mplot_unavailable_reason",
        log_fail_action="generate summary plot",
        wall_time=True,
    ),
    "heatmap": JobPlotKindSpec(
        plot_fn=plots.plot_and_reason_from_jid_table,
        empty_fallback=plots.MSG_NO_HOST_MSR_DATA,
        json_item_key="hplot_item",
        unavailable_reason_key="hplot_unavailable_reason",
        log_fail_action="generate heatmap",
    ),
    "roofline": JobPlotKindSpec(
        plot_fn=plots.plot_and_reason_roofline_from_jid_table,
        empty_fallback=plots.MSG_NO_ROOFLINE_DATA,
        json_item_key="rplot_item",
        unavailable_reason_key="rplot_unavailable_reason",
        log_fail_action="generate roofline",
    ),
    "gpu_roofline": JobPlotKindSpec(
        plot_fn=plots.plot_and_reason_gpu_roofline_from_jid_table,
        empty_fallback=plots.MSG_NO_ROOFLINE_DATA,
        json_item_key="grplot_item",
        unavailable_reason_key="grplot_unavailable_reason",
        log_fail_action="generate gpu roofline",
    ),
}

JOB_PLOT_KINDS: Tuple[str, ...] = tuple(JOB_PLOT_KIND_SPECS.keys())

JOB_PLOT_LAYOUT_NORMAL = "normal"
JOB_PLOT_LAYOUT_ZOOM_V3 = "zoom_v3"

PAYLOAD_ENCODING_GZIP_JSON = "gzip_json"

# Bump when plot artifact semantics change (independent of Bokeh version).
# See cursor-rules/job-plot-artifacts-caching.mdc and machine/tests/test_job_plot_artifacts.py.
APP_PLOT_ARTIFACT_SCHEMA_VERSION = 3

JOB_PLOT_JSON_KEYS: Dict[str, Tuple[str, str]] = {
    kind: (spec.json_item_key, spec.unavailable_reason_key)
    for kind, spec in JOB_PLOT_KIND_SPECS.items()
}


def get_job_plot_redis_max_bytes() -> int:
  return int(getattr(settings, "JOB_PLOT_REDIS_MAX_BYTES", 512 * 1024))


def get_live_distinct_time_count_for_jid(jid: str) -> int:
  """Live per-job distinct sample times (PostgreSQL); else metrics_distinct_time_count."""
  if connection.vendor != "postgresql":
    v = (
        job_data.objects.filter(jid=jid)
        .values_list("metrics_distinct_time_count", flat=True)
        .first()
    )
    return int(v) if v is not None else 0
  suffix = "." + cfg.get_host_name_ext()
  row = (
      job_data.objects.filter(jid=jid)
      .annotate(
          live_distinct_time_count=live_distinct_host_time_count_expression(suffix),
      )
      .values("live_distinct_time_count")
      .first()
  )
  if not row:
    return 0
  v = row.get("live_distinct_time_count")
  return int(v) if v is not None else 0


def compute_plot_input_fingerprint(job: job_data, live_distinct_time_count: int) -> str:
  hl = sorted(str(h) for h in (job.host_list or []) if str(h).strip())
  payload = {
      "artifact_schema": APP_PLOT_ARTIFACT_SCHEMA_VERSION,
      "bokeh": bokeh.__version__,
      "et": job.end_time.isoformat() if job.end_time else "",
      "hosts": hl,
      "jid": str(job.jid),
      "live_distinct": int(live_distinct_time_count),
      "mdc": job.metrics_distinct_time_count,
      "st": job.start_time.isoformat() if job.start_time else "",
  }
  canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def json_item_to_compressed_payload(
    plot_item: Dict[str, Any],
) -> Tuple[bytes, bytes, str]:
  """Return UTF-8 JSON bytes, gzip-compressed blob, and encoding name."""
  raw_utf8 = json.dumps(plot_item, separators=(",", ":")).encode("utf-8")
  compressed = gzip.compress(raw_utf8, compresslevel=6)
  return raw_utf8, compressed, PAYLOAD_ENCODING_GZIP_JSON


def decompress_plot_item_dict(
    payload_compressed: bytes,
    payload_encoding: str,
) -> Dict[str, Any]:
  if payload_encoding != PAYLOAD_ENCODING_GZIP_JSON:
    raise ValueError("Unsupported plot payload_encoding: {!r}".format(payload_encoding))
  raw = gzip.decompress(payload_compressed)
  return json.loads(raw.decode("utf-8"))


def upsert_job_plot_artifact(
    jid: str,
    plot_kind: str,
    layout: str,
    input_fingerprint: str,
    plot_item: Dict[str, Any],
) -> None:
  _raw_utf8, compressed, enc = json_item_to_compressed_payload(plot_item)
  job_plot_artifact.objects.update_or_create(
      jid_id=jid,
      plot_kind=plot_kind,
      layout=layout,
      defaults={
          "payload_compressed": compressed,
          "payload_encoding": enc,
          "input_fingerprint": input_fingerprint,
      },
  )


def _load_row(jid: str, plot_kind: str, layout: str) -> Optional[job_plot_artifact]:
  return (
      job_plot_artifact.objects.filter(
          jid_id=jid,
          plot_kind=plot_kind,
          layout=layout,
      )
      .only(
          "payload_compressed",
          "payload_encoding",
          "input_fingerprint",
      )
      .first()
  )


def load_cached_job_plot_entry(
    jid: str,
    plot_kind: str,
    layout_key: str,
    fingerprint: str,
) -> Optional[Dict[str, Any]]:
  """Return {'plot_item': dict, 'unavailable_reason': None} or None if miss/stale."""
  row = _load_row(jid, plot_kind, layout_key)
  if (
      row
      and row.input_fingerprint == fingerprint
      and row.payload_compressed
  ):
    try:
      item = decompress_plot_item_dict(
          bytes(row.payload_compressed),
          row.payload_encoding,
      )
      return {"plot_item": item, "unavailable_reason": None}
    except Exception:
      logger.warning(
          "Corrupt job_plot_artifact jid=%s kind=%s layout=%s",
          jid,
          plot_kind,
          layout_key,
          exc_info=True,
      )
      return None

  if layout_key == JOB_PLOT_LAYOUT_ZOOM_V3:
    base = _load_row(jid, plot_kind, JOB_PLOT_LAYOUT_NORMAL)
    if (
        base
        and base.input_fingerprint == fingerprint
        and base.payload_compressed
    ):
      try:
        item = decompress_plot_item_dict(
            bytes(base.payload_compressed),
            base.payload_encoding,
        )
        zoomed = _apply_zoom_layout_to_json_item(item)
        return {"plot_item": zoomed, "unavailable_reason": None}
      except Exception:
        logger.warning(
            "Failed zoom layout from stored normal plot jid=%s kind=%s",
            jid,
            plot_kind,
            exc_info=True,
        )
  return None


def compute_plot_item_for_kind(
    j: Any,
    plot_kind: str,
    zoom_mode: bool,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
  spec = JOB_PLOT_KIND_SPECS.get(plot_kind)
  if not spec:
    return None, "Unknown plot kind"
  plot_json, plot_reason = spec.plot_fn(j)
  if plot_json is None:
    return None, plot_reason or spec.empty_fallback
  if zoom_mode:
    _apply_zoom_layout_to_bokeh_model(plot_json)
  return json_item(plot_json), None


def persist_job_plot_artifacts_for_jid(
    jid: str,
    layouts: Optional[Sequence[str]] = None,
) -> None:
  """Build and store artifacts for each plot kind (used by update_metrics prewarm)."""
  if layouts is None:
    layouts = (JOB_PLOT_LAYOUT_NORMAL,)
  job = job_data.objects.filter(jid=jid).first()
  if not job:
    return
  live = get_live_distinct_time_count_for_jid(jid)
  fp = compute_plot_input_fingerprint(job, live)
  jt = jid_table.jid_table(jid)
  for kind in JOB_PLOT_KINDS:
    for layout in layouts:
      zoom_mode = layout == JOB_PLOT_LAYOUT_ZOOM_V3
      row = _load_row(jid, kind, layout)
      if (
          row
          and row.input_fingerprint == fp
          and row.payload_compressed
      ):
        continue
      try:
        plot_item, reason = compute_plot_item_for_kind(jt, kind, zoom_mode)
      except Exception:
        logger.warning(
            "plot prewarm failed jid=%s kind=%s layout=%s",
            jid,
            kind,
            layout,
            exc_info=True,
        )
        continue
      if plot_item is None:
        continue
      try:
        upsert_job_plot_artifact(jid, kind, layout, fp, plot_item)
      except Exception:
        logger.warning(
            "plot artifact upsert failed jid=%s kind=%s layout=%s",
            jid,
            kind,
            layout,
            exc_info=True,
        )
