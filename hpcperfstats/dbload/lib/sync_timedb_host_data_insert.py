"""
Set-based ``host_data`` insert (COPY → staging → ON CONFLICT DO NOTHING).

A/B arm ``HPCPERFSTATS_HOST_INSERT_ARM=baseline|candidate`` selects Django
``bulk_create(ignore_conflicts=True)`` vs this path. Default is ``candidate``
(COPY) after Podman large-data A/B retain; opt out with
``HPCPERFSTATS_SYNC_HOST_DATA_COPY=0``.

Attributes:
  HOST_DATA_COPY_COLUMNS (tuple[str, ...]): Column order for COPY / INSERT.
  _STAGE_DDL (str): TEMP staging table DDL (ON COMMIT DROP).
  _STAGE_INSERT (str): INSERT…SELECT…ON CONFLICT DO NOTHING SQL.
"""
from __future__ import annotations

import os
from typing import Any, Sequence

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
CREATE TEMP TABLE host_data_ingest_stage (
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
) ON COMMIT DROP
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
  copy_flag = os.environ.get("HPCPERFSTATS_SYNC_HOST_DATA_COPY", "").strip().lower()
  if copy_flag in ("0", "false", "no", "baseline"):
    return "baseline"
  # Default ON after Podman A/B retain (host_data_insert_ab_fde1031c…).
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
  # datetime / date → ISO for timestamptz COPY
  iso = getattr(value, "isoformat", None)
  if callable(iso):
    text = iso()
  else:
    text = str(value)
  return (
      text.replace("\\", "\\\\")
      .replace("\t", "\\t")
      .replace("\n", "\\n")
      .replace("\r", "\\r")
  )


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
  lines: list[str] = []
  for obj in objs:
    fields = [_sql_literal(getattr(obj, col)) for col in HOST_DATA_COPY_COLUMNS]
    lines.append("\t".join(fields))
  return ("\n".join(lines) + "\n").encode("utf-8")


def bulk_insert_host_data_ignore_conflicts(objs: Sequence[Any]) -> None:
  """
  Insert ``host_data`` rows via COPY staging with conflict skip.

  Matches Django ``bulk_create(..., ignore_conflicts=True)`` semantics on
  unique ``(time, host, type, event, dev)``.

  Args:
    objs (Sequence[Any]): Unsaved ``host_data`` model instances to insert.

  Returns:
    None

  Examples:
    >>> bulk_insert_host_data_ignore_conflicts([])  # doctest: +SKIP
  """
  if not objs:
    return
  from django.db import connection, transaction

  payload = host_data_objs_to_copy_bytes(objs)
  col_list = ", ".join(HOST_DATA_COPY_COLUMNS)
  copy_sql = (
      f"COPY host_data_ingest_stage ({col_list}) FROM STDIN"
  )
  # Django defaults to autocommit; keep TEMP visible for COPY + INSERT.
  with transaction.atomic():
    with connection.cursor() as cursor:
      cursor.execute(_STAGE_DDL)
      with cursor.copy(copy_sql) as copy:
        copy.write(payload)
      cursor.execute(_STAGE_INSERT)


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
  from hpcperfstats.site.lib.machine.models import host_data as host_data_model

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
