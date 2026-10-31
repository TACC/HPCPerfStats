"""
NumPy reference kernels over one JobFrame (Stage 4 of the columnar plan).

Each function reproduces one ``host_data`` read of the pandas path, with the
same scope rule, NULL handling and sane ceilings, but from the in-memory
frame instead of a SQL statement:

* :meth:`FrameIndex.host_time_keys` — ``jid_table.get_host_time_df``;
* :meth:`FrameIndex.sum_per_sample` — ``host_data_sum_metric_per_sample_rows``
  (``mode="sql"``) and ``host_data_sum_val_per_sample_queryset``
  (``mode="orm"``), the two engines behind ``get_aggregate_df``,
  ``TypeDetailDataProvider.get_aggregate_df`` and the ``job_arc`` sample
  sums;
* :meth:`FrameIndex.raw_rows` — ``_host_data_metric_rows_batched`` without
  ``sum_per_sample`` (``job_value_mean``);
* :meth:`FrameIndex.delta_sum_by_event` — ``_scoped_delta_sum_rows``;
* :meth:`FrameIndex.gpu_group_stats` — ``_collect_gpu_annotate_rows``;
* :meth:`FrameIndex.metrics_pivot` — the ``_JobForMetrics`` pivots.

Every result carries integer ids; names are resolved by the caller.

Attributes:
  MODE_SQL: Per-sample sum engine with NULL / ceiling filters and the
    shared-node hardware-SUM-else-``j:``-MAX collapse.
  MODE_ORM: Plain ``SUM`` engine (``Coalesce(Sum, 0)``) used for GPU,
    per-device and type-detail aggregates.
  _COLUMNS: Float column names a kernel may reduce.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar import segments as sg
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame
from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
  MAX_SANE_COUNTER_DELTA,
  MAX_SANE_COUNTER_RATE,
)
from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK

MODE_SQL = "sql"
MODE_ORM = "orm"

_COLUMNS = ("value", "arc", "delta")


@dataclass(frozen=True)
class AggTable:
  """
  Per-sample aggregate rows, sorted by ``(host, time[, dev])``.

  Attributes:
    host_id: int64 host id per row.
    time_us: int64 sample time per row.
    sum_val: float64 aggregate per row (``conv`` already applied).
    dev_id: Signed dev id per row, or None when devices were summed.
  """

  host_id: np.ndarray
  time_us: np.ndarray
  sum_val: np.ndarray
  dev_id: np.ndarray | None = None

  def __len__(self) -> int:
    """
    Row count.

    Returns:
      int: Number of aggregate rows.

    Examples:
      >>> len(AggTable.zero())
      0
    """
    return int(self.sum_val.shape[0])

  @property
  def empty(self) -> bool:
    """
    True when the table has no rows (DataFrame ``empty`` parity).

    Returns:
      bool: Emptiness flag.

    Examples:
      >>> AggTable.zero().empty
      True
    """
    return self.sum_val.shape[0] == 0

  @classmethod
  def zero(cls, *, by_dev: bool = False) -> AggTable:
    """
    Zero-row table.

    Args:
      by_dev (bool): Allocate the ``dev_id`` column.

    Returns:
      AggTable: Empty table.

    Examples:
      >>> AggTable.zero(by_dev=True).dev_id.shape
      (0,)
    """
    return cls(
      np.empty(0, dtype=np.int64),
      np.empty(0, dtype=np.int64),
      np.empty(0, dtype=np.float64),
      np.empty(0, dtype=np.int64) if by_dev else None,
    )

  def keys(self, time_index_of: np.ndarray) -> np.ndarray:
    """
    Composite ``(host, time)`` keys (requires a time-axis index per row).

    Args:
      time_index_of (np.ndarray): Position of each row's time on the job
        time axis.

    Returns:
      np.ndarray: int64 keys.

    Examples:
      >>> import numpy as np
      >>> t = AggTable(np.array([1]), np.array([5]), np.array([2.0]))
      >>> t.keys(np.array([0])).tolist()
      [4294967296]
    """
    return sg.composite_key(self.host_id, time_index_of)


@dataclass(frozen=True)
class GroupStats:
  """
  Count / max / mean per ``(host[, dev][, event])`` group.

  Attributes:
    host_id: Host id per group.
    dev_id: Signed dev id per group (or None).
    series_id: Series id per group (or None).
    count: Row count per group.
    vmax: Maximum value per group.
    vmean: Mean value per group.
  """

  host_id: np.ndarray
  dev_id: np.ndarray | None
  series_id: np.ndarray | None
  count: np.ndarray
  vmax: np.ndarray
  vmean: np.ndarray


class FrameIndex:
  """
  Query layer over a sorted JobFrame: scope masks, series lookup and the
  reference reductions behind every pandas-path read.

  Attributes:
    frame: The JobFrame.
    shared: ``job_is_shared_node(jid)``.
    type_names: Sorted distinct type names present.
    series_type: Type index per series id.
    series_bounds: Row range ``[bounds[s], bounds[s+1])`` per series id.
    host_wide_type_ids: Type indices read host-wide (shared-node residual).
    dev_allow_ids: Hardware dev ids allowed by ``job_affinity`` or None.
    owned: Boolean ownership mask per row.
    n_rows: Row count of the frame.
    _time_axis: Sorted distinct times of the frame (int64 µs).
    _time_index: Position of each row's time on ``_time_axis``.
  """

  def __init__(
    self,
    frame: JobFrame,
    *,
    shared: bool = False,
    residual_types: Iterable[str] = (),
    gpu_affinity_devs: Iterable[str] | None = None,
  ) -> None:
    """
    Build lookup tables for one frame.

    Args:
      frame (JobFrame): Sorted frame (``check_contract`` already passed).
      shared (bool): Shared-node job (affinity rows exist).
      residual_types (Iterable[str]): Types that are not on the job-scoped
        allowlist (read host-wide when ``shared``).
      gpu_affinity_devs (Iterable[str] | None): ``gpu_devs_for_job``
        result; None means unrestricted.

    Returns:
      None

    Examples:
      >>> FrameIndex(JobFrame.from_records("1", [])).n_rows
      0
    """
    self.frame = frame
    self.shared = bool(shared)
    self.n_rows = len(frame)
    self.type_names = sorted({t for t, _e in frame.series})
    type_index = {t: i for i, t in enumerate(self.type_names)}
    self.series_type = np.array(
      [type_index[t] for t, _e in frame.series], dtype=np.int64
    ).reshape(-1)
    n_series = len(frame.series)
    self.series_bounds = np.searchsorted(
      frame.series_id, np.arange(n_series + 1), side="left"
    ).astype(np.int64)
    self.host_wide_type_ids: frozenset[int] = frozenset(
      type_index[t] for t in residual_types if self.shared and t in type_index
    )
    if gpu_affinity_devs is None:
      self.dev_allow_ids: frozenset[int] | None = None
    else:
      allowed = {str(d) for d in gpu_affinity_devs}
      self.dev_allow_ids = frozenset(
        i for i, name in enumerate(frame.devs) if i > 0 and name in allowed
      )
    self.owned = frame.owned.astype(bool)
    self._time_axis = frame.time_axis()
    self._time_index = frame.time_index().astype(np.int64)

  # -- lookup -------------------------------------------------------------

  def series_ids(self, typ: str, events: Sequence[str] | None) -> list[int]:
    """
    Series ids for ``typ`` restricted to ``events`` (all events when None).

    Args:
      typ (str): Type name.
      events (Sequence[str] | None): Event names (probe-expanded by the
        caller) or None.

    Returns:
      list[int]: Matching series ids.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "cpu", "user", None, 1, 1.0, None, None)]
      ... )
      >>> FrameIndex(f).series_ids("cpu", ["user", "nice"])
      [0]
    """
    if events is None:
      return self.frame.series_ids_for_type(typ)
    wanted = set(events)
    return [
      i
      for i, (t, e) in enumerate(self.frame.series)
      if t == typ and e in wanted
    ]

  def type_id(self, typ: str) -> int | None:
    """
    Type index for ``typ`` or None when absent from the frame.

    Args:
      typ (str): Type name.

    Returns:
      int | None: Type index.

    Examples:
      >>> FrameIndex(JobFrame.from_records("1", [])).type_id("x") is None
      True
    """
    try:
      return self.type_names.index(typ)
    except ValueError:
      return None

  def rows_for_series(self, sids: Sequence[int]) -> np.ndarray:
    """
    Row positions of the given series (ascending).

    Args:
      sids (Sequence[int]): Series ids.

    Returns:
      np.ndarray: int64 row indices.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "cpu", "user", None, 1, 1.0, None, None)]
      ... )
      >>> FrameIndex(f).rows_for_series([0]).tolist()
      [0]
    """
    parts = [
      np.arange(self.series_bounds[s], self.series_bounds[s + 1])
      for s in sorted(sids)
    ]
    if not parts:
      return np.empty(0, dtype=np.int64)
    return np.concatenate(parts).astype(np.int64)

  def host_wide(self, typ: str) -> bool:
    """
    True when ``typ`` is read host-wide (shared job, residual type).

    Args:
      typ (str): Type name.

    Returns:
      bool: Host-wide flag.

    Examples:
      >>> FrameIndex(JobFrame.from_records("1", [])).host_wide("host_mem")
      False
    """
    tid = self.type_id(typ)
    return tid is not None and tid in self.host_wide_type_ids

  def scoped_rows(
    self, typ: str, rows: np.ndarray, *, job_only: bool = False
  ) -> np.ndarray:
    """
    Apply ``apply_job_or_host_wide_scope`` (or the job-only scope) to rows.

    Args:
      typ (str): Type name of the rows.
      rows (np.ndarray): Candidate row indices.
      job_only (bool): Force ``apply_job_host_data_scope`` (owned rows).

    Returns:
      np.ndarray: Row indices that pass the scope.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "cpu", "user", None, 0, 1.0, None, None)]
      ... )
      >>> FrameIndex(f).scoped_rows("cpu", np.array([0])).tolist()
      []
    """
    if not job_only and self.host_wide(typ):
      return rows
    return rows[self.owned[rows]]

  def _dev_allowed_mask(self, rows: np.ndarray) -> np.ndarray:
    """
    Mask of rows whose device passes the GPU affinity filter.

    Args:
      rows (np.ndarray): Row indices.

    Returns:
      np.ndarray: Boolean mask aligned with ``rows``.

    Examples:
      >>> FrameIndex(JobFrame.from_records("1", []))._dev_allowed_mask(
      ...   np.empty(0, dtype=np.int64)
      ... ).tolist()
      []
    """
    if self.dev_allow_ids is None:
      return np.ones(rows.shape[0], dtype=bool)
    if not self.dev_allow_ids:
      return np.zeros(rows.shape[0], dtype=bool)
    allow = np.fromiter(self.dev_allow_ids, dtype=np.int64)
    return np.isin(self.frame.dev_id[rows].astype(np.int64), allow)

  def _col(self, name: str) -> np.ndarray:
    """
    Float column by name.

    Args:
      name (str): ``value``, ``arc`` or ``delta``.

    Returns:
      np.ndarray: The column.

    Raises:
      ValueError: Unknown column name.

    Examples:
      >>> FrameIndex(JobFrame.from_records("1", []))._col("arc").shape
      (0,)
    """
    if name not in _COLUMNS:
      raise ValueError(name)
    return getattr(self.frame, name)

  # -- reductions -----------------------------------------------------------

  def host_time_keys(self) -> tuple[np.ndarray, np.ndarray]:
    """
    Distinct ``(host, time)`` over job-owned rows, sorted (``get_host_time_df``).

    Returns:
      tuple[np.ndarray, np.ndarray]: ``(host_id, time_us)`` arrays.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (2, "h", "cpu", "user", None, 1, 1.0, None, None),
      ...     (1, "h", "cpu", "nice", None, 1, 1.0, None, None),
      ...     (1, "h", "mem", "used", None, 0, 1.0, None, None),
      ...   ],
      ... )
      >>> h, t = FrameIndex(f).host_time_keys()
      >>> h.tolist(), t.tolist()
      ([0, 0], [1, 2])
    """
    rows = np.nonzero(self.owned)[0]
    if rows.size == 0:
      return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    key = sg.composite_key(self.frame.host_id[rows], self._time_index[rows])
    uniq = np.unique(key)
    hosts = uniq >> sg.TIME_BITS
    times = self._time_axis[uniq & ((1 << sg.TIME_BITS) - 1)]
    return hosts.astype(np.int64), times.astype(np.int64)

  def distinct_time_count_per_host_sum(self) -> int:
    """
    Sum over hosts of ``COUNT(DISTINCT time)`` on job-owned rows.

    Returns:
      int: Sample count used for invalidation.

    Examples:
      >>> FrameIndex(
      ...   JobFrame.from_records("1", [])
      ... ).distinct_time_count_per_host_sum()
      0
    """
    hosts, _times = self.host_time_keys()
    return int(hosts.shape[0])

  def sum_per_sample(
    self,
    typ: str,
    events: Sequence[str] | None,
    col: str,
    *,
    mode: str = MODE_SQL,
    conv: float = 1.0,
    nonnegative: bool = False,
    reject_dcgm_blank: bool = False,
    by_dev: bool = False,
    dev_filter: bool = False,
    limit: float | None = None,
    coalesce_zero: bool = True,
  ) -> AggTable:
    """
    Sum ``col`` over the selected events and devices per ``(host, time)``.

    A sample is *valid* when it is non-NULL, below the sane ceiling when one
    applies, non-negative when ``nonnegative`` is set, and (``orm`` only,
    ``reject_dcgm_blank``) below ``DCGM_FP64_BLANK``. Shared-node residual
    types collapse each ``(host, time, event)`` to the hardware-device SUM,
    else the ``j:`` MAX, before the per-sample total.

    ``mode="sql"`` mirrors the inline PostgreSQL SUM: the ceiling applies by
    default and a group exists only when it has a valid sample.

    ``mode="orm"`` mirrors ``host_data_sum_val_per_sample_queryset``: a group
    exists for every scoped row that passes the blank / device filters, and
    a group without a valid sample sums to ``0`` (``Coalesce``); the ceiling
    applies only when ``limit`` is given.

    Args:
      typ (str): Type name (already a concrete probe name).
      events (Sequence[str] | None): Event names, or None for every event.
      col (str): ``value``, ``arc`` or ``delta``.
      mode (str): ``MODE_SQL`` or ``MODE_ORM``.
      conv (float): Multiplier applied to each sum.
      nonnegative (bool): Count only non-negative samples as valid.
      reject_dcgm_blank (bool): ``orm`` only: drop rows at or above the
        DCGM blank.
      by_dev (bool): ``orm`` only: group by device too.
      dev_filter (bool): ``orm`` only: apply the GPU affinity dev filter.
      limit (float | None): Sane ceiling; ``sql`` defaults to
        ``sane_counter_abs_limit(col)``, ``orm`` to none.
      coalesce_zero (bool): ``orm`` only: a group without a valid sample
        sums to ``0`` (``Coalesce``); False keeps NaN (host plots).

    Returns:
      AggTable: Sorted aggregate rows.

    Raises:
      ValueError: Unknown ``mode``.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (1, "h", "cpu", "user", None, 1, None, 2.0, None),
      ...     (1, "h", "cpu", "nice", None, 1, None, 3.0, None),
      ...     (2, "h", "cpu", "user", None, 1, None, None, None),
      ...   ],
      ... )
      >>> t = FrameIndex(f).sum_per_sample("cpu", ["user", "nice"], "arc")
      >>> t.time_us.tolist(), t.sum_val.tolist()
      ([1], [5.0])
      >>> o = FrameIndex(f).sum_per_sample("cpu", ["user"], "arc", mode="orm")
      >>> o.time_us.tolist(), o.sum_val.tolist()
      ([1, 2], [2.0, 0.0])
    """
    if mode not in (MODE_SQL, MODE_ORM):
      raise ValueError(mode)
    sids = self.series_ids(typ, events)
    rows = self.scoped_rows(typ, self.rows_for_series(sids))
    if rows.size == 0:
      return AggTable.zero(by_dev=by_dev)
    f = self.frame
    vals = self._col(col)[rows]
    if mode == MODE_SQL and limit is None:
      limit = (
        MAX_SANE_COUNTER_DELTA if col == "delta" else MAX_SANE_COUNTER_RATE
      )
    if mode == MODE_ORM:
      keep = np.ones(rows.shape[0], dtype=bool)
      if reject_dcgm_blank:
        keep &= ~np.isnan(vals) & (vals < DCGM_FP64_BLANK)
      if dev_filter:
        keep &= self._dev_allowed_mask(rows)
      if not keep.any():
        return AggTable.zero(by_dev=by_dev)
      rows, vals = rows[keep], vals[keep]
    valid = sg.finite_mask(vals, limit=limit, nonnegative=nonnegative)
    host = f.host_id[rows].astype(np.int64)
    tix = self._time_index[rows]
    dev = f.dev_id[rows].astype(np.int64)
    if by_dev:
      group_key = sg.composite_key(host, dev + (1 << 15), tix)
    else:
      group_key = sg.composite_key(host, tix)
    if self.host_wide(typ) and not by_dev:
      key_hte = sg.composite_key(host, f.series_id[rows].astype(np.int64), tix)
      k_e, v_e = sg.collapse_residual_copies(key_hte, dev, vals, valid)
      ht_of = ((k_e >> (sg.TIME_BITS + 16)) << sg.TIME_BITS) | (
        k_e & ((1 << sg.TIME_BITS) - 1)
      )
      contrib_key, contrib_val = ht_of, v_e
    else:
      contrib_key, contrib_val = group_key[valid], vals[valid]
    keys = np.unique(contrib_key if mode == MODE_SQL else group_key)
    if keys.size == 0:
      return AggTable.zero(by_dev=by_dev)
    sums = np.zeros(keys.shape[0], dtype=np.float64)
    if contrib_key.size:
      pos = np.searchsorted(keys, contrib_key)
      np.add.at(sums, pos, contrib_val)
      if not coalesce_zero:
        touched = np.zeros(keys.shape[0], dtype=bool)
        touched[pos] = True
        sums[~touched] = np.nan
    elif not coalesce_zero:
      sums[:] = np.nan
    if by_dev:
      return self._agg_from_keys_dev(keys, sums, conv)
    return self._agg_from_keys(keys, sums, conv)

  def _agg_from_keys(
    self, keys: np.ndarray, sums: np.ndarray, conv: float
  ) -> AggTable:
    """
    Decode ``(host, time)`` keys into an AggTable.

    Args:
      keys (np.ndarray): Composite keys (sorted).
      sums (np.ndarray): Sums aligned with ``keys``.
      conv (float): Multiplier.

    Returns:
      AggTable: Table sorted by ``(host, time)``.

    Examples:
      >>> FrameIndex(JobFrame.from_records("1", []))._agg_from_keys(
      ...   np.empty(0, dtype=np.int64), np.empty(0), 1.0
      ... ).empty
      True
    """
    hosts = (keys >> sg.TIME_BITS).astype(np.int64)
    times = self._time_axis[keys & ((1 << sg.TIME_BITS) - 1)].astype(np.int64)
    return AggTable(hosts, times, sums.astype(np.float64) * float(conv))

  def _agg_from_keys_dev(
    self, keys: np.ndarray, sums: np.ndarray, conv: float
  ) -> AggTable:
    """
    Decode ``(host, dev, time)`` keys into an AggTable with ``dev_id``.

    Args:
      keys (np.ndarray): Composite keys (sorted by host, dev code, time).
      sums (np.ndarray): Sums aligned with ``keys``.
      conv (float): Multiplier.

    Returns:
      AggTable: Table sorted by ``(host, time, dev)``.

    Examples:
      >>> FrameIndex(JobFrame.from_records("1", []))._agg_from_keys_dev(
      ...   np.empty(0, dtype=np.int64), np.empty(0), 1.0
      ... ).empty
      True
    """
    time_mask = (1 << sg.TIME_BITS) - 1
    hosts = (keys >> (sg.TIME_BITS + 16)).astype(np.int64)
    dev = ((keys >> sg.TIME_BITS) & 0xFFFF).astype(np.int64) - (1 << 15)
    tix = keys & time_mask
    order = np.lexsort((dev, tix, hosts))
    return AggTable(
      hosts[order],
      self._time_axis[tix[order]].astype(np.int64),
      sums[order].astype(np.float64) * float(conv),
      dev[order],
    )

  def raw_rows(
    self,
    typ: str,
    events: Sequence[str] | None,
    col: str,
  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Scoped raw samples ``(host_id, time_us, value)`` with NaN for NULL
    (``_host_data_metric_rows_batched`` without ``sum_per_sample``).

    Args:
      typ (str): Type name.
      events (Sequence[str] | None): Event names or None.
      col (str): Column name.

    Returns:
      tuple[np.ndarray, np.ndarray, np.ndarray]: Host ids, times, values.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "gpu", "util", "0", 1, 50.0, None, None)]
      ... )
      >>> FrameIndex(f).raw_rows("gpu", ["util"], "value")[2].tolist()
      [50.0]
    """
    rows = self.scoped_rows(
      typ, self.rows_for_series(self.series_ids(typ, events))
    )
    return (
      self.frame.host_id[rows].astype(np.int64),
      self.frame.time_us[rows].astype(np.int64),
      self._col(col)[rows],
    )

  def delta_sum_by_event(
    self,
    typ: str,
    events: Sequence[str],
  ) -> dict[str, float]:
    """
    ``SUM(delta)`` per event (``_scoped_delta_sum_rows``).

    Residual types on shared jobs collapse each ``(host, time, event)`` to
    the hardware SUM else ``j:`` MAX over samples with
    ``abs(delta) < MAX_SANE_COUNTER_DELTA``; other reads keep scoped samples
    strictly inside ``(-limit, limit)`` and sum them.

    Args:
      typ (str): Type name.
      events (Sequence[str]): Event names.

    Returns:
      dict[str, float]: Event name to delta sum (events without rows are
      absent).

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (1, "h", "nfs", "r", None, 1, None, None, 4.0),
      ...     (2, "h", "nfs", "r", None, 1, None, None, 6.0),
      ...   ],
      ... )
      >>> FrameIndex(f).delta_sum_by_event("nfs", ["r", "w"])
      {'r': 10.0}
    """
    out: dict[str, float] = {}
    f = self.frame
    for sid in self.series_ids(typ, events):
      rows = self.scoped_rows(typ, self.rows_for_series([sid]))
      if rows.size == 0:
        continue
      vals = f.delta[rows]
      if self.host_wide(typ):
        mask = sg.finite_mask(vals, limit=MAX_SANE_COUNTER_DELTA)
        key = sg.composite_key(
          f.host_id[rows].astype(np.int64), self._time_index[rows]
        )
        _k, v = sg.collapse_residual_copies(
          key, f.dev_id[rows].astype(np.int64), vals, mask
        )
        if v.size == 0:
          continue
        out[f.series[sid][1]] = float(v.sum())
      else:
        mask = ~np.isnan(vals) & (vals > -MAX_SANE_COUNTER_DELTA)
        mask &= vals < MAX_SANE_COUNTER_DELTA
        if not mask.any():
          continue
        out[f.series[sid][1]] = float(vals[mask].sum())
    return out

  def gpu_group_stats(
    self,
    typ: str,
    events: Sequence[str],
    *,
    group_dev: bool = True,
    group_event: bool = True,
  ) -> GroupStats:
    """
    Count / Max / Avg of ``value`` per group (``_collect_gpu_annotate_rows``).

    Rows are job-owned, carry ``value < DCGM_FP64_BLANK`` (NULL excluded)
    and pass the GPU affinity dev filter.

    Args:
      typ (str): GPU type name.
      events (Sequence[str]): Event names.
      group_dev (bool): Group by device.
      group_event (bool): Group by event (series).

    Returns:
      GroupStats: Per-group statistics sorted by ``(host, dev, series)``.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (1, "h", "gpu", "util", "0", 1, 10.0, None, None),
      ...     (2, "h", "gpu", "util", "0", 1, 30.0, None, None),
      ...   ],
      ... )
      >>> s = FrameIndex(f).gpu_group_stats("gpu", ["util"])
      >>> s.count.tolist(), s.vmax.tolist(), s.vmean.tolist()
      ([2], [30.0], [20.0])
    """
    f = self.frame
    rows = self.scoped_rows(
      typ, self.rows_for_series(self.series_ids(typ, events)), job_only=True
    )
    if rows.size:
      vals = f.value[rows]
      keep = ~np.isnan(vals) & (vals < DCGM_FP64_BLANK)
      keep &= self._dev_allowed_mask(rows)
      rows = rows[keep]
    if rows.size == 0:
      e_i = np.empty(0, dtype=np.int64)
      e_f = np.empty(0, dtype=np.float64)
      return GroupStats(
        e_i,
        e_i if group_dev else None,
        e_i if group_event else None,
        e_i,
        e_f,
        e_f,
      )
    vals = f.value[rows]
    host = f.host_id[rows].astype(np.int64)
    dev = (
      f.dev_id[rows].astype(np.int64) + (1 << 15)
      if group_dev
      else np.zeros_like(host)
    )
    ser = (
      f.series_id[rows].astype(np.int64) if group_event else np.zeros_like(host)
    )
    key = (host << 32) | (dev << 16) | ser
    keys, counts, sums, maxima = sg.group_stats(key, vals)
    return GroupStats(
      (keys >> 32).astype(np.int64),
      (((keys >> 16) & 0xFFFF) - (1 << 15)).astype(np.int64)
      if group_dev
      else None,
      (keys & 0xFFFF).astype(np.int64) if group_event else None,
      counts,
      maxima,
      sums / counts,
    )

  def metrics_pivot(
    self,
    typ: str,
    events: Sequence[str],
  ) -> dict[str, object]:
    """
    ``_JobForMetrics`` arrays for one type over job-owned rows.

    The time axis is the distinct owned sample times of the whole job (all
    types), floored to seconds like ``datetime64[s]``. Per host, each
    ``(time, event)`` cell is the mean of ``value`` over devices; the
    cluster grids are the mean over every host's rows.

    Args:
      typ (str): Type name.
      events (Sequence[str]): Column order (``jt.schema[typ]``).

    Returns:
      dict[str, object]: ``times_s`` (float64), ``host_ids`` (hosts with
      rows of this type, ascending), ``per_host`` (3-D value grid
      ``(n_hosts, n_times, n_events)``), ``cluster_value`` and
      ``cluster_arc`` grids ``(n_times, n_events)``.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (1_000_000, "h", "cpu", "user", "0", 1, 2.0, 1.0, None),
      ...     (1_000_000, "h", "cpu", "user", "1", 1, 4.0, 3.0, None),
      ...   ],
      ... )
      >>> p = FrameIndex(f).metrics_pivot("cpu", ["user", "nice"])
      >>> p["per_host"][0].tolist(), p["cluster_arc"].tolist()
      ([[3.0, nan]], [[2.0, nan]])
    """
    f = self.frame
    owned_rows = np.nonzero(self.owned)[0]
    axis_us = (
      np.unique(f.time_us[owned_rows])
      if owned_rows.size
      else np.empty(0, dtype=np.int64)
    )
    times_s = np.floor_divide(axis_us, 1_000_000).astype(np.float64)
    n_t = axis_us.shape[0]
    n_e = len(events)
    ev_pos = {e: i for i, e in enumerate(events)}
    sids = [
      s for s in self.frame.series_ids_for_type(typ) if f.series[s][1] in ev_pos
    ]
    rows = self.scoped_rows(typ, self.rows_for_series(sids), job_only=True)
    empty_grid = np.full((n_t, n_e), np.nan, dtype=np.float64)
    if rows.size == 0 or n_t == 0:
      return {
        "times_s": times_s,
        "host_ids": np.empty(0, dtype=np.int64),
        "per_host": np.empty((0, n_t, n_e), dtype=np.float64),
        "cluster_value": empty_grid,
        "cluster_arc": empty_grid.copy(),
      }
    tix = np.searchsorted(axis_us, f.time_us[rows]).astype(np.int64)
    eix = np.array(
      [ev_pos[f.series[s][1]] for s in f.series_id[rows]], dtype=np.int64
    )
    host = f.host_id[rows].astype(np.int64)
    host_ids, hinv = np.unique(host, return_inverse=True)
    n_h = host_ids.shape[0]

    def _grid(
      keys_flat: np.ndarray, shape: tuple[int, ...], vals: np.ndarray
    ) -> np.ndarray:
      """
      Mean of ``vals`` per flat cell, NaN where no finite sample.

      Args:
        keys_flat (np.ndarray): Flat cell index per row.
        shape (tuple[int, ...]): Output grid shape.
        vals (np.ndarray): Values per row (NaN skipped).

      Returns:
        np.ndarray: Grid of means.

      Examples:
        >>> True
        True
      """
      size = int(np.prod(shape))
      return sg.grid_mean(keys_flat, vals, size).reshape(shape)

    per_host = _grid(
      (hinv * n_t + tix) * n_e + eix, (n_h, n_t, n_e), f.value[rows]
    )
    cluster_value = _grid(tix * n_e + eix, (n_t, n_e), f.value[rows])
    cluster_arc = _grid(tix * n_e + eix, (n_t, n_e), f.arc[rows])
    return {
      "times_s": times_s,
      "host_ids": host_ids,
      "per_host": per_host,
      "cluster_value": cluster_value,
      "cluster_arc": cluster_arc,
    }


def bucketed_job_value(
  agg_host: np.ndarray,
  agg_time_us: np.ndarray,
  agg_values: np.ndarray,
  conv: float,
  *,
  host_aggregate: str = "mean",
) -> float | None:
  """
  Fold per-sample rows to the ``job_arc`` scalar: 5-minute bucket means,
  first-bucket drop, per-host mean, then mean (or sum) across hosts.

  Args:
    agg_host (np.ndarray): Host id per sample row.
    agg_time_us (np.ndarray): Sample time per row.
    agg_values (np.ndarray): Per-sample value (NaN allowed).
    conv (float): Multiplier applied to bucket means.
    host_aggregate (str): ``mean`` or ``sum`` across per-host means.

  Returns:
    float | None: Scalar, NaN when every host mean is NaN, None without
    rows.

  Examples:
    >>> import numpy as np
    >>> bucketed_job_value(
    ...   np.array([0, 1]), np.array([0, 0]), np.array([2.0, 4.0]), 1.0
    ... )
    3.0
  """
  if agg_host.shape[0] == 0:
    return None
  _hosts, per_host = sg.bucket_host_means(
    agg_host, agg_time_us, agg_values, conv
  )
  if per_host.shape[0] == 0:
    return None
  fin = per_host[~np.isnan(per_host)]
  if fin.size == 0:
    return float("nan")
  if host_aggregate == "sum":
    return float(fin.sum())
  return float(fin.mean())
