"""
Set-based ``proc_data`` upsert (COPY -> staging -> ON CONFLICT DO UPDATE).

Preserves Django ``bulk_create(..., update_conflicts=True)`` semantics after
client-side peak-merge. A/B arm ``HPCPERFSTATS_PROC_INSERT_ARM``; default
``candidate`` (COPY) after Podman A/B retain; opt out with
``HPCPERFSTATS_SYNC_PROC_DATA_COPY=0``.

  Attributes:
  PROC_DATA_COPY_COLUMNS (tuple[str, ...]): Column order for COPY / INSERT.
  PROC_DATA_UPDATE_FIELDS (tuple[str, ...]): ON CONFLICT DO UPDATE columns.
  _STAGE_DDL (str): TEMP staging table DDL (ON COMMIT DELETE ROWS).
  _PROC_STAGE_NAME (str): Proc COPY stage table name.
  _PROC_PASSTHROUGH_COLUMNS (frozenset[str]): Columns copied without
    bigint coercion.
  _PROC_CONFLICT_COLS (tuple[str, ...]): Unique key columns for pre-COPY
    peak-merge collapse (same as ON CONFLICT target).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  HOST_PROC_KEYS,
  HOST_PROC_PEAK_KEYS,
  dedupe_proc_stats_peak_merge,
)

PROC_DATA_COPY_COLUMNS: tuple[str, ...] = (
  "jid",
  "host",
  "proc",
  "device",
  *HOST_PROC_KEYS,
)

PROC_DATA_UPDATE_FIELDS: tuple[str, ...] = ("device", *HOST_PROC_KEYS)

_STAGE_DDL = """
CREATE TEMP TABLE IF NOT EXISTS proc_data_ingest_stage (
  jid varchar(32) NULL,
  host varchar(64) NULL,
  proc varchar(512) NULL,
  device varchar(512) NULL,
  uid bigint NULL,
  vm_peak bigint NULL,
  vm_size bigint NULL,
  vm_lck bigint NULL,
  vm_hwm bigint NULL,
  vm_rss bigint NULL,
  vm_data bigint NULL,
  vm_stk bigint NULL,
  vm_exe bigint NULL,
  vm_lib bigint NULL,
  vm_pte bigint NULL,
  vm_swap bigint NULL,
  threads integer NULL
) ON COMMIT DELETE ROWS
"""

_PROC_STAGE_NAME = "proc_data_ingest_stage"
_PROC_PASSTHROUGH_COLUMNS = frozenset(("jid", "host", "proc"))
_PROC_CONFLICT_COLS: tuple[str, ...] = ("jid", "host", "proc")


def _collapse_proc_columnar_conflict_keys(
  cols: Mapping[str, Sequence[Any]],
) -> dict[str, list[Any]]:
  """
  Peak-merge collapse on ``(jid, host, proc)`` for columnar proc SoA.

  Args:
    cols (Mapping[str, Sequence[Any]]): Proc column lists.

  Returns:
    dict[str, list[Any]]: Collapsed SoA aligned to ``PROC_DATA_COPY_COLUMNS``.

  Examples:
    >>> _collapse_proc_columnar_conflict_keys({"proc": []})["proc"]
    []
  """
  n = len(cols.get("proc", []))
  if n == 0:
    return {c: list(cols.get(c, [])) for c in PROC_DATA_COPY_COLUMNS}
  as_dicts: list[dict[str, Any]] = []
  for i in range(n):
    as_dicts.append(
      {
        c: (cols[c][i] if c in cols and i < len(cols[c]) else None)
        for c in PROC_DATA_COPY_COLUMNS
      }
    )
  keys = [(d.get("jid"), d.get("host"), d.get("proc")) for d in as_dicts]
  if len(set(keys)) == len(keys):
    return {
      c: list(cols.get(c, [])) if c in cols else [None] * n
      for c in PROC_DATA_COPY_COLUMNS
    }
  merged = _collapse_proc_conflict_keys(as_dicts)
  if not merged:
    return {c: [] for c in PROC_DATA_COPY_COLUMNS}
  return {c: [row.get(c) for row in merged] for c in PROC_DATA_COPY_COLUMNS}


def _collapse_proc_conflict_keys(
  rows: Sequence[Any] | pd.DataFrame,
) -> Sequence[Any] | pd.DataFrame:
  """
  Keep one row per ``(jid, host, proc)`` with peak-aware merge.

  Dataframe slices use ``dedupe_proc_stats_peak_merge``. Model sequences
  merge attribute dicts with the same helper. Already-unique inputs are
  returned unchanged.

  Args:
    rows (Sequence[Any] | pd.DataFrame): Namedtuple rows, models, or a frame.

  Returns:
    Sequence[Any] | pd.DataFrame: Collapsed rows of the same kind as ``rows``.

  Examples:
    >>> import pandas as pd
    >>> f = pd.DataFrame(
    ...   [
    ...     {"jid": "1", "host": "h", "proc": "p", "vm_stk": 9, "threads": 1},
    ...     {"jid": "1", "host": "h", "proc": "p", "vm_stk": 3, "threads": 8},
    ...   ]
    ... )
    >>> out = _collapse_proc_conflict_keys(f)
    >>> len(out), int(out.iloc[0]["vm_stk"]), int(out.iloc[0]["threads"])
    (1, 9, 8)
  """
  if isinstance(rows, pd.DataFrame):
    if rows.empty:
      return rows
    subset = [c for c in _PROC_CONFLICT_COLS if c in rows.columns]
    if len(subset) != len(_PROC_CONFLICT_COLS):
      return rows
    if not rows.duplicated(subset=subset, keep=False).any():
      return rows
    merged = dedupe_proc_stats_peak_merge(rows.to_dict(orient="records"))
    return pd.DataFrame(merged)
  if not rows:
    return rows
  first = rows[0]
  if hasattr(first, "keys") and callable(getattr(first, "keys", None)):
    as_dicts = [dict(row) for row in rows]
  else:
    as_dicts = []
    for row in rows:
      as_dicts.append(
        {col: getattr(row, col, None) for col in PROC_DATA_COPY_COLUMNS}
      )
  keys = [(d.get("jid"), d.get("host"), d.get("proc")) for d in as_dicts]
  if len(set(keys)) == len(keys):
    return rows
  merged = dedupe_proc_stats_peak_merge(as_dicts)
  if hasattr(first, "keys") and callable(getattr(first, "keys", None)):
    return merged
  # Model / namedtuple path: rebuild via SimpleNamespace for COPY kwargs.
  from types import SimpleNamespace

  return [SimpleNamespace(**row) for row in merged]


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
    >>> _copy_stage_ready(object(), "proc_data_ingest_stage")
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
    >>> _mark_copy_stage(conn, "proc_data_ingest_stage")
    >>> _copy_stage_ready(conn, "proc_data_ingest_stage")
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


def _stage_upsert_sql() -> str:
  """
  Build INSERT...SELECT...ON CONFLICT DO UPDATE for ``proc_data``.

  Peak columns use ``GREATEST``, which ignores nulls. The ``WHERE`` skips
  the rewrite when no last-write field differs and no peak would rise.
  Stage rows are read in ``(jid, host, proc)`` order so concurrent upserts
  lock the unique index in one order.

  Returns:
    str: Upsert SQL string.

  Examples:
    >>> "GREATEST(proc_data.vm_peak, EXCLUDED.vm_peak)" in _stage_upsert_sql()
    True
  """
  cols = ", ".join(PROC_DATA_COPY_COLUMNS)
  set_parts: list[str] = []
  where_parts: list[str] = []
  for field in PROC_DATA_UPDATE_FIELDS:
    if field in HOST_PROC_PEAK_KEYS:
      set_parts.append(
        f"{field} = GREATEST(proc_data.{field}, EXCLUDED.{field})"
      )
      where_parts.append(
        f"(EXCLUDED.{field} IS NOT NULL AND "
        f"(proc_data.{field} IS NULL OR "
        f"EXCLUDED.{field} > proc_data.{field}))"
      )
    else:
      set_parts.append(f"{field} = EXCLUDED.{field}")
      where_parts.append(f"proc_data.{field} IS DISTINCT FROM EXCLUDED.{field}")
  return (
    f"INSERT INTO proc_data ({cols}) SELECT {cols} FROM proc_data_ingest_stage "
    f"ORDER BY jid, host, proc "
    f"ON CONFLICT (jid, host, proc) DO UPDATE SET {', '.join(set_parts)} "
    f"WHERE {' OR '.join(where_parts)}"
  )


def proc_insert_arm() -> str:
  """
  Return the proc_data insert A/B arm name.

  Returns:
    str: ``baseline`` or ``candidate``.

  Examples:
    >>> proc_insert_arm() in ("baseline", "candidate")
    True
  """
  raw = os.environ.get("HPCPERFSTATS_PROC_INSERT_ARM", "").strip().lower()
  if raw in ("baseline", "candidate"):
    return raw
  copy_flag = (
    os.environ.get("HPCPERFSTATS_SYNC_PROC_DATA_COPY", "").strip().lower()
  )
  if copy_flag in ("0", "false", "no", "baseline"):
    return "baseline"
  return "candidate"


def use_copy_proc_insert() -> bool:
  """
  Return whether set-based COPY upsert is selected for ``proc_data``.

  Returns:
    bool: True when arm is ``candidate``.

  Examples:
    >>> isinstance(use_copy_proc_insert(), bool)
    True
  """
  return proc_insert_arm() == "candidate"


def _sql_literal(value: Any) -> str:
  """
  Format one COPY text-format field (tab-separated).

  Float values from pandas-promoted sparse bigint columns are emitted as
  integer text (``0`` not ``0.0``); NaN becomes NULL.

  Args:
    value (Any): Python value from a ``proc_data`` instance field.

  Returns:
    str: Postgres text COPY token (``\\N`` for NULL).

  Examples:
    >>> _sql_literal(None) == "\\\\N"
    True
    >>> _sql_literal(0.0)
    '0'
  """
  if value is None:
    return "\\N"
  if isinstance(value, bool):
    return "t" if value else "f"
  # Sparse proc ints become pandas float64; bigint COPY rejects "0.0".
  if isinstance(value, float):
    if value != value:  # NaN
      return "\\N"
    return str(int(value))
  text = str(value)
  return (
    text.replace("\\", "\\\\")
    .replace("\t", "\\t")
    .replace("\n", "\\n")
    .replace("\r", "\\r")
  )


def coerce_proc_stats_to_columnar(
  payload: Any,
) -> dict[str, list[Any]]:
  """
  Normalize proc parse payload to parallel lists for COPY (no DataFrame).

  Args:
    payload (Any): Columnar Mapping, sequence of row dicts, or empty/``None``.

  Returns:
    dict[str, list[Any]]: Keys from ``PROC_DATA_COPY_COLUMNS`` when rows
      exist; otherwise empty lists per column.

  Examples:
    >>> coerce_proc_stats_to_columnar([{"proc": "p", "vm_peak": 1}])["proc"]
    ['p']
  """
  if payload is None:
    return {c: [] for c in PROC_DATA_COPY_COLUMNS}
  if isinstance(payload, Mapping) and not isinstance(payload, pd.DataFrame):
    n = len(payload["proc"]) if "proc" in payload else 0
    if n == 0:
      return {c: [] for c in PROC_DATA_COPY_COLUMNS}
    return {
      c: list(payload[c]) if c in payload else [None] * n
      for c in PROC_DATA_COPY_COLUMNS
    }
  if isinstance(payload, pd.DataFrame):
    if payload.empty:
      return {c: [] for c in PROC_DATA_COPY_COLUMNS}
    n = len(payload)
    return {
      c: payload[c].tolist() if c in payload.columns else [None] * n
      for c in PROC_DATA_COPY_COLUMNS
    }
  if not payload:
    return {c: [] for c in PROC_DATA_COPY_COLUMNS}
  cols: dict[str, list[Any]] = {c: [] for c in PROC_DATA_COPY_COLUMNS}
  for row in payload:
    for c in PROC_DATA_COPY_COLUMNS:
      if isinstance(row, Mapping):
        cols[c].append(row.get(c))
      else:
        cols[c].append(getattr(row, c, None))
  return cols


def normalize_proc_columnar_for_copy(
  cols: Mapping[str, Sequence[Any]],
) -> dict[str, list[Any]]:
  """
  Apply COPY bigint coercion row-wise (``_proc_copy_scalar`` on metrics).

  Args:
    cols (Mapping[str, Sequence[Any]]): Proc columnar SoA.

  Returns:
    dict[str, list[Any]]: COPY-ready parallel lists.

  Examples:
    >>> out = normalize_proc_columnar_for_copy(
    ...   {"proc": ["p"], "vm_peak": [1.0], "jid": ["1"], "host": ["h"]}
    ... )
    >>> out["vm_peak"][0]
    1
  """
  base = coerce_proc_stats_to_columnar(cols)
  n = len(base.get("proc") or [])
  if n == 0:
    return base
  out: dict[str, list[Any]] = {c: [] for c in PROC_DATA_COPY_COLUMNS}
  for i in range(n):
    for col in PROC_DATA_COPY_COLUMNS:
      seq = base.get(col)
      raw = seq[i] if seq is not None and i < len(seq) else None
      if col not in _PROC_PASSTHROUGH_COLUMNS:
        raw = _proc_copy_scalar(raw)
      out[col].append(raw)
  return out


def _proc_copy_scalar(value: Any) -> Any:
  """
  Coerce one proc COPY cell the way ``_proc_field_or_none`` does.

  Args:
    value (Any): Cell from a proc dataframe column.

  Returns:
    Any: ``None``, ``int``, or the original non-numeric value.

  Examples:
    >>> _proc_copy_scalar(0.0)
    0
    >>> _proc_copy_scalar(float("nan")) is None
    True
  """
  if value is None:
    return None
  try:
    if value != value:
      return None
  except Exception:
    pass
  if isinstance(value, float):
    return int(value)
  if type(value).__module__ == "numpy":
    try:
      return int(value)
    except TypeError, ValueError, OverflowError:
      return None
  return value


def _proc_frame_to_copy_bytes_vectorized(frame: pd.DataFrame) -> bytes | None:
  """
  Encode a proc frame to COPY text column-wise, byte-identical to the per-cell path.

  Text columns (``jid``, ``host``, ``proc``, ``device``) are factorized so
  ``_sql_literal`` runs once per unique value. Metric columns are float or
  integer arrays truncated to ``int64`` text (NaN becomes ``\\N``). Any
  column that is not plainly typed (absent, non-string text, object or bool
  metrics, values outside the ``int64`` range) makes this return ``None`` so
  the caller falls back to the per-cell encoder.

  Args:
    frame (pd.DataFrame): Slice of parsed proc stats.

  Returns:
    bytes | None: UTF-8 COPY body (``b""`` for an empty frame), or ``None``
      when the frame needs the per-cell path.

  Examples:
    >>> _proc_frame_to_copy_bytes_vectorized(pd.DataFrame())
    b''
  """
  if frame.empty:
    return b""
  if any(c not in frame.columns for c in PROC_DATA_COPY_COLUMNS):
    return None
  cols: list[list[str]] = []
  for col in PROC_DATA_COPY_COLUMNS:
    series = frame[col]
    if col in ("jid", "host", "proc", "device"):
      if pd.api.types.infer_dtype(series, skipna=True) not in (
        "string",
        "empty",
      ):
        return None
      codes, uniques = pd.factorize(series, use_na_sentinel=False)
      lit = np.empty(len(uniques), dtype=object)
      for i, u in enumerate(uniques):
        lit[i] = _sql_literal(_proc_copy_scalar(u))
      cols.append(lit.take(codes).tolist())
      continue
    if not isinstance(series.dtype, np.dtype) or series.dtype.kind not in "fiu":
      return None
    if series.dtype.kind != "f":
      cols.append(list(map(str, series.tolist())))
      continue
    arr = series.to_numpy(dtype="float64")
    nan = np.isnan(arr)
    if (np.abs(arr[~nan]) >= 9.0e18).any():
      return None
    text = list(map(str, np.where(nan, 0.0, arr).astype("int64").tolist()))
    for i in np.flatnonzero(nan).tolist():
      text[i] = "\\N"
    cols.append(text)
  return ("\n".join(map("\t".join, zip(*cols, strict=True))) + "\n").encode(
    "utf-8"
  )


def _proc_frame_to_copy_bytes(frame: pd.DataFrame) -> bytes:
  """
  Serialize a proc dataframe slice to COPY text.

  ``jid``, ``host``, and ``proc`` pass through. Other columns use the
  bigint coercion shared with the namedtuple builder. Tries
  ``_proc_frame_to_copy_bytes_vectorized`` first and falls back to the
  per-cell encoder when the frame is not plainly typed.

  Args:
    frame (pd.DataFrame): Slice of parsed proc stats.

  Returns:
    bytes: UTF-8 COPY body.

  Examples:
    >>> _proc_frame_to_copy_bytes(pd.DataFrame())
    b''
  """
  if frame.empty:
    return b""
  fast = _proc_frame_to_copy_bytes_vectorized(frame)
  if fast is not None:
    return fast
  n = len(frame)
  columns = {
    col: frame[col].tolist() if col in frame.columns else [None] * n
    for col in PROC_DATA_COPY_COLUMNS
  }
  lines: list[str] = []
  for i in range(n):
    fields = []
    for col in PROC_DATA_COPY_COLUMNS:
      value = columns[col][i]
      if col not in _PROC_PASSTHROUGH_COLUMNS:
        value = _proc_copy_scalar(value)
      fields.append(_sql_literal(value))
    lines.append("\t".join(fields))
  return ("\n".join(lines) + "\n").encode("utf-8")


def _proc_columns_to_copy_bytes(
  columns: Mapping[str, Sequence[Any]],
  *,
  cells_normalized: bool = False,
) -> bytes:
  """
  Serialize columnar proc SoA to COPY text.

  Args:
    columns (Mapping[str, Sequence[Any]]): Parallel proc columns.
    cells_normalized (bool): When True, metric cells are already COPY scalars.

  Returns:
    bytes: UTF-8 COPY body.

  Examples:
    >>> _proc_columns_to_copy_bytes({"proc": []})
    b''
  """
  n = len(columns.get("proc") or [])
  if n == 0:
    return b""
  lines: list[str] = []
  for i in range(n):
    fields = []
    for col in PROC_DATA_COPY_COLUMNS:
      seq = columns.get(col)
      value = seq[i] if seq is not None and i < len(seq) else None
      if not cells_normalized and col not in _PROC_PASSTHROUGH_COLUMNS:
        value = _proc_copy_scalar(value)
      fields.append(_sql_literal(value))
    lines.append("\t".join(fields))
  return ("\n".join(lines) + "\n").encode("utf-8")


def proc_rows_to_copy_bytes(
  rows: Sequence[Any] | pd.DataFrame | Mapping[str, Sequence[Any]],
  *,
  copy_unique: bool = False,
) -> bytes:
  """
  Serialize parsed proc rows to the same COPY body as model instances.

  A dataframe is walked by column. A sequence of namedtuples keeps the
  kwargs walk used by the ORM fallback and existing callers.

  Args:
    rows (Sequence[Any] | pd.DataFrame | Mapping[str, Sequence[Any]]):
      Namedtuple rows, a proc frame, or columnar SoA.
    copy_unique (bool): Skip pre-COPY peak-merge when the SoA is already unique.

  Returns:
    bytes: UTF-8 COPY body.

  Examples:
    >>> proc_rows_to_copy_bytes([])
    b''
  """
  if isinstance(rows, Mapping) and not isinstance(rows, pd.DataFrame):
    if not (rows.get("proc") or []):
      return b""
    if copy_unique:
      return _proc_columns_to_copy_bytes(rows, cells_normalized=True)
    frame = pd.DataFrame(dict(rows), copy=False)
    return _proc_frame_to_copy_bytes(_collapse_proc_conflict_keys(frame))
  rows = _collapse_proc_conflict_keys(rows)
  if isinstance(rows, pd.DataFrame):
    return _proc_frame_to_copy_bytes(rows)
  if not rows:
    return b""
  from hpcperfstats.dbload import sync_timedb as st

  lines: list[str] = []
  for row in rows:
    if isinstance(row, dict):
      fields = [_sql_literal(row.get(col)) for col in PROC_DATA_COPY_COLUMNS]
    else:
      kwargs = st._proc_data_row_kwargs(row)
      fields = [_sql_literal(kwargs[col]) for col in PROC_DATA_COPY_COLUMNS]
    lines.append("\t".join(fields))
  return ("\n".join(lines) + "\n").encode("utf-8")


def proc_data_objs_to_copy_bytes(objs: Sequence[Any]) -> bytes:
  """
  Serialize ``proc_data`` instances to Postgres text COPY payload.

  Args:
    objs (Sequence[Any]): Unsaved ``proc_data`` model instances.

  Returns:
    bytes: UTF-8 COPY body.

  Examples:
    >>> proc_data_objs_to_copy_bytes([])
    b''
  """
  if not objs:
    return b""
  objs = _collapse_proc_conflict_keys(objs)
  lines: list[str] = []
  for obj in objs:
    if isinstance(obj, dict):
      fields = [_sql_literal(obj.get(col)) for col in PROC_DATA_COPY_COLUMNS]
    else:
      fields = [
        _sql_literal(getattr(obj, col)) for col in PROC_DATA_COPY_COLUMNS
      ]
    lines.append("\t".join(fields))
  return ("\n".join(lines) + "\n").encode("utf-8")


def _write_proc_copy_payload(payload: bytes) -> None:
  """
  COPY ``payload`` into the proc staging table and run the upsert.

  When write telemetry is on, records ``stage_ddl_s``, ``copy_s``, and
  ``conflict_insert_s``. One PostgreSQL deadlock retries COPY and the
  upsert. A stage created in the aborted transaction is not remembered, so
  that retry runs DDL again. A stage committed by an earlier batch is
  reused. A second deadlock, or any other error, propagates.

  Args:
    payload (bytes): UTF-8 text COPY body. Empty skips the statement.

  Returns:
    None

  Raises:
    Exception: A second deadlock, or any other error from staging DDL,
      COPY, or the upsert.

  Examples:
    >>> _write_proc_copy_payload(b"")
  """
  if not payload:
    return
  from django.db import connection, transaction

  from hpcperfstats.dbload import sync_timedb as st

  col_list = ", ".join(PROC_DATA_COPY_COLUMNS)
  copy_sql = f"COPY proc_data_ingest_stage ({col_list}) FROM STDIN"
  for attempt in range(2):
    try:
      created = False
      with transaction.atomic(), connection.cursor() as cursor:
        if not _copy_stage_ready(connection, _PROC_STAGE_NAME):
          with st._held_ingest_write_phase("stage_ddl_s"):
            cursor.execute(_STAGE_DDL)
          created = True
        with (
          st._held_ingest_write_phase("copy_s"),
          cursor.copy(copy_sql) as copy,
        ):
          copy.write(payload)
        with st._held_ingest_write_phase("conflict_insert_s"):
          cursor.execute(_stage_upsert_sql())
      if created:
        _mark_copy_stage(connection, _PROC_STAGE_NAME)
      return
    except Exception as exc:
      if attempt or "deadlock detected" not in str(exc).lower():
        raise


def bulk_insert_proc_rows(
  rows: Sequence[Any] | pd.DataFrame | Mapping[str, Sequence[Any]],
  *,
  copy_unique: bool = False,
) -> None:
  """
  Upsert parsed proc rows via COPY without building ``proc_data`` models.

  Args:
    rows (Sequence[Any] | pd.DataFrame | Mapping[str, Sequence[Any]]):
      Namedtuple rows, a proc frame, or columnar SoA.
    copy_unique (bool): Skip pre-COPY peak-merge collapse when True.

  Returns:
    None

  Examples:
    >>> bulk_insert_proc_rows([])
  """
  if isinstance(rows, Mapping) and not isinstance(rows, pd.DataFrame):
    if not (rows.get("proc") or []):
      return
  elif isinstance(rows, pd.DataFrame):
    if rows.empty:
      return
  elif not rows:
    return
  from hpcperfstats.dbload import sync_timedb as st

  with st._held_ingest_write_phase("copy_encode_s"):
    payload = proc_rows_to_copy_bytes(rows, copy_unique=copy_unique)
  _write_proc_copy_payload(payload)


def bulk_insert_proc_data_update_conflicts(objs: Sequence[Any]) -> None:
  """
  Upsert ``proc_data`` via COPY staging with ON CONFLICT DO UPDATE.

  When write telemetry is on, records ``stage_ddl_s``, ``copy_s``, and
  ``conflict_insert_s``.

  Args:
    objs (Sequence[Any]): Peak-merged instances to upsert.

  Returns:
    None

  Examples:
    >>> bulk_insert_proc_data_update_conflicts([])
  """
  if not objs:
    return
  _write_proc_copy_payload(proc_data_objs_to_copy_bytes(objs))


def bulk_create_proc_data_update_conflicts(objs: Sequence[Any]) -> None:
  """
  Upsert ``proc_data`` via Django ``bulk_create(update_conflicts=True)``.

  Instances are sorted by ``(jid, host, proc)`` before insert so the ORM
  arm takes the same unique-index lock order as the COPY upsert.

  Args:
    objs (Sequence[Any]): Peak-merged instances to upsert.

  Returns:
    None

  Examples:
    >>> bulk_create_proc_data_update_conflicts([])  # doctest: +SKIP
  """
  if not objs:
    return
  from hpcperfstats.site.lib.machine.models import (
    proc_data as proc_data_model,
  )

  ordered = sorted(
    objs,
    key=lambda obj: (obj.jid or "", obj.host or "", obj.proc or ""),
  )
  proc_data_model.objects.bulk_create(
    ordered,
    update_conflicts=True,
    unique_fields=["jid", "host", "proc"],
    update_fields=list(PROC_DATA_UPDATE_FIELDS),
  )


def insert_proc_data_batch(objs: Sequence[Any]) -> None:
  """
  Upsert one ``proc_data`` batch using the selected A/B arm.

  Args:
    objs (Sequence[Any]): Peak-merged instances to upsert.

  Returns:
    None

  Examples:
    >>> insert_proc_data_batch([])  # doctest: +SKIP
  """
  if use_copy_proc_insert():
    bulk_insert_proc_data_update_conflicts(objs)
  else:
    bulk_create_proc_data_update_conflicts(objs)
