"""Session executor wiring for startup coordinators."""

import gc
import threading
import time
import weakref

import pytest

from hpcperfstats.dbload.lib.sync_timedb_session_executor import (
    SyncTimedbThreadPool,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import (
    StartupArchiveScanCoordinator,
)


def test_startup_archive_scan_coordinator_construct(tmp_path):
  coord = StartupArchiveScanCoordinator(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext=".hpc",
      tgz_archive_dir=str(tmp_path / "daily"),
      log_fn=lambda *_a, **_k: None,
  )
  assert coord.get_snapshot() is None


def test_titled_thread_pool_imap_unordered_supports_timeout_and_completion_order():
  pool = SyncTimedbThreadPool(
      max_workers=2,
      thread_role="metrics-pool",
      process_title="update_metrics.py",
  )

  def delayed(item):
    delay, value = item
    time.sleep(delay)
    return value

  iterator = pool.imap_unordered(
      delayed,
      [(0.05, "slow"), (0.0, "fast")],
      chunksize=1,
  )
  assert iterator.next(timeout=0.5) == "fast"
  assert iterator.next(timeout=0.5) == "slow"
  with pytest.raises(StopIteration):
    next(iterator)
  pool.close()
  pool.join()


def test_titled_thread_pool_exposes_bounded_worker_count():
  pool = SyncTimedbThreadPool(
      max_workers=3,
      thread_role="metrics-pool",
      process_title="update_metrics.py",
  )
  try:
    assert pool._processes == 3
    assert pool.is_active is True
  finally:
    pool.terminate()
    pool.join()


def test_titled_thread_pool_imap_keeps_only_worker_width_submitted():
  """Lazy submission bounds shared-heap Futures and input payloads."""
  gate = threading.Event()
  produced = []
  pool = SyncTimedbThreadPool(
      max_workers=2,
      thread_role="metrics-pool",
      process_title="update_metrics.py",
  )

  def items():
    for item in range(10):
      produced.append(item)
      yield item

  def blocked(item):
    gate.wait(timeout=1)
    return item

  try:
    iterator = pool.imap_unordered(blocked, items())
    assert produced == [0, 1]
    gate.set()
    assert iterator.next(timeout=1) in {0, 1}
    assert produced == [0, 1, 2]
  finally:
    gate.set()
    pool.terminate()
    pool.join()


def test_titled_thread_pool_imap_close_releases_unsubmitted_inputs():
  """Closing a stalled iterator must release its unsubmitted source."""
  gate = threading.Event()
  pool = SyncTimedbThreadPool(
      max_workers=1,
      thread_role="metrics-pool",
      process_title="update_metrics.py",
  )

  class Payload:
    pass

  retained = Payload()
  retained_ref = weakref.ref(retained)
  items = [object(), retained]

  def blocked(item):
    del item
    gate.wait(timeout=1)

  try:
    iterator = pool.imap_unordered(blocked, items)
    del items, retained
    iterator.close()
    gc.collect()
    assert retained_ref() is None
  finally:
    gate.set()
    pool.terminate()
    pool.join()


def test_titled_thread_pool_imap_worker_error_does_not_submit_next_input():
  """A terminal Future error must close the source before refill."""
  produced = []
  pool = SyncTimedbThreadPool(
      max_workers=1,
      thread_role="metrics-pool",
      process_title="update_metrics.py",
  )

  def items():
    for item in range(2):
      produced.append(item)
      yield item

  def fail(item):
    raise ValueError(str(item))

  try:
    iterator = pool.imap_unordered(fail, items())
    with pytest.raises(ValueError, match="0"):
      iterator.next(timeout=1)
    assert produced == [0]
  finally:
    pool.terminate()
    pool.join()
