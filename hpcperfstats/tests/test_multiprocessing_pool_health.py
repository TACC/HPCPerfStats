"""Regression tests for multiprocessing pool worker-death detection."""

import multiprocessing
import time
from types import SimpleNamespace

import pytest

from hpcperfstats.dbload import multiprocessing_pool_health as mph


class _DeadWorker:
  pid = 4242
  exitcode = -9

  def is_alive(self):
    return False


class _RecycledWorker:
  pid = 4242
  exitcode = 0

  def is_alive(self):
    return False


class _AliveWorker:
  pid = 4243

  def __init__(self):
    self._joined = False

  def is_alive(self):
    return not self._joined

  def join(self, timeout=None):
    del timeout
    self._joined = True


class _BlockingPool:
  def __init__(self):
    self._pool = [_AliveWorker()]

  def imap_unordered(self, fn, iterable, chunksize=1):
    del fn, iterable, chunksize

    class _IMap:
      def next(self, timeout=None):
        del timeout
        raise multiprocessing.TimeoutError()

    return _IMap()

  def kill_worker(self):
    self._pool = [_DeadWorker()]


def test_dead_pool_worker_pids_detects_exited_worker():
  pool = SimpleNamespace(_pool=[_DeadWorker(), _AliveWorker()])
  assert mph.dead_pool_worker_pids(pool) == [4242]


def test_alive_pool_worker_count():
  pool = SimpleNamespace(_pool=[_DeadWorker(), _AliveWorker()])
  assert mph.alive_pool_worker_count(pool) == 1


def test_abort_if_pool_workers_dead_raises():
  pool = SimpleNamespace(_pool=[_DeadWorker()])
  with pytest.raises(mph.MultiprocessingWorkerExitError) as excinfo:
    mph.abort_if_pool_workers_dead(pool, context="test")
  assert excinfo.value.exit_code == 137
  assert excinfo.value.dead_pids == (4242,)


def test_abort_if_pool_workers_dead_log_does_not_claim_oom():
  pool = SimpleNamespace(_pool=[_DeadWorker()])
  with pytest.raises(mph.MultiprocessingWorkerExitError) as excinfo:
    mph.abort_if_pool_workers_dead(pool, context="test")
  message = str(excinfo.value)
  assert "likely OOM" not in message
  assert "no longer alive" in message


def test_imap_unordered_watch_pool_aborts_when_worker_dies():
  pool = _BlockingPool()

  def kill_after_delay():
    time.sleep(0.05)
    pool.kill_worker()

  import threading

  threading.Thread(target=kill_after_delay, daemon=True).start()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.05,
      context="test_imap",
  )
  with pytest.raises(mph.MultiprocessingWorkerExitError):
    next(iterator)


class _BlockingAsyncResult:
  def get(self, timeout=None):
    del timeout
    raise multiprocessing.TimeoutError()


def test_async_result_get_watch_pool_aborts_when_worker_dies():
  pool = SimpleNamespace(_pool=[_AliveWorker()])

  def kill_after_delay():
    time.sleep(0.05)
    pool._pool = [_DeadWorker()]

  import threading

  threading.Thread(target=kill_after_delay, daemon=True).start()
  with pytest.raises(mph.MultiprocessingWorkerExitError):
    mph.async_result_get_watch_pool(
        _BlockingAsyncResult(),
        pool,
        poll_timeout_s=0.05,
        context="test_async",
    )


def test_async_result_get_watch_pool_returns_when_ready():
  pool = SimpleNamespace(_pool=[_AliveWorker()])

  class _ReadyAsyncResult:
    def get(self, timeout=None):
      del timeout
      return [True, False]

  assert mph.async_result_get_watch_pool(
      _ReadyAsyncResult(),
      pool,
      poll_timeout_s=0.05,
      context="test_ready",
  ) == [True, False]


class _CloseablePool:
  closed = False
  terminated = False

  def __init__(self, workers):
    self._pool = workers

  def close(self):
    self.closed = True

  def terminate(self):
    self.terminated = True


def test_close_pool_bounded_terminates_when_worker_dead():
  pool = _CloseablePool([_DeadWorker()])
  assert mph.close_pool_bounded(pool, timeout_s=0.1, force_terminate=False) is True
  assert pool.terminated is True


def test_imap_unordered_watch_pool_aborts_on_stuck_worker_stall(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 2,
  )
  pool = _BlockingPool()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.01,
      context="test_stall",
  )
  with pytest.raises(mph.MultiprocessingPoolStallError) as excinfo:
    next(iterator)
  assert excinfo.value.exit_code == 124


def test_imap_stall_logs_before_raise(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 2,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  pool = _BlockingPool()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.01,
      context="test_stall_log",
  )
  with pytest.raises(mph.MultiprocessingPoolStallError):
    next(iterator)
  assert any("ERROR:" in line and "Pool imap stalled" in line for line in logs)
  assert any("test_stall_log" in line for line in logs)


def test_imap_stall_warning_callback(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 4,
  )
  warnings = []

  def on_stall_warning(consecutive, abort_after, poll_timeout_s, context):
    warnings.append(
        (consecutive, abort_after, poll_timeout_s, context),
    )

  pool = _BlockingPool()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.01,
      context="test_warn",
      on_stall_warning=on_stall_warning,
  )
  with pytest.raises(mph.MultiprocessingPoolStallError):
    next(iterator)
  assert warnings[0][0] == 2
  assert warnings[1][0] == 3
  assert warnings[-1][0] == 4


def test_imap_unordered_watch_pool_honors_stall_abort_override(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 100,
  )
  warnings = []

  def on_stall_warning(consecutive, abort_after, poll_timeout_s, context):
    warnings.append((consecutive, abort_after))

  pool = _BlockingPool()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.01,
      stall_abort_after_timeouts=2,
      context="test_override",
      on_stall_warning=on_stall_warning,
  )
  with pytest.raises(mph.MultiprocessingPoolStallError):
    next(iterator)
  assert warnings[-1] == (2, 2)


def test_imap_stall_fatal_summary_appended_to_error(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 2,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))

  def on_stall_fatal_summary(consecutive, abort_after, poll_timeout_s, context):
    del consecutive, abort_after, poll_timeout_s, context
    return " diagnostics_summary=worker_stages=-"

  pool = _BlockingPool()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.01,
      context="test_fatal_summary",
      on_stall_fatal_summary=on_stall_fatal_summary,
  )
  with pytest.raises(mph.MultiprocessingPoolStallError):
    next(iterator)
  error_lines = [line for line in logs if "ERROR:" in line and "Pool imap stalled" in line]
  assert error_lines
  assert "diagnostics_summary=worker_stages=-" in error_lines[-1]


class _DeferStallPool:
  """Pool whose imap iterator times out until ``release_after`` poll attempts."""

  def __init__(self, release_after=5):
    self.release_after = release_after

  def imap_unordered(self, fn, iterable, chunksize=1):
    del fn, chunksize
    items = iter(iterable)
    release_after = self.release_after

    class _Iterator:
      def __init__(self):
        self._timeouts = 0

      def next(self, timeout=None):
        del timeout
        self._timeouts += 1
        if self._timeouts >= release_after:
          return next(items)
        raise multiprocessing.TimeoutError

    return _Iterator()


def test_imap_stall_counter_resets_during_redis_populate_progress(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 2,
  )
  pool = _DeferStallPool(release_after=5)
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [42],
      poll_timeout_s=0.01,
      on_stall_poll=lambda *_a, **_k: True,
  )
  assert next(iterator) == 42


def test_close_pool_bounded_closes_alive_workers():
  pool = _CloseablePool([_AliveWorker()])
  assert mph.close_pool_bounded(pool, timeout_s=0.1) is True
  assert pool.closed is True
  assert pool.terminated is False


def test_terminate_pool_bounded_logs_context(monkeypatch):
  logs = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.log_print",
      lambda msg, flush=False: logs.append(msg),
  )

  class _TermPool:
    _pool = [_AliveWorker()]

    def terminate(self):
      for worker in self._pool:
        worker.join()

  mph.terminate_pool_bounded(_TermPool(), context="ingest_pool")
  assert any("Pool workers terminated" in line and "ingest_pool" in line for line in logs)


class _StubbornWorker:
  pid = 5555

  def is_alive(self):
    return True

  def join(self, timeout=None):
    del timeout


def test_terminate_pool_bounded_sigkill_after_timeout(monkeypatch):
  logs = []
  killed = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.log_print",
      lambda msg, flush=False: logs.append(msg),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.os.kill",
      lambda pid, sig: killed.append((pid, sig)),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.os.waitpid",
      lambda pid, flags: (pid, 0),
  )

  class _StubbornPool:
    _pool = [_StubbornWorker()]

    def terminate(self):
      pass

  mph.terminate_pool_bounded(_StubbornPool(), timeout_s=0.01, context="ingest_pool")
  assert any("Pool terminate SIGKILL" in line and "5555" in line for line in logs)
  assert (5555, mph.signal.SIGKILL) in killed


def test_hard_exit_pool_worker_error_uses_os_exit(monkeypatch):
  exit_codes = []
  logs = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.log_print",
      lambda msg, flush=False: logs.append(msg),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.os._exit",
      lambda code: exit_codes.append(code),
  )
  exc = mph.MultiprocessingPoolStallError(
      "pool imap stalled",
      dead_pids=(),
      context="sync_timedb ingest pool",
      exit_code=124,
  )
  mph.hard_exit_pool_worker_error(exc)
  assert exit_codes == [124]
  assert any("hard exit code=124" in line for line in logs)


def test_handle_pool_worker_exit_fatal_hard_exits_without_terminate(monkeypatch):
  import hpcperfstats.dbload.sync_timedb as st

  exit_codes = []
  terminate_calls = []

  monkeypatch.setattr(
      st,
      "terminate_pool_bounded",
      lambda *_a, **_k: terminate_calls.append(True) or False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.log_print",
      lambda msg, flush=False: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.os._exit",
      lambda code: exit_codes.append(code),
  )
  exc = mph.MultiprocessingPoolStallError(
      "pool imap stalled",
      dead_pids=(),
      context="sync_timedb ingest pool",
      exit_code=124,
  )
  st._handle_pool_worker_exit_fatal(exc, ingest_pool=object())
  assert exit_codes == [124]
  assert terminate_calls == []


def test_handle_pool_worker_exit_fatal_hard_exits_when_terminate_would_block(monkeypatch):
  """Regression: production limbo when terminate ran before os._exit(124)."""
  import hpcperfstats.dbload.sync_timedb as st

  exit_codes = []
  terminate_started = []

  def blocking_terminate(*_a, **_k):
    terminate_started.append(True)
    time.sleep(3600)
    return False

  monkeypatch.setattr(st, "terminate_pool_bounded", blocking_terminate)
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.os._exit",
      lambda code: exit_codes.append(code),
  )
  exc = mph.MultiprocessingPoolStallError(
      "pool imap stalled",
      dead_pids=(),
      context="sync_timedb ingest pool",
      exit_code=124,
  )
  st._handle_pool_worker_exit_fatal(exc, ingest_pool=object())
  assert exit_codes == [124]
  assert terminate_started == []


def test_abort_recycle_grace_tolerates_exitcode_zero(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.get_sync_pool_worker_recycle_grace_polls",
      lambda: 2,
  )
  pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  mph.abort_if_pool_workers_dead(pool, context="recycle_test")
  mph.abort_if_pool_workers_dead(pool, context="recycle_test")
  with pytest.raises(mph.MultiprocessingWorkerExitError) as excinfo:
    mph.abort_if_pool_workers_dead(pool, context="recycle_test")
  assert excinfo.value.exit_code == 137
  assert excinfo.value.likely_cause == "recycle"


def test_abort_recycle_grace_logs_info_not_error(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.multiprocessing_pool_health.get_sync_pool_worker_recycle_grace_polls",
      lambda: 2,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  mph.abort_if_pool_workers_dead(pool, context="recycle_log")
  assert any("INFO: pool worker recycle in progress" in line for line in logs)
  assert not any("ERROR: pool worker death diagnostics" in line for line in logs)


def test_abort_sigkill_logs_diagnostics_with_non_cgroup_hint(monkeypatch):
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  monkeypatch.setattr(
      "hpcperfstats.process_memory.read_cgroup_memory_events",
      lambda: {"oom_kill": 0},
  )
  monkeypatch.setattr(
      "hpcperfstats.process_memory.read_cgroup_memory_current_bytes",
      lambda: 33205403648,
  )
  monkeypatch.setattr(
      "hpcperfstats.process_memory.read_cgroup_memory_max_bytes",
      lambda: 137438953472,
  )
  monkeypatch.setattr(
      "hpcperfstats.process_memory.format_tree_rss_breakdown_mb",
      lambda *a, **k: {"tree_total_mb": 31.0, "supervisor_mb": 1.0,
                       "ingest_pool_mb": 20.0, "db_writer_pool_mb": 5.0,
                       "archive_pool_mb": 5.0},
  )
  pool = SimpleNamespace(_pool=[_DeadWorker()])
  with pytest.raises(mph.MultiprocessingWorkerExitError) as excinfo:
    mph.abort_if_pool_workers_dead(pool, context="sigkill_test")
  assert excinfo.value.likely_cause == "sigkill_non_cgroup"
  assert any("ERROR: pool worker death diagnostics" in line for line in logs)
  assert any("likely_cause=sigkill_non_cgroup" in line for line in logs)


def test_abort_sigkill_with_cgroup_oom_reports_sigkill(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.process_memory.read_cgroup_memory_events",
      lambda: {"oom_kill": 3},
  )
  monkeypatch.setattr(
      "hpcperfstats.process_memory.read_cgroup_memory_current_bytes",
      lambda: 100,
  )
  monkeypatch.setattr(
      "hpcperfstats.process_memory.read_cgroup_memory_max_bytes",
      lambda: 1000,
  )
  monkeypatch.setattr(
      "hpcperfstats.process_memory.format_tree_rss_breakdown_mb",
      lambda *a, **k: {"tree_total_mb": 1.0, "supervisor_mb": 1.0,
                       "ingest_pool_mb": 0.0, "db_writer_pool_mb": 0.0,
                       "archive_pool_mb": 0.0},
  )
  pool = SimpleNamespace(_pool=[_DeadWorker()])
  with pytest.raises(mph.MultiprocessingWorkerExitError) as excinfo:
    mph.abort_if_pool_workers_dead(pool, context="cgroup_oom")
  assert excinfo.value.likely_cause == "sigkill"


def test_describe_dead_pool_workers_includes_in_flight_sample(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.process_memory.read_cgroup_memory_events",
      lambda: {"oom_kill": 0},
  )
  monkeypatch.setattr(
      "hpcperfstats.process_memory.read_cgroup_memory_current_bytes",
      lambda: 0,
  )
  monkeypatch.setattr(
      "hpcperfstats.process_memory.read_cgroup_memory_max_bytes",
      lambda: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.process_memory.format_tree_rss_breakdown_mb",
      lambda *a, **k: {"tree_total_mb": 0.0, "supervisor_mb": 0.0,
                       "ingest_pool_mb": 0.0, "db_writer_pool_mb": 0.0,
                       "archive_pool_mb": 0.0},
  )
  pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  diag = mph.describe_dead_pool_workers(
      pool,
      pool_health_context={"in_flight_sample": ["/pending/a"]},
  )
  assert diag["in_flight_sample"] == ["/pending/a"]
  assert diag["likely_cause"] == "recycle"


class _ManualAsyncResult:
  def __init__(self, pool, fn, path):
    self._pool = pool
    self._fn = fn
    self._path = path
    self._ready = False
    self._result = None
    pool.inflight[self] = path
    pool.peak = max(pool.peak, len(pool.inflight))
    pool.submit_count += 1

  def ready(self):
    return self._ready

  def get(self, timeout=None):
    del timeout
    if not self._ready:
      raise multiprocessing.TimeoutError()
    return self._result

  def finish(self):
    self._result = self._fn(self._path)
    self._ready = True
    self._pool.inflight.pop(self, None)


class _ManualPool:
  def __init__(self):
    self.inflight = {}
    self.peak = 0
    self.submit_count = 0
    self._pool = [_AliveWorker()]

  def apply_async(self, fn, args=()):
    return _ManualAsyncResult(self, fn, args[0])


def test_imap_sliding_window_watch_pool_refills_before_prior_batch_drains():
  import threading

  pool = _ManualPool()
  paths = [
      "slow0",
      "fast1",
      "fast2",
      "fast3",
      "slow4",
      "fast5",
      "fast6",
      "fast7",
  ]
  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=4,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 10000,
      context="test_sliding_refill",
  )
  results = []
  errors = []

  def consumer():
    try:
      for item in gen:
        results.append(item)
    except Exception as exc:
      errors.append(exc)

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 4 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert pool.submit_count == 4
  fast_first_batch = [
      ar for ar, path in pool.inflight.items() if path.startswith("fast")
  ]
  assert len(fast_first_batch) == 3
  for ar in fast_first_batch:
    ar.finish()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 7 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert pool.submit_count >= 7
  assert pool.peak == 4
  deadline = time.monotonic() + 5.0
  while len(results) < len(paths) and time.monotonic() < deadline:
    for ar in list(pool.inflight):
      ar.finish()
    time.sleep(0.01)
  thread.join(timeout=2.0)
  assert not errors
  assert sorted(results) == sorted(paths)


def test_imap_sliding_window_watch_pool_peak_concurrency():
  pool = _ManualPool()
  paths = [f"path{i}" for i in range(20)]
  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=4,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 10000,
  )
  results = []
  import threading

  def consumer():
    for item in gen:
      results.append(item)

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  time.sleep(0.02)
  assert pool.peak == 4
  deadline = time.monotonic() + 2.0
  while (pool.inflight or len(results) < len(paths)) and time.monotonic() < deadline:
    for ar in list(pool.inflight):
      ar.finish()
    time.sleep(0.005)
  thread.join(timeout=2.0)
  assert len(results) == len(paths)
  assert pool.peak == 4


def test_imap_sliding_window_recomputes_stall_abort_for_in_flight(monkeypatch):
  from hpcperfstats.dbload import sync_timedb_ingest_timeout as timeout_mod

  monkeypatch.setattr(
      "hpcperfstats.conf_parser.get_sync_pool_poll_timeout_s",
      lambda: 5.0,
  )
  monkeypatch.setattr(
      "hpcperfstats.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 2881,
  )
  monkeypatch.setattr(
      "hpcperfstats.conf_parser.get_sync_ingest_per_file_timeout_s",
      lambda: 900.0,
  )
  monkeypatch.setattr(
      timeout_mod,
      "resolve_ingest_per_file_timeout_s",
      lambda path: 900.0 if "small" in path else 7200.0,
  )

  recorded = []

  def _polls_fn(in_flight):
    value = timeout_mod.stall_abort_polls_for_paths(in_flight)
    recorded.append((list(in_flight), value))
    return value

  pool = _ManualPool()
  paths = ["large0", "small1", "small2", "small3"]
  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=2,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=_polls_fn,
  )
  import threading

  thread = threading.Thread(target=lambda: list(gen), daemon=True)
  thread.start()
  time.sleep(0.02)
  assert recorded
  large_polls = timeout_mod.stall_abort_polls_for_paths(["large0"])
  small_polls = timeout_mod.stall_abort_polls_for_paths(["small1"])
  assert large_polls > small_polls
  assert any(polls >= large_polls for _paths, polls in recorded if "large0" in _paths)
  for ar in list(pool.inflight):
    ar.finish()
  thread.join(timeout=2.0)

