"""
ColumnarJobTable: the ``jid_table`` data surface served from one JobFrame.

``jid_table`` keeps the job metadata reads (window, accounting hosts,
schema) on the ORM; every ``host_data`` read that the pandas path issued as
a SQL statement is answered here by a kernel over the frame fetched once
per job (plan decision T4 / Stage 4). Accessors return arrays and plain
Python containers, never DataFrames.

Each accessor documents the pandas-path function it replaces so parity
reviews can be done side by side.

Attributes:
  _GPU_TYPES: GPU ``host_data.type`` names (DCGM blank filter on sums).
  HANDOFF: Process-wide metrics-to-prewarm table handoff.
  _JOB_ARC_SQL_FIRST: Order of engines ``job_arc`` tries (SQL CTE first,
    then the ORM ``Coalesce(Sum, 0)`` rows), mirroring the pandas path.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame
from hpcperfstats.analysis.metrics.lib.columnar.kernels import (
  MODE_ORM,
  MODE_SQL,
  AggTable,
  FrameIndex,
  bucketed_job_value,
)
from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
  is_job_scoped_host_data_type,
  sane_counter_abs_limit,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  canonical_event_name_for_type,
  events_probe_names,
  type_probe_names,
)
from hpcperfstats.lib.dcgm_blank import is_dcgm_numeric_blank

_GPU_TYPES = frozenset({"nvidia_gpu", "amd_gpu", "intel_gpu"})
_JOB_ARC_SQL_FIRST = (MODE_SQL, MODE_ORM)


@dataclass(frozen=True)
class HostTimeTable:
  """
  Distinct job-owned ``(host, time)`` pairs sorted by host then time.

  Attributes:
    host_id: Host id per row.
    time_us: Sample time per row (epoch µs).
    hosts: Host names by id (shared with the frame).
  """

  host_id: np.ndarray
  time_us: np.ndarray
  hosts: list[str]

  def __len__(self) -> int:
    """
    Row count.

    Returns:
      int: Rows.

    Examples:
      >>> import numpy as np
      >>> len(HostTimeTable(np.array([0]), np.array([1]), ["h"]))
      1
    """
    return int(self.host_id.shape[0])

  @property
  def empty(self) -> bool:
    """
    True without rows.

    Returns:
      bool: Emptiness.

    Examples:
      >>> import numpy as np
      >>> HostTimeTable(
      ...   np.array([], dtype=np.int64), np.array([], dtype=np.int64), []
      ... ).empty
      True
    """
    return self.host_id.shape[0] == 0

  def host_names(self) -> list[str]:
    """
    Host name per row.

    Returns:
      list[str]: Names.

    Examples:
      >>> import numpy as np
      >>> HostTimeTable(np.array([0]), np.array([1]), ["h"]).host_names()
      ['h']
    """
    return [self.hosts[int(i)] for i in self.host_id]


def _parse_dt(value: Any) -> datetime | None:
  """
  ISO text, epoch microseconds (golden fixtures) or datetime to an aware
  datetime.

  Args:
    value (Any): ISO text, int epoch µs, datetime or None.

  Returns:
    datetime | None: Parsed value.

  Examples:
    >>> _parse_dt("2026-01-01T00:00:00+00:00").year
    2026
    >>> _parse_dt(1_767_225_600_000_000).year
    2026
    >>> _parse_dt(None) is None
    True
  """
  if value is None or isinstance(value, datetime):
    return value
  if isinstance(value, (int, float)) and not isinstance(value, bool):
    return datetime.fromtimestamp(float(value) / 1_000_000.0, tz=UTC)
  return datetime.fromisoformat(str(value))


class ColumnarJobTable:
  """
  Job view for metrics, Job Detail and plots backed by one JobFrame.

  Attributes:
    jid: Job id.
    frame: The JobFrame.
    index: Kernel query layer over ``frame``.
    host_list: Hosts with job-owned rows in the window (sorted).
    acct_host_list: Accounting hosts as ``host_data`` FQDNs.
    schema: ``type -> sorted events`` as ``jid_table.schema``.
    start_time: Window start.
    end_time: Window end.
    time_in: Sampled time axis for large jobs, or None.
    gpu_affinity_devs: ``gpu_devs_for_job`` result or None.
    units: ``(type, event) -> unit`` labels (type-detail plots).
    coalesce_zero: Aggregate rule for groups without a valid sample.
    _shared_node: Shared-node flag (affinity rows exist).
    _base_filter: ``jid_table._base_filter`` equivalent for legacy guards.
    _large_job_plot_cache_token: Cache token mirrored from ``jid_table``.
    _agg_memo: Per-table memo of aggregate results.
  """

  def __init__(
    self,
    jid: str,
    frame: JobFrame,
    *,
    host_list: Sequence[str],
    acct_host_list: Sequence[str],
    schema: dict[str, list[str]] | None,
    start_time: Any,
    end_time: Any,
    time_in: Sequence[Any] | None = None,
    shared: bool = False,
    gpu_affinity_devs: Sequence[str] | None = None,
    units: dict[tuple[str, str], str] | None = None,
    coalesce_zero: bool = True,
  ) -> None:
    """
    Wrap a frame with the job metadata the consumers read from ``jid_table``.

    Args:
      jid (str): Job id.
      frame (JobFrame): Frame fetched for this job.
      host_list (Sequence[str]): Hosts with owned rows.
      acct_host_list (Sequence[str]): Accounting host FQDNs.
      schema (dict[str, list[str]] | None): Type to events.
      start_time (Any): Window start (datetime or ISO text).
      end_time (Any): Window end (datetime or ISO text).
      time_in (Sequence[Any] | None): Sampled times for large jobs.
      shared (bool): Shared-node job.
      gpu_affinity_devs (Sequence[str] | None): Allowed GPU devices.
      units (dict[tuple[str, str], str] | None): ``(type, event) -> unit``
        labels for the type-detail plots.
      coalesce_zero (bool): Aggregate groups without a valid sample sum to
        ``0`` (job plots) or stay NaN (single-host plots).

    Returns:
      None

    Examples:
      >>> t = ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... )
      >>> t.has_host_data()
      False
    """
    self.jid = str(jid)
    self.frame = frame
    self._shared_node = bool(shared)
    residual = [
      t for t, _e in frame.series if not is_job_scoped_host_data_type(t)
    ]
    self.gpu_affinity_devs = (
      None if gpu_affinity_devs is None else [str(d) for d in gpu_affinity_devs]
    )
    self.index = FrameIndex(
      frame,
      shared=self._shared_node,
      residual_types=residual,
      gpu_affinity_devs=self.gpu_affinity_devs,
    )
    self.host_list = [str(h) for h in host_list]
    self.acct_host_list = [str(h) for h in acct_host_list]
    self.schema = dict(schema or {})
    self.start_time = _parse_dt(start_time)
    self.end_time = _parse_dt(end_time)
    self.time_in = list(time_in) if time_in else None
    if self.acct_host_list and (self.start_time or self.time_in):
      if self.time_in:
        self._base_filter: dict[str, Any] = {
          "time__in": self.time_in,
          "host__in": self.acct_host_list,
        }
      else:
        self._base_filter = {
          "time__gte": self.start_time,
          "time__lte": self.end_time,
          "host__in": self.acct_host_list,
        }
    else:
      self._base_filter = {}
    self._large_job_plot_cache_token = (
      f"lb{len(self.time_in)}" if self.time_in else "full"
    )
    self.units: dict[tuple[str, str], str] = dict(units or {})
    self.coalesce_zero = bool(coalesce_zero)
    self._agg_memo: dict[tuple[Any, ...], AggTable] = {}

  # -- construction -------------------------------------------------------

  @classmethod
  def from_jid_table(
    cls, jt: Any, frame: JobFrame | None = None
  ) -> ColumnarJobTable:
    """
    Build from a live ``jid_table`` (fetching the frame when not given).

    Args:
      jt (Any): ``jid_table`` instance.
      frame (JobFrame | None): Pre-fetched frame.

    Returns:
      ColumnarJobTable: Table.

    Examples:
      >>> ColumnarJobTable.from_jid_table(jt)  # doctest: +SKIP
    """
    if frame is None:
      from hpcperfstats.analysis.metrics.lib.columnar.copy_fetch import (
        fetch_job_frame,
      )

      frame = fetch_job_frame(jt)
    base = getattr(jt, "_base_filter", None) or {}
    aff = getattr(jt, "_gpu_affinity_devs_cached", None)
    return cls(
      str(getattr(jt, "jid", "")),
      frame,
      host_list=getattr(jt, "host_list", None) or [],
      acct_host_list=getattr(jt, "acct_host_list", None) or [],
      schema=getattr(jt, "schema", None) or {},
      start_time=getattr(jt, "start_time", None),
      end_time=getattr(jt, "end_time", None),
      time_in=base.get("time__in"),
      shared=bool(getattr(jt, "_shared_node", False)),
      gpu_affinity_devs=aff() if callable(aff) else None,
      units=getattr(jt, "units", None) or {},
    )

  @classmethod
  def from_golden(
    cls, jid: str, frame: JobFrame, meta: dict[str, Any]
  ) -> ColumnarJobTable:
    """
    Build from a golden fixture's ``jid_table`` metadata block.

    Args:
      jid (str): Job id.
      frame (JobFrame): Frame loaded from ``jobs/<jid>.frame.npz``.
      meta (dict[str, Any]): ``golden["jid_table"]``.

    Returns:
      ColumnarJobTable: Table.

    Examples:
      >>> ColumnarJobTable.from_golden("1", frame, meta)  # doctest: +SKIP
    """
    return cls(
      jid,
      frame,
      host_list=meta.get("host_list") or [],
      acct_host_list=meta.get("acct_host_list") or [],
      schema=meta.get("schema") or {},
      start_time=meta.get("start_time"),
      end_time=meta.get("end_time"),
      time_in=meta.get("time_in"),
      shared=bool(meta.get("shared_node")),
      gpu_affinity_devs=meta.get("gpu_affinity_devs"),
    )

  # -- context manager (jid_table parity) ---------------------------------

  def __enter__(self) -> ColumnarJobTable:
    """
    Context-manager entry (no resources to acquire).

    Returns:
      ColumnarJobTable: self.

    Examples:
      >>> with ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ) as t:
      ...   t.jid
      '1'
    """
    return self

  def __exit__(self, *exc: Any) -> None:
    """
    Context-manager exit (no-op).

    Args:
      *exc (Any): Exception triple, ignored.

    Returns:
      None

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).__exit__(None, None, None)
    """
    return None

  # -- basic reads --------------------------------------------------------

  def has_host_data(self) -> bool:
    """
    ``jt._host_data_qs().exists()``: any job-owned row in the window.

    Returns:
      bool: Presence of owned rows.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).has_host_data()
      False
    """
    return bool(self._base_filter) and bool(self.index.owned.any())

  def get_host_time(self) -> HostTimeTable:
    """
    ``jid_table.get_host_time_df``: distinct owned ``(host, time)``.

    Returns:
      HostTimeTable: Sorted pairs.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).get_host_time().empty
      True
    """
    if not self._base_filter or not self.acct_host_list:
      return HostTimeTable(
        np.empty(0, dtype=np.int64),
        np.empty(0, dtype=np.int64),
        self.frame.hosts,
      )
    h, t = self.index.host_time_keys()
    return HostTimeTable(h, t, self.frame.hosts)

  def distinct_time_count(self) -> int:
    """
    Sum over hosts of distinct owned sample times (``per_host_distinct_time_sum``).

    Returns:
      int: Count.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).distinct_time_count()
      0
    """
    return self.index.distinct_time_count_per_host_sum()

  # -- aggregates ---------------------------------------------------------

  def get_aggregate(
    self,
    typ: str,
    val_col: str,
    events: Sequence[str],
    conv: float = 1.0,
    *,
    group_by_dev: bool = False,
  ) -> AggTable:
    """
    ``jid_table.get_aggregate_df``: per-sample sums of ``val_col`` for the
    first probe type with rows.

    The pandas path answered this with two engines depending on the time
    chunk (D36): the inline SQL SUM (NULL and over-ceiling samples dropped,
    group absent without a valid sample) for the last hourly chunk and the
    ORM ``Coalesce(SUM, 0)`` (no ceiling) for earlier chunks. The columnar
    path uses one rule for every sample: a group exists for every scoped
    sample, NULL samples (and ``arc`` / ``delta`` samples at or above the
    sane ceiling) do not contribute, and a group without a contribution
    sums to ``0``. ``value`` is never ceiling-filtered (peak FLOP/s and
    cumulative byte counters exceed 1e13). GPU types drop ``val_col >=
    DCGM_FP64_BLANK`` rows and never apply the ceiling; ``group_by_dev``
    keeps devices and applies the GPU affinity filter. Shared-node residual
    types collapse hardware / ``j:`` copies per event.

    Args:
      typ (str): Type name or alias.
      val_col (str): ``arc``, ``value`` or ``delta``.
      events (Sequence[str]): Event names (probe-expanded here).
      conv (float): Multiplier.
      group_by_dev (bool): Per-device rows.

    Returns:
      AggTable: Sorted rows (empty when nothing matched).

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).get_aggregate("host_cpu", "arc", ["user"]).empty
      True
    """
    probed = events_probe_names(list(events), typ=typ)
    key = (
      str(typ),
      str(val_col),
      tuple(probed),
      round(float(conv), 12),
      bool(group_by_dev),
    )
    hit = self._agg_memo.get(key)
    if hit is not None:
      return hit
    out = AggTable.zero(by_dev=group_by_dev)
    if (
      self._base_filter
      and self.acct_host_list
      and val_col in ("arc", "value", "delta")
    ):
      candidates = type_probe_names(typ)
      reject_blank = typ in _GPU_TYPES or any(
        t in _GPU_TYPES for t in candidates
      )
      # The sane ceiling guards rates and deltas; ``value`` gauges and raw
      # counters legitimately exceed 1e13 (peak FLOP/s, cumulative bytes).
      limit = (
        sane_counter_abs_limit(val_col)
        if val_col in ("arc", "delta") and not reject_blank
        else None
      )
      for ct in candidates:
        agg = self.index.sum_per_sample(
          ct,
          probed,
          val_col,
          mode=MODE_ORM,
          conv=conv,
          reject_dcgm_blank=reject_blank,
          by_dev=group_by_dev,
          dev_filter=group_by_dev,
          limit=limit,
          coalesce_zero=self.coalesce_zero,
        )
        if not agg.empty:
          out = agg
          break
    self._agg_memo[key] = out
    return out

  def get_aggregate_df(self, *args: Any, **kwargs: Any) -> AggTable:
    """
    Alias of :meth:`get_aggregate` for callers written against ``jid_table``.

    Args:
      *args (Any): Positional ``get_aggregate`` arguments.
      **kwargs (Any): Keyword ``get_aggregate`` arguments.

    Returns:
      AggTable: Aggregate rows.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).get_aggregate_df("host_cpu", "arc", ["user"]).empty
      True
    """
    return self.get_aggregate(*args, **kwargs)

  def job_arc_value(
    self,
    typ: str,
    events: Sequence[str],
    conv: float,
    *,
    nonnegative: bool = False,
    host_aggregate: str = "mean",
  ) -> float | None:
    """
    ``Metrics.job_arc`` for one concrete probe type: SQL-CTE sample sums,
    else the ORM ``Coalesce(Sum, 0)`` rows, then the 5-minute fold.

    Args:
      typ (str): Concrete type name.
      events (Sequence[str]): Probe-expanded event names.
      conv (float): Multiplier.
      nonnegative (bool): Drop negative ``arc`` samples.
      host_aggregate (str): ``mean`` or ``sum`` across hosts.

    Returns:
      float | None: Metric value or None without samples.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).job_arc_value("host_cpu", ["user"], 0.01) is None
      True
    """
    if not self._base_filter or not self.acct_host_list:
      return None
    for mode in _JOB_ARC_SQL_FIRST:
      agg = self.index.sum_per_sample(
        typ, list(events), "arc", mode=mode, nonnegative=nonnegative
      )
      if agg.empty:
        continue
      return bucketed_job_value(
        agg.host_id,
        agg.time_us,
        agg.sum_val,
        conv,
        host_aggregate=host_aggregate,
      )
    return None

  def sample_sums_orm(
    self,
    typ: str,
    events: Sequence[str],
    col: str,
    *,
    nonnegative: bool = False,
  ) -> AggTable:
    """
    ``_host_data_metric_rows_batched(sum_per_sample=True)``: ORM-engine sums.

    Args:
      typ (str): Concrete type name.
      events (Sequence[str]): Probe-expanded events.
      col (str): Column.
      nonnegative (bool): ``SUM FILTER (col >= 0)``.

    Returns:
      AggTable: Rows (empty when no scoped row exists).

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).sample_sums_orm("host_cpu", ["user"], "arc").empty
      True
    """
    if not self._base_filter or not self.acct_host_list:
      return AggTable.zero()
    return self.index.sum_per_sample(
      typ, list(events), col, mode=MODE_ORM, nonnegative=nonnegative
    )

  def raw_rows(
    self,
    typ: str,
    events: Sequence[str],
    col: str,
  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    ``_host_data_metric_rows_batched`` raw rows: ``(host_id, time_us, value)``.

    Args:
      typ (str): Concrete type name.
      events (Sequence[str]): Probe-expanded events.
      col (str): Column.

    Returns:
      tuple[np.ndarray, np.ndarray, np.ndarray]: Arrays (NaN for NULL).

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).raw_rows("nvidia_gpu", ["gpu_util"], "value")[2].shape
      (0,)
    """
    if not self._base_filter or not self.acct_host_list:
      e = np.empty(0, dtype=np.int64)
      return e, e.copy(), np.empty(0, dtype=np.float64)
    return self.index.raw_rows(typ, list(events), col)

  # -- FSIO deltas ---------------------------------------------------------

  def _delta_by_event(
    self, alias: str, events: Sequence[str]
  ) -> dict[str, float]:
    """
    First probe type with delta sums; events canonicalised and merged.

    Args:
      alias (str): Canonical type alias (``lustre_llite`` …).
      events (Sequence[str]): Canonical event names.

    Returns:
      dict[str, float]: Canonical event to delta sum (empty when none).

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... )._delta_by_event("lustre_llite", ["vfs_read_bytes"])
      {}
    """
    if not self._base_filter or not self.acct_host_list:
      return {}
    for typ in type_probe_names(alias):
      probed = events_probe_names(list(events), typ=typ)
      sums = self.index.delta_sum_by_event(typ, probed)
      if not sums:
        continue
      out: dict[str, float] = {}
      for ev, total in sums.items():
        canon = canonical_event_name_for_type(typ, str(ev))
        out[canon] = out.get(canon, 0.0) + float(total)
      return dict(sorted(out.items()))
    return {}

  def get_llite_delta_by_event(self) -> dict[str, float]:
    """
    ``jid_table.get_llite_delta_by_event`` as ``{event: delta_sum}``.

    Returns:
      dict[str, float]: Canonical ``vfs_*`` byte deltas.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).get_llite_delta_by_event()
      {}
    """
    return self._delta_by_event(
      "lustre_llite", ["vfs_read_bytes", "vfs_write_bytes"]
    )

  def get_beegfs_delta_by_event(self) -> dict[str, float]:
    """
    ``jid_table.get_beegfs_delta_by_event`` as ``{event: delta_sum}``.

    Returns:
      dict[str, float]: Canonical byte deltas.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).get_beegfs_delta_by_event()
      {}
    """
    return self._delta_by_event(
      "beegfs_client", ["vfs_read_bytes", "vfs_write_bytes"]
    )

  def get_nfs_delta_totals_mb(self) -> list[float] | None:
    """
    ``jid_table.get_nfs_delta_totals_mb``: ``[read_mb, write_mb]`` or None.

    Returns:
      list[float] | None: Totals, or None when both are zero / absent.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).get_nfs_delta_totals_mb() is None
      True
    """
    read_events = ("normal_read", "direct_read", "server_read")
    write_events = ("normal_write", "direct_write", "server_write")
    if not self._base_filter or not self.acct_host_list:
      return None
    rows: dict[str, float] = {}
    for typ in type_probe_names("nfs"):
      rows = self.index.delta_sum_by_event(typ, [*read_events, *write_events])
      if rows:
        break
    if not rows:
      return None
    read_total = sum(v for e, v in rows.items() if e in read_events)
    write_total = sum(v for e, v in rows.items() if e in write_events)
    if read_total == 0.0 and write_total == 0.0:
      return None
    return [read_total / (1024 * 1024), write_total / (1024 * 1024)]

  # -- GPU aggregates -------------------------------------------------------

  def gpu_util_rows(
    self, gpu_typ: str, events: Sequence[str]
  ) -> list[dict[str, Any]]:
    """
    ``_collect_gpu_annotate_rows`` with Count/Max/Avg per (host, dev, event).

    Args:
      gpu_typ (str): GPU type.
      events (Sequence[str]): Util event names.

    Returns:
      list[dict[str, Any]]: Rows ``host, dev, event, cnt, vmax, vmean``.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).gpu_util_rows("nvidia_gpu", ["gpu_util"])
      []
    """
    if not self._base_filter or not self.acct_host_list:
      return []
    st = self.index.gpu_group_stats(gpu_typ, list(events))
    f = self.frame
    return [
      {
        "host": f.hosts[int(h)],
        "dev": f.devs[abs(int(d))]
        if int(d) >= 0
        else f"j:{f.devs[abs(int(d))]}",
        "event": f.series[int(s)][1],
        "cnt": int(c),
        "vmax": float(mx),
        "vmean": float(mn),
      }
      for h, d, s, c, mx, mn in zip(
        st.host_id,
        st.dev_id,
        st.series_id,
        st.count,
        st.vmax,
        st.vmean,
        strict=True,
      )
    ]

  def gpu_max_per_host(self, gpu_typ: str, event: str) -> list[dict[str, Any]]:
    """
    ``_collect_gpu_annotate_rows`` with ``Max(value)`` per host.

    Args:
      gpu_typ (str): GPU type.
      event (str): Event name (``gpu_count``).

    Returns:
      list[dict[str, Any]]: Rows ``host, mv``.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).gpu_max_per_host("nvidia_gpu", "gpu_count")
      []
    """
    if not self._base_filter or not self.acct_host_list:
      return []
    st = self.index.gpu_group_stats(
      gpu_typ, [event], group_dev=False, group_event=False
    )
    return [
      {"host": self.frame.hosts[int(h)], "mv": float(mx)}
      for h, mx in zip(st.host_id, st.vmax, strict=True)
    ]

  def gpu_max_per_host_dev(
    self, gpu_typ: str, event: str
  ) -> list[dict[str, Any]]:
    """
    ``_collect_gpu_annotate_rows`` with ``Max(value)`` per (host, dev).

    Args:
      gpu_typ (str): GPU type.
      event (str): Event name (``power_usage``).

    Returns:
      list[dict[str, Any]]: Rows ``host, dev, pmax``.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).gpu_max_per_host_dev("nvidia_gpu", "power_usage")
      []
    """
    if not self._base_filter or not self.acct_host_list:
      return []
    st = self.index.gpu_group_stats(
      gpu_typ, [event], group_dev=True, group_event=False
    )
    f = self.frame
    return [
      {
        "host": f.hosts[int(h)],
        "dev": f.devs[abs(int(d))]
        if int(d) >= 0
        else f"j:{f.devs[abs(int(d))]}",
        "pmax": float(mx),
      }
      for h, d, mx in zip(st.host_id, st.dev_id, st.vmax, strict=True)
    ]

  def gpu_count_total(self, precedence: Sequence[str]) -> int | None:
    """
    ``gpu_count_total_for_job_window``: shared jobs count affinity GPUs,
    exclusive jobs sum per-host ``max(gpu_count)`` by vendor precedence.

    Args:
      precedence (Sequence[str]): GPU type precedence.

    Returns:
      int | None: Total GPUs or None.

    Examples:
      >>> ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).gpu_count_total(("nvidia_gpu",)) is None
      True
    """
    if self._shared_node:
      return len(self.gpu_affinity_devs or [])
    if not self.acct_host_list:
      return None
    for gpu_typ in precedence:
      rows = self.gpu_max_per_host(gpu_typ, "gpu_count")
      if not rows:
        continue
      total = 0
      for r in rows:
        v = r.get("mv")
        if v is None or is_dcgm_numeric_blank(v):
          continue
        total += round(float(v))
      if total > 0:
        return total
    return None

  # -- metrics view -------------------------------------------------------

  def metrics_view(self) -> Any:
    """
    ``_JobForMetrics`` replacement: per-host and cluster grids per type.

    Returns:
      Any: Object with ``jid``, ``hosts``, ``schemas``, ``times``,
      ``cluster_mean_by_type``, ``cluster_mean_arc_by_type``,
      ``per_host_distinct_time_sum`` and ``acct``.

    Examples:
      >>> v = ColumnarJobTable(
      ...   "1",
      ...   JobFrame.from_records("1", []),
      ...   host_list=[],
      ...   acct_host_list=[],
      ...   schema={},
      ...   start_time=None,
      ...   end_time=None,
      ... ).metrics_view()
      >>> v.per_host_distinct_time_sum
      0
    """
    from hpcperfstats.analysis.metrics.lib.metrics import (
      _coerce_metrics_identity_str,
      _Host,
      _Schema,
    )

    view = _ColumnarMetricsView(self.jid)
    if not self.has_host_data():
      return view
    view.per_host_distinct_time_sum = self.distinct_time_count()
    owned_hosts = np.unique(self.frame.host_id[self.index.owned])
    for hid in owned_hosts:
      view.hosts[_coerce_metrics_identity_str(self.frame.hosts[int(hid)])] = (
        _Host()
      )
    times_set = False
    for raw_typename, events in self.schema.items():
      typename = _coerce_metrics_identity_str(raw_typename)
      ev_list = [_coerce_metrics_identity_str(e) for e in (events or [])]
      view.schemas[typename] = _Schema(ev_list)
      if not ev_list:
        continue
      piv = self.index.metrics_pivot(typename, ev_list)
      if not times_set:
        view.times = piv["times_s"]
        times_set = True
      if piv["host_ids"].shape[0] == 0:
        continue
      view.cluster_mean_by_type[typename] = piv["cluster_value"]
      view.cluster_mean_arc_by_type[typename] = piv["cluster_arc"]
      for k, hid in enumerate(piv["host_ids"].tolist()):
        host_obj = view.hosts.get(
          _coerce_metrics_identity_str(self.frame.hosts[int(hid)])
        )
        if host_obj is None:
          continue
        host_obj.stats.setdefault(typename, {})["agg"] = np.ascontiguousarray(
          piv["per_host"][k]
        )
    if not times_set:
      axis = np.unique(self.frame.time_us[self.index.owned])
      view.times = np.floor_divide(axis, 1_000_000).astype(np.float64)
    return view


def open_job_table(job: Any, *, handoff: bool = True) -> ColumnarJobTable:
  """
  Build the per-job table: ``jid_table`` metadata reads plus one COPY fetch.

  The table is also stored in :data:`HANDOFF` so the detail / plot prewarm
  of the same jid reuses the frame (plan T4: one fetch per job).

  Args:
    job (Any): Job-like object with ``jid`` (candidate ref or model).
    handoff (bool): Register the table for the prewarm stage.

  Returns:
    ColumnarJobTable: Table (empty frame when the job has no window).

  Examples:
    >>> open_job_table(job_ref)  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod
  from hpcperfstats.analysis.metrics.lib.phase_profile import phase

  jid = str(getattr(job, "jid", job))
  with jt_mod.jid_table(jid) as jt, phase("db_fetch"):
    table = ColumnarJobTable.from_jid_table(jt)
  if handoff:
    HANDOFF.put(table)
  return table


def host_data_table(
  host_fqdn: str, start_time: Any, end_time: Any
) -> ColumnarJobTable:
  """
  Single-host table for the host plot: every ``host_data`` row of one host
  in a time range, no job scope (the ``HostDataProvider`` replacement).

  Groups without a valid sample stay NaN (``coalesce_zero=False``), as the
  single-host aggregates did.

  Args:
    host_fqdn (str): Host FQDN.
    start_time (Any): Window start.
    end_time (Any): Window end.

  Returns:
    ColumnarJobTable: Table whose ``jid`` is the short host name.

  Examples:
    >>> host_data_table("c1.cluster", start, end)  # doctest: +SKIP
  """
  from types import SimpleNamespace

  from hpcperfstats.analysis.metrics.lib.columnar.copy_fetch import (
    fetch_job_frame,
  )

  start = _parse_dt(start_time)
  end = _parse_dt(end_time)
  base = {"time__gte": start, "time__lte": end, "host__in": [str(host_fqdn)]}
  probe = SimpleNamespace(
    jid="",
    acct_host_list=[str(host_fqdn)],
    _base_filter=base,
    _host_data_time_filter_kwargs=lambda: {
      "time__gte": start,
      "time__lte": end,
    },
    _shared_node=False,
  )
  frame = fetch_job_frame(probe, job_scoped=False)
  return ColumnarJobTable(
    str(host_fqdn).split(".")[0].replace("-", "_"),
    frame,
    host_list=[str(host_fqdn)],
    acct_host_list=[str(host_fqdn)],
    schema=frame.schema(),
    start_time=start,
    end_time=end,
    coalesce_zero=False,
  )


class _ColumnarMetricsView:
  """
  Job-like object consumed by ``gen.utils.utils`` and the complex metrics.

  Attributes:
    jid: Job id.
    hosts: Host name to ``_Host`` (stats by type).
    schemas: Type name to ``_Schema``.
    times: Sample times (float seconds).
    cluster_mean_by_type: Type to ``(n_times, n_events)`` value means.
    cluster_mean_arc_by_type: Type to ``(n_times, n_events)`` arc means.
    per_host_distinct_time_sum: Sample count for invalidation.
    acct: ``{"cores": 1, "nodes": 1}`` placeholder as in ``_JobForMetrics``.
  """

  def __init__(self, jid: str) -> None:
    """
    Empty view.

    Args:
      jid (str): Job id.

    Returns:
      None

    Examples:
      >>> _ColumnarMetricsView("1").times.shape
      (0,)
    """
    self.jid = jid
    self.hosts: dict[str, Any] = {}
    self.schemas: dict[str, Any] = {}
    self.times = np.array([], dtype=np.float64)
    self.cluster_mean_by_type: dict[str, np.ndarray] = {}
    self.cluster_mean_arc_by_type: dict[str, np.ndarray] = {}
    self.per_host_distinct_time_sum = 0
    self.acct = {"cores": 1, "nodes": 1}


class JobTableHandoff:
  """
  Thread-safe per-jid handoff of a ColumnarJobTable from the metrics worker
  to the detail/plot prewarm (T4: one fetch per job).

  Attributes:
    _lock: Guard.
    _tables: Pending tables by jid.
    max_entries: Cap; oldest entries are dropped past it.
  """

  def __init__(self, max_entries: int = 256) -> None:
    """
    Create an empty handoff.

    Args:
      max_entries (int): Maximum pending tables.

    Returns:
      None

    Examples:
      >>> JobTableHandoff().pop("1") is None
      True
    """
    self._lock = threading.Lock()
    self._tables: dict[str, ColumnarJobTable] = {}
    self.max_entries = int(max_entries)

  def put(self, table: ColumnarJobTable) -> None:
    """
    Store a table for its jid (replacing any pending one).

    Args:
      table (ColumnarJobTable): Table.

    Returns:
      None

    Examples:
      >>> h = JobTableHandoff()
      >>> h.put(
      ...   ColumnarJobTable(
      ...     "1",
      ...     JobFrame.from_records("1", []),
      ...     host_list=[],
      ...     acct_host_list=[],
      ...     schema={},
      ...     start_time=None,
      ...     end_time=None,
      ...   )
      ... )
      >>> h.pop("1").jid
      '1'
    """
    with self._lock:
      self._tables[table.jid] = table
      while len(self._tables) > self.max_entries:
        oldest = next(iter(self._tables))
        del self._tables[oldest]

  def pop(self, jid: str) -> ColumnarJobTable | None:
    """
    Take the pending table for ``jid`` (or None).

    Args:
      jid (str): Job id.

    Returns:
      ColumnarJobTable | None: Table.

    Examples:
      >>> JobTableHandoff().pop("x") is None
      True
    """
    with self._lock:
      return self._tables.pop(str(jid), None)

  def discard(self, jid: str) -> None:
    """
    Drop a pending table (failed job).

    Args:
      jid (str): Job id.

    Returns:
      None

    Examples:
      >>> JobTableHandoff().discard("x")
    """
    with self._lock:
      self._tables.pop(str(jid), None)


HANDOFF = JobTableHandoff()
