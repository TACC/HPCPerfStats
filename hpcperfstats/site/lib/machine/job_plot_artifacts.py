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
from django.utils import timezone as dj_tz

import hpcperfstats.analysis.metrics.lib.gen.jid_table as jid_table
import hpcperfstats.analysis.metrics.lib.plot as plots
import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.analysis.metrics.lib.live_host_sample_count import (
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
# See hpcperfstats/cursor-rules/job-plot-artifacts-caching.mdc and machine/tests/test_job_plot_artifacts.py.
APP_PLOT_ARTIFACT_SCHEMA_VERSION = 14

JOB_PLOT_JSON_KEYS: Dict[str, Tuple[str, str]] = {
    kind: (spec.json_item_key, spec.unavailable_reason_key)
    for kind, spec in JOB_PLOT_KIND_SPECS.items()
}

# Shared aggregate bundle for common roofline probes to reduce repeated
# per-kind aggregate misses while preserving plot-specific fallback behavior.
COMMON_PLOT_AGGREGATE_BUNDLE: Tuple[Tuple[str, str, Tuple[str, ...], float], ...] = (
    ("amd64_pmc", "arc", ("FLOPS",), 1e-9),
    (
        "amd64_df",
        "arc",
        (
            "MBW_CHANNEL_0",
            "MBW_CHANNEL_1",
            "MBW_CHANNEL_2",
            "MBW_CHANNEL_3",
            "MBW_CHANNEL_4",
            "MBW_CHANNEL_5",
            "MBW_CHANNEL_6",
            "MBW_CHANNEL_7",
        ),
        2 / (1024 ** 3),
    ),
    ("nvidia_gpu", "arc", ("gpu_flops",), 1e-9),
    ("nvidia_gpu", "arc", ("gpu_io_link_total_bytes",), 1 / (1024 ** 3)),
)


def get_job_plot_redis_max_bytes() -> int:
  return int(getattr(settings, "JOB_PLOT_REDIS_MAX_BYTES", 512 * 1024))


def _utc_iso_for_plot_fingerprint(dt) -> str:
  """UTC ISO string matching PlotArtifactInputFingerprintHex SQL (US + OF)."""
  if dt is None:
    return ""
  if dj_tz.is_naive(dt):
    dt = dj_tz.make_aware(dt, dj_tz.utc)
  dt = dt.astimezone(dj_tz.utc)
  # PostgreSQL ``to_char(..., 'USOF')`` uses ISO 8601 basic offset (±HHMM), no colon.
  return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}" + dt.strftime("%z")


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
  if connection.vendor == "postgresql":
    suffix = "." + cfg.get_host_name_ext()
    from hpcperfstats.site.lib.machine.artifact_readiness_expressions import (
        PlotArtifactInputFingerprintHex,
    )

    return (
        job_data.objects.filter(pk=job.pk)
        .annotate(fp=PlotArtifactInputFingerprintHex(suffix))
        .values_list("fp", flat=True)
        .get()
    )
  hl = sorted(str(h) for h in (job.host_list or []) if str(h).strip())
  payload = {
      "artifact_schema": APP_PLOT_ARTIFACT_SCHEMA_VERSION,
      "bokeh": bokeh.__version__,
      "et": _utc_iso_for_plot_fingerprint(job.end_time),
      "hosts": hl,
      "jid": str(job.jid).strip(),
      "live_distinct": int(live_distinct_time_count),
      "mdc": job.metrics_distinct_time_count,
      "st": _utc_iso_for_plot_fingerprint(job.start_time),
      "tft": _utc_iso_for_plot_fingerprint(job.telemetry_first_time),
      "tlt": _utc_iso_for_plot_fingerprint(job.telemetry_last_time),
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


def _plot_artifact_storage_payload(
    plot_item: Optional[Dict[str, Any]],
    unavailable_reason: Optional[str],
) -> Dict[str, Any]:
  """Canonical stored payload for plot artifacts, including fresh unavailable rows."""
  return {
      "plot_item": plot_item,
      "unavailable_reason": unavailable_reason,
  }


def _normalize_loaded_plot_artifact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
  """Decode legacy raw-json-item rows and new explicit unavailable-state rows."""
  if isinstance(payload, dict) and (
      "plot_item" in payload or "unavailable_reason" in payload
  ):
    return {
        "plot_item": payload.get("plot_item"),
        "unavailable_reason": payload.get("unavailable_reason"),
    }
  return {
      "plot_item": payload,
      "unavailable_reason": None,
  }


def upsert_job_plot_artifact(
    jid: str,
    plot_kind: str,
    layout: str,
    input_fingerprint: str,
    plot_item: Dict[str, Any],
) -> None:
  payload = _plot_artifact_storage_payload(plot_item, None)
  _raw_utf8, compressed, enc = json_item_to_compressed_payload(payload)
  job_plot_artifact.objects.bulk_create(
      [job_plot_artifact(
          jid_id=jid,
          plot_kind=plot_kind,
          layout=layout,
          payload_compressed=compressed,
          payload_encoding=enc,
          input_fingerprint=input_fingerprint,
          artifact_schema=APP_PLOT_ARTIFACT_SCHEMA_VERSION,
      )],
      update_conflicts=True,
      update_fields=[
          "payload_compressed",
          "payload_encoding",
          "input_fingerprint",
          "artifact_schema",
      ],
      unique_fields=["jid", "plot_kind", "layout"],
  )


def upsert_job_plot_artifact_batch(
    rows: Sequence[Tuple[str, str, str, str, Dict[str, Any]]],
) -> None:
  """Bulk upsert multiple plot artifacts in one DB round-trip."""
  if not rows:
    return
  objs = []
  for jid, plot_kind, layout, input_fingerprint, plot_item in rows:
    payload = (
        plot_item
        if isinstance(plot_item, dict) and (
            "plot_item" in plot_item or "unavailable_reason" in plot_item
        )
        else _plot_artifact_storage_payload(plot_item, None)
    )
    _raw_utf8, compressed, enc = json_item_to_compressed_payload(payload)
    objs.append(
        job_plot_artifact(
            jid_id=jid,
            plot_kind=plot_kind,
            layout=layout,
            payload_compressed=compressed,
            payload_encoding=enc,
            input_fingerprint=input_fingerprint,
            artifact_schema=APP_PLOT_ARTIFACT_SCHEMA_VERSION,
        )
    )
  job_plot_artifact.objects.bulk_create(
      objs,
      update_conflicts=True,
      update_fields=[
          "payload_compressed",
          "payload_encoding",
          "input_fingerprint",
          "artifact_schema",
      ],
      unique_fields=["jid", "plot_kind", "layout"],
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


def _load_rows_map(jid: str, layouts: Sequence[str]) -> Dict[Tuple[str, str], job_plot_artifact]:
  rows = (
      job_plot_artifact.objects.filter(
          jid_id=jid,
          plot_kind__in=JOB_PLOT_KINDS,
          layout__in=list(layouts),
      )
      .only("plot_kind", "layout", "payload_compressed", "payload_encoding", "input_fingerprint")
  )
  return {(row.plot_kind, row.layout): row for row in rows}


class _JtMemoProxy:
  """Per-call memo wrapper for jid_table aggregate/dataframe reads."""

  def __init__(self, jt: Any, telemetry: Optional[Dict[str, int]] = None):
    self._jt = jt
    self._host_time_df = None
    self._aggregate_cache: Dict[Tuple[str, str, Tuple[str, ...], float], Any] = {}
    self._telemetry = telemetry if isinstance(telemetry, dict) else None

  def __getattr__(self, name):
    return getattr(self._jt, name)

  def _normalize_events(self, events):
    return tuple(sorted(str(e) for e in (events or ())))

  def _normalize_conv(self, conv):
    try:
      return round(float(conv), 12)
    except Exception:
      return conv

  def get_host_time_df(self):
    if self._host_time_df is None:
      self._host_time_df = self._jt.get_host_time_df()
    elif self._telemetry is not None:
      self._telemetry["plot_jt_memo_host_time_hits"] = int(
          self._telemetry.get("plot_jt_memo_host_time_hits", 0)
      ) + 1
    return self._host_time_df.copy()

  def get_aggregate_df(self, typ, metric_column, events, conv):
    key = (
        str(typ),
        str(metric_column),
        self._normalize_events(events),
        self._normalize_conv(conv),
    )
    if key not in self._aggregate_cache:
      self._aggregate_cache[key] = self._jt.get_aggregate_df(typ, metric_column, events, conv)
      if self._telemetry is not None:
        self._telemetry["plot_jt_memo_aggregate_misses"] = int(
            self._telemetry.get("plot_jt_memo_aggregate_misses", 0)
        ) + 1
    elif self._telemetry is not None:
      self._telemetry["plot_jt_memo_aggregate_hits"] = int(
          self._telemetry.get("plot_jt_memo_aggregate_hits", 0)
      ) + 1
    return self._aggregate_cache[key].copy()

  def prefetch_aggregate_bundle(
      self,
      specs: Sequence[Tuple[str, str, Sequence[str], float]],
  ) -> None:
    for typ, metric_column, events, conv in specs:
      self.get_aggregate_df(typ, metric_column, events, conv)


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
      item = _normalize_loaded_plot_artifact_payload(decompress_plot_item_dict(
          bytes(row.payload_compressed),
          row.payload_encoding,
      ))
      return item
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
        item = _normalize_loaded_plot_artifact_payload(decompress_plot_item_dict(
            bytes(base.payload_compressed),
            base.payload_encoding,
        ))
        if item.get("plot_item") is None:
          return item
        zoomed = _apply_zoom_layout_to_json_item(item["plot_item"])
        return {"plot_item": zoomed, "unavailable_reason": item.get("unavailable_reason")}
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
    context: Optional[Dict[str, Any]] = None,
) -> None:
  """Build and store artifacts for each plot kind (used by update_metrics prewarm)."""
  if layouts is None:
    layouts = (JOB_PLOT_LAYOUT_NORMAL,)
  shared = context if isinstance(context, dict) else {}
  telemetry = shared.get("_telemetry") if isinstance(shared.get("_telemetry"), dict) else None
  job = shared.get("job")
  if job is None:
    job = job_data.objects.filter(jid=jid).first()
    shared["job"] = job
  if not job:
    return
  fp = shared.get("plot_fingerprint")
  if fp is None:
    live = get_live_distinct_time_count_for_jid(jid)
    fp = compute_plot_input_fingerprint(job, live)
    shared["plot_fingerprint"] = fp
  jt = shared.get("jt")
  if jt is None:
    jt = jid_table.jid_table(jid)
    shared["jt"] = jt
  plot_jt = shared.get("plot_jt")
  if plot_jt is None:
    plot_jt = _JtMemoProxy(jt, telemetry=telemetry)
    try:
      plot_jt.prefetch_aggregate_bundle(COMMON_PLOT_AGGREGATE_BUNDLE)
    except Exception:
      # Plot builders still probe independently; bundle prefetch is best-effort.
      pass
    shared["plot_jt"] = plot_jt
  existing = shared.get("existing_plot_rows")
  if existing is None:
    if telemetry is not None:
      telemetry["plot_row_lookup_queries"] = int(
          telemetry.get("plot_row_lookup_queries", 0)
      ) + 1
    existing = _load_rows_map(jid, layouts)
    if telemetry is not None:
      telemetry["plot_row_lookup_hits"] = int(
          telemetry.get("plot_row_lookup_hits", 0)
      ) + len(existing)
    shared["existing_plot_rows"] = existing
  write_rows = []
  for kind in JOB_PLOT_KINDS:
    for layout in layouts:
      zoom_mode = layout == JOB_PLOT_LAYOUT_ZOOM_V3
      row = existing.get((kind, layout))
      if (
          row
          and row.input_fingerprint == fp
          and row.payload_compressed
      ):
        continue
      try:
        plot_item, reason = compute_plot_item_for_kind(plot_jt, kind, zoom_mode)
      except Exception:
        logger.warning(
            "plot prewarm failed jid=%s kind=%s layout=%s",
            jid,
            kind,
            layout,
            exc_info=True,
        )
        plot_item = None
        reason = "Plot generation failed during artifact prewarm."
      write_rows.append((
          jid,
          kind,
          layout,
          fp,
          _plot_artifact_storage_payload(plot_item, reason),
      ))
  if not write_rows:
    return
  try:
    upsert_job_plot_artifact_batch(write_rows)
  except Exception:
    logger.warning(
        "plot artifact batch upsert failed jid=%s rows=%s",
        jid,
        len(write_rows),
        exc_info=True,
    )
