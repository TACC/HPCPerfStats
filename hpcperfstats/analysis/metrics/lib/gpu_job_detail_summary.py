"""
ORM GPU aggregates for job-detail and metrics_data (shared with API).

Uses the same reduction rules as historical ``api._compute_job_gpu_stats``:
hostxtime chunked ``host__in``, util aggregates by vendor precedence, then
``gpu_count``.

Vendor precedence for summary fields: ``nvidia_gpu`` -> ``amd_gpu`` ->
``intel_gpu`` (no mixed-vendor merge for a single field). DCGM blank-family
samples are rejected.

Attributes:
  GPU_TYPE_PRECEDENCE: ``GPU_TYPE_PRECEDENCE``.
  _GPU_UTIL_EVENTS: ``_GPU_UTIL_EVENTS``.
"""

from __future__ import annotations

from typing import Any

from hpcperfstats.lib.dcgm_blank import is_dcgm_numeric_blank

GPU_TYPE_PRECEDENCE: tuple[str, ...] = ("nvidia_gpu", "amd_gpu", "intel_gpu")
_GPU_UTIL_EVENTS = ("gpu_util", "utilization")


def _coerce_job_jid(j: Any) -> str | None:
  """
  Return a real job id string from a job-like object, or ``None``.

  Ignores non-string/int stubs (for example bare ``MagicMock`` attributes)
  so unit tests without a jid do not trigger affinity ORM lookups.

  Args:
    j (Any): Job record or mapping.

  Returns:
    str | None: Stripped jid, or ``None`` when unset/invalid.

  Examples:
    >>> _coerce_job_jid(type("J", (), {"jid": "42"})())
    '42'
  """
  jid = getattr(j, "jid", None)
  if jid is None and isinstance(j, dict):
    jid = j.get("jid")
  if not isinstance(jid, (str, int)):
    return None
  s = str(jid).strip()
  return s or None


def gpu_agg_rows_for_job_window(j: Any) -> list[dict]:
  """
  Per-(host, dev, event) Count/Max/Avg for GPU util in the job window.

  Uses the first vendor in ``GPU_TYPE_PRECEDENCE`` that has util rows, read
  from the job's JobFrame (``ColumnarJobTable``).

  Args:
    j (Any): ``ColumnarJobTable`` of the job.

  Returns:
    List[dict]: Rows ``host, dev, event, cnt, vmax, vmean``.

  Examples:
    >>> gpu_agg_rows_for_job_window(None)  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics.lib.columnar.detail_series import (
    gpu_agg_rows,
  )

  return gpu_agg_rows(j)


def gpu_count_total_for_job_window(j: Any) -> int | None:
  """
  Shared jobs return ``len(gpu_list)``. Exclusive jobs sum per-host
  max(gpu_count) (nvidia -> amd -> intel) from the JobFrame.

  Args:
    j (Any): ``ColumnarJobTable`` of the job.

  Returns:
    Optional[int]: GPU count, or None when unavailable.

  Examples:
    >>> gpu_count_total_for_job_window(None)  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics.lib.columnar.detail_series import (
    gpu_count_total,
  )

  return gpu_count_total(j)


def gpu_inventory_for_job_window(j: Any) -> list[dict]:
  """
  Per-(host, dev) util max/mean (+ optional power peak) for Resources inventory.

  Args:
    j (Any): ``ColumnarJobTable`` of the job.

  Returns:
    List[dict]: Inventory rows sorted by host then dev.

  Examples:
    >>> gpu_inventory_for_job_window(None)  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics.lib.columnar.detail_series import (
    gpu_inventory,
  )

  return gpu_inventory(j)


def reduce_gpu_agg_to_util_stats(
  agg: Any,
) -> tuple[int | None, float | None, float | None]:
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
  gpu_active: int | None = None
  gpu_max: float | None = None
  gpu_mean: float | None = None

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
    except TypeError, ValueError:
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
) -> tuple[
  int | None,
  float | None,
  float | None,
  int | None,
]:
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
