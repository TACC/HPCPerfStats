"""Unit tests for pipelined startup tail ingest coordinator."""

import os
import threading
import time
from datetime import date
from unittest.mock import MagicMock

import pytest

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.shutdown_utils import shutdown_requested
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    tail_eligible_days_from_unprocessed,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_tail_ingest import (
    StartupTailIngestCoordinator,
)


@pytest.fixture(autouse=True)
def _reset_shutdown():
  prev = shutdown_requested[0]
  shutdown_requested[0] = False
  yield
  shutdown_requested[0] = prev


def _make_coordinator(**kwargs):
  ingest_calls = []
  day_close_calls = []

  def run_ingest_batch(paths, context):
    ingest_calls.append((list(paths), context))
    return list(paths), []

  def submit_day_close(tar_norm, reason):
    day_close_calls.append((tar_norm, reason))
    return True

  defaults = {
      "log_fn": MagicMock(),
      "run_ingest_batch": run_ingest_batch,
      "submit_day_close": submit_day_close,
      "signal_janitor": MagicMock(),
      "get_startup_snapshot": lambda: None,
      "live_unprocessed_by_tar": lambda: {},
      "discover_done_fn": lambda: True,
      "process_title": "sync_timedb.py",
  }
  defaults.update(kwargs)
  coord = StartupTailIngestCoordinator(**defaults)
  coord.enabled = True
  coord._ingest_calls = ingest_calls
  coord._day_close_calls = day_close_calls
  return coord


def test_tail_eligible_days_from_unprocessed_oldest_first(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar1 = os.path.normpath(str(daily_dir / "2020-01-01.tar"))
  tar2 = os.path.normpath(str(daily_dir / "2020-01-02.tar"))
  open(tar1, "wb").close()
  open(tar2, "wb").close()
  p1 = str(tmp_path / "a")
  p2 = str(tmp_path / "b")
  open(p1, "wb").close()
  open(p2, "wb").close()
  unprocessed = {tar2: [p2], tar1: [p1]}
  eligible = tail_eligible_days_from_unprocessed(
      unprocessed,
      tgz_archive_dir=str(daily_dir),
      max_files=100,
  )
  assert [t for t, _paths in eligible] == [tar1, tar2]


def test_enqueue_tail_day_is_idempotent():
  coord = _make_coordinator()
  tar = "/daily/2020-01-01.tar"
  assert coord.enqueue_tail_day(tar, ["/raw/a"]) is True
  assert coord.enqueue_tail_day(tar, ["/raw/b"]) is False
  assert coord.pending_count() == 1


def test_coordinator_processes_enqueued_days_oldest_first():
  discover_done = {"value": False}
  coord = _make_coordinator(discover_done_fn=lambda: discover_done["value"])
  tar1 = os.path.normpath("/daily/2020-01-01.tar")
  tar2 = os.path.normpath("/daily/2020-01-02.tar")
  coord.enqueue_tail_day(tar2, ["/raw/b"])
  coord.enqueue_tail_day(tar1, ["/raw/a"])
  coord.start_async_tail_ingest()
  deadline = time.time() + 5.0
  while time.time() < deadline:
    if len(coord._ingest_calls) >= 2:
      break
    time.sleep(0.05)
  discover_done["value"] = True
  deadline = time.time() + 5.0
  while time.time() < deadline:
    if coord.tail_ingest_done():
      break
    time.sleep(0.05)
  coord.shutdown()
  assert [call[0][0] for call in coord._ingest_calls] == ["/raw/a", "/raw/b"]
  assert len(coord._day_close_calls) == 2
  assert coord.tail_ingest_done()


def test_coordinator_begins_before_discover_done():
  discover_done = {"value": False}
  started = threading.Event()
  ingest_started = threading.Event()

  def run_ingest_batch(paths, context):
    ingest_started.set()
    started.wait(timeout=5.0)
    return list(paths), []

  coord = _make_coordinator(
      discover_done_fn=lambda: discover_done["value"],
      run_ingest_batch=run_ingest_batch,
  )
  coord.enqueue_tail_day("/daily/2020-01-01.tar", ["/raw/x"])
  coord.start_async_tail_ingest()
  assert ingest_started.wait(timeout=5.0)
  assert discover_done["value"] is False
  started.set()
  discover_done["value"] = True
  deadline = time.time() + 5.0
  while time.time() < deadline:
    if coord.tail_ingest_done():
      break
    time.sleep(0.05)
  coord.shutdown()
  assert coord.tail_ingest_done()


def test_note_deferred_above_max_logs_once():
  log = MagicMock()
  coord = _make_coordinator(log_fn=log)
  tar = os.path.normpath("/daily/2020-01-01.tar")
  coord.note_deferred_above_max(tar, 150, 100)
  coord.note_deferred_above_max(tar, 150, 100)
  defer_logs = [
      call
      for call in log.call_args_list
      if call.args and "above_max_files" in str(call.args[0])
  ]
  assert len(defer_logs) == 1


def test_tail_ingest_done_waits_for_discover_and_queue():
  discover_done = {"value": False}
  coord = _make_coordinator(discover_done_fn=lambda: discover_done["value"])
  coord.enqueue_tail_day("/daily/2020-01-01.tar", ["/raw/a"])
  coord.start_async_tail_ingest()
  deadline = time.time() + 5.0
  while time.time() < deadline:
    if len(coord._ingest_calls) >= 1:
      break
    time.sleep(0.05)
  assert not coord.tail_ingest_done()
  discover_done["value"] = True
  deadline = time.time() + 5.0
  while time.time() < deadline:
    if coord.tail_ingest_done():
      break
    time.sleep(0.05)
  coord.shutdown()
  assert coord.tail_ingest_done()
