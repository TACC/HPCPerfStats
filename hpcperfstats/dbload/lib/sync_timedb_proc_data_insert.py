"""
Set-based ``proc_data`` upsert (COPY → staging → ON CONFLICT DO UPDATE).

Preserves Django ``bulk_create(..., update_conflicts=True)`` semantics after
client-side peak-merge. A/B arm ``HPCPERFSTATS_PROC_INSERT_ARM``; default
``candidate`` (COPY) after Podman A/B retain; opt out with
``HPCPERFSTATS_SYNC_PROC_DATA_COPY=0``.

Attributes:
  PROC_DATA_COPY_COLUMNS (tuple[str, ...]): Column order for COPY / INSERT.
  PROC_DATA_UPDATE_FIELDS (tuple[str, ...]): ON CONFLICT DO UPDATE columns.
  _STAGE_DDL (str): TEMP staging table DDL (ON COMMIT DROP).
"""
from __future__ import annotations

import os
from typing import Any, Sequence

from hpcperfstats.dbload.lib.sync_timedb_parsing import HOST_PROC_KEYS

PROC_DATA_COPY_COLUMNS: tuple[str, ...] = (
    "jid",
    "host",
    "proc",
    "device",
) + HOST_PROC_KEYS

PROC_DATA_UPDATE_FIELDS: tuple[str, ...] = ("device",) + HOST_PROC_KEYS

_STAGE_DDL = """
CREATE TEMP TABLE proc_data_ingest_stage (
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
) ON COMMIT DROP
"""


def _stage_upsert_sql() -> str:
  """
  Build INSERT…SELECT…ON CONFLICT DO UPDATE for ``proc_data``.

  Returns:
    str: Upsert SQL string.

  Examples:
    >>> "ON CONFLICT" in _stage_upsert_sql()
    True
  """
  cols = ", ".join(PROC_DATA_COPY_COLUMNS)
  set_clause = ", ".join(
      "%s = EXCLUDED.%s" % (field, field) for field in PROC_DATA_UPDATE_FIELDS
  )
  return (
      "INSERT INTO proc_data (%s) SELECT %s FROM proc_data_ingest_stage "
      "ON CONFLICT (jid, host, proc) DO UPDATE SET %s"
      % (cols, cols, set_clause)
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
  copy_flag = os.environ.get("HPCPERFSTATS_SYNC_PROC_DATA_COPY", "").strip().lower()
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

  Args:
    value (Any): Python value from a ``proc_data`` instance field.

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
  text = str(value)
  return (
      text.replace("\\", "\\\\")
      .replace("\t", "\\t")
      .replace("\n", "\\n")
      .replace("\r", "\\r")
  )


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
  lines: list[str] = []
  for obj in objs:
    fields = [_sql_literal(getattr(obj, col)) for col in PROC_DATA_COPY_COLUMNS]
    lines.append("\t".join(fields))
  return ("\n".join(lines) + "\n").encode("utf-8")


def bulk_insert_proc_data_update_conflicts(objs: Sequence[Any]) -> None:
  """
  Upsert ``proc_data`` via COPY staging with ON CONFLICT DO UPDATE.

  When write telemetry is on, records ``copy_s`` and ``conflict_insert_s``.

  Args:
    objs (Sequence[Any]): Peak-merged instances to upsert.

  Returns:
    None

  Examples:
    >>> bulk_insert_proc_data_update_conflicts([])  # doctest: +SKIP
  """
  if not objs:
    return
  from contextlib import nullcontext

  from django.db import connection, transaction

  payload = proc_data_objs_to_copy_bytes(objs)
  col_list = ", ".join(PROC_DATA_COPY_COLUMNS)
  copy_sql = "COPY proc_data_ingest_stage (%s) FROM STDIN" % col_list
  from hpcperfstats.dbload import sync_timedb as st

  telem = bool(getattr(st, "_ingest_write_telem_on", False))
  copy_cm = st._held_ingest_write_phase("copy_s") if telem else nullcontext()
  conflict_cm = (
      st._held_ingest_write_phase("conflict_insert_s") if telem else nullcontext()
  )
  with transaction.atomic():
    with connection.cursor() as cursor:
      cursor.execute(_STAGE_DDL)
      with copy_cm:
        with cursor.copy(copy_sql) as copy:
          copy.write(payload)
      with conflict_cm:
        cursor.execute(_stage_upsert_sql())


def bulk_create_proc_data_update_conflicts(objs: Sequence[Any]) -> None:
  """
  Upsert ``proc_data`` via Django ``bulk_create(update_conflicts=True)``.

  Args:
    objs (Sequence[Any]): Peak-merged instances to upsert.

  Returns:
    None

  Examples:
    >>> bulk_create_proc_data_update_conflicts([])  # doctest: +SKIP
  """
  if not objs:
    return
  from hpcperfstats.site.lib.machine.models import proc_data as proc_data_model

  proc_data_model.objects.bulk_create(
      list(objs),
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
