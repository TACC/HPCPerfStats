"""Plan the one-off repair of per-core ``host_data`` rows.

Commit ce8f834f (2026-10-24) briefly stored whole-node non-GPU rows once per
hardware device instead of summed to ``dev=''``. This module is the pure (no
database) step of the repair used by ``manage.py collapse_percore_host_data``:
it sums the per-core rows with the same collapse function ingest uses, keeps a
``dev=''`` row that already exists, and computes ``arc`` the way ingest does.

Attributes:
  GPU_TYPES: Types that legitimately keep a hardware ``dev``.
  KEY_COLS: Columns that identify one whole-node row (its ``dev`` is ``''``).
  OUTPUT_COLS: Column order of the planned rows.
  _SERIES_COLS: Columns that identify one metric series (all samples of it).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  _GPU_STATS_TYPES,
  _collapse_stats_with_deltas,
)

GPU_TYPES = tuple(sorted(_GPU_STATS_TYPES))
KEY_COLS = ["host", "type", "event", "time"]
OUTPUT_COLS = [
  "time",
  "host",
  "jid",
  "type",
  "dev",
  "event",
  "unit",
  "value",
  "arc",
  "delta",
]
_SERIES_COLS = ["host", "type", "event"]


class CollapsePlan(NamedTuple):
  """Result of planning one window of per-core rows.

  Subclasses ``typing.NamedTuple``, the standard immutable record base; this
  class only names the four planning outputs.

  Attributes:
    new_rows: Whole-node rows to insert (``OUTPUT_COLS``, ``dev=''``).
    percore_rows: Number of per-core input rows considered.
    groups: Number of distinct whole-node keys those rows belong to.
    kept_existing: Number of keys that already had a ``dev=''`` row, which is
      kept unchanged (no new row is planned for them).
  """

  new_rows: Any
  percore_rows: int
  groups: int
  kept_existing: int


def percore_mask(frame: Any) -> Any:
  """Mark rows that are per-core copies of a whole-node (non-GPU) metric.

  A row qualifies when its type is not a GPU type and its ``dev`` is neither
  empty nor a shared-node ``j:<jid>`` stamp.

  Args:
    frame (Any): Frame with ``type`` and ``dev`` columns.

  Returns:
    Any: Boolean Series aligned with ``frame``.

  Examples:
    >>> import pandas as pd
    >>> df = pd.DataFrame(
    ...   {
    ...     "type": ["cpu", "cpu", "cpu", "nvidia_gpu"],
    ...     "dev": ["0", "", "j:9", "0"],
    ...   }
    ... )
    >>> percore_mask(df).tolist()
    [True, False, False, False]
  """
  dev = frame["dev"].fillna("").astype(str)
  return (
    (dev != "") & ~dev.str.startswith("j:") & ~frame["type"].isin(GPU_TYPES)
  )


def _empty_output() -> Any:
  """Return an empty frame with the planned-row columns.

  Returns:
    Any: Empty ``pandas.DataFrame`` with ``OUTPUT_COLS``.

  Examples:
    >>> list(_empty_output().columns) == OUTPUT_COLS
    True
  """
  return pd.DataFrame(columns=OUTPUT_COLS)


def _utc(series: Any) -> Any:
  """Return ``series`` as timezone-aware UTC timestamps.

  Args:
    series (Any): Datetime-like Series.

  Returns:
    Any: Series of ``datetime64[ns, UTC]``.

  Examples:
    >>> import pandas as pd
    >>> str(_utc(pd.Series(["2026-10-24 00:00:00"])).dt.tz)
    'UTC'
  """
  return pd.to_datetime(series, utc=True)


def _previous_times(time_context: Any) -> Any:
  """Return each (host, type, event, time) with the time of its predecessor.

  Args:
    time_context (Any): Frame with ``host``, ``type``, ``event`` and ``time``
      columns, any ``dev``; duplicates across devices are expected.

  Returns:
    Any: Frame with ``KEY_COLS`` plus ``prev_time`` (``NaT`` for the first
    sample of a series).

  Examples:
    >>> import pandas as pd
    >>> ctx = pd.DataFrame(
    ...   {
    ...     "host": ["h", "h"],
    ...     "type": ["cpu", "cpu"],
    ...     "event": ["user", "user"],
    ...     "time": ["2026-10-24 00:00:00", "2026-10-24 00:00:10"],
    ...   }
    ... )
    >>> _previous_times(ctx)["prev_time"].isna().tolist()
    [True, False]
  """
  ctx = time_context[KEY_COLS].drop_duplicates().copy()
  ctx["time"] = _utc(ctx["time"])
  ctx = ctx.sort_values([*_SERIES_COLS, "time"])
  ctx["prev_time"] = ctx.groupby(_SERIES_COLS, sort=False)["time"].shift()
  return ctx


def plan_collapse(
  percore: Any,
  existing_keys: Iterable[tuple[Any, ...]],
  time_context: Any,
) -> CollapsePlan:
  """Plan the whole-node rows that replace a window of per-core rows.

  Value and delta come from ``_collapse_stats_with_deltas``, the function
  ingest uses, so the sums (and the DCGM cluster-mean for CCM power) match
  what ingest now writes. A key that already has a ``dev=''`` row is left
  alone: no row is planned for it. ``arc`` is ``delta / dt`` with ``dt`` taken
  from the previous sample in ``time_context``; a series' first sample falls
  back to the sum of the per-core ``arc`` values.

  Args:
    percore (Any): Per-core rows with ``host``, ``jid``, ``type``, ``dev``,
      ``event``, ``unit``, ``time``, ``value``, ``delta`` and ``arc``. Rows
      that are not per-core (see ``percore_mask``) are ignored.
    existing_keys (Iterable[tuple[Any, ...]]): ``(host, type, event, time)``
      keys that already have a ``dev=''`` row.
    time_context (Any): Distinct sample times per (host, type, event) around
      the window (any ``dev``), used to find each sample's predecessor.

  Returns:
    CollapsePlan: Planned rows and counts.

  Examples:
    >>> import pandas as pd
    >>> plan_collapse(
    ...   pd.DataFrame(columns=["type", "dev"]), [], pd.DataFrame()
    ... ).groups
    0
  """
  if percore.empty:
    return CollapsePlan(_empty_output(), 0, 0, 0)
  frame = percore.loc[percore_mask(percore)].copy()
  if frame.empty:
    return CollapsePlan(_empty_output(), 0, 0, 0)
  frame["time"] = _utc(frame["time"])
  for col in ("value", "delta", "arc"):
    frame[col] = pd.to_numeric(frame[col], errors="coerce")
  keys = pd.MultiIndex.from_frame(frame[KEY_COLS])
  existing = pd.MultiIndex.from_tuples(
    [(h, t, e, _utc(pd.Series([ts])).iloc[0]) for h, t, e, ts in existing_keys],
    names=KEY_COLS,
  )
  has_existing = keys.isin(existing)
  groups = int(keys.nunique())
  kept_existing = int(keys[has_existing].nunique())
  todo = frame.loc[~has_existing]
  if todo.empty:
    return CollapsePlan(_empty_output(), len(frame), groups, kept_existing)

  collapsed = _collapse_stats_with_deltas(
    todo[
      ["host", "jid", "type", "dev", "event", "unit", "time", "value", "delta"]
    ].copy()
  )
  arc_sum = (
    todo.groupby(KEY_COLS)["arc"]
    .sum(min_count=1)
    .rename("arc_sum")
    .reset_index()
  )
  collapsed = collapsed.merge(
    _previous_times(time_context)[[*KEY_COLS, "prev_time"]],
    on=KEY_COLS,
    how="left",
  ).merge(arc_sum, on=KEY_COLS, how="left")
  dt = (collapsed["time"] - collapsed["prev_time"]).dt.total_seconds()
  delta = collapsed["delta"].to_numpy(dtype=np.float64)
  dt = dt.to_numpy(dtype=np.float64)
  ok = (dt > 0) & np.isfinite(dt) & np.isfinite(delta)
  rate = np.divide(delta, dt, out=np.full(len(collapsed), np.nan), where=ok)
  collapsed["arc"] = np.where(ok, rate, collapsed["arc_sum"])
  collapsed["dev"] = ""
  return CollapsePlan(
    collapsed[OUTPUT_COLS].reset_index(drop=True),
    len(frame),
    groups,
    kept_existing,
  )
