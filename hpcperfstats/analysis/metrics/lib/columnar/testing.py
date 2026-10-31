"""
Test helpers: build a ColumnarJobTable from literal rows without a database.

Used by unit tests of the metrics, detail and plot code that previously
faked ``jid_table`` DataFrames; production code never imports this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from hpcperfstats.analysis.metrics.lib.columnar.job_table import (
  ColumnarJobTable,
)
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame


def to_time_us(value: Any) -> int:
  """
  Epoch microseconds from an int, float seconds, ``datetime`` or
  pandas-like timestamp (``.timestamp()``).

  Args:
    value (Any): Time value.

  Returns:
    int: Epoch µs.

  Raises:
    TypeError: Unsupported value type (bool or non-time object).

  Examples:
    >>> to_time_us(datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC))
    1000000
    >>> to_time_us(5)
    5
  """
  if isinstance(value, bool):
    raise TypeError("bool is not a time")
  if isinstance(value, int):
    return value
  if isinstance(value, float):
    return round(value * 1_000_000)
  if isinstance(value, datetime):
    if value.tzinfo is None:
      value = value.replace(tzinfo=UTC)
    return round(value.timestamp() * 1_000_000)
  if hasattr(value, "timestamp"):
    ts = value.timestamp()
    return round(float(ts) * 1_000_000)
  raise TypeError(f"unsupported time value {value!r}")


def table_from_records(
  records: Iterable[Sequence[Any]],
  *,
  jid: str = "1",
  hosts: Sequence[str] | None = None,
  schema: dict[str, list[str]] | None = None,
  start_time: Any = None,
  end_time: Any = None,
  shared: bool = False,
  gpu_affinity_devs: Sequence[str] | None = None,
  time_in: Sequence[Any] | None = None,
) -> ColumnarJobTable:
  """
  Build a table from ``(time, host, type, event, dev, value, arc, delta)``
  rows (``owned`` defaults to 1; a 9-tuple may carry it before ``value``).

  ``hosts`` defaults to the distinct hosts of the rows, ``schema`` to the
  rows' ``type -> events``, and the window to the row time span.

  Args:
    records (Iterable[Sequence[Any]]): Row tuples; times accept anything
      :func:`to_time_us` accepts, floats accept None for NULL.
    jid (str): Job id.
    hosts (Sequence[str] | None): Accounting hosts.
    schema (dict[str, list[str]] | None): ``jid_table.schema`` override.
    start_time (Any): Window start (default: first sample).
    end_time (Any): Window end (default: last sample).
    shared (bool): Shared-node job.
    gpu_affinity_devs (Sequence[str] | None): Allowed GPU devices.
    time_in (Sequence[Any] | None): Sampled time axis (large-job
      ``time__in`` filter) instead of the window.

  Returns:
    ColumnarJobTable: Table.

  Raises:
    ValueError: A record does not have 8 or 9 fields.

  Examples:
    >>> t = table_from_records(
    ...   [(1_000_000, "h1", "host_cpu", "user", None, 10.0, 1.0, None)]
    ... )
    >>> t.schema, t.host_list
    ({'host_cpu': ['user']}, ['h1'])
  """
  rows = []
  for rec in records:
    rec = list(rec)
    if len(rec) == 8:
      t, host, typ, event, dev, value, arc, delta = rec
      owned = 1
    elif len(rec) == 9:
      t, host, typ, event, dev, owned, value, arc, delta = rec
    else:
      raise ValueError(f"record needs 8 or 9 fields: {rec!r}")
    rows.append(
      (to_time_us(t), host, typ, event, dev, owned, value, arc, delta)
    )
  frame = JobFrame.from_records(jid, rows)
  frame.check_contract()
  host_list = list(hosts) if hosts is not None else list(frame.hosts)
  times = [r[0] for r in rows]
  t0 = (
    to_time_us(start_time)
    if start_time is not None
    else (min(times) if times else None)
  )
  t1 = (
    to_time_us(end_time)
    if end_time is not None
    else (max(times) if times else None)
  )
  return ColumnarJobTable(
    jid,
    frame,
    host_list=sorted(set(host_list)),
    acct_host_list=host_list,
    schema=schema if schema is not None else frame.schema(),
    start_time=None if t0 is None else datetime.fromtimestamp(t0 / 1e6, tz=UTC),
    end_time=None if t1 is None else datetime.fromtimestamp(t1 / 1e6, tz=UTC),
    time_in=[
      datetime.fromtimestamp(to_time_us(t) / 1e6, tz=UTC) for t in time_in
    ]
    if time_in
    else None,
    shared=shared,
    gpu_affinity_devs=gpu_affinity_devs,
  )


def frame_from_df(df: Any, hosts: Sequence[str] | None = None) -> Any:
  """
  ``SeriesFrame`` from a ``host`` / ``time`` (+ metric columns) DataFrame
  used by legacy plot tests.

  Args:
    df (Any): pandas DataFrame with ``host`` and ``time`` columns.
    hosts (Sequence[str] | None): Host id order (default: order of first
      appearance).

  Returns:
    Any: ``SeriesFrame`` with every non-key column as a float column.

  Examples:
    >>> frame_from_df(df)  # doctest: +SKIP
  """
  import numpy as np

  from hpcperfstats.analysis.metrics.lib.columnar.series_frame import (
    SeriesFrame,
  )

  host_names = [str(h) for h in df["host"].tolist()]
  order = list(hosts) if hosts is not None else []
  for h in host_names:
    if h not in order:
      order.append(h)
  index = {h: i for i, h in enumerate(order)}
  host_id = np.array([index[h] for h in host_names], dtype=np.int64)
  time_us = np.array(
    [to_time_us(t) for t in df["time"].tolist()], dtype=np.int64
  )
  frame = SeriesFrame.from_base(
    host_id, time_us, order, sort_by_host_name=False
  )
  # from_base sorts by (host, time); re-align the metric columns.
  keys = list(zip(host_id.tolist(), time_us.tolist(), strict=True))
  pos = {k: i for i, k in enumerate(keys)}
  sel = [
    pos[(int(h), int(t))]
    for h, t in zip(frame.host_id, frame.time_us, strict=True)
  ]
  for col in df.columns:
    if col in ("host", "time", "index", "dev"):
      continue
    values = np.asarray(df[col].to_numpy(dtype=float), dtype=np.float64)[sel]
    frame.set(str(col), values)
  return frame


def points_from_df(df: Any) -> Any:
  """
  ``RooflinePoints`` from a ``host`` / [``dev``] / ``time`` / ``flops_gf`` /
  ``bw_gb`` DataFrame used by legacy roofline tests.

  Args:
    df (Any): pandas DataFrame.

  Returns:
    Any: ``RooflinePoints``.

  Examples:
    >>> points_from_df(df)  # doctest: +SKIP
  """
  import numpy as np

  from hpcperfstats.analysis.metrics.lib.columnar.roofline_series import (
    RooflinePoints,
  )

  hosts: list[str] = []
  for h in df["host"].tolist():
    if str(h) not in hosts:
      hosts.append(str(h))
  host_id = np.array(
    [hosts.index(str(h)) for h in df["host"].tolist()], dtype=np.int64
  )
  time_us = np.array(
    [to_time_us(t) for t in df["time"].tolist()], dtype=np.int64
  )
  dev_id = None
  devs = None
  if "dev" in df.columns:
    devs = [""]
    ids = []
    for d in df["dev"].tolist():
      name = "" if d is None else str(d)
      if name not in devs:
        devs.append(name)
      ids.append(devs.index(name))
    dev_id = np.array(ids, dtype=np.int64)
  return RooflinePoints(
    host_id,
    time_us,
    np.asarray(df["flops_gf"].to_numpy(dtype=float), dtype=np.float64),
    np.asarray(df["bw_gb"].to_numpy(dtype=float), dtype=np.float64),
    hosts,
    dev_id,
    devs,
  )


class MockJobTable:
  """
  Drop-in for the ``MagicMock`` job tables of the legacy plot tests.

  Tests configure ``get_host_time_df`` / ``get_aggregate_df`` exactly as
  before (``return_value`` / ``side_effect`` returning pandas DataFrames);
  the columnar accessors ``get_host_time`` / ``get_aggregate`` convert
  those frames to ``HostTimeTable`` / ``AggTable`` with a shared host and
  device interner.

  Attributes:
    get_host_time_df: ``MagicMock`` returning a ``host`` / ``time``
      DataFrame.
    get_aggregate_df: ``MagicMock`` returning a ``host`` / ``time`` [/
      ``dev``] / ``sum_val`` DataFrame.
    host_list: Hosts with data.
    acct_host_list: Accounting hosts.
    schema: ``type -> events``.
    jid: Job id.
    start_time: Window start.
    end_time: Window end.
    units: ``(type, event) -> unit``.
    gpu_affinity_devs: Allowed GPU devices or None.
    coalesce_zero: Aggregate rule flag (unused by the mock).
    _shared_node: Shared-node flag.
    _base_filter: Non-empty marker dict.
    frame: Namespace with ``hosts`` and ``devs`` name tables.
  """

  def __init__(self, host_list: Sequence[str] | None = None) -> None:
    """
    Create the mock with empty aggregates.

    Args:
      host_list (Sequence[str] | None): Initial host list.

    Returns:
      None

    Examples:
      >>> MockJobTable(["h1"]).host_list
      ['h1']
    """
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    self.get_host_time_df = MagicMock()
    self.get_aggregate_df = MagicMock()
    self.host_list = list(host_list or [])
    self.acct_host_list = list(self.host_list)
    self.schema: Any = None
    self.jid = "1"
    self.start_time: Any = None
    self.end_time: Any = None
    self.units: dict[tuple[str, str], str] = {}
    self.gpu_affinity_devs: list[str] | None = None
    self.coalesce_zero = True
    self._shared_node = False
    self._base_filter = {"host__in": list(self.host_list)}
    self.frame = SimpleNamespace(hosts=[], devs=[""])

  def _host_id(self, name: Any) -> int:
    """
    Intern a host name.

    Args:
      name (Any): Host name.

    Returns:
      int: Host id.

    Examples:
      >>> MockJobTable()._host_id("h")
      0
    """
    s = str(name)
    hosts = self.frame.hosts
    if s not in hosts:
      hosts.append(s)
    return hosts.index(s)

  def _dev_id(self, name: Any) -> int:
    """
    Intern a device name (``''`` / None -> 0).

    Args:
      name (Any): Device name.

    Returns:
      int: Device id.

    Examples:
      >>> MockJobTable()._dev_id(None)
      0
    """
    s = (
      ""
      if name is None or (isinstance(name, float) and name != name)
      else str(name)
    )
    devs = self.frame.devs
    if s not in devs:
      devs.append(s)
    return devs.index(s)

  def get_host_time(self) -> Any:
    """
    ``HostTimeTable`` from ``get_host_time_df()``.

    Returns:
      Any: ``HostTimeTable``.

    Examples:
      >>> MockJobTable().get_host_time()  # doctest: +SKIP
    """
    import numpy as np

    from hpcperfstats.analysis.metrics.lib.columnar.job_table import (
      HostTimeTable,
    )

    for h in self.host_list:
      self._host_id(h)
    df = self.get_host_time_df()
    if df is None or getattr(df, "empty", True) or "host" not in df.columns:
      return HostTimeTable(
        np.empty(0, dtype=np.int64),
        np.empty(0, dtype=np.int64),
        self.frame.hosts,
      )
    host_id = np.array(
      [self._host_id(h) for h in df["host"].tolist()], dtype=np.int64
    )
    time_us = np.array(
      [to_time_us(t) for t in df["time"].tolist()], dtype=np.int64
    )
    order = np.lexsort((time_us, host_id))
    return HostTimeTable(host_id[order], time_us[order], self.frame.hosts)

  def get_aggregate(
    self,
    typ: str,
    val_col: str,
    events: Sequence[str],
    conv: float = 1.0,
    *,
    group_by_dev: bool = False,
  ) -> Any:
    """
    ``AggTable`` from ``get_aggregate_df(...)`` (same call signature).

    Args:
      typ (str): Type.
      val_col (str): Column.
      events (Sequence[str]): Events.
      conv (float): Multiplier (forwarded; the mock decides whether to apply).
      group_by_dev (bool): Per-device rows.

    Returns:
      Any: ``AggTable``.

    Examples:
      >>> MockJobTable().get_aggregate("t", "arc", ["e"])  # doctest: +SKIP
    """
    import inspect

    import numpy as np

    from hpcperfstats.analysis.metrics.lib.columnar.kernels import AggTable

    fn = self.get_aggregate_df
    side = getattr(fn, "side_effect", None)
    if callable(side) and not isinstance(side, type):
      try:
        params = inspect.signature(side).parameters
      except TypeError, ValueError:
        params = {}
      kwargs = (
        {"group_by_dev": group_by_dev}
        if "group_by_dev" in params
        or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        else {}
      )
      df = fn(typ, val_col, list(events), conv, **kwargs)
    else:
      df = fn(typ, val_col, list(events), conv, group_by_dev=group_by_dev)
    if df is None or getattr(df, "empty", True) or "sum_val" not in df.columns:
      return AggTable.zero(by_dev=group_by_dev)
    host_id = np.array(
      [self._host_id(h) for h in df["host"].tolist()], dtype=np.int64
    )
    time_us = np.array(
      [to_time_us(t) for t in df["time"].tolist()], dtype=np.int64
    )
    vals = np.asarray(df["sum_val"].to_numpy(dtype=float), dtype=np.float64)
    if group_by_dev or "dev" in df.columns:
      devs = df["dev"].tolist() if "dev" in df.columns else [""] * len(df)
      dev_id = np.array([self._dev_id(d) for d in devs], dtype=np.int64)
      order = np.lexsort((dev_id, time_us, host_id))
      return AggTable(
        host_id[order], time_us[order], vals[order], dev_id[order]
      )
    order = np.lexsort((time_us, host_id))
    return AggTable(host_id[order], time_us[order], vals[order])
