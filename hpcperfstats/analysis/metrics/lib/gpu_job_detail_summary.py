"""ORM GPU aggregates for job-detail and metrics_data (shared with API).

Uses the same reduction rules as historical ``api._compute_job_gpu_stats``:
chunked ``host__in``, nvidia util aggregates, then ``gpu_count`` per vendor.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from django.db.models import Avg, Count, Max

from hpcperfstats.analysis.metrics.lib.gen import jid_table as jid_table_mod
from hpcperfstats.site.lib.machine.models import host_data


def gpu_agg_rows_for_job_window(j) -> List[dict]:
  """Per-(host, dev, event) Count/Max/Avg for nvidia_gpu util in the job window."""
  hosts = getattr(j, "acct_host_list", None) or []
  out: List[dict] = []
  for chunk in jid_table_mod._iter_acct_host_batches(hosts):
    out.extend(
        host_data.objects.filter(
            type="nvidia_gpu",
            event__in=["gpu_util", "utilization"],
            time__gte=j.start_time,
            time__lte=j.end_time,
            host__in=chunk,
        )
        .values("host", "dev", "event")
        .annotate(
            cnt=Count("time"),
            vmax=Max("value"),
            vmean=Avg("value"),
        )
    )
  return out


def gpu_count_total_for_job_window(j) -> Optional[int]:
  """Sum over hosts of max(gpu_count) in window (nvidia_gpu or amd_gpu)."""
  hosts = getattr(j, "acct_host_list", None) or []
  if not hosts:
    return None
  for gpu_typ in ("nvidia_gpu", "amd_gpu"):
    rows: List[dict] = []
    for chunk in jid_table_mod._iter_acct_host_batches(hosts):
      rows.extend(
          list(
              host_data.objects.filter(
                  type=gpu_typ,
                  event="gpu_count",
                  time__gte=j.start_time,
                  time__lte=j.end_time,
                  host__in=chunk,
              )
              .values("host")
              .annotate(mv=Max("value"))
          )
      )
    if not rows:
      continue
    total = 0
    for r in rows:
      v = r.get("mv")
      if v is None:
        continue
      try:
        total += int(round(float(v)))
      except (TypeError, ValueError):
        continue
    if total > 0:
      return total
  return None


def reduce_gpu_agg_to_util_stats(
    agg: Any,
) -> Tuple[Optional[int], Optional[float], Optional[float]]:
  """From cached ORM aggregate rows (list of dict) to active/max/mean."""
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


def compute_job_gpu_summary_tuple(j) -> Tuple[
    Optional[int],
    Optional[float],
    Optional[float],
    Optional[int],
]:
  """Fresh host_data reads: (gpu_active, gpu_util_max, gpu_util_mean, gpu_count)."""
  try:
    agg_list = gpu_agg_rows_for_job_window(j)
    gpu_active, gpu_max, gpu_mean = reduce_gpu_agg_to_util_stats(agg_list)
    gpu_count = gpu_count_total_for_job_window(j)
    return gpu_active, gpu_max, gpu_mean, gpu_count
  except Exception:
    return None, None, None, None
