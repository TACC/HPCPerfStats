"""Unit tests for host_data COPY insert arm and conflict skip wiring."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from hpcperfstats.dbload.lib import sync_timedb_host_data_insert as hdi
from hpcperfstats.site.lib.machine.models import host_data


def _obj(**overrides):
  base = {
    "time": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    "host": "benchhost.example.edu",
    "jid": "123",
    "type": "cpu",
    "dev": "",
    "event": "user",
    "unit": "%",
    "value": 1.0,
    "delta": 0.5,
    "arc": 0.25,
  }
  base.update(overrides)
  return host_data(**base)


def test_host_soa_copy_unique_skips_collapse() -> None:
  """copy_unique columnar SoA encodes without host conflict collapse pass."""
  cols = {
    "time": [datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)] * 2,
    "host": ["h", "h"],
    "jid": ["1", "1"],
    "type": ["cpu", "cpu"],
    "dev": ["", ""],
    "event": ["user", "user"],
    "unit": ["%", "%"],
    "value": [1.0, 2.0],
    "delta": [None, None],
    "arc": [None, None],
  }
  raw = hdi.host_rows_to_copy_bytes(cols, copy_unique=True)
  collapsed = hdi.host_rows_to_copy_bytes(cols, copy_unique=False)
  assert raw.count(b"\n") == 2
  assert collapsed.count(b"\n") == 1


def test_copy_fail_falls_back_to_insert(monkeypatch):
  """Host COPY failure uses individual INSERT without re-parse."""
  import pandas as pd

  from hpcperfstats.dbload import sync_timedb as st

  individual: list[int] = []

  def _copy_boom(*_a, **_k):
    raise RuntimeError("copy failed")

  monkeypatch.setenv("HPCPERFSTATS_HOST_INSERT_ARM", "candidate")
  monkeypatch.setenv("HPCPERFSTATS_PROC_INSERT_ARM", "baseline")
  monkeypatch.setattr(st, "bulk_insert_host_rows", _copy_boom)
  monkeypatch.setattr(st, "bulk_insert_proc_rows", lambda *_a, **_k: None)
  monkeypatch.setattr(
    st,
    "_insert_host_data_individually",
    lambda df: individual.append(len(df)) or True,
  )
  monkeypatch.setattr(st, "_insert_proc_data_individually", lambda _df: None)
  monkeypatch.setattr(st, "_invalidate_jid_caches", lambda *_a, **_k: None)
  monkeypatch.setattr(
    st,
    "_raise_if_ingest_per_file_deadline_exceeded",
    lambda *_a, **_k: None,
  )
  monkeypatch.setattr(st, "_ingest_write_frame_slice_rows", lambda: 100)
  monkeypatch.setattr(st, "is_database_unavailable_error", lambda _e: False)
  monkeypatch.setattr(
    st,
    "_reset_ingest_db_connection_after_write_error",
    lambda: None,
  )
  stats = pd.DataFrame(
    [
      {
        "time": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        "host": "h",
        "jid": "1",
        "type": "cpu",
        "dev": "",
        "event": "user",
        "unit": "%",
        "value": 1.0,
        "delta": 0.0,
        "arc": None,
      }
    ]
  )
  st._write_stats_payload_to_db("/f", stats, pd.DataFrame(), copy_unique=True)
  assert individual == [1]


def test_host_insert_arm_default_candidate_after_retain(monkeypatch):
  monkeypatch.delenv("HPCPERFSTATS_HOST_INSERT_ARM", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_SYNC_HOST_DATA_COPY", raising=False)
  assert hdi.host_insert_arm() == "candidate"
  assert hdi.use_copy_host_insert() is True


def test_host_insert_arm_opt_out_baseline(monkeypatch):
  monkeypatch.delenv("HPCPERFSTATS_HOST_INSERT_ARM", raising=False)
  monkeypatch.setenv("HPCPERFSTATS_SYNC_HOST_DATA_COPY", "0")
  assert hdi.host_insert_arm() == "baseline"
  assert hdi.use_copy_host_insert() is False


def test_host_insert_arm_candidate(monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_HOST_INSERT_ARM", "candidate")
  assert hdi.host_insert_arm() == "candidate"
  assert hdi.use_copy_host_insert() is True


def test_host_data_objs_to_copy_bytes_escapes_null():
  payload = hdi.host_data_objs_to_copy_bytes([_obj(value=None)])
  assert b"\\N" in payload
  assert payload.endswith(b"\n")


def test_host_stage_insert_unchanged():
  """Host hypertable insert stays DO NOTHING with no peak rewrite."""
  assert (
    "ON CONFLICT (time, host, type, event, dev) DO NOTHING" in hdi._STAGE_INSERT
  )
  assert "GREATEST" not in hdi._STAGE_INSERT
  assert "WHERE" not in hdi._STAGE_INSERT


def test_host_copy_batch_collapses_conflict_keys():
  """Duplicate conflict keys keep the last row before COPY."""
  import pandas as pd

  t = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
  frame = pd.DataFrame(
    [
      {
        "time": t,
        "host": "h",
        "jid": "1",
        "type": "cpu",
        "dev": "",
        "event": "user",
        "unit": "%",
        "value": 1.0,
        "delta": 0.0,
        "arc": 0.0,
      },
      {
        "time": t,
        "host": "h",
        "jid": "1",
        "type": "cpu",
        "dev": "",
        "event": "user",
        "unit": "%",
        "value": 9.0,
        "delta": 0.0,
        "arc": 0.0,
      },
    ]
  )
  payload = hdi.host_rows_to_copy_bytes(frame)
  lines = [ln for ln in payload.split(b"\n") if ln]
  assert len(lines) == 1
  assert b"9.0" in lines[0] or b"9" in lines[0]
  assert (
    "ON CONFLICT (time, host, type, event, dev) DO NOTHING" in hdi._STAGE_INSERT
  )


def test_host_unique_key_batch_non_inferior():
  """Already-unique host frames stay one COPY line per row."""
  import pandas as pd

  t = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
  frame = pd.DataFrame(
    [
      {
        "time": t,
        "host": "h",
        "jid": "1",
        "type": "cpu",
        "dev": "",
        "event": "user",
        "unit": "%",
        "value": 1.0,
        "delta": 0.0,
        "arc": 0.0,
      },
      {
        "time": t,
        "host": "h",
        "jid": "1",
        "type": "cpu",
        "dev": "",
        "event": "system",
        "unit": "%",
        "value": 2.0,
        "delta": 0.0,
        "arc": 0.0,
      },
    ]
  )
  payload = hdi.host_rows_to_copy_bytes(frame)
  lines = [ln for ln in payload.split(b"\n") if ln]
  assert len(lines) == 2


def test_host_rows_copy_bytes_match_model_tokens():
  """Namedtuple COPY tokens match host_data_instance_from_stats_row."""
  from types import SimpleNamespace

  from hpcperfstats.dbload.lib.io_helpers import (
    host_data_instance_from_stats_row,
  )

  row = SimpleNamespace(
    time=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    host="h",
    jid="-",
    type="cpu",
    dev=float("nan"),
    event="user",
    unit="%",
    value=float("nan"),
    delta=None,
    arc=1.5,
  )
  model = host_data_instance_from_stats_row(row)
  assert hdi.host_rows_to_copy_bytes([row]) == hdi.host_data_objs_to_copy_bytes(
    [model]
  )
  payload = hdi.host_rows_to_copy_bytes([row])
  assert b"\\N" in payload
  fields = payload.strip().split(b"\t")
  assert fields[4] == b""  # blank device, not NULL


def test_insert_host_data_batch_routes_baseline_to_bulk_create(monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_HOST_INSERT_ARM", "baseline")
  seen = []

  def _bulk(objs):
    seen.append(len(objs))

  monkeypatch.setattr(hdi, "bulk_create_host_data_ignore_conflicts", _bulk)
  monkeypatch.setattr(
    hdi,
    "bulk_insert_host_data_ignore_conflicts",
    lambda _o: (_ for _ in ()).throw(AssertionError("copy used")),
  )
  hdi.insert_host_data_batch([_obj(), _obj(event="system")])
  assert seen == [2]


def test_insert_host_data_batch_routes_candidate_to_copy(monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_HOST_INSERT_ARM", "candidate")
  seen = []

  def _copy(objs):
    seen.append(len(objs))

  monkeypatch.setattr(hdi, "bulk_insert_host_data_ignore_conflicts", _copy)
  monkeypatch.setattr(
    hdi,
    "bulk_create_host_data_ignore_conflicts",
    lambda _o: (_ for _ in ()).throw(AssertionError("bulk used")),
  )
  hdi.insert_host_data_batch([_obj()])
  assert seen == [1]


def test_bulk_insert_host_data_ignore_conflicts_uses_stage_and_conflict(
  monkeypatch,
):
  """COPY path must stage then INSERT ... ON CONFLICT DO NOTHING."""
  executes: list[str] = []
  copy_writes: list[bytes] = []

  class _CopyCtx:
    def __enter__(self):
      return self

    def __exit__(self, *a):
      return False

    def write(self, data: bytes):
      copy_writes.append(data)

  class _Cursor:
    def __enter__(self):
      return self

    def __exit__(self, *a):
      return False

    def execute(self, sql, params=None):
      executes.append(str(sql))

    def copy(self, sql):
      executes.append(str(sql))
      return _CopyCtx()

  class _Conn:
    def cursor(self):
      return _Cursor()

  class _Atomic:
    def __enter__(self):
      return self

    def __exit__(self, *a):
      return False

  import django.db as django_db

  monkeypatch.setattr(django_db, "connection", _Conn())
  monkeypatch.setattr(
    django_db,
    "transaction",
    type("T", (), {"atomic": staticmethod(lambda: _Atomic())})(),
  )
  hdi.bulk_insert_host_data_ignore_conflicts([_obj(), _obj(event="idle")])
  joined = "\n".join(executes)
  assert "CREATE TEMP TABLE IF NOT EXISTS host_data_ingest_stage" in joined
  assert "COPY host_data_ingest_stage" in joined
  assert "ON CONFLICT (time, host, type, event, dev) DO NOTHING" in joined
  assert len(copy_writes) == 1
  assert copy_writes[0].count(b"\n") == 2


def test_bulk_insert_host_copy_s_and_conflict_insert_under_write_telem(
  monkeypatch,
):
  """COPY path must accumulate copy_s and conflict_insert_s when write telem on."""
  from hpcperfstats.dbload import sync_timedb as st

  class _CopyCtx:
    def __enter__(self):
      return self

    def __exit__(self, *a):
      return False

    def write(self, data: bytes):
      del data

  class _Cursor:
    def __enter__(self):
      return self

    def __exit__(self, *a):
      return False

    def execute(self, sql, params=None):
      del sql, params

    def copy(self, sql):
      del sql
      return _CopyCtx()

  class _Conn:
    def cursor(self):
      return _Cursor()

  class _Atomic:
    def __enter__(self):
      return self

    def __exit__(self, *a):
      return False

  import django.db as django_db

  monkeypatch.setattr(django_db, "connection", _Conn())
  monkeypatch.setattr(
    django_db,
    "transaction",
    type("T", (), {"atomic": staticmethod(lambda: _Atomic())})(),
  )
  st._reset_ingest_write_timing(enabled=True)
  try:
    with st._held_ingest_write_timing():
      hdi.bulk_insert_host_data_ignore_conflicts([_obj()])
    snap = st._snapshot_ingest_write_timing()
    assert snap["copy_s"] > 0.0
    assert snap["conflict_insert_s"] > 0.0
  finally:
    st._reset_ingest_write_timing(enabled=False)


def test_write_stats_payload_uses_insert_host_data_batch(monkeypatch):
  """sync_timedb host write path must call insert_host_data_batch."""
  import pandas as pd

  from hpcperfstats.dbload import sync_timedb as st

  called = []

  monkeypatch.setattr(
    st,
    "insert_host_data_batch",
    lambda objs: called.append(len(objs)),
  )
  monkeypatch.setattr(st, "insert_proc_data_batch", lambda objs: None)
  monkeypatch.setenv("HPCPERFSTATS_HOST_INSERT_ARM", "baseline")
  monkeypatch.setenv("HPCPERFSTATS_PROC_INSERT_ARM", "baseline")
  monkeypatch.setattr(st, "_peak_merge_proc_objs_with_existing", lambda o: o)
  monkeypatch.setattr(
    st,
    "_proc_data_row_kwargs",
    lambda _r: {"jid": "1", "host": "h", "proc": "p"},
  )
  monkeypatch.setattr(st, "_invalidate_jid_caches", lambda *_a, **_k: None)
  monkeypatch.setattr(
    st,
    "_raise_if_ingest_per_file_deadline_exceeded",
    lambda *_a, **_k: None,
  )
  monkeypatch.setattr(st, "_ingest_write_frame_slice_rows", lambda: 100)
  monkeypatch.setattr(
    st.proc_data.objects, "bulk_create", lambda *_a, **_k: None
  )
  monkeypatch.setattr(st, "_ingest_write_telem_on", False)

  stats = pd.DataFrame(
    [
      {
        "time": datetime(2026, 1, 1, tzinfo=UTC),
        "host": "h",
        "jid": "1",
        "type": "cpu",
        "dev": "",
        "event": "user",
        "unit": "%",
        "value": 1.0,
        "delta": 0.0,
        "arc": 0.0,
      }
    ]
  )
  st._write_stats_payload_to_db("/archive/h/1", stats, pd.DataFrame())
  assert called == [1]


def test_host_stage_ddl_once_per_connection(monkeypatch):
  """Two host batches on one socket run CREATE TEMP only once."""
  executes: list[str] = []

  class _CopyCtx:
    def __enter__(self):
      return self

    def __exit__(self, *a):
      return False

    def write(self, data: bytes):
      del data

  class _Cursor:
    def __enter__(self):
      return self

    def __exit__(self, *a):
      return False

    def execute(self, sql, params=None):
      del params
      executes.append(str(sql))

    def copy(self, sql):
      executes.append(str(sql))
      return _CopyCtx()

  class _Conn:
    connection = object()

    def cursor(self):
      return _Cursor()

  class _Atomic:
    def __enter__(self):
      return self

    def __exit__(self, *a):
      return False

  import django.db as django_db

  monkeypatch.setattr(django_db, "connection", _Conn())
  monkeypatch.setattr(
    django_db,
    "transaction",
    type("T", (), {"atomic": staticmethod(lambda: _Atomic())})(),
  )
  hdi.bulk_insert_host_data_ignore_conflicts([_obj()])
  hdi.bulk_insert_host_data_ignore_conflicts([_obj(event="idle")])
  creates = [sql for sql in executes if "CREATE TEMP" in sql]
  assert len(creates) == 1
  assert "IF NOT EXISTS" in hdi._STAGE_DDL
  assert "ON COMMIT DELETE ROWS" in hdi._STAGE_DDL
  assert "ON COMMIT DROP" not in hdi._STAGE_DDL
  assert sum("COPY host_data_ingest_stage" in sql for sql in executes) == 2


def test_host_copy_bytes_match_tuple_builder():
  """Column-slice COPY bytes match the namedtuple builder, including nulls."""
  import pandas as pd

  frame = pd.DataFrame(
    [
      {
        "time": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        "host": "h",
        "jid": "-",
        "type": "cpu",
        "dev": float("nan"),
        "event": "user",
        "unit": "%",
        "value": float("nan"),
        "delta": None,
        "arc": 1.5,
      },
      {
        "time": datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC),
        "host": "h2",
        "jid": "9",
        "type": "cpu",
        "dev": "",
        "event": "sys",
        "unit": "%",
        "value": 1.0,
        "delta": 0.0,
        "arc": None,
      },
    ]
  )
  tuples = list(frame.itertuples(index=False))
  assert hdi.host_rows_to_copy_bytes(frame) == hdi.host_rows_to_copy_bytes(
    tuples
  )
  assert hdi.host_rows_to_copy_bytes(frame.iloc[0:0]) == b""


@pytest.mark.skipif(
  os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK") != "1",
  reason="TEMP stage lifecycle needs the compose PostgreSQL workflow",
)
@pytest.mark.django_db(transaction=True)
def test_host_stage_empty_after_commit_and_rollback():
  """A committed host stage keeps the table and drops its rows."""
  from django.db import connection, transaction

  if connection.vendor != "postgresql":
    pytest.skip("ON COMMIT DELETE ROWS is PostgreSQL")
  with transaction.atomic():
    with connection.cursor() as cursor:
      cursor.execute(hdi._STAGE_DDL)
      cursor.execute(
        "INSERT INTO host_data_ingest_stage (host) VALUES ('keep')"
      )
      cursor.execute("SELECT count(*) FROM host_data_ingest_stage")
      assert cursor.fetchone()[0] == 1
  with connection.cursor() as cursor:
    cursor.execute("SELECT count(*) FROM host_data_ingest_stage")
    assert cursor.fetchone()[0] == 0
  try:
    with transaction.atomic():
      with connection.cursor() as cursor:
        cursor.execute(
          "INSERT INTO host_data_ingest_stage (host) VALUES ('gone')"
        )
        raise RuntimeError("rollback")
  except RuntimeError:
    pass
  with connection.cursor() as cursor:
    cursor.execute("SELECT count(*) FROM host_data_ingest_stage")
    assert cursor.fetchone()[0] == 0
