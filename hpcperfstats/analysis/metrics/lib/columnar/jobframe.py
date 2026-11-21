"""
JobFrame: one job's ``host_data`` slice as a sorted structure of arrays.

Layout:

* ``time_us`` int64 epoch microseconds (UTC);
* ``host_id`` / ``series_id`` / ``dev_id`` int32 ids into the name tables
  (``series`` is a ``(type, event)`` pair; ``dev_id`` 0 is the empty device,
  positive ids are hardware devices, negative ids are shared-node ``j:``
  copies);
* ``owned`` uint8 (1 when the row belongs to the job: ``jid`` match or
  ``dev = 'j:<jid>'``; 0 for host-wide residual rows of shared-node jobs);
* ``value`` / ``arc`` / ``delta`` float32 with NaN for SQL NULL (the
  ``host_data`` columns are PostgreSQL ``real``, so storage is exact);
  readers widen the rows they select to float64 before any arithmetic
  (``kernels.FrameIndex.values_at``).

Rows are sorted by ``(series_id, host_id, dev_id, time_us)`` so every kernel
can run segment scans without hashing. Names never reach the kernels.

Attributes:
  TIME_DTYPE: NumPy dtype of ``time_us``.
  ID_DTYPE: NumPy dtype of the id columns.
  VALUE_DTYPE: NumPy dtype of the float columns.
  JOB_DEV_PREFIX: ``host_data.dev`` prefix marking shared-node job copies.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from hpcperfstats.lib.monitor_sentinel import sentinel_codes

TIME_DTYPE = np.int64
ID_DTYPE = np.int32
VALUE_DTYPE = np.float32
JOB_DEV_PREFIX = "j:"


class NameInterner:
  """
  Dictionary-encode host, type, event and dev names into small integer ids.

  Shared by every decoded chunk of one job so ids are stable across chunks.
  Device ids follow the JobFrame convention: ``''`` is 0, hardware devices
  get positive ids, ``j:`` copies get negative ids (the absolute value
  indexes ``devs``).

  Attributes:
    hosts: Host names by id.
    types: Type names by id.
    events: Event names by id.
    devs: Device names by ``abs(id)`` (index 0 is ``''``).
    _host_ids: Host name -> id.
    _type_ids: Type name -> id.
    _event_ids: Event name -> id.
    _dev_ids: Device name -> signed id.
  """

  def __init__(self) -> None:
    """
    Start with empty tables (device table seeded with the empty device).

    Returns:
      None

    Examples:
      >>> NameInterner().devs
      ['']
    """
    self.hosts: list[str] = []
    self.types: list[str] = []
    self.events: list[str] = []
    self.devs: list[str] = [""]
    self._host_ids: dict[str, int] = {}
    self._type_ids: dict[str, int] = {}
    self._event_ids: dict[str, int] = {}
    self._dev_ids: dict[str, int] = {"": 0}

  def host(self, name: str) -> int:
    """
    Return the id for a host name, assigning the next id when new.

    Args:
      name (str): Host FQDN.

    Returns:
      int: Host id.

    Examples:
      >>> ni = NameInterner()
      >>> ni.host("a"), ni.host("b"), ni.host("a")
      (0, 1, 0)
    """
    hid = self._host_ids.get(name)
    if hid is None:
      hid = len(self.hosts)
      self._host_ids[name] = hid
      self.hosts.append(name)
    return hid

  def type(self, name: str) -> int:
    """
    Return the id for a ``host_data.type`` name, assigning when new.

    Args:
      name (str): Type name.

    Returns:
      int: Type id.

    Examples:
      >>> NameInterner().type("host_cpu")
      0
    """
    tid = self._type_ids.get(name)
    if tid is None:
      tid = len(self.types)
      self._type_ids[name] = tid
      self.types.append(name)
    return tid

  def event(self, name: str) -> int:
    """
    Return the id for a ``host_data.event`` name, assigning when new.

    Args:
      name (str): Event name.

    Returns:
      int: Event id.

    Examples:
      >>> NameInterner().event("user")
      0
    """
    eid = self._event_ids.get(name)
    if eid is None:
      eid = len(self.events)
      self._event_ids[name] = eid
      self.events.append(name)
    return eid

  def dev(self, name: str) -> int:
    """
    Return the signed id for a device name (``''`` 0, ``j:`` copies negative).

    Args:
      name (str): ``host_data.dev`` value (NULL callers pass ``''``).

    Returns:
      int: Signed device id.

    Examples:
      >>> ni = NameInterner()
      >>> ni.dev(""), ni.dev("0"), ni.dev("j:42"), ni.dev("0")
      (0, 1, -2, 1)
    """
    did = self._dev_ids.get(name)
    if did is None:
      idx = len(self.devs)
      self.devs.append(name)
      did = -idx if name.startswith(JOB_DEV_PREFIX) else idx
      self._dev_ids[name] = did
    return did


@dataclass
class DecodedChunk:
  """
  Unsorted columns decoded from COPY streams, ids per the shared interner.

  The input of :func:`build_job_frame`, which takes ownership of it.

  Attributes:
    time_us: int64 epoch microseconds.
    host_id: int32 host ids.
    type_id: int32 type ids.
    event_id: int32 event ids.
    dev_id: int32 signed device ids.
    owned: uint8 ownership flags.
    value: ``VALUE_DTYPE`` values (NaN for NULL).
    arc: ``VALUE_DTYPE`` arcs (NaN for NULL).
    delta: ``VALUE_DTYPE`` deltas (NaN for NULL).
  """

  time_us: np.ndarray
  host_id: np.ndarray
  type_id: np.ndarray
  event_id: np.ndarray
  dev_id: np.ndarray
  owned: np.ndarray
  value: np.ndarray
  arc: np.ndarray
  delta: np.ndarray

  def __len__(self) -> int:
    """
    Row count.

    Returns:
      int: Rows in the chunk.

    Examples:
      >>> e = np.empty(0)
      >>> len(DecodedChunk(e, e, e, e, e, e, e, e, e))
      0
    """
    return int(self.time_us.shape[0])

  def pop(self, name: str) -> np.ndarray:
    """
    Hand column ``name`` to the caller and keep an empty array in its place.

    The chunk then holds no reference to the column, so its memory goes as
    soon as the caller drops it.

    Args:
      name (str): Field name.

    Returns:
      np.ndarray: The column as it was.

    Examples:
      >>> c = DecodedChunk(*[np.arange(3)] * 9)
      >>> c.pop("arc").tolist(), c.arc.shape
      ([0, 1, 2], (0,))
    """
    col = getattr(self, name)
    setattr(self, name, np.empty(0, dtype=col.dtype))
    return col


@dataclass
class JobFrame:
  """
  Sorted structure of arrays for one job's ``host_data`` rows.

  Attributes:
    jid: Job id.
    time_us: int64 epoch microseconds per row.
    host_id: int32 host id per row.
    series_id: int32 ``(type, event)`` series id per row.
    dev_id: int32 signed device id per row.
    owned: uint8 job-ownership flag per row.
    value: float32 ``value`` column (NaN for NULL and for monitor value
      sentinels, so every reader sees a sentinel as missing).
    arc: float32 ``arc`` column (NaN for NULL).
    delta: float32 ``delta`` column (NaN for NULL).
    sentinel: int16 monitor value-sentinel code per row (0 for data and
      NULL); the code that ``build_job_frame`` took out of ``value``.
    hosts: Host names by id.
    series: ``(type, event)`` by series id.
    devs: Device names by ``abs(dev_id)``.
    _time_axis: Cached sorted distinct times (int64) or None.
    _time_index: Cached int32 index of each row into ``_time_axis`` or None.
  """

  jid: str
  time_us: np.ndarray
  host_id: np.ndarray
  series_id: np.ndarray
  dev_id: np.ndarray
  owned: np.ndarray
  value: np.ndarray
  arc: np.ndarray
  delta: np.ndarray
  sentinel: np.ndarray
  hosts: list[str]
  series: list[tuple[str, str]]
  devs: list[str]
  _time_axis: np.ndarray | None = field(default=None, repr=False)
  _time_index: np.ndarray | None = field(default=None, repr=False)

  def __len__(self) -> int:
    """
    Number of rows.

    Returns:
      int: Row count.

    Examples:
      >>> len(JobFrame.from_records("1", []))
      0
    """
    return int(self.time_us.shape[0])

  @property
  def n_rows(self) -> int:
    """
    Number of rows (alias of ``len``).

    Returns:
      int: Row count.

    Examples:
      >>> JobFrame.from_records("1", []).n_rows
      0
    """
    return len(self)

  @classmethod
  def from_records(cls, jid: str, records: Iterable[Sequence[Any]]) -> JobFrame:
    """
    Build a frame from ``(time_us, host, type, event, dev, owned, value, arc,
    delta)`` records (tests and the NumPy decoder).

    ``dev`` None is the empty device; float None is NaN.

    Args:
      jid (str): Job id.
      records (Iterable[Sequence[Any]]): Row tuples in any order.

    Returns:
      JobFrame: Sorted frame.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "7",
      ...   [
      ...     (2_000_000, "h1", "host_cpu", "user", None, 1, 5.0, None, 1.0),
      ...     (1_000_000, "h1", "host_cpu", "user", None, 1, 4.0, 0.5, None),
      ...   ],
      ... )
      >>> f.time_us.tolist(), f.series, float(f.arc[1])
      ([1000000, 2000000], [('host_cpu', 'user')], nan)
    """
    interner = NameInterner()
    rows = list(records)
    n = len(rows)
    time_us = np.empty(n, dtype=TIME_DTYPE)
    host_id = np.empty(n, dtype=ID_DTYPE)
    type_id = np.empty(n, dtype=ID_DTYPE)
    event_id = np.empty(n, dtype=ID_DTYPE)
    dev_id = np.empty(n, dtype=ID_DTYPE)
    owned = np.empty(n, dtype=np.uint8)
    value = np.empty(n, dtype=VALUE_DTYPE)
    arc = np.empty(n, dtype=VALUE_DTYPE)
    delta = np.empty(n, dtype=VALUE_DTYPE)
    for i, (t, host, typ, event, dev, own, v, a, d) in enumerate(rows):
      time_us[i] = int(t)
      host_id[i] = interner.host(str(host))
      type_id[i] = interner.type(str(typ))
      event_id[i] = interner.event(str(event))
      dev_id[i] = interner.dev("" if dev is None else str(dev))
      owned[i] = 1 if own else 0
      value[i] = np.nan if v is None else float(v)
      arc[i] = np.nan if a is None else float(a)
      delta[i] = np.nan if d is None else float(d)
    return build_job_frame(
      jid,
      interner,
      DecodedChunk(
        time_us, host_id, type_id, event_id, dev_id, owned, value, arc, delta
      ),
    )

  def time_axis(self) -> np.ndarray:
    """
    Sorted distinct sample times across every row (int64 microseconds).

    Returns:
      np.ndarray: Sorted unique ``time_us`` values.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (5, "h", "t", "e", None, 1, 1.0, None, None),
      ...     (3, "h", "t", "e", None, 1, 1.0, None, None),
      ...     (5, "h", "t", "x", None, 1, 1.0, None, None),
      ...   ],
      ... )
      >>> f.time_axis().tolist()
      [3, 5]
    """
    if self._time_axis is None:
      self._time_axis = np.unique(self.time_us)
    return self._time_axis

  def time_index(self) -> np.ndarray:
    """
    Index of each row's time in :meth:`time_axis` (int32).

    Returns:
      np.ndarray: Per-row position into the time axis.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (5, "h", "t", "e", None, 1, 1.0, None, None),
      ...     (3, "h", "t", "e", None, 1, 1.0, None, None),
      ...   ],
      ... )
      >>> f.time_index().tolist()
      [0, 1]
    """
    if self._time_index is None:
      axis = self.time_axis()
      self._time_index = np.searchsorted(axis, self.time_us).astype(ID_DTYPE)
    return self._time_index

  def series_ids_for_type(self, typ: str) -> list[int]:
    """
    Series ids whose type equals ``typ`` (in id order).

    Args:
      typ (str): ``host_data.type`` name.

    Returns:
      list[int]: Matching series ids (possibly empty).

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (1, "h", "cpu", "user", None, 1, 1.0, None, None),
      ...     (1, "h", "cpu", "nice", None, 1, 1.0, None, None),
      ...     (1, "h", "mem", "used", None, 1, 1.0, None, None),
      ...   ],
      ... )
      >>> f.series_ids_for_type("cpu")
      [0, 1]
    """
    return [i for i, (t, _e) in enumerate(self.series) if t == typ]

  def series_id_for(self, typ: str, event: str) -> int | None:
    """
    Series id for a ``(type, event)`` pair, or None when absent.

    Args:
      typ (str): Type name.
      event (str): Event name.

    Returns:
      int | None: Series id.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (1, "h", "cpu", "user", None, 1, 1.0, None, None),
      ...   ],
      ... )
      >>> f.series_id_for("cpu", "user"), f.series_id_for("cpu", "nice")
      (0, None)
    """
    try:
      return self.series.index((typ, event))
    except ValueError:
      return None

  def schema(self) -> dict[str, list[str]]:
    """
    ``type -> sorted events`` for the rows present (same shape as
    ``jid_table.schema``).

    Returns:
      dict[str, list[str]]: Schema mapping.

    Examples:
      >>> f = JobFrame.from_records(
      ...   "1",
      ...   [
      ...     (1, "h", "cpu", "user", None, 1, 1.0, None, None),
      ...     (1, "h", "cpu", "nice", None, 1, 1.0, None, None),
      ...   ],
      ... )
      >>> f.schema()
      {'cpu': ['nice', 'user']}
    """
    out: dict[str, set[str]] = {}
    for typ, event in self.series:
      out.setdefault(typ, set()).add(event)
    return {t: sorted(evs) for t, evs in sorted(out.items())}

  def check_contract(self) -> None:
    """
    Raise when dtypes, lengths or sort order violate the JobFrame layout.

    Returns:
      None

    Raises:
      ValueError: A column has the wrong dtype or length, or rows are not
        sorted by ``(series_id, host_id, dev_id, time_us)``.

    Examples:
      >>> JobFrame.from_records("1", []).check_contract()
    """
    n = len(self)
    expected = {
      "time_us": TIME_DTYPE,
      "host_id": ID_DTYPE,
      "series_id": ID_DTYPE,
      "dev_id": ID_DTYPE,
      "owned": np.uint8,
      "value": VALUE_DTYPE,
      "arc": VALUE_DTYPE,
      "delta": VALUE_DTYPE,
      "sentinel": np.int16,
    }
    for name, dtype in expected.items():
      col = getattr(self, name)
      if col.dtype != np.dtype(dtype):
        raise ValueError(f"{name} dtype {col.dtype} != {np.dtype(dtype)}")
      if col.shape != (n,):
        raise ValueError(f"{name} shape {col.shape} != ({n},)")
    if n > 1:
      key = _sort_key_matrix(
        self.series_id, self.host_id, self.dev_id, self.time_us
      )
      if not np.all(np.lexsort(key) == np.arange(n)):
        raise ValueError("rows are not sorted by (series, host, dev, time)")
    if self.series_id.size and int(self.series_id.max()) >= len(self.series):
      raise ValueError("series_id out of range")
    if self.host_id.size and int(self.host_id.max()) >= len(self.hosts):
      raise ValueError("host_id out of range")
    if self.dev_id.size and int(np.abs(self.dev_id).max()) >= len(self.devs):
      raise ValueError("dev_id out of range")


def _sort_key_matrix(
  series_id: np.ndarray,
  host_id: np.ndarray,
  dev_id: np.ndarray,
  time_us: np.ndarray,
) -> tuple[np.ndarray, ...]:
  """
  Keys for ``np.lexsort`` (last key is primary): series, host, dev, time.

  Args:
    series_id (np.ndarray): Series ids.
    host_id (np.ndarray): Host ids.
    dev_id (np.ndarray): Device ids.
    time_us (np.ndarray): Times.

  Returns:
    tuple[np.ndarray, ...]: ``(time_us, dev_id, host_id, series_id)``.

  Examples:
    >>> import numpy as np
    >>> k = _sort_key_matrix(
    ...   np.array([1, 0]), np.array([0, 0]), np.array([0, 0]), np.array([5, 9])
    ... )
    >>> np.lexsort(k).tolist()
    [1, 0]
  """
  return (time_us, dev_id, host_id, series_id)


def build_job_frame(
  jid: str, interner: NameInterner, chunk: DecodedChunk
) -> JobFrame:
  """
  Assemble decoded columns into a sorted JobFrame, consuming ``chunk``.

  ``(type_id, event_id)`` pairs become series ids in first-seen order of the
  sorted ``(type, event)`` names so series ids are deterministic for a job
  regardless of chunk order. One ``lexsort`` order gathers every column and
  each source column is popped from ``chunk`` as its sorted copy is made,
  so the peak stays near one frame plus one column; ``chunk`` is left with
  empty columns.

  Args:
    jid (str): Job id.
    interner (NameInterner): Name tables used while decoding.
    chunk (DecodedChunk): Unsorted columns (ownership moves here).

  Returns:
    JobFrame: Sorted frame with name tables.

  Examples:
    >>> ni = NameInterner()
    >>> h, t, e = ni.host("h"), ni.type("cpu"), ni.event("user")
    >>> ch = DecodedChunk(
    ...   time_us=np.array([2, 1]),
    ...   host_id=np.array([h, h], dtype=np.int32),
    ...   type_id=np.array([t, t], dtype=np.int32),
    ...   event_id=np.array([e, e], dtype=np.int32),
    ...   dev_id=np.zeros(2, dtype=np.int32),
    ...   owned=np.ones(2, dtype=np.uint8),
    ...   value=np.array([1.0, 2.0]),
    ...   arc=np.array([0.0, 0.0]),
    ...   delta=np.array([0.0, 0.0]),
    ... )
    >>> f = build_job_frame("1", ni, ch)
    >>> f.time_us.tolist(), f.series, len(ch)
    ([1, 2], [('cpu', 'user')], 0)
  """
  n = len(chunk)
  n_events = max(1, len(interner.events))
  pair_code = chunk.pop("type_id").astype(np.int64)
  pair_code *= n_events
  pair_code += chunk.pop("event_id")
  present = (
    np.flatnonzero(np.bincount(pair_code)) if n else np.empty(0, np.int64)
  )
  series = sorted(
    (interner.types[int(c // n_events)], interner.events[int(c % n_events)])
    for c in present.tolist()
  )
  lookup = np.empty(int(present.max()) + 1 if n else 0, dtype=ID_DTYPE)
  for sid, (t, e) in enumerate(series):
    lookup[interner.types.index(t) * n_events + interner.events.index(e)] = sid
  series_id = lookup[pair_code]
  del pair_code
  order = (
    np.lexsort(
      _sort_key_matrix(series_id, chunk.host_id, chunk.dev_id, chunk.time_us)
    )
    if n > 1
    else np.arange(n)
  )

  def take(name: str, dtype: Any) -> np.ndarray:
    """
    Pop one source column and return its sorted copy.

    Args:
      name (str): ``chunk`` field name.
      dtype (Any): Contract dtype of the column.

    Returns:
      np.ndarray: Column gathered by ``order`` with the contract dtype.

    Examples:
      >>> take("owned", np.uint8)  # doctest: +SKIP
    """
    return chunk.pop(name)[order].astype(dtype, copy=False)

  cols = {
    "time_us": take("time_us", TIME_DTYPE),
    "host_id": take("host_id", ID_DTYPE),
    "series_id": series_id[order],
    "dev_id": take("dev_id", ID_DTYPE),
    "owned": take("owned", np.uint8),
    "arc": take("arc", VALUE_DTYPE),
    "delta": take("delta", VALUE_DTYPE),
  }
  del series_id
  # Last, once ``chunk`` is empty: the sentinel pass widens ``value``.
  value = take("value", VALUE_DTYPE)
  codes = sentinel_codes(value)
  value = np.where(codes != 0, np.nan, value)
  return JobFrame(
    jid=str(jid),
    **cols,
    value=value,
    sentinel=codes,
    hosts=list(interner.hosts),
    series=series,
    devs=list(interner.devs),
  )
