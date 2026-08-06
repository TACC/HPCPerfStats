"""
ORM GPU aggregates for job-detail and metrics_data (shared with API).

Uses the same reduction rules as historical ``api._compute_job_gpu_stats``:
host×time chunked ``host__in``, util aggregates by vendor precedence, then
``gpu_count``.

Vendor precedence for summary fields: ``nvidia_gpu`` → ``amd_gpu`` →
``intel_gpu`` (no mixed-vendor merge for a single field). DCGM blank-family
samples are rejected.

Attributes:
  GPU_TYPE_PRECEDENCE: ``GPU_TYPE_PRECEDENCE``.
  _GPU_UTIL_EVENTS: ``_GPU_UTIL_EVENTS``.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from django.db.models import Avg, Count, Max

from hpcperfstats.analysis.metrics.lib.gen import jid_table as jid_table_mod
from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK, is_dcgm_numeric_blank
from hpcperfstats.site.lib.machine.models import host_data

GPU_TYPE_PRECEDENCE: tuple[str, ...] = ("nvidia_gpu", "amd_gpu", "intel_gpu")
_GPU_UTIL_EVENTS = ("gpu_util", "utilization")


def _blank_excluded_value_q(field: str = "value") -> Any:
  """
  ORM kwargs / Q fragment: exclude DCGM FP64 blank family (covers INT64 blanks).
  
  Args:
    field (str): String for field.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _blank_excluded_value_q("x")  # doctest: +SKIP
  """
  return {f"{field}__lt": DCGM_FP64_BLANK}


def _job_window_time_filter(j: Any) -> Any:
  """
  Build ``time__gte`` / ``time__lte`` kwargs for a job-like object.

  Args:
    j (Any): Job with ``start_time`` / ``end_time``.

  Returns:
    Any: Time filter dict.

  Examples:
    >>> _job_window_time_filter(None)  # doctest: +SKIP
  """
  return {"time__gte": j.start_time, "time__lte": j.end_time}


def _collect_gpu_annotate_rows(
  hosts: Any,
  tkw: Any,
  *,
  gpu_typ: str,
  events: Any,
  group_fields: Any,
  annotate_kwargs: Any,
) -> List[dict]:
  """
  Host×time chunked annotate query with statement_timeout split/retry.

  Args:
    hosts (Any): Accounting hosts.
    tkw (Any): Base time filter.
    gpu_typ (str): ``host_data.type`` (nvidia/amd/intel).
    events (Any): Event name or list for ``event`` / ``event__in``.
    group_fields (Any): ``.values(...)`` grouping fields.
    annotate_kwargs (Any): Annotate kwargs (Count/Max/Avg).

  Returns:
    List[dict]: Folded annotate rows across chunks.

  Examples:
    >>> _collect_gpu_annotate_rows([], {}, gpu_typ="nvidia_gpu",
    ...     events=["gpu_util"], group_fields=["host"], annotate_kwargs={})
    []
  """
  slice_s = int(cfg.get_metrics_plot_aggregate_time_slice_s())
  batch = jid_table_mod.TYPE_DETAIL_HOST_QUERY_BATCH
  event_filter: dict
  if isinstance(events, (list, tuple)):
    event_filter = {"event__in": list(events)}
  else:
    event_filter = {"event": events}
  group = list(group_fields)

  def run(hosts_list: Any, tf_cur: Any) -> Any:
    """
    Run one annotate chunk.

    Args:
      hosts_list (Any): Hostnames for this attempt.
      tf_cur (Any): Time filter dict.

    Returns:
      Any: List of annotate dicts.

    Examples:
      >>> True
      True
    """
    qs = (
        host_data.objects.filter(
            type=gpu_typ,
            host__in=hosts_list,
            **_blank_excluded_value_q("value"),
            **event_filter,
            **(tf_cur or {}),
        )
        .values(*group)
        .annotate(**annotate_kwargs)
    )
    return list(qs)

  all_rows: List[dict] = []
  for host_chunk, tf in jid_table_mod._iter_host_time_query_chunks(
      hosts,
      tkw,
      batch_size=batch,
      slice_s=slice_s,
  ):
    all_rows.extend(
        jid_table_mod._run_with_host_time_timeout_retry(
            host_chunk,
            tf,
            run,
            jid_table_mod._merge_list_results,
            empty=[],
        )
    )
  if not all_rows:
    return []
  if "cnt" in annotate_kwargs and (
      "vmax" in annotate_kwargs or "vmean" in annotate_kwargs
  ):
    return jid_table_mod._fold_count_max_avg_rows(all_rows, group)
  if "mv" in annotate_kwargs:
    return jid_table_mod._fold_max_by_key(all_rows, group, "mv")
  if "pmax" in annotate_kwargs:
    return jid_table_mod._fold_max_by_key(all_rows, group, "pmax")
  return all_rows


def gpu_agg_rows_for_job_window(j: Any) -> List[dict]:
  """
  Per-(host, dev, event) Count/Max/Avg for GPU util in the job window.
  
  Uses the first vendor in ``GPU_TYPE_PRECEDENCE`` that has util rows.
  
  Args:
    j (Any): Job record (Django ``job_data`` or job-like mapping).
  
  Returns:
    List[dict]: List[dict] produced by this call.
  
  Examples:
    >>> gpu_agg_rows_for_job_window(None)  # doctest: +SKIP
  """
  hosts = getattr(j, "acct_host_list", None) or []
  if not hosts:
    return []
  tkw = _job_window_time_filter(j)
  for gpu_typ in GPU_TYPE_PRECEDENCE:
    out = _collect_gpu_annotate_rows(
        hosts,
        tkw,
        gpu_typ=gpu_typ,
        events=list(_GPU_UTIL_EVENTS),
        group_fields=["host", "dev", "event"],
        annotate_kwargs={
            "cnt": Count("time"),
            "vmax": Max("value"),
            "vmean": Avg("value"),
        },
    )
    if out:
      return out
  return []


def gpu_count_total_for_job_window(j: Any) -> Optional[int]:
  """
  Sum over hosts of max(gpu_count) in window (nvidia → amd → intel).
  
  Args:
    j (Any): Job record (Django ``job_data`` or job-like mapping).
  
  Returns:
    Optional[int]: Optional[int] — the result, or None when unavailable.
  
  Examples:
    >>> gpu_count_total_for_job_window(None)  # doctest: +SKIP
  """
  hosts = getattr(j, "acct_host_list", None) or []
  if not hosts:
    return None
  tkw = _job_window_time_filter(j)
  for gpu_typ in GPU_TYPE_PRECEDENCE:
    rows = _collect_gpu_annotate_rows(
        hosts,
        tkw,
        gpu_typ=gpu_typ,
        events="gpu_count",
        group_fields=["host"],
        annotate_kwargs={"mv": Max("value")},
    )
    if not rows:
      continue
    total = 0
    for r in rows:
      v = r.get("mv")
      if v is None or is_dcgm_numeric_blank(v):
        continue
      try:
        total += int(round(float(v)))
      except (TypeError, ValueError):
        continue
    if total > 0:
      return total
  return None


def gpu_inventory_for_job_window(j: Any) -> List[dict]:
  """
  Per-(host, dev) util max/mean (+ optional power peak) for Resources inventory.
  
  Uses the first vendor in ``GPU_TYPE_PRECEDENCE`` that has device-level util
    rows.
  
  Args:
    j (Any): Job record (Django ``job_data`` or job-like mapping).
  
  Returns:
    List[dict]: List[dict] produced by this call.
  
  Examples:
    >>> gpu_inventory_for_job_window(None)  # doctest: +SKIP
  """
  hosts = getattr(j, "acct_host_list", None) or []
  if not hosts:
    return []
  tkw = _job_window_time_filter(j)
  for gpu_typ in GPU_TYPE_PRECEDENCE:
    util_rows = _collect_gpu_annotate_rows(
        hosts,
        tkw,
        gpu_typ=gpu_typ,
        events=list(_GPU_UTIL_EVENTS),
        group_fields=["host", "dev", "event"],
        annotate_kwargs={
            "cnt": Count("time"),
            "vmax": Max("value"),
            "vmean": Avg("value"),
        },
    )
    if not util_rows:
      continue
    power_rows = _collect_gpu_annotate_rows(
        hosts,
        tkw,
        gpu_typ=gpu_typ,
        events="power_usage",
        group_fields=["host", "dev"],
        annotate_kwargs={"pmax": Max("value")},
    )
    per_device: dict = {}
    for r in util_rows:
      key = (str(r.get("host") or ""), str(r.get("dev") or ""))
      event = str(r.get("event") or "")
      slot = per_device.setdefault(
          key, {"host": key[0], "dev": key[1], "type": gpu_typ}
      )
      if event == "gpu_util" or (
          event == "utilization" and "util_max" not in slot
      ):
        vmax = r.get("vmax")
        vmean = r.get("vmean")
        if vmax is None or is_dcgm_numeric_blank(vmax):
          continue
        try:
          slot["util_max"] = float(vmax)
          slot["util_mean"] = (
              None
              if vmean is None or is_dcgm_numeric_blank(vmean)
              else float(vmean)
          )
          slot["sample_count"] = int(r.get("cnt") or 0)
        except (TypeError, ValueError):
          continue
    for r in power_rows:
      key = (str(r.get("host") or ""), str(r.get("dev") or ""))
      if key not in per_device:
        continue
      pmax = r.get("pmax")
      if pmax is None or is_dcgm_numeric_blank(pmax):
        continue
      try:
        per_device[key]["power_max_w"] = float(pmax)
      except (TypeError, ValueError):
        continue
    out = [
        row
        for row in per_device.values()
        if "util_max" in row
    ]
    out.sort(key=lambda r: (r.get("host") or "", r.get("dev") or ""))
    if out:
      return out
  return []


def reduce_gpu_agg_to_util_stats(
  agg: Any,
) -> Tuple[Optional[int], Optional[float], Optional[float]]:
  """
  From cached ORM aggregate rows (list of dict) to active/max/mean.
  
  Args:
    agg (Any): Agg passed to this helper.
  
  Returns:
    Tuple[Optional[int], Optional[float], Optional[float]]:
    Tuple[Optional[int], Optional[float], Optional[float]] produced by this
    call.
  
  Examples:
    >>> reduce_gpu_agg_to_util_stats(None)  # doctest: +SKIP
  """
  gpu_active: Optional[int] = None
  gpu_max: Optional[float] = None
  gpu_mean: Optional[float] = None

  if not isinstance(agg, (list, tuple)):
    agg = []
  rows = [r for r in agg if isinstance(r, dict)]
  per_device: dict = {}
  for r in rows:
    device_key = (str(r.get("host") or ""), str(r.get("dev") or ""))
    event = str(r.get("event") or "")
    slot = per_device.setdefault(device_key, {})
    slot[event] = r

  selected_rows = []
  for slot in per_device.values():
    row = slot.get("gpu_util") or slot.get("utilization")
    if row:
      selected_rows.append(row)

  valid_rows = []
  for row in selected_rows:
    cnt = int(row.get("cnt") or 0)
    vmax = row.get("vmax")
    vmean = row.get("vmean")
    if cnt <= 2 or vmax is None:
      continue
    try:
      vmax_f = float(vmax)
      vmean_f = float(vmean) if vmean is not None else None
    except (TypeError, ValueError):
      continue
    if is_dcgm_numeric_blank(vmax_f):
      continue
    if vmean_f is not None and is_dcgm_numeric_blank(vmean_f):
      vmean_f = None
    valid_rows.append((cnt, vmax_f, vmean_f))

  if valid_rows:
    gpu_max = sum(vmax_f for _cnt, vmax_f, _vmean_f in valid_rows)
    mean_values = [
        vmean_f for _cnt, _vmax_f, vmean_f in valid_rows if vmean_f is not None
    ]
    if mean_values:
      gpu_mean = sum(mean_values)
    gpu_active = sum(1 for _cnt, vmax_f, _vmean_f in valid_rows if vmax_f > 0.0)

  return gpu_active, gpu_max, gpu_mean


def compute_job_gpu_summary_tuple(
  j: Any,
) -> Tuple[ Optional[int], Optional[float], Optional[float], Optional[int], ]:
  """
  Fresh host_data reads: (gpu_active, gpu_util_max, gpu_util_mean, gpu_count).
  
  Args:
    j (Any): Job record (Django ``job_data`` or job-like mapping).
  
  Returns:
    Tuple[ Optional[int], Optional[float], Optional[float], Optional[int], ]:
    Tuple[ Optional[int], Optional[float], Optional[float], Optional[int], ]
    produced by this call.
  
  Examples:
    >>> compute_job_gpu_summary_tuple(None)  # doctest: +SKIP
  """
  try:
    agg_list = gpu_agg_rows_for_job_window(j)
    gpu_active, gpu_max, gpu_mean = reduce_gpu_agg_to_util_stats(agg_list)
    gpu_count = gpu_count_total_for_job_window(j)
    return gpu_active, gpu_max, gpu_mean, gpu_count
  except Exception:
    return None, None, None, None
