"""
K2/K3 type-detail series: the data half of ``DevPlot.plot`` and the
``stats_data`` table of the ``type_detail`` artifact, from a ColumnarJobTable.

``TypeDetailDataProvider`` read one type at a time with job-or-host-wide
scope: distinct ``(host, time)`` of that type, ``(event, unit)`` pairs and
the type list of the first host, and a ``Coalesce(SUM, 0)`` per sample for
each event. Here the same answers come from the frame, and ``(event, unit)``
pairs come from ``jid_table`` metadata (``units`` map) or the golden
fixture.

Attributes:
  _VALUE_METRIC_TYPES: Type names plotted from ``value`` instead of ``arc``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar import segments as sg
from hpcperfstats.analysis.metrics.lib.columnar.kernels import MODE_ORM
from hpcperfstats.analysis.metrics.lib.columnar.series_frame import SeriesFrame

# ``DevPlot.plot`` switches from ``arc`` to ``value`` for these type names.
_VALUE_METRIC_TYPES = ("mem", "nvidia_gpu", "amd_gpu")


@dataclass
class TypeDetailSeries:
  """
  Per-type series for Job Detail.

  Attributes:
    type_name: ``host_data.type``.
    frame: ``(host, time)`` base of the type with one column per plotted
      event (events with any missing sample are dropped, as in ``DevPlot``).
    events_units: ``(event, unit)`` pairs considered, in order.
    metric: ``arc`` or ``value`` column used.
  """

  type_name: str
  frame: SeriesFrame
  events_units: list[tuple[str, str]] = field(default_factory=list)
  metric: str = "arc"


def type_host_time(jt: Any, type_name: str) -> tuple[np.ndarray, np.ndarray]:
  """
  ``TypeDetailDataProvider.get_host_time_df``: distinct ``(host, time)`` of
  rows of ``type_name`` under the job-or-host-wide scope.

  Args:
    jt (Any): ColumnarJobTable.
    type_name (str): Type.

  Returns:
    tuple[np.ndarray, np.ndarray]: ``(host_id, time_us)`` sorted by host id
    then time.

  Examples:
    >>> type_host_time(jt, "host_cpu")  # doctest: +SKIP
  """
  idx = jt.index
  rows = idx.scoped_rows(
    type_name, idx.rows_for_series(idx.series_ids(type_name, None))
  )
  if rows.size == 0:
    return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
  f = jt.frame
  tix = np.searchsorted(f.time_axis(), f.time_us[rows]).astype(np.int64)
  key = np.unique(sg.composite_key(f.host_id[rows].astype(np.int64), tix))
  hosts = (key >> sg.TIME_BITS).astype(np.int64)
  times = f.time_axis()[key & ((1 << sg.TIME_BITS) - 1)].astype(np.int64)
  return hosts, times


def type_list_for_first_host(jt: Any, type_name: str) -> list[str]:
  """
  ``TypeDetailDataProvider.get_type_list``: ``[type_name]`` when the first
  accounting host has scoped rows of that type, else ``[]``.

  Args:
    jt (Any): ColumnarJobTable.
    type_name (str): Type.

  Returns:
    list[str]: Type list.

  Examples:
    >>> type_list_for_first_host(jt, "host_cpu")  # doctest: +SKIP
  """
  hosts = list(getattr(jt, "acct_host_list", []) or [])
  if not hosts:
    return []
  try:
    hid = jt.frame.hosts.index(str(hosts[0]))
  except ValueError:
    return []
  idx = jt.index
  rows = idx.scoped_rows(
    type_name, idx.rows_for_series(idx.series_ids(type_name, None))
  )
  if rows.size and bool((jt.frame.host_id[rows] == hid).any()):
    return [type_name]
  return []


def events_units_for_first_host(
  jt: Any, type_name: str, units: dict[tuple[str, str], str] | None
) -> list[tuple[str, str]]:
  """
  ``TypeDetailDataProvider.get_events_units``: distinct ``(event, unit)`` of
  the first accounting host for ``type_name`` (sorted by event; the ORM
  returned them in database order).

  Args:
    jt (Any): ColumnarJobTable.
    type_name (str): Type.
    units (dict[tuple[str, str], str] | None): ``(type, event) -> unit``
      metadata; events without an entry get ``""``.

  Returns:
    list[tuple[str, str]]: Pairs.

  Examples:
    >>> events_units_for_first_host(jt, "host_cpu", {})  # doctest: +SKIP
  """
  hosts = list(getattr(jt, "acct_host_list", []) or [])
  if not hosts:
    return []
  try:
    hid = jt.frame.hosts.index(str(hosts[0]))
  except ValueError:
    return []
  idx = jt.index
  f = jt.frame
  out: list[tuple[str, str]] = []
  for sid in idx.series_ids(type_name, None):
    rows = idx.scoped_rows(type_name, idx.rows_for_series([sid]))
    if rows.size and bool((f.host_id[rows] == hid).any()):
      event = f.series[sid][1]
      out.append((event, (units or {}).get((type_name, event), "")))
  return sorted(out)


def build_type_detail_series(
  jt: Any,
  type_name: str,
  events_units: Sequence[tuple[str, str]],
  type_list: Sequence[str] | None = None,
) -> TypeDetailSeries:
  """
  ``DevPlot.plot`` data: per-event ``Coalesce(SUM, 0)`` per sample joined on
  the type's ``(host, time)`` base; events with any missing cell are dropped.

  Args:
    jt (Any): ColumnarJobTable.
    type_name (str): Type.
    events_units (Sequence[tuple[str, str]]): ``(event, unit)`` pairs.
    type_list (Sequence[str] | None): First-host type list (default:
      computed) that selects ``value`` for memory / GPU types.

  Returns:
    TypeDetailSeries: Base frame plus one column per kept event.

  Examples:
    >>> build_type_detail_series(
    ...   jt, "host_cpu", [("user", "%")]
    ... )  # doctest: +SKIP
  """
  hosts, times = type_host_time(jt, type_name)
  frame = SeriesFrame.from_base(hosts, times, jt.frame.hosts)
  types = (
    list(type_list)
    if type_list is not None
    else type_list_for_first_host(jt, type_name)
  )
  metric = (
    "value" if types and any(t in types for t in _VALUE_METRIC_TYPES) else "arc"
  )
  for event, _unit in events_units:
    agg = jt.index.sum_per_sample(type_name, [event], metric, mode=MODE_ORM)
    vals = (
      frame.join_values(agg) if not agg.empty else np.full(len(frame), np.nan)
    )
    if np.isnan(vals).any():
      continue
    frame.set(event, vals)
  return TypeDetailSeries(type_name, frame, list(events_units), metric)


def _timedelta_str(seconds: int) -> str:
  """
  ``str(pandas.Timedelta)`` for a whole number of seconds.

  Args:
    seconds (int): Non-negative seconds.

  Returns:
    str: ``"D days HH:MM:SS"``.

  Examples:
    >>> _timedelta_str(300)
    '0 days 00:05:00'
    >>> _timedelta_str(90061)
    '1 days 01:01:01'
  """
  s = int(seconds)
  days, rem = divmod(s, 86400)
  hours, rem = divmod(rem, 3600)
  minutes, secs = divmod(rem, 60)
  return f"{days} days {hours:02d}:{minutes:02d}:{secs:02d}"


def stats_data_rows(
  series: TypeDetailSeries,
) -> tuple[list[list[Any]], list[str]]:
  """
  The ``type_detail`` artifact ``stats_data``: mean of every kept event per
  offset from the first sample (``dt`` in whole seconds), as
  ``[[str(dt), [values…]], …]`` in ascending ``dt``.

  Args:
    series (TypeDetailSeries): Built series.

  Returns:
    tuple[list[list[Any]], list[str]]: Rows and the column order.

  Examples:
    >>> import numpy as np
    >>> f = SeriesFrame.from_base(
    ...   np.array([0, 1]), np.array([0, 60_000_000]), ["a", "b"]
    ... )
    >>> f.set("user", np.array([1.0, 3.0]))
    >>> stats_data_rows(TypeDetailSeries("host_cpu", f))
    ([['0 days 00:00:00', [1.0]], ['0 days 00:01:00', [3.0]]], ['user'])
  """
  frame = series.frame
  cols = list(frame.columns)
  if frame.empty or not cols:
    return [], cols
  t0 = int(frame.time_us[0])
  dt_s = (frame.time_us.astype(np.int64) - t0) // 1_000_000
  uniq, inverse = np.unique(dt_s, return_inverse=True)
  rows: list[list[Any]] = []
  means = []
  for name in cols:
    vals = frame.columns[name]
    fin = ~np.isnan(vals)
    sums = np.bincount(inverse[fin], weights=vals[fin], minlength=uniq.shape[0])
    cnt = np.bincount(inverse[fin], minlength=uniq.shape[0])
    with np.errstate(invalid="ignore", divide="ignore"):
      means.append(np.where(cnt > 0, sums / np.maximum(cnt, 1), np.nan))
  for k, d in enumerate(uniq.tolist()):
    rows.append([_timedelta_str(d), [float(m[k]) for m in means]])
  return rows, cols
