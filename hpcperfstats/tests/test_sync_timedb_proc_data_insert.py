"""Unit tests for proc_data COPY upsert arm routing."""

from __future__ import annotations

import os

import pytest

from hpcperfstats.dbload.lib import sync_timedb_proc_data_insert as pdi
from hpcperfstats.site.lib.machine.models import proc_data


def _obj(**overrides):
  base = {
    "jid": "1",
    "host": "h",
    "proc": "bash",
    "device": "bash/1",
    "uid": 1000,
    "vm_peak": 1,
    "vm_size": 1,
    "vm_lck": 0,
    "vm_hwm": 1,
    "vm_rss": 1,
    "vm_data": 1,
    "vm_stk": 1,
    "vm_exe": 1,
    "vm_lib": 1,
    "vm_pte": 1,
    "vm_swap": 0,
    "threads": 1,
  }
  base.update(overrides)
  return proc_data(**base)


def test_proc_soa_peak_merge_copy() -> None:
  """Columnar peak-merge matches DataFrame collapse + COPY bytes."""
  import pandas as pd

  cols = {
    "jid": ["1", "1"],
    "host": ["h", "h"],
    "proc": ["bash", "bash"],
    "device": ["d", "d"],
    "uid": [1000, 1000],
    "vm_peak": [10, 99],
    "vm_size": [1, 1],
    "vm_lck": [0, 0],
    "vm_hwm": [1, 1],
    "vm_rss": [1, 1],
    "vm_data": [1, 1],
    "vm_stk": [9, 3],
    "vm_exe": [1, 1],
    "vm_lib": [1, 1],
    "vm_pte": [1, 1],
    "vm_swap": [0, 0],
    "threads": [1, 8],
  }
  frame = pd.DataFrame(cols)
  via_frame = pdi.proc_rows_to_copy_bytes(frame, copy_unique=False)
  via_soa = pdi.proc_rows_to_copy_bytes(
    pdi._collapse_proc_columnar_conflict_keys(cols),
    copy_unique=True,
  )
  assert via_frame == via_soa


def test_proc_soa_copy_unique_skips_collapse() -> None:
  """copy_unique columnar SoA encodes without peak-merge collapse pass."""
  cols = {
    "jid": ["1", "1"],
    "host": ["h", "h"],
    "proc": ["bash", "bash"],
    "device": ["d", "d"],
    "uid": [1000, 1000],
    "vm_peak": [10, 99],
    "vm_size": [1, 1],
    "vm_lck": [0, 0],
    "vm_hwm": [1, 1],
    "vm_rss": [1, 1],
    "vm_data": [1, 1],
    "vm_stk": [1, 1],
    "vm_exe": [1, 1],
    "vm_lib": [1, 1],
    "vm_pte": [1, 1],
    "vm_swap": [0, 0],
    "threads": [1, 1],
  }
  raw = pdi.proc_rows_to_copy_bytes(cols, copy_unique=True)
  collapsed = pdi.proc_rows_to_copy_bytes(cols, copy_unique=False)
  assert raw.count(b"\n") == 2
  assert collapsed.count(b"\n") == 1


def test_proc_insert_arm_default_candidate_after_retain(monkeypatch):
  monkeypatch.delenv("HPCPERFSTATS_PROC_INSERT_ARM", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_SYNC_PROC_DATA_COPY", raising=False)
  assert pdi.proc_insert_arm() == "candidate"


def test_proc_insert_routes_candidate(monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_PROC_INSERT_ARM", "candidate")
  seen = []
  monkeypatch.setattr(
    pdi,
    "bulk_insert_proc_data_update_conflicts",
    lambda o: seen.append(len(o)),
  )
  monkeypatch.setattr(
    pdi,
    "bulk_create_proc_data_update_conflicts",
    lambda _o: (_ for _ in ()).throw(AssertionError("bulk")),
  )
  pdi.insert_proc_data_batch([_obj()])
  assert seen == [1]


def test_proc_copy_sql_has_conflict_update():
  sql = pdi._stage_upsert_sql()
  assert "ON CONFLICT (jid, host, proc) DO UPDATE SET" in sql
  assert "vm_rss = EXCLUDED.vm_rss" in sql
  assert "GREATEST(proc_data.vm_peak" in sql


def test_proc_copy_batch_peak_merges_duplicate_keys():
  """Duplicate (jid, host, proc) keep peak oracle in one COPY row."""
  import pandas as pd

  frame = pd.DataFrame(
    [
      {
        "jid": "1",
        "host": "h",
        "proc": "bash",
        "device": "bash/1",
        "uid": 1000,
        "vm_peak": 100,
        "vm_size": 50,
        "vm_lck": 0,
        "vm_hwm": 40,
        "vm_rss": 40,
        "vm_data": 10,
        "vm_stk": 9,
        "vm_exe": 1,
        "vm_lib": 1,
        "vm_pte": 1,
        "vm_swap": 0,
        "threads": 1,
      },
      {
        "jid": "1",
        "host": "h",
        "proc": "bash",
        "device": "bash/1",
        "uid": 1000,
        "vm_peak": 80,
        "vm_size": 60,
        "vm_lck": 0,
        "vm_hwm": 50,
        "vm_rss": 50,
        "vm_data": 20,
        "vm_stk": 3,
        "vm_exe": 1,
        "vm_lib": 1,
        "vm_pte": 1,
        "vm_swap": 0,
        "threads": 8,
      },
    ]
  )
  payload = pdi.proc_rows_to_copy_bytes(frame)
  lines = [ln for ln in payload.split(b"\n") if ln]
  assert len(lines) == 1
  # Peak vm_stk stays 9; last-write threads is 8.
  fields = lines[0].split(b"\t")
  # Columns: jid host proc device uid vm_peak ... vm_stk ... threads
  assert fields[0] == b"1"
  assert b"9" in fields  # vm_stk peak
  assert fields[-1] == b"8"  # threads last-write
  sql = pdi._stage_upsert_sql()
  assert "GREATEST(proc_data.vm_peak" in sql
  assert " WHERE " in sql


def test_proc_unique_key_batch_non_inferior():
  """Already-unique proc frames stay one COPY line per row."""
  import pandas as pd

  frame = pd.DataFrame(
    [
      {
        "jid": "1",
        "host": "h",
        "proc": "bash",
        "device": "bash/1",
        "uid": 1,
        "vm_peak": 1,
        "vm_size": 1,
        "vm_lck": 0,
        "vm_hwm": 1,
        "vm_rss": 1,
        "vm_data": 1,
        "vm_stk": 1,
        "vm_exe": 1,
        "vm_lib": 1,
        "vm_pte": 1,
        "vm_swap": 0,
        "threads": 1,
      },
      {
        "jid": "1",
        "host": "h",
        "proc": "zsh",
        "device": "zsh/2",
        "uid": 1,
        "vm_peak": 2,
        "vm_size": 2,
        "vm_lck": 0,
        "vm_hwm": 2,
        "vm_rss": 2,
        "vm_data": 2,
        "vm_stk": 2,
        "vm_exe": 2,
        "vm_lib": 2,
        "vm_pte": 2,
        "vm_swap": 0,
        "threads": 2,
      },
    ]
  )
  payload = pdi.proc_rows_to_copy_bytes(frame)
  lines = [ln for ln in payload.split(b"\n") if ln]
  assert len(lines) == 2


def test_proc_upsert_orders_conflict_keys():
  """Stage rows lock the unique key in (jid, host, proc) order."""
  sql = pdi._stage_upsert_sql()
  order_at = sql.index("ORDER BY jid, host, proc")
  conflict_at = sql.index("ON CONFLICT (jid, host, proc)")
  assert order_at < conflict_at
  assert "GREATEST(proc_data.vm_peak, EXCLUDED.vm_peak)" in sql
  assert " WHERE " in sql


def test_orm_proc_bulk_create_sorts_conflict_key(monkeypatch):
  """ORM bulk_create receives (jid, host, proc) order."""
  import hpcperfstats.site.lib.machine.models as models

  seen: list[list[tuple[str, str, str]]] = []

  class _Manager:
    def bulk_create(self, objs, **kwargs):
      del kwargs
      seen.append([(obj.jid, obj.host, obj.proc) for obj in objs])

  class _Model:
    objects = _Manager()

  monkeypatch.setattr(models, "proc_data", _Model)
  pdi.bulk_create_proc_data_update_conflicts(
    [
      _obj(jid="2", host="b", proc="z"),
      _obj(jid="1", host="a", proc="m"),
      _obj(jid="1", host="b", proc="a"),
    ]
  )
  assert seen == [[("1", "a", "m"), ("1", "b", "a"), ("2", "b", "z")]]


def _patch_proc_copy_connection(monkeypatch, execute, state):
  """Install a Django connection double whose execute is ``execute``."""
  import django.db as django_db

  class _CopyCtx:
    def __enter__(self):
      state["copy"] += 1
      return self

    def __exit__(self, *args):
      return False

    def write(self, data: bytes):
      del data

  class _Cursor:
    def __enter__(self):
      return self

    def __exit__(self, *args):
      return False

    def execute(self, sql, params=None):
      execute(sql, params)

    def copy(self, sql):
      del sql
      return _CopyCtx()

  class _Conn:
    def cursor(self):
      return _Cursor()

  class _Atomic:
    def __enter__(self):
      return self

    def __exit__(self, *args):
      return False

  monkeypatch.setattr(django_db, "connection", _Conn())
  monkeypatch.setattr(
    django_db,
    "transaction",
    type("T", (), {"atomic": staticmethod(lambda: _Atomic())})(),
  )


def test_copy_upsert_retries_one_deadlock(monkeypatch):
  """One deadlock reruns DDL and COPY; the second upsert succeeds."""
  from django.db.utils import OperationalError

  state = {"ddl": 0, "copy": 0, "upsert": 0}

  def execute(sql, params=None):
    del params
    text = str(sql)
    if "CREATE TEMP" in text:
      state["ddl"] += 1
      return
    if "INSERT INTO proc_data" in text:
      state["upsert"] += 1
      if state["upsert"] == 1:
        raise OperationalError("deadlock detected")
      return
    raise AssertionError(text)

  _patch_proc_copy_connection(monkeypatch, execute, state)
  pdi.bulk_insert_proc_data_update_conflicts([_obj()])
  assert state == {"ddl": 2, "copy": 2, "upsert": 2}


def test_copy_upsert_raises_second_deadlock(monkeypatch):
  """A second deadlock propagates after DDL and COPY run twice."""
  import pytest
  from django.db.utils import OperationalError

  state = {"ddl": 0, "copy": 0, "upsert": 0}

  def execute(sql, params=None):
    del params
    text = str(sql)
    if "CREATE TEMP" in text:
      state["ddl"] += 1
      return
    if "INSERT INTO proc_data" in text:
      state["upsert"] += 1
      raise OperationalError("deadlock detected")
    raise AssertionError(text)

  _patch_proc_copy_connection(monkeypatch, execute, state)
  with pytest.raises(OperationalError, match="deadlock detected"):
    pdi.bulk_insert_proc_data_update_conflicts([_obj()])
  assert state["upsert"] == 2


def test_copy_upsert_does_not_retry_statement_timeout(monkeypatch):
  """A statement timeout is not a deadlock and is not retried."""
  import pytest
  from django.db.utils import OperationalError

  state = {"ddl": 0, "copy": 0, "upsert": 0}

  def execute(sql, params=None):
    del params
    text = str(sql)
    if "CREATE TEMP" in text:
      state["ddl"] += 1
      return
    if "INSERT INTO proc_data" in text:
      state["upsert"] += 1
      raise OperationalError("canceling statement due to statement timeout")
    raise AssertionError(text)

  _patch_proc_copy_connection(monkeypatch, execute, state)
  with pytest.raises(OperationalError, match="statement timeout"):
    pdi.bulk_insert_proc_data_update_conflicts([_obj()])
  assert state == {"ddl": 1, "copy": 1, "upsert": 1}


def test_proc_upsert_uses_greatest_and_skips_noop_rewrite():
  """Peak SET is GREATEST. WHERE skips a conflict that changes nothing."""
  sql = pdi._stage_upsert_sql()
  assert "CASE WHEN" not in sql
  for key in ("vm_peak", "vm_hwm", "vm_stk", "vm_exe", "vm_lib"):
    assert f"{key} = GREATEST(proc_data.{key}, EXCLUDED.{key})" in sql
    assert (
      f"(EXCLUDED.{key} IS NOT NULL AND "
      f"(proc_data.{key} IS NULL OR EXCLUDED.{key} > proc_data.{key}))"
    ) in sql
  assert "proc_data.device IS DISTINCT FROM EXCLUDED.device" in sql
  assert "proc_data.uid IS DISTINCT FROM EXCLUDED.uid" in sql
  assert "proc_data.vm_rss IS DISTINCT FROM EXCLUDED.vm_rss" in sql
  assert " WHERE " in sql


def test_proc_rows_copy_bytes_match_model_tokens():
  """Namedtuple COPY tokens match the model serializer, including nulls."""
  from types import SimpleNamespace

  from hpcperfstats.dbload import sync_timedb as st

  row = SimpleNamespace(
    jid=None,
    host="h",
    proc="bash",
    device="",
    uid=float("nan"),
    vm_peak=10.0,
    vm_size=0.0,
    vm_lck=float("nan"),
    vm_hwm=4.0,
    vm_rss=1.0,
    vm_data=float("nan"),
    vm_stk=2.0,
    vm_exe=3.0,
    vm_lib=5.0,
    vm_pte=float("nan"),
    vm_swap=236948.0,
    threads=1.0,
  )
  model = proc_data(**st._proc_data_row_kwargs(row))
  assert pdi.proc_rows_to_copy_bytes([row]) == pdi.proc_data_objs_to_copy_bytes(
    [model]
  )
  assert b"236948.0" not in pdi.proc_rows_to_copy_bytes([row])
  assert b"\\N" in pdi.proc_rows_to_copy_bytes([row])


def test_proc_copy_bytes_coerces_float_bigint_fields():
  """
  Pandas float64 on sparse proc ints must not become COPY text like ``0.0``.

  Production signature (2026-09-28): Postgres rejected bigint COPY tokens
  ``\"0.0\"`` / ``\"236948.0\"`` for ``uid`` / ``vm_swap`` / ``vm_size``.
  """
  payload = pdi.proc_data_objs_to_copy_bytes(
    [
      _obj(
        uid=0.0,
        vm_swap=0.0,
        vm_size=236948.0,
        threads=1.0,
      )
    ]
  ).decode("utf-8")
  fields = payload.strip().split("\t")
  # PROC_DATA_COPY_COLUMNS: jid host proc device uid ... vm_size ... vm_swap threads
  assert "0.0" not in fields
  assert "236948.0" not in fields
  assert fields[4] == "0"  # uid
  assert fields[6] == "236948"  # vm_size
  assert fields[15] == "0"  # vm_swap
  assert fields[16] == "1"  # threads
  assert pdi._sql_literal(float("nan")) == "\\N"


def test_proc_field_or_none_coerces_float_keeps_device_str():
  """Materialize ints from float64; do not int()-coerce string device."""
  from types import SimpleNamespace

  from hpcperfstats.dbload import sync_timedb as st

  row = SimpleNamespace(
    uid=0.0, vm_size=236948.0, device="bash/1", bog=float("nan")
  )
  assert st._proc_field_or_none(row, "uid") == 0
  assert st._proc_field_or_none(row, "vm_size") == 236948
  assert st._proc_field_or_none(row, "device") == "bash/1"
  assert st._proc_field_or_none(row, "bog") is None


def test_bulk_insert_proc_copy_s_and_conflict_insert_under_write_telem(
  monkeypatch,
):
  """COPY upsert must accumulate copy_s and conflict_insert_s when write telem on."""
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
      pdi.bulk_insert_proc_data_update_conflicts([_obj()])
    snap = st._snapshot_ingest_write_timing()
    assert snap["copy_s"] > 0.0
    assert snap["conflict_insert_s"] > 0.0
  finally:
    st._reset_ingest_write_timing(enabled=False)


def test_copy_arm_skips_models(monkeypatch):
  """COPY write builds no proc_data or host_data and leaves orm_rows at zero."""
  import pandas as pd

  from hpcperfstats.dbload import sync_timedb as st

  monkeypatch.delenv("HPCPERFSTATS_PROC_INSERT_ARM", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_HOST_INSERT_ARM", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_SYNC_PROC_DATA_COPY", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_SYNC_HOST_DATA_COPY", raising=False)

  def _proc_model(**_kwargs):
    raise AssertionError("proc model")

  def _host_model(_row):
    raise AssertionError("host model")

  monkeypatch.setattr(st, "proc_data", _proc_model)
  monkeypatch.setattr(st, "host_data_instance_from_stats_row", _host_model)
  monkeypatch.setattr(st, "bulk_insert_proc_rows", lambda rows: None)
  monkeypatch.setattr(st, "bulk_insert_host_rows", lambda rows: None)
  monkeypatch.setattr(st, "_invalidate_jid_caches", lambda *_a, **_k: None)
  monkeypatch.setattr(
    st,
    "_raise_if_ingest_per_file_deadline_exceeded",
    lambda *_a, **_k: None,
  )
  monkeypatch.setattr(st, "_ingest_write_frame_slice_rows", lambda: 100)
  st._reset_ingest_write_timing(enabled=True)
  monkeypatch.setattr(st, "_ingest_write_telem_on", True)
  proc = pd.DataFrame([{"jid": "1", "host": "h", "proc": "bash"}])
  stats = pd.DataFrame(
    [
      {
        "time": "2026-01-01T00:00:00Z",
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
  try:
    st._write_stats_payload_to_db("/archive/h/1", stats, proc)
    snap = st._snapshot_ingest_write_timing()
    assert snap["orm_rows"] == 0.0
    assert snap["orm_materialize_s"] == 0.0
  finally:
    st._reset_ingest_write_timing(enabled=False)


def test_baseline_arm_counts_orm_rows(monkeypatch):
  """ORM opt-out still builds one model per proc row and per host row."""
  from datetime import UTC, datetime

  import pandas as pd

  from hpcperfstats.dbload import sync_timedb as st

  monkeypatch.setenv("HPCPERFSTATS_PROC_INSERT_ARM", "baseline")
  monkeypatch.setenv("HPCPERFSTATS_HOST_INSERT_ARM", "baseline")
  monkeypatch.setattr(st, "insert_proc_data_batch", lambda objs: None)
  monkeypatch.setattr(st, "insert_host_data_batch", lambda objs: None)
  monkeypatch.setattr(st, "_time_ingest_db_reconnect", lambda: None)
  monkeypatch.setattr(st, "_invalidate_jid_caches", lambda *_a, **_k: None)
  monkeypatch.setattr(
    st,
    "_raise_if_ingest_per_file_deadline_exceeded",
    lambda *_a, **_k: None,
  )
  monkeypatch.setattr(st, "_ingest_write_frame_slice_rows", lambda: 100)
  st._reset_ingest_write_timing(enabled=True)
  monkeypatch.setattr(st, "_ingest_write_telem_on", True)
  proc = pd.DataFrame([{"jid": "1", "host": "h", "proc": "bash"}])
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
  try:
    st._write_stats_payload_to_db("/archive/h/1", stats, proc)
    snap = st._snapshot_ingest_write_timing()
    assert snap["orm_rows"] == 2.0
    assert snap["orm_materialize_s"] > 0.0
  finally:
    st._reset_ingest_write_timing(enabled=False)


def test_proc_stage_ddl_once_per_connection_and_deadlock_retry(monkeypatch):
  """One CREATE across batches; a later deadlock retries the write once."""
  from django.db.utils import OperationalError

  state = {"ddl": 0, "copy": 0, "upsert": 0, "ok": 0}

  def execute(sql, params=None):
    del params
    text = str(sql)
    if "CREATE TEMP" in text:
      state["ddl"] += 1
      return
    if "INSERT INTO proc_data" in text:
      state["upsert"] += 1
      if state["upsert"] == 2:
        raise OperationalError("deadlock detected")
      state["ok"] += 1
      return
    raise AssertionError(text)

  _patch_proc_copy_connection(monkeypatch, execute, state)
  import django.db as django_db

  django_db.connection.connection = object()
  pdi.bulk_insert_proc_data_update_conflicts([_obj()])
  pdi.bulk_insert_proc_data_update_conflicts([_obj(proc="other")])
  assert state["ddl"] == 1
  assert state["ok"] == 2
  assert state["upsert"] == 3
  assert "IF NOT EXISTS" in pdi._STAGE_DDL
  assert "ON COMMIT DELETE ROWS" in pdi._STAGE_DDL
  assert "ON COMMIT DROP" not in pdi._STAGE_DDL


def test_proc_copy_bytes_match_tuple_builder():
  """Column-slice COPY bytes match the namedtuple builder, including nulls."""
  from types import SimpleNamespace

  import pandas as pd

  from hpcperfstats.dbload import sync_timedb as st

  row = SimpleNamespace(
    jid=None,
    host="h",
    proc="bash",
    device="",
    uid=float("nan"),
    vm_peak=10.0,
    vm_size=0.0,
    vm_lck=float("nan"),
    vm_hwm=4.0,
    vm_rss=1.0,
    vm_data=float("nan"),
    vm_stk=2.0,
    vm_exe=3.0,
    vm_lib=5.0,
    vm_pte=float("nan"),
    vm_swap=236948.0,
    threads=1.0,
  )
  frame = pd.DataFrame([row.__dict__])
  assert pdi.proc_rows_to_copy_bytes(frame) == pdi.proc_rows_to_copy_bytes(
    list(frame.itertuples(index=False))
  )
  payload = pdi.proc_rows_to_copy_bytes(frame)
  assert b"236948.0" not in payload
  assert b"\\N" in payload
  assert payload == pdi.proc_data_objs_to_copy_bytes(
    [proc_data(**st._proc_data_row_kwargs(row))]
  )
  assert pdi.proc_rows_to_copy_bytes(frame.iloc[0:0]) == b""


@pytest.mark.skipif(
  os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK") != "1",
  reason="TEMP stage lifecycle needs the compose PostgreSQL workflow",
)
@pytest.mark.django_db(transaction=True)
def test_proc_stage_empty_after_commit_and_rollback():
  """A committed proc stage keeps the table and drops its rows."""
  from django.db import connection, transaction

  if connection.vendor != "postgresql":
    pytest.skip("ON COMMIT DELETE ROWS is PostgreSQL")
  with transaction.atomic():
    with connection.cursor() as cursor:
      cursor.execute(pdi._STAGE_DDL)
      cursor.execute(
        "INSERT INTO proc_data_ingest_stage (jid, host, proc) "
        "VALUES ('1', 'h', 'bash')"
      )
      cursor.execute("SELECT count(*) FROM proc_data_ingest_stage")
      assert cursor.fetchone()[0] == 1
  with connection.cursor() as cursor:
    cursor.execute("SELECT count(*) FROM proc_data_ingest_stage")
    assert cursor.fetchone()[0] == 0
  try:
    with transaction.atomic():
      with connection.cursor() as cursor:
        cursor.execute(
          "INSERT INTO proc_data_ingest_stage (jid, host, proc) "
          "VALUES ('2', 'h', 'zsh')"
        )
        raise RuntimeError("rollback")
  except RuntimeError:
    pass
  with connection.cursor() as cursor:
    cursor.execute("SELECT count(*) FROM proc_data_ingest_stage")
    assert cursor.fetchone()[0] == 0
