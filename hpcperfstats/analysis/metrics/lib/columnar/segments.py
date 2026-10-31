"""
Segment reductions over a sorted JobFrame (NumPy reference for the Codon
kernels).

Every pandas ``groupby`` / SQL ``GROUP BY`` on the update_metrics path maps to
one of these primitives:

* :func:`group_sum` — sum of a column per integer key (SQL
  ``SUM … GROUP BY``), with optional NULL/ceiling/sign filters and the
  shared-node "hardware SUM else ``j:`` MAX" collapse;
* :func:`group_mean` / :func:`group_stats` — mean, count, max per key;
* :func:`bucket_host_means` — the 5-minute bucket mean, first-bucket drop
  and per-host mean behind ``job_arc`` / ``job_value_mean``;
* :func:`composite_key` / :func:`join_keys` — the (host, time) joins behind
  the plot merges.

Keys are int64 composites so the Codon port can keep the exact same shape
with plain loops.

Attributes:
  FIVE_MIN_US: Microseconds in the five-minute ``job_arc`` bucket.
  TIME_BITS: Bits reserved for the time index in composite keys.
"""

from __future__ import annotations

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar import codon_bridge

FIVE_MIN_US = 300 * 1_000_000
TIME_BITS = 32


def _np_group_reduce(
  keys: np.ndarray,
  values: np.ndarray,
  mask: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
  """
  NumPy reference of the Codon ``group_reduce`` kernel.

  Args:
    keys (np.ndarray): int64 keys (any order).
    values (np.ndarray): float64 values.
    mask (np.ndarray | None): Row mask; None keeps every row.

  Returns:
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]: ``(unique_keys,
    counts, sums, maxima)`` sorted by key; empty arrays without rows.

  Examples:
    >>> import numpy as np
    >>> k, c, s, m = _np_group_reduce(
    ...   np.array([2, 1, 2]), np.array([1.0, 2.0, 3.0]), None
    ... )
    >>> k.tolist(), c.tolist(), s.tolist(), m.tolist()
    ([1, 2], [1, 2], [2.0, 4.0], [2.0, 3.0])
  """
  keys = np.asarray(keys, dtype=np.int64)
  values = np.asarray(values, dtype=np.float64)
  if mask is not None:
    keys = keys[mask]
    values = values[mask]
  if keys.size == 0:
    empty_f = np.empty(0, dtype=np.float64)
    return (
      np.empty(0, dtype=np.int64),
      np.empty(0, dtype=np.int64),
      empty_f,
      empty_f,
    )
  uniq, inverse, counts = np.unique(
    keys, return_inverse=True, return_counts=True
  )
  sums = np.zeros(uniq.shape[0], dtype=np.float64)
  np.add.at(sums, inverse, values)
  maxima = np.full(uniq.shape[0], -np.inf, dtype=np.float64)
  np.maximum.at(maxima, inverse, values)
  return uniq, counts.astype(np.int64), sums, maxima


def group_reduce(
  keys: np.ndarray,
  values: np.ndarray,
  mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
  """
  Count / sum / max per key: the Codon kernel when baked, else NumPy.

  Args:
    keys (np.ndarray): int64 keys (any order).
    values (np.ndarray): float64 values.
    mask (np.ndarray | None): Row mask; None keeps every row.

  Returns:
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]: ``(unique_keys,
    counts, sums, maxima)`` sorted by key.

  Examples:
    >>> import numpy as np
    >>> group_reduce(np.array([1, 1]), np.array([1.0, 2.0]))[2].tolist()
    [3.0]
  """
  if codon_bridge.kernels() is not None:
    return codon_bridge.group_reduce(keys, values, mask)
  return _np_group_reduce(keys, values, mask)


def composite_key(*parts: np.ndarray) -> np.ndarray:
  """
  Pack small non-negative int columns into one int64 key (last part is the
  least significant, ``TIME_BITS`` wide; earlier parts get 16 bits each).

  Args:
    *parts (np.ndarray): Integer arrays of equal length; the last one may
      use up to ``TIME_BITS`` bits, the others 16 bits.

  Returns:
    np.ndarray: int64 keys.

  Raises:
    ValueError: A part exceeds its bit budget or is negative.

  Examples:
    >>> import numpy as np
    >>> composite_key(np.array([1, 2]), np.array([3, 4])).tolist()
    [4294967299, 8589934596]
  """
  if not parts:
    return np.empty(0, dtype=np.int64)
  n = int(parts[-1].shape[0])
  key = np.zeros(n, dtype=np.int64)
  shift = 0
  for idx, part in enumerate(reversed(parts)):
    arr = np.asarray(part, dtype=np.int64)
    width = TIME_BITS if idx == 0 else 16
    if arr.size and (int(arr.min()) < 0 or int(arr.max()) >= (1 << width)):
      raise ValueError("composite key part out of range")
    key |= arr << shift
    shift += width
  return key


def _finite_mask(
  values: np.ndarray, *, limit: float | None, nonnegative: bool
) -> np.ndarray:
  """
  Row mask for a value column: finite, below an absolute ceiling, optionally
  non-negative (mirrors the SQL ``IS NOT NULL AND abs(col) < limit
  [AND col >= 0]`` filters).

  Args:
    values (np.ndarray): float64 column (NaN for NULL).
    limit (float | None): Absolute ceiling (exclusive), or None.
    nonnegative (bool): Keep only ``values >= 0``.

  Returns:
    np.ndarray: Boolean mask.

  Examples:
    >>> import numpy as np
    >>> _finite_mask(
    ...   np.array([1.0, np.nan, -2.0, 1e14]), limit=1e13, nonnegative=True
    ... ).tolist()
    [True, False, False, False]
  """
  mask = ~np.isnan(values)
  if limit is not None:
    mask &= np.abs(values) < float(limit)
  if nonnegative:
    mask &= values >= 0.0
  return mask


def group_sum(
  keys: np.ndarray,
  values: np.ndarray,
  mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
  """
  Sum ``values`` per distinct key over rows where ``mask`` is True.

  Groups with no masked row are absent (SQL ``SUM`` over filtered rows).

  Args:
    keys (np.ndarray): int64 keys (any order).
    values (np.ndarray): float64 values.
    mask (np.ndarray | None): Row mask; None keeps every row.

  Returns:
    tuple[np.ndarray, np.ndarray, np.ndarray]: ``(unique_keys, sums,
    counts)`` sorted by key.

  Examples:
    >>> import numpy as np
    >>> k, s, c = group_sum(np.array([2, 1, 2]), np.array([1.0, 2.0, 3.0]))
    >>> k.tolist(), s.tolist(), c.tolist()
    ([1, 2], [2.0, 4.0], [1, 2])
  """
  uniq, counts, sums, _m = group_reduce(keys, values, mask)
  return uniq, sums, counts


def group_stats(
  keys: np.ndarray,
  values: np.ndarray,
  mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
  """
  Count, sum and max of ``values`` per key (SQL ``COUNT``/``AVG``/``MAX``).

  Args:
    keys (np.ndarray): int64 keys.
    values (np.ndarray): float64 values.
    mask (np.ndarray | None): Row mask; None keeps every row.

  Returns:
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]: ``(unique_keys,
    counts, sums, maxima)`` sorted by key.

  Examples:
    >>> import numpy as np
    >>> k, c, s, m = group_stats(np.array([1, 1, 2]), np.array([1.0, 3.0, 5.0]))
    >>> c.tolist(), s.tolist(), m.tolist()
    ([2, 1], [4.0, 5.0], [3.0, 5.0])
  """
  return group_reduce(keys, values, mask)


def collapse_residual_copies(
  group_keys: np.ndarray,
  dev_id: np.ndarray,
  values: np.ndarray,
  mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
  """
  Per key: SUM over hardware devices when any exist, else MAX over ``j:``
  copies (``_reduced_event_value_sql`` / ``collapse_residual_sample_copies``).

  Args:
    group_keys (np.ndarray): int64 key per row (for example host, time,
      event).
    dev_id (np.ndarray): Signed device ids (negative = ``j:`` copy).
    values (np.ndarray): float64 values.
    mask (np.ndarray): Rows that pass the NULL / ceiling filter.

  Returns:
    tuple[np.ndarray, np.ndarray]: ``(unique_keys, reduced)`` sorted by key;
    keys with no masked row are absent.

  Examples:
    >>> import numpy as np
    >>> k, v = collapse_residual_copies(
    ...   np.array([1, 1, 2, 2]),
    ...   np.array([1, 2, -1, -2]),
    ...   np.array([1.0, 2.0, 5.0, 7.0]),
    ...   np.ones(4, dtype=bool),
    ... )
    >>> k.tolist(), v.tolist()
    ([1, 2], [3.0, 7.0])
  """
  hw_mask = mask & (dev_id >= 0)
  j_mask = mask & (dev_id < 0)
  hw_keys, hw_sums, _ = group_sum(group_keys, values, hw_mask)
  j_keys, _c, _s, j_max = group_stats(group_keys, values, j_mask)
  only_j = ~np.isin(j_keys, hw_keys)
  keys = np.concatenate([hw_keys, j_keys[only_j]])
  vals = np.concatenate([hw_sums, j_max[only_j]])
  order = np.argsort(keys, kind="stable")
  return keys[order], vals[order]


def bucket_host_means(
  host_id: np.ndarray,
  time_us: np.ndarray,
  values: np.ndarray,
  conv: float,
) -> tuple[np.ndarray, np.ndarray]:
  """
  ``job_arc`` / ``job_value_mean`` bucketing: mean per (host, 5-minute
  bucket), drop the first bucket of each host when it has more than one,
  then mean of the remaining buckets per host, times ``conv``.

  Every row defines bucket presence (pandas keeps an all-NaN bucket as a
  NaN row); means skip NaN. A host whose kept buckets are all NaN yields
  NaN, as ``groupby("host").mean()`` does.

  Args:
    host_id (np.ndarray): Host id per sample row.
    time_us (np.ndarray): Sample time (epoch µs) per row.
    values (np.ndarray): Per-row value (already summed per sample when the
      caller mirrors ``sum_per_sample``); NaN rows count for presence only.
    conv (float): Unit conversion applied to bucket means.

  Returns:
    tuple[np.ndarray, np.ndarray]: ``(host_ids, per_host_means)`` for every
    host with at least one row, sorted by host id.

  Examples:
    >>> import numpy as np
    >>> h = np.array([0, 0, 0, 1])
    >>> t = np.array([0, 10, 400, 0]) * 1_000_000
    >>> v = np.array([1.0, 3.0, 10.0, 7.0])
    >>> hs, means = bucket_host_means(h, t, v, 1.0)
    >>> hs.tolist(), means.tolist()
    ([0, 1], [10.0, 7.0])
  """
  if host_id.shape[0] == 0:
    return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64)
  values = np.asarray(values, dtype=np.float64)
  finite = ~np.isnan(values)
  bucket = np.asarray(time_us, dtype=np.int64) // FIVE_MIN_US
  key = (np.asarray(host_id, dtype=np.int64) << TIME_BITS) | bucket
  # Presence from every row; sums and counts from finite rows only.
  keys, _c, _s, _m = group_reduce(key, values, None)
  fin_keys, fin_cnt, fin_sum, _m = group_reduce(key, values, finite)
  li, ri = join_keys(keys, fin_keys)
  means = np.full(keys.shape[0], np.nan, dtype=np.float64)
  means[li] = fin_sum[ri] / fin_cnt[ri]
  hosts = keys >> TIME_BITS
  # First bucket per host (keys are sorted by host then bucket).
  first = np.ones(hosts.shape[0], dtype=bool)
  first[1:] = hosts[1:] != hosts[:-1]
  host_counts = np.bincount(hosts, minlength=int(hosts.max()) + 1)
  keep = ~(first & (host_counts[hosts] > 1))
  hosts_k = hosts[keep]
  means_k = means[keep] * float(conv)
  uniq_hosts, _c, _s, _m = group_reduce(hosts_k, means_k, None)
  fin_hosts, n_fin, sum_fin, _m = group_reduce(
    hosts_k, means_k, ~np.isnan(means_k)
  )
  per_host = np.full(uniq_hosts.shape[0], np.nan, dtype=np.float64)
  li, ri = join_keys(uniq_hosts, fin_hosts)
  per_host[li] = sum_fin[ri] / n_fin[ri]
  return uniq_hosts, per_host


def join_keys(
  left: np.ndarray, right: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
  """
  Inner join of two sorted unique int64 key arrays.

  Args:
    left (np.ndarray): Sorted unique keys.
    right (np.ndarray): Sorted unique keys.

  Returns:
    tuple[np.ndarray, np.ndarray]: ``(left_idx, right_idx)`` positions of
    the common keys.

  Examples:
    >>> import numpy as np
    >>> li, ri = join_keys(np.array([1, 3, 5]), np.array([3, 4, 5]))
    >>> li.tolist(), ri.tolist()
    ([1, 2], [0, 2])
  """
  left = np.asarray(left, dtype=np.int64)
  right = np.asarray(right, dtype=np.int64)
  if codon_bridge.kernels() is not None:
    return codon_bridge.join_sorted(left, right)
  return _np_join_keys(left, right)


def _np_join_keys(
  left: np.ndarray, right: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
  """
  NumPy reference of the Codon ``join_sorted`` kernel.

  Args:
    left (np.ndarray): Sorted unique int64 keys.
    right (np.ndarray): Sorted unique int64 keys.

  Returns:
    tuple[np.ndarray, np.ndarray]: ``(left_idx, right_idx)`` of common keys.

  Examples:
    >>> import numpy as np
    >>> li, ri = _np_join_keys(np.array([1, 3, 5]), np.array([3, 4, 5]))
    >>> li.tolist(), ri.tolist()
    ([1, 2], [0, 2])
  """
  pos = np.searchsorted(right, left)
  pos_clipped = np.minimum(pos, max(0, right.shape[0] - 1))
  hit = (
    (pos < right.shape[0]) & (right[pos_clipped] == left)
    if right.size
    else np.zeros(left.shape[0], dtype=bool)
  )
  left_idx = np.nonzero(hit)[0]
  return left_idx, pos[hit]


def lookup_keys(
  base: np.ndarray,
  keys: np.ndarray,
  values: np.ndarray,
) -> np.ndarray:
  """
  Left join: value for each ``base`` key from ``(keys, values)``, NaN when
  absent (pandas ``merge(how="left")`` of one column).

  Args:
    base (np.ndarray): Sorted unique base keys.
    keys (np.ndarray): Sorted unique keys with values.
    values (np.ndarray): Values aligned with ``keys``.

  Returns:
    np.ndarray: float64 values aligned with ``base`` (NaN where missing).

  Examples:
    >>> import numpy as np
    >>> lookup_keys(
    ...   np.array([1, 2, 3]), np.array([1, 3]), np.array([5.0, 6.0])
    ... ).tolist()
    [5.0, nan, 6.0]
  """
  out = np.full(base.shape[0], np.nan, dtype=np.float64)
  li, ri = join_keys(base, keys)
  out[li] = values[ri]
  return out


def _np_grid_mean(
  cells: np.ndarray, values: np.ndarray, size: int
) -> np.ndarray:
  """
  NumPy reference of the Codon ``grid_mean`` kernel.

  Args:
    cells (np.ndarray): Flat cell index per row (``0 <= cell < size``).
    values (np.ndarray): float64 values; NaN rows are skipped.
    size (int): Cell count.

  Returns:
    np.ndarray: float64 mean per cell, NaN where no finite sample landed.

  Examples:
    >>> import numpy as np
    >>> _np_grid_mean(
    ...   np.array([0, 0, 1]), np.array([1.0, 3.0, np.nan]), 2
    ... ).tolist()
    [2.0, nan]
  """
  values = np.asarray(values, dtype=np.float64)
  cells = np.asarray(cells, dtype=np.int64)
  fin = ~np.isnan(values)
  sums = np.bincount(cells[fin], weights=values[fin], minlength=size)
  cnt = np.bincount(cells[fin], minlength=size)
  with np.errstate(invalid="ignore", divide="ignore"):
    return np.where(cnt > 0, sums / np.maximum(cnt, 1), np.nan)


def grid_mean(cells: np.ndarray, values: np.ndarray, size: int) -> np.ndarray:
  """
  Mean of finite ``values`` per dense cell: Codon kernel when baked, else
  NumPy (``metrics_pivot`` grids).

  Args:
    cells (np.ndarray): Flat cell index per row.
    values (np.ndarray): float64 values.
    size (int): Cell count.

  Returns:
    np.ndarray: float64 means of length ``size`` (NaN for empty cells).

  Raises:
    ValueError: A cell index is out of range.

  Examples:
    >>> import numpy as np
    >>> grid_mean(np.array([1]), np.array([4.0]), 2).tolist()
    [nan, 4.0]
  """
  cells = np.asarray(cells, dtype=np.int64)
  if cells.size and (int(cells.min()) < 0 or int(cells.max()) >= size):
    raise ValueError("grid cell index out of range")
  if codon_bridge.kernels() is not None:
    return codon_bridge.grid_mean(cells, values, size)
  return _np_grid_mean(cells, values, size)


def finite_mask(
  values: np.ndarray,
  *,
  limit: float | None = None,
  nonnegative: bool = False,
) -> np.ndarray:
  """
  Public alias of the SQL-style value filter (NULL, ceiling, sign).

  Args:
    values (np.ndarray): float64 column.
    limit (float | None): Absolute ceiling (exclusive) or None.
    nonnegative (bool): Keep only non-negative values.

  Returns:
    np.ndarray: Boolean mask.

  Examples:
    >>> import numpy as np
    >>> finite_mask(np.array([np.nan, 1.0])).tolist()
    [False, True]
  """
  return _finite_mask(values, limit=limit, nonnegative=nonnegative)
