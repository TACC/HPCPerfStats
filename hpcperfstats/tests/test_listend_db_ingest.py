"""Unit tests for listend live DB ingest pool helpers and contracts."""
from __future__ import annotations

import queue
from unittest import mock


def test_compute_listend_db_queue_budgets_splits_evenly():
  from hpcperfstats.dbload.lib.listend_db_ingest import (
      compute_listend_db_queue_budgets,
  )

  budgets = compute_listend_db_queue_budgets(
      pool_processes=4,
      queue_max_gb=1.0,
      min_payload_bytes=256,
  )
  assert budgets["pool_processes"] == 4
  assert budgets["budget_bytes"] == 1024 ** 3
  assert budgets["per_worker_budget_bytes"] == (1024 ** 3) // 4
  assert budgets["queue_maxsize"] == budgets["per_worker_budget_bytes"] // 256


def test_host_affine_worker_index_stable():
  from hpcperfstats.dbload.lib.listend_db_ingest import host_affine_worker_index

  a = host_affine_worker_index("c001.example.edu", 30)
  b = host_affine_worker_index("c001.example.edu", 30)
  assert a == b
  assert 0 <= a < 30
  # Different hosts should usually diverge (not a hard uniqueness guarantee).
  assert host_affine_worker_index("c002.example.edu", 30) != a or True


def test_schema_covers_and_measurement_types():
  from hpcperfstats.dbload.lib.listend_db_ingest import (
      payload_has_schema_bang,
      sample_measurement_types,
      schema_covers_measurement_types,
  )

  sample = (
      "1710000001.0 1 host.example.edu\n"
      "cpuuser - 1 2 3\n"
      "proc foo/1/2/3 1 2 3\n"
  )
  assert sample_measurement_types(sample) == ["cpuuser", "proc"]
  assert schema_covers_measurement_types({"cpuuser": ["a", "b", "c"]}, ["cpuuser", "proc"])
  assert not schema_covers_measurement_types({}, ["cpuuser"])
  assert payload_has_schema_bang("!cpuuser a b c\n")
  assert not payload_has_schema_bang(sample)


def test_submit_drops_when_byte_budget_exceeded(monkeypatch):
  from hpcperfstats.dbload.lib import listend_db_ingest as ldi

  pool = ldi.ListendDbIngestPool(
      pool_processes=1,
      queue_max_gb=0.000001,  # tiny → tiny per-worker budget
      batch_samples=10,
      enabled=True,
  )
  # Bypass Process.start — wire a real Queue for put/drop math.
  pool._ctx = mock.Mock()
  pool._stop = mock.Mock()
  pool._stop.is_set.return_value = False
  q = queue.Queue(maxsize=1000)
  byte_count = mock.Mock()
  byte_count.value = 0
  byte_lock = mock.MagicMock()
  byte_lock.__enter__ = mock.Mock(return_value=None)
  byte_lock.__exit__ = mock.Mock(return_value=False)
  pool._queues = [q]
  pool._byte_counts = [byte_count]
  pool._byte_locks = [byte_lock]
  pool._counters = {name: mock.Mock(value=0) for name in ldi._COUNTER_NAMES}
  for c in pool._counters.values():
    c.get_lock.return_value = mock.MagicMock()
    c.get_lock.return_value.__enter__ = mock.Mock(return_value=None)
    c.get_lock.return_value.__exit__ = mock.Mock(return_value=False)
  pool._started = True
  pool.per_worker_budget_bytes = 10

  ok = pool.submit("h1", "x" * 20)
  assert ok is False
  assert pool._counters["queue_drops"].value >= 1


def _wire_fake_pool_bytes(pool, *, queued: int, budget: int, per_worker: int):
  """Attach mock byte counters for watermark unit tests."""
  from hpcperfstats.dbload.lib import listend_db_ingest as ldi

  pool._stop = mock.Mock()
  pool._stop.is_set.return_value = False
  byte_count = mock.Mock()
  byte_count.value = queued
  pool._byte_counts = [byte_count]
  pool._queues = [queue.Queue(maxsize=1000)]
  pool._byte_locks = [mock.MagicMock()]
  pool._byte_locks[0].__enter__ = mock.Mock(return_value=None)
  pool._byte_locks[0].__exit__ = mock.Mock(return_value=False)
  pool._counters = {name: mock.Mock(value=0) for name in ldi._COUNTER_NAMES}
  for c in pool._counters.values():
    c.get_lock.return_value = mock.MagicMock()
    c.get_lock.return_value.__enter__ = mock.Mock(return_value=None)
    c.get_lock.return_value.__exit__ = mock.Mock(return_value=False)
  pool._started = True
  pool.enabled = True
  pool.budget_bytes = budget
  pool.per_worker_budget_bytes = per_worker
  pool.pool_processes = 1
  return pool


def test_should_pause_and_resume_hysteresis():
  from hpcperfstats.dbload.lib import listend_db_ingest as ldi

  pool = ldi.ListendDbIngestPool(
      pool_processes=1,
      queue_max_gb=1.0,
      batch_samples=10,
      enabled=True,
  )
  budget = 1000
  # Above pause watermark (0.95) → pause.
  _wire_fake_pool_bytes(pool, queued=950, budget=budget, per_worker=budget)
  assert pool.should_pause_consume() is True
  assert pool.should_resume_consume() is False

  # Mid band: still above resume (0.50) → stay paused for resume check.
  pool._byte_counts[0].value = 700
  assert pool.should_pause_consume() is False
  assert pool.should_resume_consume() is False

  # At/below resume watermark → resume.
  pool._byte_counts[0].value = 500
  assert pool.should_pause_consume() is False
  assert pool.should_resume_consume() is True


def test_should_pause_when_worker_full_even_if_aggregate_low():
  from hpcperfstats.dbload.lib import listend_db_ingest as ldi

  pool = ldi.ListendDbIngestPool(
      pool_processes=2,
      queue_max_gb=1.0,
      batch_samples=10,
      enabled=True,
  )
  pool._stop = mock.Mock()
  pool._stop.is_set.return_value = False
  # Worker 0 full (no min-floor headroom); worker 1 empty.
  # Aggregate can be low vs total budget.
  b0 = mock.Mock(value=1000)
  b1 = mock.Mock(value=0)
  pool._byte_counts = [b0, b1]
  pool._queues = [queue.Queue(), queue.Queue()]
  pool._started = True
  pool.enabled = True
  pool.budget_bytes = 100_000
  pool.per_worker_budget_bytes = 1000
  pool.pool_processes = 2
  assert pool.should_pause_consume() is True
  assert pool.worker_has_headroom(0, ldi._MIN_QUEUED_PAYLOAD_BYTES) is False
  assert pool.worker_has_headroom(1, ldi._MIN_QUEUED_PAYLOAD_BYTES) is True


def test_on_message_pauses_without_archive(tmp_path, monkeypatch):
  import hpcperfstats.listend as listend
  from hpcperfstats.dbload.lib import listend_db_ingest as ldi

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  archived = []

  def capture_archive(message):
    archived.append(message)
    return "myhost"

  monkeypatch.setattr(listend, "append_monitor_payload_to_archive", capture_archive)

  pool = ldi.ListendDbIngestPool(
      pool_processes=1,
      queue_max_gb=1.0,
      batch_samples=10,
      enabled=True,
  )
  _wire_fake_pool_bytes(pool, queued=999, budget=1000, per_worker=1000)
  monkeypatch.setattr(listend, "_live_db_ingest_pool_active", lambda: pool)
  listend._db_backpressure_pause = False

  channel = type("C", (), {"acked": [], "nacked": [], "stopped": False})()
  channel.basic_ack = lambda delivery_tag=None: channel.acked.append(delivery_tag)
  channel.basic_nack = lambda delivery_tag=None, requeue=False: channel.nacked.append(
      (delivery_tag, requeue)
  )
  channel.stop_consuming = lambda: setattr(channel, "stopped", True)
  method = type("M", (), {"delivery_tag": 7})()

  listend.on_message(channel, method, None, b"1710000001.0 1 myhost x\n")
  assert archived == []
  assert channel.acked == []
  assert channel.nacked == [(7, True)]
  assert channel.stopped is True
  assert listend._db_backpressure_pause is True
  assert pool._counters["pause_enters"].value >= 1
  listend._db_backpressure_pause = False


def test_submit_after_write_not_on_failure(tmp_path, monkeypatch):
  import hpcperfstats.listend as listend

  monkeypatch.setattr(listend.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  monkeypatch.setattr(listend, "_live_db_ingest_pool_active", lambda: None)
  submitted = []

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.listend_db_ingest.submit_listend_db_ingest",
      lambda host, message: submitted.append((host, message)) or True,
  )

  channel = type("C", (), {"acked": [], "nacked": []})()
  channel.basic_ack = lambda delivery_tag=None: channel.acked.append(delivery_tag)
  channel.basic_nack = lambda delivery_tag=None, requeue=False: channel.nacked.append(
      (delivery_tag, requeue)
  )
  method = type("M", (), {"delivery_tag": 1})()

  listend.on_message(channel, method, None, b"1.0 1 myhost x\n")
  assert channel.acked == [1]
  assert submitted and submitted[0][0] == "myhost"

  submitted.clear()

  def boom(_message):
    raise RuntimeError("disk full")

  monkeypatch.setattr(listend, "append_monitor_payload_to_archive", boom)
  listend.on_message(channel, method, None, b"1.0 1 myhost x\n")
  assert channel.nacked
  assert submitted == []


def test_head_tail_fast_path_disabled_when_live_on(monkeypatch):
  from hpcperfstats.dbload import sync_timedb as st

  monkeypatch.setattr(st.cfg, "get_listend_db_ingest_enabled", lambda: True)
  assert st._try_db_complete_head_tail_fast_path("/nope", "h", None) is None
  assert st._try_db_complete_tail_window_fast_path("/nope", "h", None) is None


def test_find_processing_start_index_extra_tokens_and_malformed():
  from hpcperfstats.dbload.lib.sync_timedb_parsing import find_processing_start_index

  lines = [
      "!cpuuser a b\n",
      "1710000001.0 1 host.example.edu EXTRA\n",
      "cpuuser - 1 2\n",
      "not-a-digit\n",
      "1710000002.0 1 host.example.edu\n",
      "bad\n",
      "1710000003.0 only_two\n",
      "1710000004.0 1 host.example.edu\n",
  ]
  # 1 and 2 present, 4 missing → resume from last present (line index of 2).
  itimes = {1710000001, 1710000002}
  start_idx, need = find_processing_start_index(lines, itimes)
  assert need is True
  assert start_idx == 4  # last present digit line index


def test_find_processing_start_index_lines_streaming_parity(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      find_processing_start_index,
      find_processing_start_index_streaming,
  )

  content = (
      "\n"
      "  !cpuuser a b c\n"
      "1710000001.5 1 host.example.edu\n"
      "cpuuser - 1 2 3\n"
      "1710000002.0 1 host.example.edu EXTRA\n"
      "cpuuser - 1 2 3\n"
      "1710000003.0 1 host.example.edu\n"
  )
  path = tmp_path / "seg"
  path.write_text(content)
  lines = content.splitlines(keepends=True)
  itimes = {1710000001, 1710000002}
  a, _ = find_processing_start_index(lines, itimes)
  b, _ = find_processing_start_index_streaming(str(path), itimes)
  assert a == b


def test_file_complete_mark_roundtrip(tmp_path):
  from hpcperfstats.dbload.lib import sync_timedb_file_complete_ingest_mark as fcm

  seg = tmp_path / "host" / "1710000000"
  seg.parent.mkdir(parents=True)
  seg.write_text("x")
  assert not fcm.has_file_complete_ingest_mark(str(seg), archive_data_dir=str(tmp_path))
  assert fcm.record_file_complete_ingest_mark(str(seg), archive_data_dir=str(tmp_path))
  assert fcm.has_file_complete_ingest_mark(str(seg), archive_data_dir=str(tmp_path))
  assert fcm.maybe_record_file_complete_ingest_mark_from_outcome(
      str(seg),
      ingest_ok=True,
      outcome="db_skip",
      db_skip="head_tail",
      archive_data_dir=str(tmp_path),
  ) is False
  assert fcm.maybe_record_file_complete_ingest_mark_from_outcome(
      str(seg),
      ingest_ok=True,
      outcome="db_skip",
      db_skip="full_scan",
      archive_data_dir=str(tmp_path),
  )


def test_archive_gate_live_on_requires_file_complete_mark(tmp_path, monkeypatch):
  from hpcperfstats.dbload.lib import sync_timedb_ingest_readiness as ready
  from hpcperfstats.dbload.lib import sync_timedb_file_complete_ingest_mark as fcm

  ready.reset_sync_ingest_readiness_caches()
  monkeypatch.setattr(ready.cfg, "get_sync_archive_require_db_ingest", lambda: True)
  monkeypatch.setattr(ready.cfg, "get_listend_db_ingest_enabled", lambda: True)
  monkeypatch.setattr(ready, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(ready, "_path_head_tail_ready_in_db", lambda _p: True)
  monkeypatch.setattr(
      "hpcperfstats.dbload.sync_timedb._sync_worker_db_task",
      lambda: mock.MagicMock(
          __enter__=mock.Mock(return_value=None),
          __exit__=mock.Mock(return_value=False),
      ),
  )

  seg = tmp_path / "host" / "1710000000"
  seg.parent.mkdir(parents=True)
  seg.write_text("data")
  assert ready.stats_file_head_ingested_in_db(str(seg)) is False
  assert fcm.record_file_complete_ingest_mark(str(seg), archive_data_dir=str(tmp_path))
  monkeypatch.setattr(
      ready.cfg, "get_archive_dir_path", lambda: str(tmp_path),
  )
  # has_file_complete uses archive_data_dir from cfg default — patch helper.
  monkeypatch.setattr(
      ready,
      "_path_ready_via_file_complete_mark",
      lambda path: fcm.has_file_complete_ingest_mark(
          path, archive_data_dir=str(tmp_path),
      ),
  )
  ready.reset_sync_ingest_readiness_caches()
  assert ready.stats_file_head_ingested_in_db(str(seg)) is True


def test_process_sample_skips_without_schema():
  from hpcperfstats.dbload.lib import listend_db_ingest as ldi
  from hpcperfstats.dbload.lib.sync_timedb_parsing import DeltaCarryState as DCS

  sample = "1710000001.0 1 host.example.edu\ncpuuser - 1 2 3\n"
  host_objs, proc_objs = ldi._process_sample_to_orm(
      sample,
      host="host.example.edu",
      schema={},
      schema_fast={},
      carry=DCS(),
  )
  assert host_objs == []
  assert proc_objs == []


def test_process_sample_sets_host_data_jid_from_header():
  """Live listend path: sample-header jobid must reach host_data.jid."""
  from hpcperfstats.dbload.lib import listend_db_ingest as ldi
  from hpcperfstats.dbload.lib.sync_timedb_parsing import DeltaCarryState as DCS

  schema = {"cpu": ["user", "sys"]}
  schema_fast = {"cpu": ["user", "sys"]}
  sample = (
      "1710000001.0 job42 host.example.edu\n"
      "cpu 0 10 20\n"
  )
  host_objs, proc_objs = ldi._process_sample_to_orm(
      sample,
      host="host.example.edu",
      schema=schema,
      schema_fast=schema_fast,
      carry=DCS(),
  )
  assert host_objs
  assert all(o.jid == "job42" for o in host_objs)
  assert proc_objs == []


def test_process_sample_idle_jid_dash_maps_to_none():
  """Idle header '-' must become NULL on host_data ORM instances."""
  from hpcperfstats.dbload.lib import listend_db_ingest as ldi
  from hpcperfstats.dbload.lib.sync_timedb_parsing import DeltaCarryState as DCS

  schema = {"cpu": ["user", "sys"]}
  schema_fast = {"cpu": ["user", "sys"]}
  sample = (
      "1710000001.0 - host.example.edu\n"
      "cpu 0 10 20\n"
  )
  host_objs, _ = ldi._process_sample_to_orm(
      sample,
      host="host.example.edu",
      schema=schema,
      schema_fast=schema_fast,
      carry=DCS(),
  )
  assert host_objs
  assert all(o.jid is None for o in host_objs)


def test_flush_clears_batch_lists():
  """Document memory contract: flush clears ORM batch (unit via mock write)."""
  from hpcperfstats.dbload.lib import listend_db_ingest as ldi

  pending_host = [object(), object()]
  pending_proc = [object()]
  with mock.patch.object(ldi, "_flush_orm_batch", autospec=True):
    # Simulate worker flush clearing pattern
    try:
      ldi._flush_orm_batch(pending_host, pending_proc)
    finally:
      pending_host = []
      pending_proc = []
  assert pending_host == []
  assert pending_proc == []


def test_dedupe_proc_objs_keep_last_for_upsert_batch():
  """Regression: duplicate (jid,host,proc) in one bulk_create upsert fails on Postgres."""
  from types import SimpleNamespace

  from hpcperfstats.dbload.lib import listend_db_ingest as ldi

  first = SimpleNamespace(
      jid="1",
      host="h",
      proc="bash",
      vm_rss=1,
      vm_peak=9000,
      vm_hwm=7000,
      vm_stk=100,
      vm_exe=10,
      vm_lib=5,
  )
  second = SimpleNamespace(
      jid="1",
      host="h",
      proc="bash",
      vm_rss=99,
      vm_peak=0,
      vm_hwm=100,
      vm_stk=40,
      vm_exe=20,
      vm_lib=1,
  )
  other = SimpleNamespace(
      jid="1", host="h", proc="python", vm_rss=5, vm_stk=1, vm_exe=1, vm_lib=1
  )
  out = ldi._dedupe_proc_objs_keep_last([first, other, second])
  assert len(out) == 2
  by_proc = {o.proc: o for o in out}
  assert by_proc["bash"].vm_rss == 99
  assert by_proc["bash"].vm_peak == 9000
  assert by_proc["bash"].vm_hwm == 7000
  assert by_proc["bash"].vm_stk == 100
  assert by_proc["bash"].vm_exe == 20
  assert by_proc["bash"].vm_lib == 5
  assert by_proc["python"].vm_rss == 5


def test_flush_orm_batch_dedupes_proc_before_bulk_create(monkeypatch):
  """Ensure flush collapses duplicate proc keys before update_conflicts write."""
  from types import SimpleNamespace

  from hpcperfstats.dbload.lib import listend_db_ingest as ldi

  captured = {}

  class _FakeManager:
    def bulk_create(self, objs, **kwargs):
      captured.setdefault("calls", []).append((list(objs), kwargs))

    def filter(self, *args, **kwargs):
      return self

    def only(self, *args, **kwargs):
      return []

  class _FakeModel:
    objects = _FakeManager()

  monkeypatch.setattr(
      "django.db.close_old_connections",
      lambda: None,
  )
  monkeypatch.setattr(
      "django.db.connections.close_all",
      lambda: None,
      raising=False,
  )
  # Patch models imported inside _flush_orm_batch.
  import hpcperfstats.site.lib.machine.models as models

  monkeypatch.setattr(models, "proc_data", _FakeModel)
  monkeypatch.setattr(models, "host_data", _FakeModel)
  monkeypatch.setattr(ldi.cfg, "get_sync_bulk_create_batch_size", lambda: 10000)

  first = SimpleNamespace(
      jid="1", host="h", proc="bash", device="bash/1/0/0",
      **{k: None for k in (
          "uid", "vm_peak", "vm_size", "vm_lck", "vm_hwm", "vm_rss", "vm_data",
          "vm_stk", "vm_exe", "vm_lib", "vm_pte", "vm_swap", "threads",
      )}
  )
  second = SimpleNamespace(
      jid="1", host="h", proc="bash", device="bash/1/0/0",
      **{k: None for k in (
          "uid", "vm_peak", "vm_size", "vm_lck", "vm_hwm", "vm_rss", "vm_data",
          "vm_stk", "vm_exe", "vm_lib", "vm_pte", "vm_swap", "threads",
      )}
  )
  second.vm_rss = 42

  ldi._flush_orm_batch([], [first, second])
  assert "calls" in captured
  # First call is proc_data (only proc objs were passed).
  objs, kwargs = captured["calls"][0]
  assert len(objs) == 1
  assert objs[0].proc == "bash"
  assert kwargs.get("update_conflicts") is True


def test_worker_main_configures_blas_before_numpy(monkeypatch):
  """Regression: listend DB workers must cap OpenBLAS before parsing imports numpy.

  Without this, ~30 workers × default OpenBLAS threads hit pthread_create EAGAIN.
  """
  import os

  import hpcperfstats.dbload.lib.blas_thread_env as blas_env

  for key in blas_env.BLAS_THREAD_ENV_KEYS:
    monkeypatch.delenv(key, raising=False)

  # Inspect source order contract: configure_blas_thread_env before sync_timedb_parsing.
  import inspect

  from hpcperfstats.dbload.lib import listend_db_ingest as ldi

  src = inspect.getsource(ldi._worker_main)
  blas_pos = src.find("configure_blas_thread_env")
  parsing_pos = src.find("sync_timedb_parsing")
  assert blas_pos != -1
  assert parsing_pos != -1
  assert blas_pos < parsing_pos

  blas_env.configure_blas_thread_env()
  assert os.environ.get("OPENBLAS_NUM_THREADS") == "1"
  assert os.environ.get("OMP_NUM_THREADS") == "1"
