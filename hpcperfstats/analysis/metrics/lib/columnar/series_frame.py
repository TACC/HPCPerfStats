"""
SeriesFrame: the array-backed replacement for the ``(host, time, …)``
DataFrames the plot builders assembled with ``DataFrame.merge``.

A frame is a sorted base of ``(host_id, time_us)`` rows plus named float64
columns aligned with those rows. ``left_join`` adds one aggregate column
(pandas ``merge(how="left")`` on ``host`` and ``time``); missing keys are
NaN.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar import segments as sg
from hpcperfstats.analysis.metrics.lib.columnar.kernels import AggTable


def _key_of(
  host_id: np.ndarray, time_us: np.ndarray, time_axis: np.ndarray
) -> np.ndarray:
  """
  Composite ``(host, time)`` key using positions on a shared time axis.

  Args:
    host_id (np.ndarray): Host ids.
    time_us (np.ndarray): Times (must be on ``time_axis``).
    time_axis (np.ndarray): Sorted distinct times.

  Returns:
    np.ndarray: int64 keys.

  Examples:
    >>> import numpy as np
    >>> _key_of(np.array([1]), np.array([5]), np.array([3, 5])).tolist()
    [4294967297]
  """
  tix = np.searchsorted(time_axis, time_us).astype(np.int64)
  return sg.composite_key(host_id.astype(np.int64), tix)


@dataclass
class SeriesFrame:
  """
  Sorted ``(host, time)`` base with aligned float columns.

  Attributes:
    host_id: Host id per row.
    time_us: Sample time per row (epoch µs).
    hosts: Host names by id.
    columns: Column name to float64 array (insertion order kept).
    time_axis: Sorted distinct times used for join keys.
  """

  host_id: np.ndarray
  time_us: np.ndarray
  hosts: list[str]
  columns: dict[str, np.ndarray] = field(default_factory=dict)
  time_axis: np.ndarray = field(
    default_factory=lambda: np.empty(0, dtype=np.int64)
  )

  @classmethod
  def from_base(
    cls,
    host_id: np.ndarray,
    time_us: np.ndarray,
    hosts: list[str],
    *,
    time_axis: np.ndarray | None = None,
    sort_by_host_name: bool = True,
  ) -> SeriesFrame:
    """
    Build a frame from base rows, sorted by host name then time like the
    pandas ``get_host_time_df`` (``ORDER BY host, time``).

    Args:
      host_id (np.ndarray): Host ids.
      time_us (np.ndarray): Times.
      hosts (list[str]): Host names by id.
      time_axis (np.ndarray | None): Shared time axis (default: distinct
        ``time_us``).
      sort_by_host_name (bool): Sort rows by host name (else by id).

    Returns:
      SeriesFrame: Frame.

    Examples:
      >>> import numpy as np
      >>> f = SeriesFrame.from_base(
      ...   np.array([1, 0]), np.array([5, 3]), ["b", "a"]
      ... )
      >>> f.host_names(), f.time_us.tolist()
      (['a', 'b'], [5, 3])
    """
    host_id = np.asarray(host_id, dtype=np.int64)
    time_us = np.asarray(time_us, dtype=np.int64)
    if sort_by_host_name and host_id.size:
      rank = np.argsort(
        np.array(hosts, dtype=object).astype(str), kind="stable"
      )
      host_rank = np.empty(len(hosts), dtype=np.int64)
      host_rank[rank] = np.arange(len(hosts))
      order = np.lexsort((time_us, host_rank[host_id]))
    else:
      order = np.lexsort((time_us, host_id))
    axis = (
      np.unique(time_us)
      if time_axis is None
      else np.asarray(time_axis, dtype=np.int64)
    )
    return cls(host_id[order], time_us[order], list(hosts), {}, axis)

  def __len__(self) -> int:
    """
    Row count.

    Returns:
      int: Rows.

    Examples:
      >>> import numpy as np
      >>> len(SeriesFrame.from_base(np.array([0]), np.array([1]), ["h"]))
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
      >>> SeriesFrame.from_base(
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
      >>> SeriesFrame.from_base(
      ...   np.array([0]), np.array([1]), ["h"]
      ... ).host_names()
      ['h']
    """
    return [self.hosts[int(i)] for i in self.host_id]

  def keys(self) -> np.ndarray:
    """
    Composite keys of the base rows.

    Returns:
      np.ndarray: int64 keys.

    Examples:
      >>> import numpy as np
      >>> SeriesFrame.from_base(
      ...   np.array([0]), np.array([7]), ["h"]
      ... ).keys().tolist()
      [0]
    """
    return _key_of(self.host_id, self.time_us, self.time_axis)

  def join_values(self, agg: AggTable) -> np.ndarray:
    """
    Values of ``agg`` aligned with the base rows (NaN where absent):
    ``base.merge(agg, on=["host", "time"], how="left")["sum_val"]``.

    Args:
      agg (AggTable): Aggregate rows (unique per ``(host, time)``).

    Returns:
      np.ndarray: float64 column.

    Examples:
      >>> import numpy as np
      >>> f = SeriesFrame.from_base(np.array([0, 0]), np.array([1, 2]), ["h"])
      >>> f.join_values(
      ...   AggTable(np.array([0]), np.array([2]), np.array([5.0]))
      ... ).tolist()
      [nan, 5.0]
    """
    if agg.empty:
      return np.full(len(self), np.nan, dtype=np.float64)
    extra = np.setdiff1d(agg.time_us, self.time_axis)
    axis = (
      self.time_axis if extra.size == 0 else np.union1d(self.time_axis, extra)
    )
    base_keys = _key_of(self.host_id, self.time_us, axis)
    agg_keys = _key_of(agg.host_id, agg.time_us, axis)
    order = np.argsort(agg_keys, kind="stable")
    return sg.lookup_keys(base_keys, agg_keys[order], agg.sum_val[order])

  def set(self, name: str, values: np.ndarray) -> None:
    """
    Add or replace a column.

    Args:
      name (str): Column name.
      values (np.ndarray): Aligned values.

    Returns:
      None

    Raises:
      ValueError: Length mismatch.

    Examples:
      >>> import numpy as np
      >>> f = SeriesFrame.from_base(np.array([0]), np.array([1]), ["h"])
      >>> f.set("x", np.array([1.0]))
      >>> sorted(f.columns)
      ['x']
    """
    arr = np.asarray(values, dtype=np.float64)
    if arr.shape != (len(self),):
      raise ValueError(
        f"column {name!r} has shape {arr.shape}, expected ({len(self)},)"
      )
    self.columns[name] = arr

  def drop(self, *names: str) -> None:
    """
    Remove columns (missing names raise ``KeyError`` like ``del df[col]``).

    Args:
      *names (str): Column names.

    Returns:
      None

    Raises:
      KeyError: A name is not a column.

    Examples:
      >>> import numpy as np
      >>> f = SeriesFrame.from_base(np.array([0]), np.array([1]), ["h"])
      >>> f.set("x", np.array([1.0]))
      >>> f.drop("x")
      >>> f.columns
      {}
    """
    for n in names:
      if n not in self.columns:
        raise KeyError(n)
      del self.columns[n]

  def has_null(self, name: str) -> bool:
    """
    ``df[name].isnull().values.any()``.

    Args:
      name (str): Column.

    Returns:
      bool: Any NaN.

    Examples:
      >>> import numpy as np
      >>> f = SeriesFrame.from_base(np.array([0]), np.array([1]), ["h"])
      >>> f.set("x", np.array([np.nan]))
      >>> f.has_null("x")
      True
    """
    return bool(np.isnan(self.columns[name]).any())

  def any_finite(self, name: str) -> bool:
    """
    ``df[name].notna().any()``.

    Args:
      name (str): Column.

    Returns:
      bool: Any non-NaN.

    Examples:
      >>> import numpy as np
      >>> f = SeriesFrame.from_base(np.array([0]), np.array([1]), ["h"])
      >>> f.set("x", np.array([np.nan]))
      >>> f.any_finite("x")
      False
    """
    return bool((~np.isnan(self.columns[name])).any())

  def copy(self) -> SeriesFrame:
    """
    Shallow copy with an independent column dict.

    Returns:
      SeriesFrame: Copy.

    Examples:
      >>> import numpy as np
      >>> f = SeriesFrame.from_base(np.array([0]), np.array([1]), ["h"])
      >>> f.copy().columns is f.columns
      False
    """
    return SeriesFrame(
      self.host_id, self.time_us, self.hosts, dict(self.columns), self.time_axis
    )


def outer_join_sum(left: AggTable, right: AggTable) -> AggTable:
  """
  ``left.merge(right, how="outer")`` on ``(host, time)`` with
  ``fillna(0)`` and the two values summed (IMC DDR + HBM, weighted FLOP events).

  Args:
    left (AggTable): First table.
    right (AggTable): Second table.

  Returns:
    AggTable: Union of keys sorted by ``(host, time)`` with summed values.

  Examples:
    >>> import numpy as np
    >>> a = AggTable(np.array([0]), np.array([1]), np.array([1.0]))
    >>> b = AggTable(np.array([0, 0]), np.array([1, 2]), np.array([2.0, 3.0]))
    >>> outer_join_sum(a, b).sum_val.tolist()
    [3.0, 3.0]
  """
  axis = np.union1d(left.time_us, right.time_us)
  lk = _key_of(left.host_id, left.time_us, axis)
  rk = _key_of(right.host_id, right.time_us, axis)
  keys = np.union1d(lk, rk)
  out = np.zeros(keys.shape[0], dtype=np.float64)
  lv = np.nan_to_num(left.sum_val, nan=0.0)
  rv = np.nan_to_num(right.sum_val, nan=0.0)
  np.add.at(out, np.searchsorted(keys, lk), lv)
  np.add.at(out, np.searchsorted(keys, rk), rv)
  hosts = (keys >> sg.TIME_BITS).astype(np.int64)
  times = axis[keys & ((1 << sg.TIME_BITS) - 1)].astype(np.int64)
  return AggTable(hosts, times, out)


def inner_join(
  left: AggTable, right: AggTable, *, by_dev: bool = False
) -> tuple[np.ndarray, np.ndarray]:
  """
  Row positions of the inner join of two aggregate tables on ``(host,
  time)`` or ``(host, dev, time)``.

  Args:
    left (AggTable): Left table.
    right (AggTable): Right table.
    by_dev (bool): Join on the device too.

  Returns:
    tuple[np.ndarray, np.ndarray]: ``(left_idx, right_idx)`` in left order.

  Examples:
    >>> import numpy as np
    >>> a = AggTable(np.array([0, 0]), np.array([1, 2]), np.array([1.0, 2.0]))
    >>> b = AggTable(np.array([0]), np.array([2]), np.array([9.0]))
    >>> li, ri = inner_join(a, b)
    >>> li.tolist(), ri.tolist()
    ([1], [0])
  """
  axis = np.union1d(left.time_us, right.time_us)

  def _k(t: AggTable) -> np.ndarray:
    """
    Composite key per row of ``t``.

    Args:
      t (AggTable): Table.

    Returns:
      np.ndarray: Keys.

    Examples:
      >>> True
      True
    """
    tix = np.searchsorted(axis, t.time_us).astype(np.int64)
    if by_dev:
      dev = (
        t.dev_id if t.dev_id is not None else np.zeros_like(t.host_id)
      ).astype(np.int64)
      return sg.composite_key(t.host_id.astype(np.int64), dev + (1 << 15), tix)
    return sg.composite_key(t.host_id.astype(np.int64), tix)

  lk, rk = _k(left), _k(right)
  order = np.argsort(rk, kind="stable")
  pos = np.searchsorted(rk[order], lk)
  pos_c = np.minimum(pos, max(0, rk.shape[0] - 1))
  hit = (
    (pos < rk.shape[0]) & (rk[order][pos_c] == lk)
    if rk.size
    else np.zeros(lk.shape[0], dtype=bool)
  )
  left_idx = np.nonzero(hit)[0]
  return left_idx, order[pos[hit]]
