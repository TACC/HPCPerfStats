"""Ingest DB writes have no in-process mutex; uniqueness is Postgres."""

from __future__ import annotations

import inspect
import threading
from pathlib import Path

import pandas as pd

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload import sync_timedb_archive as sta
from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

_CHECKOUT = Path(__file__).resolve().parents[2]


def _empty_host_frame() -> pd.DataFrame:
  return pd.DataFrame()


def _one_proc_frame() -> pd.DataFrame:
  return pd.DataFrame(
      [{"jid": "1", "host": "h", "proc": "p", "device": None}],
  )


def _patch_write_path_no_db(monkeypatch, *, bulk_create):
  monkeypatch.setattr(st, "_peak_merge_proc_objs_with_existing", lambda objs: objs)
  monkeypatch.setattr(
      st,
      "_proc_data_row_kwargs",
      lambda _row: {"jid": "1", "host": "h", "proc": "p"},
  )
  monkeypatch.setattr(st, "_invalidate_jid_caches", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "_raise_if_ingest_per_file_deadline_exceeded", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "bulk_create_batch_size", lambda: 100)
  monkeypatch.setattr(st.proc_data.objects, "bulk_create", bulk_create)
  monkeypatch.setattr(st.host_data.objects, "bulk_create", lambda *_a, **_k: None)


def test_in_process_db_writer_lock_api_absent():
  for name in (
      "_pick_write_lock_for_path",
      "_log_db_lock_wait",
      "_held_ingest_write_lock",
      "_add_ingest_db_shard_lock_s",
      "_ingest_db_shard_lock_s",
      "LOCK_WAIT_LOG_THRESHOLD_SECONDS",
  ):
    assert not hasattr(st, name)
  assert not hasattr(cfg, "get_sync_write_lock_shards")
  assert "sync_write_lock_shards" not in cfg.INI_OPTION_DEFAULTS
  assert not any(
      option == "sync_write_lock_shards"
      for _section, option, _default in cfg.INI_OPTION_REGISTRY
  )
  assert "manager_lock" not in inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "manager_lock" not in inspect.getsource(qo._ingest_worker)
  assert "manager_lock" not in inspect.getsource(
      st.run_sync_timedb_supervisor_from_parsed,
  )
  assert "manager_lock" not in Path(sta.__file__).read_text(encoding="utf-8")
  outcome_src = inspect.getsource(st.IngestFileOutcome)
  assert "db_shard_lock_s" not in outcome_src
  log_src = inspect.getsource(st._log_ingest_file_outcome)
  assert "db_shard_lock_s" not in log_src


def test_write_stats_payload_two_threads_complete_without_mutex(monkeypatch):
  barrier = threading.Barrier(2)
  errors: list[BaseException] = []

  def _bulk_create(*_a, **_k):
    try:
      barrier.wait(timeout=2.0)
    except threading.BrokenBarrierError as exc:
      raise AssertionError(
          "in-process write lock serialized concurrent bulk_create",
      ) from exc

  _patch_write_path_no_db(monkeypatch, bulk_create=_bulk_create)

  def _run():
    try:
      st._write_stats_payload_to_db(
          "/archive/host/1700000000",
          _empty_host_frame(),
          _one_proc_frame(),
      )
    except BaseException as exc:
      errors.append(exc)

  threads = [
      threading.Thread(target=_run, name="ingest-write-a"),
      threading.Thread(target=_run, name="ingest-write-b"),
  ]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join(timeout=5.0)
    assert not thread.is_alive()
  assert errors == []


def test_write_stats_payload_resets_connection_after_bulk_create_error(monkeypatch):
  reset_n = {"n": 0}

  def _boom(*_a, **_k):
    raise RuntimeError("bulk_create failed")

  _patch_write_path_no_db(monkeypatch, bulk_create=_boom)
  monkeypatch.setattr(
      st,
      "_reset_ingest_db_connection_after_write_error",
      lambda: reset_n.__setitem__("n", reset_n["n"] + 1),
  )
  monkeypatch.setattr(st, "_insert_proc_data_individually", lambda _df: None)
  monkeypatch.setattr(st, "is_database_unavailable_error", lambda _exc: False)

  st._write_stats_payload_to_db(
      "/archive/host/1700000001",
      _empty_host_frame(),
      _one_proc_frame(),
  )
  assert reset_n["n"] >= 1


def test_operator_docs_have_no_writer_shard_tokens():
  operator = (_CHECKOUT / "docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md").read_text(
      encoding="utf-8",
  )
  assert "DB lock wait" in operator
  assert "must not appear" in operator
  assert "db_shard_lock_s" not in operator
  deploy = (_CHECKOUT / "docs/DEPLOY_CONCURRENCY_AND_NUMA.md").read_text(
      encoding="utf-8",
  )
  assert "sync_write_lock_shards" not in deploy
  assert "db_shard_lock_s" not in deploy
  ini = (_CHECKOUT / "hpcperfstats.ini.example").read_text(encoding="utf-8")
  assert "sync_write_lock_shards" not in ini
  no_timers = (
      _CHECKOUT / "hpcperfstats/cursor-rules/sync-timedb-no-timers.mdc"
  ).read_text(encoding="utf-8")
  assert "DB writer shard" not in no_timers
  assert "Manager shard" not in no_timers


def test_regression_gate_drops_hold_write_lock_bullet():
  gate = (
      _CHECKOUT / "hpcperfstats/cursor-rules/sync-timedb-change-regression-gate.mdc"
  ).read_text(encoding="utf-8")
  assert "Hold ``write_lock`` for the entire individual-insert pass" not in gate
  assert "In-process DB writer shards" not in gate
  assert "close_old_connections" in gate
  assert "_reraise_if_ingest_control_flow" in gate
  assert "test_write_stats_payload_resets_connection_after_bulk_create_error" in gate
