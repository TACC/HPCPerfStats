"""
Columnar reductions over one JobFrame (``segments`` kernels: Codon or NumPy).

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
* :meth:`FrameIndex.metrics_pivot` — the ``_JobForMetrics`` pivots, built
  per type on first use through :class:`TypePivots`.

Every result carries integer ids; names are resolved by the caller.

Attributes:
  MODE_SQL: Per-sample sum engine with NULL / ceiling filters and the
    shared-node hardware-SUM-else-``j:``-MAX collapse.
  MODE_ORM: Plain ``SUM`` engine (``Coalesce(Sum, 0)``) used for GPU,
    per-device and type-detail aggregates.
  _COLUMNS: Float column names a kernel may reduce.
  PIVOT_CACHE_MAX_BYTES: Grid bytes ``TypePivots`` keeps across reads of a
    job's types. A grid is hosts x the union of every host's sample times x
    events, so it grows with hosts squared; keeping every pivoted type of a
    162-host job held 19.5 GiB (hpcperfstats04, 2026-11-21). An evicted
    type is pivoted again on its next read, with the same values.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar import (
  consumed_series,
  segments as sg,
)
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame
from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
  MAX_SANE_COUNTER_DELTA,
  MAX_SANE_COUNTER_RATE,
)
from hpcperfstats.analysis.metrics.lib.sentinel_profile import (
  SentinelConfig,
  fill_sample_gaps,
  sentinel_sample_mask,
)
from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK

MODE_SQL = "sql"
MODE_ORM = "orm"

_COLUMNS = ("value", "arc", "delta")
PIVOT_CACHE_MAX_BYTES = 2 << 30


@dataclass(frozen=True)
class AggTable:
  """
  Per-sample aggregate rows, sorted by ``(host, time[, dev])``.

  Attributes:
    host_id: int64 host id per row.
    time_us: int64 sample time per row.
    sum_val: float64 aggregate per row (``conv`` already applied).
    dev_id: Signed dev id per row, or None when devices were summed.
    filled: Rows interpolated over interior sentinel gaps (series readers).
  """

  host_id: np.ndarray
  time_us: np.ndarray
  sum_val: np.ndarray
  dev_id: np.ndarray | None = None
  filled: int = 0

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
    gpu_restricted_hosts: Host ids scoped by ``job_affinity`` or None
      (no host restricted).
    gpu_allowed_keys: ``host_id * len(devs) + dev_id`` keys allowed on the
      restricted hosts.
    owned: Boolean ownership mask per row.
    n_rows: Row count of the frame.
    _time_axis: Sorted distinct times of the frame (int64 us).
    _time_index: Position of each row's time on ``_time_axis``.
    _owned_axis: Memoised :meth:`owned_time_axis` or None.
  """

  def __init__(
    self,
    frame: JobFrame,
    *,
    shared: bool = False,
    residual_types: Iterable[str] = (),
    gpu_affinity_devs: Iterable[str]
    | Mapping[str, Iterable[str]]
    | None = None,
  ) -> None:
    """
    Build lookup tables for one frame.

    Args:
      frame (JobFrame): Sorted frame (``check_contract`` already passed).
      shared (bool): Shared-node job (affinity rows exist).
      residual_types (Iterable[str]): Types that are not on the job-scoped
        allowlist (read host-wide when ``shared``).
      gpu_affinity_devs (Iterable[str] | Mapping[str, Iterable[str]] |
        None): ``gpu_devs_by_host_for_job`` map (hosts absent from it keep
        every GPU), one device list applied to every host (golden
        fixtures), or None (unrestricted).

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
    self.gpu_restricted_hosts: np.ndarray | None = None
    self.gpu_allowed_keys = np.empty(0, dtype=np.int64)
    if gpu_affinity_devs is not None:
      per_host = (
        gpu_affinity_devs
        if isinstance(gpu_affinity_devs, Mapping)
        else {h: tuple(gpu_affinity_devs) for h in frame.hosts}
      )
      host_ids = {name: i for i, name in enumerate(frame.hosts)}
      dev_ids = {name: i for i, name in enumerate(frame.devs) if i > 0}
      n_dev = len(frame.devs)
      scoped = [(host_ids[h], d) for h, d in per_host.items() if h in host_ids]
      self.gpu_restricted_hosts = np.array([h for h, _ in scoped], np.int64)
      self.gpu_allowed_keys = np.array(
        [
          h * n_dev + dev_ids[str(d)]
          for h, devs in scoped
          for d in devs
          if str(d) in dev_ids
        ],
        dtype=np.int64,
      )
    self.owned = frame.owned.astype(bool)
    self._time_axis = frame.time_axis()
    self._time_index = frame.time_index().astype(np.int64)
    self._owned_axis: np.ndarray | None = None

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

  def _select(self, typ: str, events: Sequence[str] | None) -> list[int]:
    """
    Series ids for a reduction, recorded as consumed for the frame's jid.

    Every public reduction selects series through here, so the runtime
    record of consumed ``(type, event)`` pairs (``consumed_series``) stays
    complete by construction. Recording is a no-op unless update_metrics
    opened the jid.

    Args:
      typ (str): Type name.
      events (Sequence[str] | None): Event names or None for all events.

    Returns:
      list[int]: Matching series ids (same as :meth:`series_ids`).

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "cpu", "user", None, 1, 1.0, None, None)]
      ... )
      >>> FrameIndex(f)._select("cpu", None)
      [0]
    """
    sids = self.series_ids(typ, events)
    if sids:
      series = self.frame.series
      consumed_series.record(self.frame.jid, [series[s] for s in sids])
    return sids

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
    if self.gpu_restricted_hosts is None:
      return np.ones(rows.shape[0], dtype=bool)
    host = self.frame.host_id[rows].astype(np.int64)
    dev = self.frame.dev_id[rows].astype(np.int64)
    keys = host * len(self.frame.devs) + dev
    return ~np.isin(host, self.gpu_restricted_hosts) | np.isin(
      keys, self.gpu_allowed_keys
    )

  def values_at(self, name: str, rows: np.ndarray) -> np.ndarray:
    """
    Float column ``name`` at ``rows`` (``jobframe.VALUE_DTYPE``, float64).

    Args:
      name (str): ``value``, ``arc`` or ``delta``.
      rows (np.ndarray): Integer row positions.

    Returns:
      np.ndarray: float64 values aligned with ``rows``.

    Raises:
      ValueError: Unknown column name.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "cpu", "user", None, 1, 0.1, None, None)]
      ... )
      >>> v = FrameIndex(f).values_at("value", np.array([0]))
      >>> v.dtype, float(v[0])
      (dtype('float64'), 0.1)
    """
    if name not in _COLUMNS:
      raise ValueError(name)
    return getattr(self.frame, name)[rows]

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
    fill: SentinelConfig | None = None,
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
    exists for every scoped row that passes the blank / device filters and
    is not a monitor value sentinel (``JobFrame.sentinel`` code 0), and
    a group without a valid sample sums to ``0`` (``Coalesce``); the ceiling
    applies only when ``limit`` is given.

    Monitor value sentinels follow the fold's sample states
    (``sentinel_profile.sentinel_sample_mask``): a static-kind code is an
    absent counter, a bridged code takes the next valid rate, and any other
    code makes the whole sample a gap (missing), never a partial sum. With
    ``fill`` the interior gap samples of each series are interpolated
    (``sentinel_profile.fill_sample_gaps``); peaks and metric folds pass
    none. Frames without sentinel rows skip all of this.

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
      fill (SentinelConfig | None): Interpolate interior gap samples with
        these limits (series readers); None leaves gaps missing.

    Returns:
      AggTable: Sorted aggregate rows (``filled`` counts interpolated rows).

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
    sids = self._select(typ, events)
    rows = self.scoped_rows(typ, self.rows_for_series(sids))
    if rows.size == 0:
      return AggTable.zero(by_dev=by_dev)
    f = self.frame
    vals = self.values_at(col, rows)
    if mode == MODE_SQL and limit is None:
      limit = (
        MAX_SANE_COUNTER_DELTA if col == "delta" else MAX_SANE_COUNTER_RATE
      )
    if mode == MODE_ORM and (reject_dcgm_blank or dev_filter):
      # Sentinel rows pass the blank filter; the sample states decide them.
      keep = (
        (f.sentinel[rows] != 0) | (~np.isnan(vals) & (vals < DCGM_FP64_BLANK))
        if reject_dcgm_blank
        else np.ones(rows.shape[0], dtype=bool)
      )
      if dev_filter:
        keep &= self._dev_allowed_mask(rows)
      rows, vals = rows[keep], vals[keep]
    valid = sg.finite_mask(vals, limit=limit, nonnegative=nonnegative)
    host = f.host_id[rows].astype(np.int64)
    tix = self._time_index[rows]
    dev = f.dev_id[rows].astype(np.int64)
    if by_dev:
      group_key = sg.composite_key(host, dev + (1 << 15), tix)
    else:
      group_key = sg.composite_key(host, tix)
    gap_keys = None
    if f.sentinel[rows].any():
      keep, vals, valid, gap_keys = sentinel_sample_mask(
        self, rows, group_key, vals, valid, col
      )
      rows, vals, valid = rows[keep], vals[keep], valid[keep]
      host, tix, dev = host[keep], tix[keep], dev[keep]
      group_key = group_key[keep]
    if rows.size == 0:
      return AggTable.zero(by_dev=by_dev)
    if self.host_wide(typ) and not by_dev:
      key_hte = sg.composite_key(host, f.series_id[rows].astype(np.int64), tix)
      k_e, v_e = sg.collapse_residual_copies(key_hte, dev, vals, valid)
      ht_of = ((k_e >> (sg.TIME_BITS + 16)) << sg.TIME_BITS) | (
        k_e & ((1 << sg.TIME_BITS) - 1)
      )
      contrib_key, contrib_val = ht_of, v_e
    else:
      contrib_key, contrib_val = group_key[valid], vals[valid]
    # Row-order sums of the contributing samples; ORM groups also exist for
    # every scoped non-sentinel sample key and fill with 0 (Coalesce) or NaN.
    keys, _c, sums, _m = sg.group_reduce(contrib_key, contrib_val)
    if mode == MODE_ORM:
      contrib_keys, contrib_sums = keys, sums
      keys = sg.group_reduce(group_key, np.zeros(group_key.shape[0]))[0]
      sums = np.full(keys.shape[0], 0.0 if coalesce_zero else np.nan)
      li, ri = sg.join_keys(keys, contrib_keys)
      sums[li] = contrib_sums[ri]
    filled = 0
    if fill is not None and gap_keys is not None and gap_keys.size:
      keys, sums, filled = fill_sample_gaps(
        keys, sums, gap_keys, self._time_axis, fill
      )
    if keys.size == 0:
      return AggTable.zero(by_dev=by_dev)
    agg = (
      self._agg_from_keys_dev(keys, sums, conv)
      if by_dev
      else self._agg_from_keys(keys, sums, conv)
    )
    return replace(agg, filled=filled) if filled else agg

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
      typ, self.rows_for_series(self._select(typ, events))
    )
    return (
      self.frame.host_id[rows].astype(np.int64),
      self.frame.time_us[rows].astype(np.int64),
      self.values_at(col, rows),
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
    strictly inside ``(-limit, limit)`` and sum them. Totals are summed in
    row order (``segments.group_reduce``), so the Codon kernel and the NumPy
    reference agree bit for bit.

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
    f = self.frame
    rows = self.scoped_rows(
      typ, self.rows_for_series(self._select(typ, events))
    )
    vals = self.values_at("delta", rows)
    sid = f.series_id[rows].astype(np.int64)
    if self.host_wide(typ):
      mask = sg.finite_mask(vals, limit=MAX_SANE_COUNTER_DELTA)
      key = sg.composite_key(
        sid, f.host_id[rows].astype(np.int64), self._time_index[rows]
      )
      k, vals = sg.collapse_residual_copies(
        key, f.dev_id[rows].astype(np.int64), vals, mask
      )
      sid = k >> (sg.TIME_BITS + 16)
      mask = None
    else:
      mask = ~np.isnan(vals) & (vals > -MAX_SANE_COUNTER_DELTA)
      mask &= vals < MAX_SANE_COUNTER_DELTA
    # Row-order totals per series (series id order = ``_select`` order).
    sids, _c, totals, _m = sg.group_reduce(sid, vals, mask)
    return {
      f.series[s][1]: float(t)
      for s, t in zip(sids.tolist(), totals.tolist(), strict=True)
    }

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
      typ, self.rows_for_series(self._select(typ, events)), job_only=True
    )
    if rows.size:
      vals = self.values_at("value", rows)
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
    vals = self.values_at("value", rows)
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

  def owned_time_axis(self) -> np.ndarray:
    """
    Sorted distinct sample times of the job-owned rows (int64 us), memoised.

    Every :meth:`metrics_pivot` shares this axis, so it is computed once per
    frame instead of once per type.

    Returns:
      np.ndarray: int64 times (empty without owned rows).

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (5, "h", "cpu", "user", None, 1, 1.0, None, None),
      ...     (3, "h", "cpu", "user", None, 0, 1.0, None, None),
      ...   ],
      ... )
      >>> FrameIndex(f).owned_time_axis().tolist()
      [5]
    """
    if self._owned_axis is None:
      self._owned_axis = np.unique(self.frame.time_us[self.owned])
    return self._owned_axis

  def has_owned_rows(self, typ: str, events: Sequence[str]) -> bool:
    """
    True when ``typ`` has a job-owned row of one of ``events``.

    Exactly the types for which :meth:`metrics_pivot` returns hosts, found
    from the series bounds without pivoting. The series are recorded as
    consumed like a pivot records them, so a lazily pivoted view leaves the
    same ``consumed_series`` record as pivoting every type.

    Args:
      typ (str): Type name.
      events (Sequence[str]): Event names.

    Returns:
      bool: Owned rows present.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "cpu", "user", None, 0, 1.0, None, None)]
      ... )
      >>> FrameIndex(f).has_owned_rows("cpu", ["user"])
      False
    """
    b = self.series_bounds
    return any(
      self.owned[b[s] : b[s + 1]].any() for s in self._select(typ, events)
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
    axis_us = self.owned_time_axis()
    times_s = np.floor_divide(axis_us, 1_000_000).astype(np.float64)
    n_t = axis_us.shape[0]
    n_e = len(events)
    ev_pos = {e: i for i, e in enumerate(events)}
    sids = self._select(typ, list(ev_pos))
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
    event_of_series = np.zeros(len(f.series), dtype=np.int64)
    for s in sids:
      event_of_series[s] = ev_pos[f.series[s][1]]
    eix = event_of_series[f.series_id[rows]]
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

    value = self.values_at("value", rows)
    per_host = _grid((hinv * n_t + tix) * n_e + eix, (n_h, n_t, n_e), value)
    cluster_value = _grid(tix * n_e + eix, (n_t, n_e), value)
    cluster_arc = _grid(
      tix * n_e + eix, (n_t, n_e), self.values_at("arc", rows)
    )
    return {
      "times_s": times_s,
      "host_ids": host_ids,
      "per_host": per_host,
      "cluster_value": cluster_value,
      "cluster_arc": cluster_arc,
    }


class TypePivots:
  """
  :meth:`FrameIndex.metrics_pivot` per type, computed on first use.

  Complex metrics read a few types; a type nobody reads is never pivoted.
  Pivots are kept least-recently-used first until their grids exceed
  ``max_bytes``; the newest pivot is always kept, and an evicted type is
  pivoted again on its next read with the same values.

  Attributes:
    index: FrameIndex the pivots read.
    events: Event column order per type.
    types: Types whose pivot has hosts (job-owned rows of their events), in
      ``events`` order.
    _known: ``types`` as a set.
    max_bytes: Grid bytes kept across types.
    pivots_built: ``metrics_pivot`` calls so far (re-pivots included).
    _cache: Type to ``(pivot, position of each host id in the pivot)``,
      least recently read first.
    _bytes: Grid bytes of each cached pivot.
  """

  def __init__(
    self,
    index: FrameIndex,
    events: Mapping[str, Sequence[str]],
    max_bytes: int = PIVOT_CACHE_MAX_BYTES,
  ) -> None:
    """
    Find the pivotable types without pivoting any.

    Args:
      index (FrameIndex): Frame index of the job.
      events (Mapping[str, Sequence[str]]): Event column order per type.
      max_bytes (int): Grid bytes kept across types.

    Returns:
      None

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "cpu", "user", None, 1, 1.0, None, None)]
      ... )
      >>> TypePivots(FrameIndex(f), {"cpu": ["user"], "mem": ["used"]}).types
      ['cpu']
    """
    self.index = index
    self.events = {t: list(ev) for t, ev in events.items()}
    self.types = [
      t for t, ev in self.events.items() if ev and index.has_owned_rows(t, ev)
    ]
    self._known = set(self.types)
    self.max_bytes = int(max_bytes)
    self.pivots_built = 0
    self._cache: OrderedDict[str, tuple[dict[str, object], dict[int, int]]] = (
      OrderedDict()
    )
    self._bytes: dict[str, int] = {}

  def get(self, typ: str) -> tuple[dict[str, object], dict[int, int]] | None:
    """
    Pivot of ``typ`` and its host positions, or None for a type without one.

    Args:
      typ (str): Type name.

    Returns:
      tuple[dict[str, object], dict[int, int]] | None: ``(pivot, {host_id:
      row in per_host})``.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "cpu", "user", None, 1, 1.0, None, None)]
      ... )
      >>> TypePivots(FrameIndex(f), {"cpu": ["user"]}).get("cpu")[1]
      {0: 0}
    """
    if typ not in self._known:
      return None
    hit = self._cache.get(typ)
    if hit is not None:
      self._cache.move_to_end(typ)
      return hit
    piv = self.index.metrics_pivot(typ, self.events[typ])
    self.pivots_built += 1
    pos = {h: k for k, h in enumerate(piv["host_ids"].tolist())}
    hit = self._cache[typ] = (piv, pos)
    self._bytes[typ] = sum(
      v.nbytes for v in piv.values() if isinstance(v, np.ndarray)
    )
    while len(self._cache) > 1 and sum(self._bytes.values()) > self.max_bytes:
      oldest, _ = self._cache.popitem(last=False)
      del self._bytes[oldest]
    return hit


class PivotGrids(Mapping[str, np.ndarray]):
  """
  Read-only type -> cluster grid mapping over :class:`TypePivots`.

  A ``collections.abc.Mapping``: keys are the pivotable types; a value is
  pivoted on first lookup, so ``get`` / ``in`` touch one type only.

  Attributes:
    pivots: Shared per-type pivots.
    field: ``cluster_value`` or ``cluster_arc``.
  """

  def __init__(self, pivots: TypePivots, field: str) -> None:
    """
    Bind the pivots and the grid name.

    Args:
      pivots (TypePivots): Shared per-type pivots.
      field (str): ``cluster_value`` or ``cluster_arc``.

    Returns:
      None

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "cpu", "user", None, 1, 1.0, 2.0, None)]
      ... )
      >>> p = TypePivots(FrameIndex(f), {"cpu": ["user"]})
      >>> PivotGrids(p, "cluster_arc")["cpu"].tolist()
      [[2.0]]
    """
    self.pivots = pivots
    self.field = field

  def __getitem__(self, typ: str) -> np.ndarray:
    """
    Cluster grid of ``typ`` (pivots the type on first use).

    Args:
      typ (str): Type name.

    Returns:
      np.ndarray: ``(n_times, n_events)`` grid.

    Raises:
      KeyError: ``typ`` has no owned rows.

    Examples:
      >>> f = JobFrame.from_records("1", [])
      >>> PivotGrids(TypePivots(FrameIndex(f), {}), "cluster_value").get("x")
    """
    hit = self.pivots.get(typ)
    if hit is None:
      raise KeyError(typ)
    return hit[0][self.field]

  def __iter__(self) -> Iterator[str]:
    """
    Pivotable types in schema order (no pivoting).

    Returns:
      Iterator[str]: Type names.

    Examples:
      >>> f = JobFrame.from_records("1", [])
      >>> list(PivotGrids(TypePivots(FrameIndex(f), {}), "cluster_value"))
      []
    """
    return iter(self.pivots.types)

  def __len__(self) -> int:
    """
    Number of pivotable types (no pivoting).

    Returns:
      int: Type count.

    Examples:
      >>> f = JobFrame.from_records("1", [])
      >>> len(PivotGrids(TypePivots(FrameIndex(f), {}), "cluster_value"))
      0
    """
    return len(self.pivots.types)


class HostPivotStats(Mapping[str, dict[str, np.ndarray]]):
  """
  Read-only type -> ``{"agg": grid}`` mapping of one host (``_Host.stats``).

  A ``collections.abc.Mapping`` over :class:`TypePivots`: ``get`` / ``in``
  / ``[]`` pivot only the type asked for; iterating pivots every type.

  Attributes:
    pivots: Shared per-type pivots.
    host_id: Frame host id.
  """

  def __init__(self, pivots: TypePivots, host_id: int) -> None:
    """
    Bind the pivots and the host.

    Args:
      pivots (TypePivots): Shared per-type pivots.
      host_id (int): Frame host id.

    Returns:
      None

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1", [(1, "h", "cpu", "user", None, 1, 1.0, None, None)]
      ... )
      >>> s = HostPivotStats(TypePivots(FrameIndex(f), {"cpu": ["user"]}), 0)
      >>> s["cpu"]["agg"].tolist()
      [[1.0]]
    """
    self.pivots = pivots
    self.host_id = int(host_id)

  def __getitem__(self, typ: str) -> dict[str, np.ndarray]:
    """
    ``{"agg": (n_times, n_events) grid}`` of ``typ`` on this host.

    Args:
      typ (str): Type name.

    Returns:
      dict[str, np.ndarray]: Device-mean grid under ``agg``.

    Raises:
      KeyError: The host has no owned rows of ``typ``.

    Examples:
      >>> f = JobFrame.from_records("1", [])
      >>> HostPivotStats(TypePivots(FrameIndex(f), {}), 0).get("cpu")
    """
    hit = self.pivots.get(typ)
    k = None if hit is None else hit[1].get(self.host_id)
    if k is None:
      raise KeyError(typ)
    return {"agg": hit[0]["per_host"][k]}

  def __iter__(self) -> Iterator[str]:
    """
    Types with rows on this host (pivots every pivotable type).

    Returns:
      Iterator[str]: Type names in schema order.

    Examples:
      >>> f = JobFrame.from_records("1", [])
      >>> list(HostPivotStats(TypePivots(FrameIndex(f), {}), 0))
      []
    """
    return (
      t for t in self.pivots.types if self.host_id in self.pivots.get(t)[1]
    )

  def __len__(self) -> int:
    """
    Number of types with rows on this host (pivots every pivotable type).

    Returns:
      int: Type count.

    Examples:
      >>> f = JobFrame.from_records("1", [])
      >>> len(HostPivotStats(TypePivots(FrameIndex(f), {}), 0))
      0
    """
    return sum(1 for _ in self)


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
