"""
Set-based ``host_data`` insert (COPY -> staging -> ON CONFLICT DO NOTHING).

A/B arm ``HPCPERFSTATS_HOST_INSERT_ARM=baseline|candidate`` selects Django
``bulk_create(ignore_conflicts=True)`` vs this path. Default is ``candidate``
(COPY) after Podman large-data A/B retain; opt out with
``HPCPERFSTATS_SYNC_HOST_DATA_COPY=0``.

  Attributes:
  HOST_DATA_COPY_COLUMNS (tuple[str, ...]): Column order for COPY / INSERT.
  _STAGE_DDL (str): TEMP staging table DDL (ON COMMIT DELETE ROWS).
  _STAGE_INSERT (str): INSERT...SELECT...ON CONFLICT DO NOTHING SQL.
  _HOST_STAGE_NAME (str): Host COPY stage table name.
  _HOST_CONFLICT_COLS (tuple[str, ...]): Unique key columns for pre-COPY
    collapse (same as Timescale ON CONFLICT target).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from hpcperfstats.dbload.lib.date_utils import to_pydatetime_or_none
from hpcperfstats.dbload.lib.io_helpers import _dev_str_from_stats_row

HOST_DATA_COPY_COLUMNS: tuple[str, ...] = (
  "time",
  "host",
  "jid",
  "type",
  "dev",
  "event",
  "unit",
  "value",
  "delta",
  "arc",
)

_STAGE_DDL = """
CREATE TEMP TABLE IF NOT EXISTS host_data_ingest_stage (
  time timestamptz NULL,
  host varchar(64) NULL,
  jid varchar(32) NULL,
  type varchar(32) NULL,
  dev varchar(64) NULL,
  event varchar(64) NULL,
  unit varchar(16) NULL,
  value real NULL,
  delta real NULL,
  arc real NULL
) ON COMMIT DELETE ROWS
"""

_STAGE_INSERT = """
INSERT INTO host_data (
  time, host, jid, type, dev, event, unit, value, delta, arc
)
SELECT
  time, host, jid, type, dev, event, unit, value, delta, arc
FROM host_data_ingest_stage
ON CONFLICT (time, host, type, event, dev) DO NOTHING
"""

_HOST_STAGE_NAME = "host_data_ingest_stage"
_HOST_CONFLICT_COLS: tuple[str, ...] = (
  "time",
  "host",
  "type",
  "event",
  "dev",
)


def _collapse_host_conflict_keys(
  rows: Sequence[Any] | pd.DataFrame,
) -> Sequence[Any] | pd.DataFrame:
  """
  Keep one row per Timescale conflict key ``(time, host, type, event, dev)``.

  Duplicate keys keep the last row in batch order. Already-unique inputs are
  returned unchanged.

  Args:
    rows (Sequence[Any] | pd.DataFrame): Namedtuple rows or a host frame.

  Returns:
    Sequence[Any] | pd.DataFrame: Collapsed rows of the same kind as ``rows``.

  Examples:
    >>> import pandas as pd
    >>> from datetime import UTC, datetime
    >>> t = datetime(2026, 1, 1, tzinfo=UTC)
    >>> f = pd.DataFrame(
    ...   [
    ...     {
    ...       "time": t,
    ...       "host": "h",
    ...       "type": "cpu",
    ...       "event": "user",
    ...       "dev": "",
    ...       "value": 1.0,
    ...     },
    ...     {
    ...       "time": t,
    ...       "host": "h",
    ...       "type": "cpu",
    ...       "event": "user",
    ...       "dev": "",
    ...       "value": 9.0,
    ...     },
    ...   ]
    ... )
    >>> len(_collapse_host_conflict_keys(f))
    1
  """
  if isinstance(rows, pd.DataFrame):
    if rows.empty:
      return rows
    subset = [c for c in _HOST_CONFLICT_COLS if c in rows.columns]
    if len(subset) != len(_HOST_CONFLICT_COLS):
      return rows
    if not rows.duplicated(subset=subset, keep=False).any():
      return rows
    return rows.drop_duplicates(subset=subset, keep="last")
  if not rows:
    return rows
  by_key: dict[tuple[Any, ...], Any] = {}
  for row in rows:
    key = tuple(_host_row_value(row, col) for col in _HOST_CONFLICT_COLS)
    by_key[key] = row
  if len(by_key) == len(rows):
    return rows
  return list(by_key.values())


def _copy_stage_ready(connection: Any, name: str) -> bool:
  """
  Return whether this live backend already created ``name``.

  The set lives on the Django connection wrapper and is ignored when the
  socket object changes, including after a reconnect.

  Args:
    connection (Any): Django connection wrapper for this thread.
    name (str): Temporary stage table name.

  Returns:
    bool: True when ``name`` was committed on the current socket.

  Examples:
    >>> _copy_stage_ready(object(), "host_data_ingest_stage")
    False
  """
  raw = getattr(connection, "connection", None)
  state = getattr(connection, "_hps_copy_stages", None)
  if not isinstance(state, tuple) or len(state) != 2:
    return False
  remembered, names = state
  return remembered is raw and name in names


def _mark_copy_stage(connection: Any, name: str) -> None:
  """
  Remember that ``name`` exists on the current database socket.

  Args:
    connection (Any): Django connection wrapper for this thread.
    name (str): Temporary stage table name.

  Returns:
    None

  Examples:
    >>> conn = type("C", (), {})()
    >>> _mark_copy_stage(conn, "host_data_ingest_stage")
    >>> _copy_stage_ready(conn, "host_data_ingest_stage")
    True
  """
  raw = getattr(connection, "connection", None)
  state = getattr(connection, "_hps_copy_stages", None)
  if not isinstance(state, tuple) or len(state) != 2 or state[0] is not raw:
    names: set[str] = set()
    connection._hps_copy_stages = (raw, names)
  else:
    names = state[1]
  names.add(name)


def host_insert_arm() -> str:
  """
  Return the host_data insert A/B arm name.

  Reads ``HPCPERFSTATS_HOST_INSERT_ARM`` (``baseline`` or ``candidate``).
  When unset, returns ``candidate`` (COPY) after retaining A/B; use
  ``HPCPERFSTATS_SYNC_HOST_DATA_COPY=0`` or ``ARM=baseline`` for ORM.

  Returns:
    str: ``baseline`` or ``candidate``.

  Examples:
    >>> host_insert_arm() in ("baseline", "candidate")
    True
  """
  raw = os.environ.get("HPCPERFSTATS_HOST_INSERT_ARM", "").strip().lower()
  if raw in ("baseline", "candidate"):
    return raw
  # Opt-out of retained COPY default (A/B baseline re-runs).
  copy_flag = (
    os.environ.get("HPCPERFSTATS_SYNC_HOST_DATA_COPY", "").strip().lower()
  )
  if copy_flag in ("0", "false", "no", "baseline"):
    return "baseline"
  # Default ON after Podman A/B retain (host_data_insert_ab_fde1031c...).
  # Opt-out via HPCPERFSTATS_SYNC_HOST_DATA_COPY=0 or ARM=baseline.
  return "candidate"


def use_copy_host_insert() -> bool:
  """
  Return whether set-based COPY insert is selected.

  Returns:
    bool: True when arm is ``candidate``.

  Examples:
    >>> isinstance(use_copy_host_insert(), bool)
    True
  """
  return host_insert_arm() == "candidate"


def _sql_literal(value: Any) -> str:
  """
  Format one COPY text-format field (tab-separated).

  Args:
    value (Any): Python value from a ``host_data`` instance field.

  Returns:
    str: Postgres text COPY token (``\\N`` for NULL).

  Examples:
    >>> _sql_literal(None) == "\\\\N"
    True
  """
  if value is None:
    return "\\N"
  if isinstance(value, bool):
    return "t" if value else "f"
  # datetime / date -> ISO for timestamptz COPY
  iso = getattr(value, "isoformat", None)
  text = iso() if callable(iso) else str(value)
  return (
    text.replace("\\", "\\\\")
    .replace("\t", "\\t")
    .replace("\n", "\\n")
    .replace("\r", "\\r")
  )


def _host_row_value(row: Any, col: str) -> Any:
  """
  Return one host_data COPY field from a stats namedtuple.

  Matches ``host_data_instance_from_stats_row``: blank device stays ``''``,
  idle jid ``-`` and NaN measures become NULL.

  Args:
    row (Any): itertuples row with host_data field attributes.
    col (str): ``HOST_DATA_COPY_COLUMNS`` name.

  Returns:
    Any: Scalar passed to ``_sql_literal``.

  Examples:
    >>> from types import SimpleNamespace
    >>> _host_row_value(SimpleNamespace(dev=None), "dev")
    ''
  """
  if col == "time":
    return to_pydatetime_or_none(row.time)
  if col == "jid":
    jid_val = getattr(row, "jid", None)
    if pd.notna(jid_val) and str(jid_val) != "-":
      return str(jid_val)
    return None
  if col == "dev":
    return _dev_str_from_stats_row(row)
  if col in ("value", "delta", "arc"):
    val = getattr(row, col)
    return float(val) if pd.notna(val) else None
  return getattr(row, col)


def _copy_dev_text(dev_val: Any) -> str:
  """
  Match the namedtuple device rule for one dataframe cell.

  Missing and NaN cells become a blank device, not NULL.

  Args:
    dev_val (Any): Device cell from a host stats frame.

  Returns:
    str: Device text, or ``''`` when the cell is missing.

  Examples:
    >>> _copy_dev_text(float("nan"))
    ''
    >>> _copy_dev_text("gpu0")
    'gpu0'
  """
  if dev_val is None or (isinstance(dev_val, float) and pd.isna(dev_val)):
    return ""
  if pd.isna(dev_val):
    return ""
  text = str(dev_val).strip()
  if not text or text.lower() == "nan":
    return ""
  return text


def _host_frame_cell(col: str, value: Any) -> Any:
  """
  Return one host COPY field from a dataframe cell.

  Args:
    col (str): ``HOST_DATA_COPY_COLUMNS`` name.
    value (Any): Cell from that column.

  Returns:
    Any: Scalar passed to ``_sql_literal``.

  Examples:
    >>> _host_frame_cell("jid", "-") is None
    True
  """
  if col == "time":
    return to_pydatetime_or_none(value)
  if col == "jid":
    if pd.notna(value) and str(value) != "-":
      return str(value)
    return None
  if col == "dev":
    return _copy_dev_text(value)
  if col in ("value", "delta", "arc"):
    return float(value) if pd.notna(value) else None
  return value


def coerce_host_stats_to_columnar(
  payload: Any,
) -> dict[str, list[Any]]:
  """
  Normalize host parse payload to parallel lists for COPY (no DataFrame).

  Args:
    payload (Any): Columnar Mapping, sequence of row dicts/namedtuples, or
      empty/``None``.

  Returns:
    dict[str, list[Any]]: Keys from ``HOST_DATA_COPY_COLUMNS`` (missing
      columns filled with ``None``). Empty when there are no rows.

  Examples:
    >>> coerce_host_stats_to_columnar([{"time": 1.0, "host": "h"}])["host"]
    ['h']
    >>> coerce_host_stats_to_columnar(None)["time"]
    []
  """
  if payload is None:
    return {c: [] for c in HOST_DATA_COPY_COLUMNS}
  if isinstance(payload, Mapping) and not isinstance(payload, pd.DataFrame):
    n = len(payload["time"]) if "time" in payload else 0
    if n == 0:
      return {c: [] for c in HOST_DATA_COPY_COLUMNS}
    return {
      c: list(payload[c]) if c in payload else [None] * n
      for c in HOST_DATA_COPY_COLUMNS
    }
  if isinstance(payload, pd.DataFrame):
    if payload.empty:
      return {c: [] for c in HOST_DATA_COPY_COLUMNS}
    n = len(payload)
    return {
      c: payload[c].tolist() if c in payload.columns else [None] * n
      for c in HOST_DATA_COPY_COLUMNS
    }
  if not payload:
    return {c: [] for c in HOST_DATA_COPY_COLUMNS}
  cols: dict[str, list[Any]] = {c: [] for c in HOST_DATA_COPY_COLUMNS}
  for row in payload:
    for c in HOST_DATA_COPY_COLUMNS:
      if isinstance(row, Mapping):
        cols[c].append(row.get(c))
      else:
        cols[c].append(getattr(row, c, None))
  return cols


def normalize_host_columnar_for_copy(
  cols: Mapping[str, Sequence[Any]],
) -> dict[str, list[Any]]:
  """
  Apply COPY cell coercion row-wise (same rules as ``_host_frame_cell``).

  Used on Codon parse output before IPC so the write stage can serialize
  without re-normalizing scalars.

  Args:
    cols (Mapping[str, Sequence[Any]]): Host columnar SoA.

  Returns:
    dict[str, list[Any]]: COPY-ready parallel lists.

  Examples:
    >>> out = normalize_host_columnar_for_copy(
    ...   {"time": [1.0], "jid": ["-"], "dev": [float("nan")], "host": ["h"]}
    ... )
    >>> out["jid"][0] is None
    True
    >>> out["dev"][0]
    ''
  """
  base = coerce_host_stats_to_columnar(cols)
  n = len(base.get("time") or [])
  if n == 0:
    return base
  out: dict[str, list[Any]] = {c: [] for c in HOST_DATA_COPY_COLUMNS}
  for i in range(n):
    for col in HOST_DATA_COPY_COLUMNS:
      seq = base.get(col)
      raw = seq[i] if seq is not None and i < len(seq) else None
      out[col].append(_host_frame_cell(col, raw))
  return out


def _host_columns_to_copy_bytes(
  cols: Mapping[str, Sequence[Any]],
  *,
  cells_normalized: bool = False,
) -> bytes:
  """
  Serialize columnar host SoA (parallel lists) to COPY text.

  Args:
    cols (Mapping[str, Sequence[Any]]): Column name -> values. Missing
      ``HOST_DATA_COPY_COLUMNS`` keys are treated as all-NULL for that
      column. Row count is ``len(cols[\"time\"])`` when present, else the
      longest column length.
    cells_normalized (bool): When True, cells are already COPY scalars.

  Returns:
    bytes: UTF-8 COPY body (empty when there are no rows).

  Examples:
    >>> _host_columns_to_copy_bytes({\"time\": []})
    b''
  """
  n = len(cols["time"]) if "time" in cols else 0
  if n == 0:
    for col in HOST_DATA_COPY_COLUMNS:
      seq = cols.get(col)
      if seq is not None:
        n = max(n, len(seq))
  if n == 0:
    return b""
  columns = {
    col: list(cols[col]) if col in cols else [None] * n
    for col in HOST_DATA_COPY_COLUMNS
  }
  for col in HOST_DATA_COPY_COLUMNS:
    if len(columns[col]) < n:
      columns[col].extend([None] * (n - len(columns[col])))
  lines: list[str] = []
  for i in range(n):
    fields = []
    for col in HOST_DATA_COPY_COLUMNS:
      cell = columns[col][i]
      if not cells_normalized:
        cell = _host_frame_cell(col, cell)
      fields.append(_sql_literal(cell))
    lines.append("\t".join(fields))
  return ("\n".join(lines) + "\n").encode("utf-8")


def _collapse_host_columnar_conflict_keys(
  cols: Mapping[str, Sequence[Any]],
) -> dict[str, list[Any]]:
  """
  Last-wins collapse on ``_HOST_CONFLICT_COLS`` for a columnar SoA.

  Args:
    cols (Mapping[str, Sequence[Any]]): Host column lists.

  Returns:
    dict[str, list[Any]]: Collapsed SoA (copy of lists).

  Examples:
    >>> _collapse_host_columnar_conflict_keys({\"time\": []})[\"time\"]
    []
  """
  n = len(cols.get("time", []))
  if n == 0:
    return {c: list(cols.get(c, [])) for c in HOST_DATA_COPY_COLUMNS}
  by_key: dict[tuple[Any, ...], int] = {}
  order: list[int] = []
  for i in range(n):
    key = tuple(
      _host_frame_cell(
        c, cols[c][i] if c in cols and i < len(cols[c]) else None
      )
      for c in _HOST_CONFLICT_COLS
    )
    if key in by_key:
      prev = by_key[key]
      order[prev] = -1
    by_key[key] = len(order)
    order.append(i)
  keep = [i for i in order if i >= 0]
  if len(keep) == n:
    return {
      c: list(cols[c]) if c in cols else [None] * n
      for c in HOST_DATA_COPY_COLUMNS
    }
  return {
    c: [(cols[c][i] if c in cols and i < len(cols[c]) else None) for i in keep]
    for c in HOST_DATA_COPY_COLUMNS
  }


def _host_frame_to_copy_bytes(frame: pd.DataFrame) -> bytes:
  """
  Serialize a host dataframe slice to COPY text.

  Args:
    frame (pd.DataFrame): Slice of parsed host stats.

  Returns:
    bytes: UTF-8 COPY body.

  Examples:
    >>> _host_frame_to_copy_bytes(pd.DataFrame())
    b''
  """
  if frame.empty:
    return b""
  n = len(frame)
  columns = {
    col: frame[col].tolist() if col in frame.columns else [None] * n
    for col in HOST_DATA_COPY_COLUMNS
  }
  return _host_columns_to_copy_bytes(columns)


def host_rows_to_copy_bytes(
  rows: Sequence[Any] | pd.DataFrame | Mapping[str, Sequence[Any]],
  *,
  copy_unique: bool = False,
) -> bytes:
  """
  Serialize parsed host rows to the same COPY body as model instances.

  A dataframe is walked by column. A columnar Mapping is encoded via
  ``_host_columns_to_copy_bytes``. A sequence of namedtuples keeps the
  attribute walk used by the ORM fallback and existing callers.

  Args:
    rows (Sequence[Any] | pd.DataFrame | Mapping[str, Sequence[Any]]):
      Namedtuple rows, a host frame, or finished Codon SoA columns.
    copy_unique (bool): Skip pre-COPY collapse when the SoA is already unique.

  Returns:
    bytes: UTF-8 COPY body.

  Examples:
    >>> host_rows_to_copy_bytes([])
    b''
  """
  if isinstance(rows, Mapping) and not isinstance(rows, pd.DataFrame):
    if copy_unique:
      return _host_columns_to_copy_bytes(rows, cells_normalized=True)
    collapsed = _collapse_host_columnar_conflict_keys(rows)
    return _host_columns_to_copy_bytes(collapsed)
  rows = _collapse_host_conflict_keys(rows)
  if isinstance(rows, pd.DataFrame):
    return _host_frame_to_copy_bytes(rows)
  if not rows:
    return b""
  lines: list[str] = []
  for row in rows:
    fields = [
      _sql_literal(_host_row_value(row, col)) for col in HOST_DATA_COPY_COLUMNS
    ]
    lines.append("\t".join(fields))
  return ("\n".join(lines) + "\n").encode("utf-8")


def host_data_objs_to_copy_bytes(objs: Sequence[Any]) -> bytes:
  """
  Serialize ``host_data`` instances to Postgres text COPY payload.

  Args:
    objs (Sequence[Any]): Unsaved ``host_data`` model instances.

  Returns:
    bytes: UTF-8 COPY body (tab-separated columns, newline rows).

  Examples:
    >>> host_data_objs_to_copy_bytes([])
    b''
  """
  if not objs:
    return b""
  objs = _collapse_host_conflict_keys(objs)
  lines: list[str] = []
  for obj in objs:
    fields = [_sql_literal(getattr(obj, col)) for col in HOST_DATA_COPY_COLUMNS]
    lines.append("\t".join(fields))
  return ("\n".join(lines) + "\n").encode("utf-8")


def _write_host_copy_payload(payload: bytes) -> None:
  """
  COPY ``payload`` into the host staging table and run the insert.

  When write telemetry is on, records ``stage_ddl_s``, ``copy_s``, and
  ``conflict_insert_s``. The ``DO NOTHING`` statement is unchanged.

  Args:
    payload (bytes): UTF-8 text COPY body. Empty skips the statement.

  Returns:
    None

  Examples:
    >>> _write_host_copy_payload(b"")
  """
  if not payload:
    return
  from contextlib import nullcontext

  from django.db import connection, transaction

  col_list = ", ".join(HOST_DATA_COPY_COLUMNS)
  copy_sql = f"COPY host_data_ingest_stage ({col_list}) FROM STDIN"
  from hpcperfstats.dbload import sync_timedb as st

  telem = bool(getattr(st, "_ingest_write_telem_on", False))
  copy_cm = st._held_ingest_write_phase("copy_s") if telem else nullcontext()
  conflict_cm = (
    st._held_ingest_write_phase("conflict_insert_s") if telem else nullcontext()
  )
  ddl_cm = (
    st._held_ingest_write_phase("stage_ddl_s") if telem else nullcontext()
  )
  # Django defaults to autocommit; keep TEMP visible for COPY + INSERT.
  created = False
  with transaction.atomic(), connection.cursor() as cursor:
    if not _copy_stage_ready(connection, _HOST_STAGE_NAME):
      with ddl_cm:
        cursor.execute(_STAGE_DDL)
      created = True
    with copy_cm, cursor.copy(copy_sql) as copy:
      copy.write(payload)
    with conflict_cm:
      cursor.execute(_STAGE_INSERT)
  if created:
    _mark_copy_stage(connection, _HOST_STAGE_NAME)


def bulk_insert_host_rows(
  rows: Sequence[Any] | pd.DataFrame | Mapping[str, Sequence[Any]],
  *,
  copy_unique: bool = False,
) -> None:
  """
  Insert parsed host rows via COPY without building ``host_data`` models.

  Args:
    rows (Sequence[Any] | pd.DataFrame | Mapping[str, Sequence[Any]]):
      Namedtuple rows, a host frame, or finished Codon SoA columns.
    copy_unique (bool): Skip pre-COPY collapse when the SoA is already unique.

  Returns:
    None

  Examples:
    >>> bulk_insert_host_rows([])
  """
  if isinstance(rows, Mapping) and not isinstance(rows, pd.DataFrame):
    if not rows.get("time"):
      return
  elif isinstance(rows, pd.DataFrame):
    if rows.empty:
      return
  elif not rows:
    return
  _write_host_copy_payload(
    host_rows_to_copy_bytes(rows, copy_unique=copy_unique)
  )


def bulk_insert_host_data_ignore_conflicts(objs: Sequence[Any]) -> None:
  """
  Insert ``host_data`` rows via COPY staging with conflict skip.

  Matches Django ``bulk_create(..., ignore_conflicts=True)`` semantics on
  unique ``(time, host, type, event, dev)``. When write telemetry is on,
  records ``stage_ddl_s``, ``copy_s``, and ``conflict_insert_s``.

  Args:
    objs (Sequence[Any]): Unsaved ``host_data`` model instances to insert.

  Returns:
    None

  Examples:
    >>> bulk_insert_host_data_ignore_conflicts([])
  """
  if not objs:
    return
  _write_host_copy_payload(host_data_objs_to_copy_bytes(objs))


def bulk_create_host_data_ignore_conflicts(objs: Sequence[Any]) -> None:
  """
  Insert ``host_data`` via Django ``bulk_create(ignore_conflicts=True)``.

  Args:
    objs (Sequence[Any]): Unsaved ``host_data`` model instances to insert.

  Returns:
    None

  Examples:
    >>> bulk_create_host_data_ignore_conflicts([])  # doctest: +SKIP
  """
  if not objs:
    return
  from hpcperfstats.site.lib.machine.models import (
    host_data as host_data_model,
  )

  host_data_model.objects.bulk_create(list(objs), ignore_conflicts=True)


def insert_host_data_batch(objs: Sequence[Any]) -> None:
  """
  Insert one ``host_data`` batch using the selected A/B arm.

  Args:
    objs (Sequence[Any]): Unsaved ``host_data`` model instances to insert.

  Returns:
    None

  Examples:
    >>> insert_host_data_batch([])  # doctest: +SKIP
  """
  if use_copy_host_insert():
    bulk_insert_host_data_ignore_conflicts(objs)
  else:
    bulk_create_host_data_ignore_conflicts(objs)
