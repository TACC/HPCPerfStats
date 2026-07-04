"""Unit tests for sync_timedb supervisor loop (no real multiprocessing or DB)."""
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import json
import os
from unittest.mock import MagicMock, patch
import hpcperfstats.dbload.sync_timedb as st
import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as archive_helpers
import hpcperfstats.dbload.lib.sync_timedb_archive_janitor as janitor_mod
import hpcperfstats.dbload.lib.sync_timedb_session_executor as session_executor_mod
import hpcperfstats.dbload.lib.sync_timedb_day_close_manifest as async_day_close_mod
import pandas as pd
import pytest
from hpcperfstats.dbload.lib.shutdown_utils import shutdown_requested

def _fake_map_async_result(value):
    """``AsyncResult`` double with ``ready()`` for ``ArchiveDispatchCoordinator``."""

    class _R:

        def ready(self):
            return True

        def get(self, timeout=None):
            del timeout
            return value() if callable(value) else value
    return _R()

class _FakeIngestPool:

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, exc, tb):
        return False

    def imap_unordered(self, fn, chunk):
        del fn
        for path in chunk:
            yield (path, False)

    def apply_async(self, fn, args=(), kwds=None):
        del fn, kwds
        path = args[0] if args else None
        return _fake_map_async_result((path, False))

class _FakeFailedIngestPool:

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, exc, tb):
        return False

    def imap_unordered(self, fn, chunk):
        del fn
        for path in chunk:
            yield (path, False, False)

    def apply_async(self, fn, args=(), kwds=None):
        del kwds
        return _fake_map_async_result(lambda: fn(*args))

class _FakeArchivePool:

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, exc, tb):
        return False

    def map_async(self, fn, items):
        del fn, items
        return _fake_map_async_result(None)

class _FakeArchivePoolPending:

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, exc, tb):
        return False

    def map_async(self, fn, items):
        del fn, items
        return _fake_map_async_result(None)

class _FakeArchivePoolRetry:

    def __init__(self):
        self.calls = 0

    def map_async(self, fn, items):
        del fn, items
        self.calls += 1
        result = [False] if self.calls == 1 else [True]
        return _fake_map_async_result(result)

def _empty_maintenance_snapshot(*_a, **_k):
    from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot
    return ArchiveMaintenanceSnapshot(closed_paths=[], remaining_raw_by_gz={}, mapping={}, ready_paths=set())

class _InlineThreadPoolExecutor:
    """Run janitor / day-close work inline so supervisor unit tests do not hang."""

    def __init__(self, *args, **kwargs):
        del args, kwargs

    def submit(self, fn, *args, **kwargs):
        from concurrent.futures import Future

        fut = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except BaseException as exc:
            fut.set_exception(exc)
        return fut

    def shutdown(self, wait=True):
        del wait

@pytest.fixture(autouse=True)
def _default_startup_daily_tar_count(monkeypatch):
    """Keep startup archival gating deterministic unless a test overrides it."""
    monkeypatch.setattr(st, '_count_daily_tars', lambda *_a, **_k: 0)
    monkeypatch.setattr(st.cfg, 'get_sync_archive_maint_hints', lambda: False)
    monkeypatch.setattr(janitor_mod, 'build_archive_maintenance_snapshot', _empty_maintenance_snapshot)
    monkeypatch.setattr(janitor_mod, 'save_archive_maint_hints', lambda *_a, **_k: None)
    monkeypatch.setattr(session_executor_mod, 'ThreadPoolExecutor', _InlineThreadPoolExecutor)
    # Day-close workers also use ThreadPoolExecutor; keep them inline in unit tests.
    monkeypatch.setattr(janitor_mod, 'ThreadPoolExecutor', _InlineThreadPoolExecutor)
    monkeypatch.setattr(
        async_day_close_mod.DayCloseManifestCoordinator,
        'enqueue_day_close',
        lambda self, tar_path, *, reason, disqualified_daily_tars=None: bool(tar_path),
    )
    monkeypatch.setattr(
        async_day_close_mod.DayCloseManifestCoordinator,
        'enqueue_day_close',
        lambda self, tar_path, reason, *, disqualified_daily_tars=None: bool(tar_path),
    )
    monkeypatch.setattr(
        async_day_close_mod.DayCloseManifestCoordinator,
        'is_complete',
        lambda self, tar_path: bool(tar_path),
    )
    monkeypatch.setattr(
        async_day_close_mod.DayCloseManifestCoordinator,
        'active_or_submitted_tar_paths',
        lambda self: set(),
    )
    _orig_janitor_init = janitor_mod.ArchiveJanitor.__init__

    def _janitor_init_no_tick_chain(self, *args, **kwargs):
        _orig_janitor_init(self, *args, **kwargs)
        self._allow_tick_chaining = False
    monkeypatch.setattr(janitor_mod.ArchiveJanitor, '__init__', _janitor_init_no_tick_chain)
    monkeypatch.setattr(janitor_mod, 'build_remaining_raw_stats_by_daily_gz', lambda *a, **k: {})
    monkeypatch.setattr(archive_helpers, 'build_remaining_raw_stats_by_daily_gz', lambda *a, **k: {})
    monkeypatch.setattr(janitor_mod, 'build_remaining_raw_for_daily_tar', lambda *a, **k: {})
    monkeypatch.setattr(archive_helpers, 'build_remaining_raw_for_daily_tar', lambda *a, **k: {})
    monkeypatch.setattr(janitor_mod, 'iter_daily_tar_paths', lambda *a, **k: [])
    from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
    _orig_wait_idle = StartupArchiveScanCoordinator.wait_for_startup_maintenance_idle

    def _fast_wait_idle(self, *, timeout_s=None):
        if timeout_s is None:
            timeout_s = 5.0
        return _orig_wait_idle(self, timeout_s=timeout_s)
    monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_startup_maintenance_idle', _fast_wait_idle)
    monkeypatch.setattr(st, '_paths_all_db_complete_for_prewarm_skip', lambda _paths: False)

def test_periodic_maintenance_always_runs_gated_tar_removal(monkeypatch, tmp_path):
    """Janitor debt accrual and ticks invoke remove_verified_uncompressed_daily_tars."""
    shutdown_requested[0] = False
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot
        tar_removal_calls = []

        def fake_rescan(*_a, **_k):
            return []

        def snapshot_with_tar_debt(*_a, **_k):
            return ArchiveMaintenanceSnapshot(closed_paths=[], remaining_raw_by_gz={'/tmp/2026-01-01.tar.gz': ['/tmp/raw-a']}, mapping={}, ready_paths=set())
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(archive_helpers, 'get_unmapped_closed_raw_daily_tars_cached', lambda *_a, **_k: frozenset())
        monkeypatch.setattr(janitor_mod, 'build_archive_maintenance_snapshot', snapshot_with_tar_debt)
        monkeypatch.setattr(janitor_mod, 'atomic_seal_tar_to_zst', lambda *a, **k: None)
        monkeypatch.setattr(janitor_mod, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(janitor_mod, 'remove_verified_uncompressed_daily_tars', lambda *a, **k: tar_removal_calls.append(1, raising=False))
        clock = {'t': 10000.0}

        def fake_time():
            clock['t'] += 2.0
            return clock['t']
        monkeypatch.setattr(st.time, 'time', fake_time)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), archive_pool, run_once=True)
        finally:
            archive_pool.__exit__(None, None, None)
        assert len(tar_removal_calls) == 0
    finally:
        shutdown_requested[0] = False

def test_supervisor_wires_ingest_ready_fn_into_day_raw_removal(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    captured = {}
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        original_coord = st.DayRawRemovalCoordinator

        def spy_coord(**kwargs):
            captured['ingest_ready_fn'] = kwargs.get('ingest_ready_fn')
            return original_coord(**kwargs)

        def fake_rescan(*_a, **_k):
            shutdown_requested[0] = True
            return []
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', spy_coord)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert captured['ingest_ready_fn'] is st.stats_file_head_ingested_in_db
    finally:
        shutdown_requested[0] = False

def test_supervisor_calls_ensure_persistence_contract_at_startup(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    contract_calls = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()

        def spy_contract(directory, *, log_fn=None):
            contract_calls.append(directory)
            return False

        def fake_rescan(*_a, **_k):
            shutdown_requested[0] = True
            return []
        monkeypatch.setattr(st, 'ensure_persistence_contract', spy_contract)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert contract_calls == [str(archive_dir)]
    finally:
        shutdown_requested[0] = False

def test_normalize_archive_groups_by_tgz_sorts_and_copies_paths():
    mapping = {'/tmp/2026-03-02.tar.gz': ['/tmp/p2', '/tmp/p1'], '/tmp/2026-03-01.tar.gz': ['/tmp/a']}
    tasks = st._normalize_archive_groups_by_tgz(mapping)
    assert tasks == [('/tmp/2026-03-01.tar.gz', ['/tmp/a']), ('/tmp/2026-03-02.tar.gz', ['/tmp/p2', '/tmp/p1'])]
    tasks[0][1].append('/tmp/mut')
    assert mapping['/tmp/2026-03-01.tar.gz'] == ['/tmp/a']

def test_parse_sync_timedb_argv_defaults_include_current_day(monkeypatch):
    """Default end date should include current day time, not midnight-only."""

    class _FakeDateTime(datetime):

        @classmethod
        def today(cls):
            return cls(2026, 4, 14, 10, 30, 45)
    monkeypatch.setattr(st, 'datetime', _FakeDateTime)
    run_once, startdate, enddate = st.parse_sync_timedb_argv(['sync_timedb.py'])
    assert run_once is False
    assert startdate == datetime(2026, 4, 14, 0, 0, 0) - st.timedelta(days=st.days_to_process)
    assert enddate == datetime(2026, 4, 14, 10, 30, 45)

def test_parse_sync_timedb_argv_once_and_all(monkeypatch):

    class _FakeDateTime(datetime):

        @classmethod
        def today(cls):
            return cls(2026, 4, 14, 10, 30, 45)
    monkeypatch.setattr(st, 'datetime', _FakeDateTime)
    run_once, startdate, enddate = st.parse_sync_timedb_argv(['sync_timedb.py', 'once', 'all'])
    assert run_once is True
    assert startdate == 'all'
    assert enddate is None

def test_parse_sync_timedb_argv_single_date_end_of_that_day(monkeypatch):
    """One YYYY-MM-DD ingests that calendar day only, not through today."""

    class _FakeDateTime(datetime):

        @classmethod
        def today(cls):
            return cls(2026, 4, 14, 10, 30, 45)
    monkeypatch.setattr(st, 'datetime', _FakeDateTime)
    run_once, startdate, enddate = st.parse_sync_timedb_argv(['sync_timedb.py', '2024-01-15'])
    assert run_once is False
    assert startdate == datetime(2024, 1, 15, 0, 0, 0)
    assert enddate == datetime.combine(datetime(2024, 1, 15).date(), datetime.max.time())
    assert enddate != datetime(2026, 4, 14, 10, 30, 45)

def test_parse_sync_timedb_argv_once_single_date(monkeypatch):

    class _FakeDateTime(datetime):

        @classmethod
        def today(cls):
            return cls(2026, 4, 14, 10, 30, 45)
    monkeypatch.setattr(st, 'datetime', _FakeDateTime)
    run_once, startdate, enddate = st.parse_sync_timedb_argv(['sync_timedb.py', 'once', '2024-01-15'])
    assert run_once is True
    assert startdate == datetime(2024, 1, 15, 0, 0, 0)
    assert enddate == datetime.combine(datetime(2024, 1, 15).date(), datetime.max.time())

def test_parse_sync_timedb_argv_two_dates_unchanged(monkeypatch):

    class _FakeDateTime(datetime):

        @classmethod
        def today(cls):
            return cls(2026, 4, 14, 10, 30, 45)
    monkeypatch.setattr(st, 'datetime', _FakeDateTime)
    run_once, startdate, enddate = st.parse_sync_timedb_argv(['sync_timedb.py', '2024-01-15', '2024-01-20'])
    assert run_once is False
    assert startdate == datetime(2024, 1, 15, 0, 0, 0)
    assert enddate == datetime(2024, 1, 20, 0, 0, 0)

def test_run_sync_timedb_supervisor_from_parsed_resets_runtime_caches(monkeypatch):
    """Session start clears timestamp caches so stale state never leaks across runs."""
    import hpcperfstats.dbload.lib.sync_timedb_ingest_readiness as readiness
    readiness._HEAD_DB_CACHE['hostA', 1] = {'present': True, 'checked_at': 1.0}
    st._HOST_ITIMES_CACHE['hostA', 1, 2] = {'times': (1, 2), 'checked_at': 1.0}
    monkeypatch.setattr(st, 'run_sync_timedb_supervisor_loop', lambda *a, **k: None)
    monkeypatch.setattr(st, 'log_date_range', lambda *a, **k: None)
    monkeypatch.setattr(st.cfg, 'get_host_name_ext', lambda: 'demo.cluster.local')
    monkeypatch.setattr(st.cfg, 'get_archive_dir_path', lambda: '/tmp/archive')
    monkeypatch.setattr(st.cfg, 'get_sync_enable_cpuset_priority_budget', lambda: False)
    monkeypatch.setattr(st.cfg, 'get_sync_write_lock_shards', lambda: 1)

    class _Manager:

        def Lock(self):
            return object()

        def shutdown(self):
            return None

    class _Pool:

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, exc, tb):
            return False

    class _Context:

        def Pool(self, processes=None, **kwargs):
            del processes
            return _Pool()
    monkeypatch.setattr(st.multiprocessing, 'Manager', lambda: _Manager())
    monkeypatch.setattr(st.multiprocessing, 'get_context', lambda _name: _Context())
    st.run_sync_timedb_supervisor_from_parsed(run_once=True, startdate='all', enddate=None)
    assert readiness._HEAD_DB_CACHE == {}
    assert readiness._PATH_READY_CACHE == {}
    assert st._HOST_ITIMES_CACHE == {}

def test_supervisor_sleeps_once_then_exits_after_empty_full_rescan(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        rescans = deque([['/fake/stats0'], []])

        def fake_rescan(*a, **k):
            if rescans:
                return list(rescans.popleft())
            return []
        sleeps = []
        final_maintenance = {'calls': 0, 'remove_verified_tars_calls': 0}

        def fake_sleep(secs):
            sleeps.append(secs)

        def fake_get_context(name):
            assert name == 'spawn'

            class _Ctx:

                def Pool(self, processes=None, **kwargs):
                    del processes
                    return _FakeIngestPool()
            return _Ctx()
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'sleep_until_shutdown', fake_sleep)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *a, **k: {})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: final_maintenance.__setitem__('calls', final_maintenance['calls'] + 1, raising=False))
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_uncompressed_daily_tars', lambda *a, **k: final_maintenance.__setitem__('remove_verified_tars_calls', final_maintenance['remove_verified_tars_calls'] + 1, raising=False))
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st.multiprocessing, 'get_context', fake_get_context)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        _supervisor_startup_preflight_disabled(monkeypatch)
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), archive_pool)
        finally:
            archive_pool.__exit__(None, None, None)
        assert sleeps == [st.EMPTY_QUEUE_RESCAN_SLEEP_SECONDS]
        assert final_maintenance['calls'] == 0
        assert final_maintenance['remove_verified_tars_calls'] == 0
    finally:
        shutdown_requested[0] = False

def test_supervisor_runs_full_archive_maintenance_before_rescan_when_idle(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    archive_dir = tmp_path / 'archive'
    archive_dir.mkdir()
    try:
        events = []

        def fake_rescan(*_a, **_k):
            events.append('rescan')
            return []
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *a, **k: {})
        monkeypatch.setattr(st, '_count_daily_tars', lambda *_a, **_k: 3)
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: events.append('maintenance', raising=False))
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_uncompressed_daily_tars', lambda *a, **k: events.append('tar_removal', raising=False))
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), archive_pool, run_once=True)
        finally:
            archive_pool.__exit__(None, None, None)
        assert events[0] == 'rescan'
        assert 'maintenance' not in events
        assert 'tar_removal' not in events
        assert events[-1] == 'rescan'
    finally:
        shutdown_requested[0] = False

def test_supervisor_rescans_before_full_maintenance_when_queue_empty(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    archive_dir = tmp_path / 'archive'
    archive_dir.mkdir()
    try:
        events = []
        rescans = deque([['/fake/stats0'], []])

        def fake_rescan(*_a, **_k):
            events.append('rescan')
            if rescans:
                return list(rescans.popleft())
            return []
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *a, **k: {})
        monkeypatch.setattr(st, '_count_daily_tars', lambda *_a, **_k: 0)
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: events.append('maintenance', raising=False))
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_uncompressed_daily_tars', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)

        class _Ctx:

            def Pool(self, processes=None, **kwargs):
                del processes
                return _FakeIngestPool()
        monkeypatch.setattr(st.multiprocessing, 'get_context', lambda _name: _Ctx())
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), archive_pool)
        finally:
            archive_pool.__exit__(None, None, None)
        assert events[:2] == ['rescan', 'rescan']
        assert 'maintenance' not in events
    finally:
        shutdown_requested[0] = False

def test_supervisor_runs_startup_archive_maintenance_when_daily_tars_above_threshold(monkeypatch):
    """Startup no longer blocks on maintenance; janitor enqueue runs before first rescan."""
    shutdown_requested[0] = False
    try:
        events = []
        janitor_signals = {'n': 0}

        def fake_rescan(*_a, **_k):
            events.append('rescan')
            return []
        original_signal = janitor_mod.ArchiveJanitor.signal_work_available

        def counting_signal(self):
            janitor_signals['n'] += 1
            return original_signal(self)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', counting_signal)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *a, **k: {})
        monkeypatch.setattr(st, '_count_daily_tars', lambda *_a, **_k: 4)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
        finally:
            archive_pool.__exit__(None, None, None)
        assert events[0] == 'rescan'
        assert janitor_signals['n'] >= 1
    finally:
        shutdown_requested[0] = False

def test_supervisor_run_once_exits_without_idle_sleep_when_empty(monkeypatch):
    """``run_once=True`` must not call the 300s empty-queue sleep."""
    shutdown_requested[0] = False
    try:

        def fake_rescan(*a, **k):
            return []
        sleeps = []

        def fake_sleep(secs):
            sleeps.append(secs)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'sleep_until_shutdown', fake_sleep)
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
        finally:
            archive_pool.__exit__(None, None, None)
        assert sleeps == []
    finally:
        shutdown_requested[0] = False

def test_supervisor_logs_queue_watermarks(monkeypatch):
    shutdown_requested[0] = False
    try:
        logs = []
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *a, **k: [])
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_queue_max_size', lambda: 10)
        monkeypatch.setattr(st.cfg, 'get_sync_archive_queue_max_size', lambda: 8)
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'log_print', lambda *args, **kwargs: logs.append(' '.join((str(a) for a in args))))
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
        finally:
            archive_pool.__exit__(None, None, None)
    finally:
        shutdown_requested[0] = False
    assert any(('Queue watermarks ingest' in line for line in logs))

def test_supervisor_logs_completed_file_with_global_remaining(monkeypatch):
    """Successful ingest logs path, elapsed, and backlog remaining (not chunk index)."""
    shutdown_requested[0] = False
    try:
        paths = ['/tmp/sync-a', '/tmp/sync-b']
        calls = {'n': 0}
        logs = []

        def fake_rescan(*_a, **_k):
            if calls['n'] == 0:
                calls['n'] += 1
                return list(paths)
            return []
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, p, _c=None: (p, True, True, 0.05))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'log_print', lambda *args, **kwargs: logs.append(' '.join((str(a) for a in args))))
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
        finally:
            archive_pool.__exit__(None, None, None)
        completed_lines = [ln for ln in logs if "ingest file path=" in ln]
        assert len(completed_lines) == 2
        for ln in completed_lines:
            assert "outcome=" in ln
            assert "elapsed_s=" in ln
            assert "remaining=" in ln
        assert any(("remaining=1" in ln for ln in completed_lines))
        assert any(("remaining=0" in ln for ln in completed_lines))
        assert not any(("Completed file " in ln for ln in logs))
        assert not any(("ingest file completed" in ln for ln in logs))
        assert not any(('chunk ' in ln and 'completed file' in ln for ln in logs))
    finally:
        shutdown_requested[0] = False

def test_periodic_maintenance_runs_with_backlog_and_logs_context(monkeypatch):
    """Ingest backlog does not run removed interval accrual at chunk boundaries."""
    shutdown_requested[0] = False
    try:
        calls = {'n': 0}
        logs = []

        def fake_rescan(*_a, **_k):
            if calls['n'] == 0:
                calls['n'] += 1
                return ['/tmp/stats-a', '/tmp/stats-b', '/tmp/stats-c', '/tmp/stats-d']
            return []

        class _Clock:

            def __init__(self):
                self.t = 0.0

            def __call__(self):
                self.t += 0.25
                return self.t
        clock = _Clock()
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (_a[1], True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'chunk_size', 1)
        monkeypatch.setattr('hpcperfstats.dbload.sync_timedb.time.time', clock)
        monkeypatch.setattr(st, 'log_print', lambda *args, **kwargs: logs.append(' '.join((str(a) for a in args))))
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
        finally:
            archive_pool.__exit__(None, None, None)
        assert not any(('Archive debt accrual deferred' in line for line in logs))
        assert not any(('Archive janitor accrue reason=' in line for line in logs))
    finally:
        shutdown_requested[0] = False

def test_checkpoint_round_trip_persists_completed_entries(tmp_path):
    """Completed file metadata should survive restart round-trip."""
    state_path = Path(tmp_path) / 'sync_timedb_state.json'
    completed = [{'path': '/a/1', 'size': 10, 'mtime': 1000}, {'path': '/b/2', 'size': 20, 'mtime': 2000}]
    st._save_sync_checkpoint(state_path, completed)
    loaded = st._load_sync_checkpoint(state_path)
    assert loaded == completed

def test_checkpoint_load_ignores_invalid_shape(tmp_path):
    """Corrupt checkpoint content should not crash startup."""
    state_path = Path(tmp_path) / 'sync_timedb_state.json'
    state_path.write_text(json.dumps({'bad': 'shape'}))
    loaded = st._load_sync_checkpoint(state_path)
    assert loaded == []

def test_failed_ingest_is_not_marked_processed(monkeypatch):
    """Failed ingest must remain eligible on the next rescan."""
    shutdown_requested[0] = False
    try:
        path = '/fake/stats0'
        seen_processed = []
        call_count = {'n': 0}

        def fake_rescan(_directory, _start, _end, _ext, processed_files, **_kwargs):
            call_count['n'] += 1
            seen_processed.append(set(processed_files))
            if call_count['n'] <= 2:
                return [path]
            return []
        sleeps = []

        def fake_sleep(secs):
            sleeps.append(secs)
            shutdown_requested[0] = True

        def fake_get_context(name):
            assert name == 'spawn'

            class _Ctx:

                def Pool(self, processes=None, **kwargs):
                    del processes
                    return _FakeFailedIngestPool()
            return _Ctx()
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'sleep_until_shutdown', fake_sleep)
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *a, **k: {})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st.multiprocessing, 'get_context', fake_get_context)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool)
        finally:
            archive_pool.__exit__(None, None, None)
        assert path not in seen_processed[1]
    finally:
        shutdown_requested[0] = False

def test_checkpoint_flush_is_coalesced(monkeypatch, tmp_path):
    """Checkpoint writes should be coalesced, not rewritten per-file."""
    shutdown_requested[0] = False
    try:
        stats_files = ['/fake/stats0', '/fake/stats1', '/fake/stats2']

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return list(stats_files)
            return []
        fake_rescan.calls = 0

        def fake_get_context(name):
            assert name == 'spawn'

            class _Ctx:

                def Pool(self, processes=None, **kwargs):
                    del processes
                    return _FakeIngestPool()
            return _Ctx()
        writes = {'count': 0}

        def fake_save(_path, _entries):
            writes['count'] += 1
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, '_save_sync_checkpoint', fake_save)
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *a, **k: {})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st.multiprocessing, 'get_context', fake_get_context)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop(str(tmp_path), 'all', None, '.hpc', object(), archive_pool, run_once=True)
        finally:
            archive_pool.__exit__(None, None, None)
        assert writes['count'] == 1
    finally:
        shutdown_requested[0] = False

def test_rescan_excludes_inflight_archive_paths(monkeypatch):
    """Files waiting on archive completion should not be rediscovered on rescan."""
    shutdown_requested[0] = False
    try:
        target = '/fake/statsA'
        seen_processed = []
        call_count = {'n': 0}

        def fake_rescan(_directory, _start, _end, _ext, processed_files, **_kwargs):
            call_count['n'] += 1
            seen_processed.append(set(processed_files))
            if call_count['n'] == 1:
                return [target]
            shutdown_requested[0] = True
            return []

        def fake_add(_lock, path, _contents=None):
            return (path, True, True, 0.0)

        class _NeverDone:

            def ready(self):
                return False

            def get(self, timeout=None):
                del timeout
                return None

        class _ArchivePool:

            def map_async(self, _fn, _items):
                return _NeverDone()
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {'/tmp/day.tar.gz': [target]})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'chunk_size', 1)
        monkeypatch.setattr(st, 'rescan_every_chunks', 1)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), _ArchivePool())
        assert target in seen_processed[1]
    finally:
        shutdown_requested[0] = False

def test_pick_write_lock_for_path_uses_stable_sharding():
    locks = [object(), object(), object()]
    selected_a = st._pick_write_lock_for_path(locks, '/tmp/a')
    selected_a_again = st._pick_write_lock_for_path(locks, '/tmp/a')
    selected_b = st._pick_write_lock_for_path(locks, '/tmp/b')
    assert selected_a is selected_a_again
    assert selected_a in locks
    assert selected_b in locks

def test_head_timestamp_cache_reuses_recent_lookup(monkeypatch):
    import hpcperfstats.dbload.lib.sync_timedb_ingest_readiness as readiness
    calls = {'n': 0}

    class _QS:

        def exists(self):
            calls['n'] += 1
            return True

    class _Mgr:

        def filter(self, **_kwargs):
            return _QS()
    monkeypatch.setattr(st.host_data, 'objects', _Mgr())
    readiness.reset_sync_ingest_readiness_caches()
    ts = st.datetime.now(st.timezone.utc)
    assert readiness.head_timestamp_present_in_db('h1', ts)
    assert readiness.head_timestamp_present_in_db('h1', ts)
    assert calls['n'] == 1

def test_archive_retry_backoff_requeues_failed_archive(monkeypatch):
    shutdown_requested[0] = False
    try:
        target = '/fake/stats-retry'

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            shutdown_requested[0] = True
            return []
        fake_rescan.calls = 0
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {'/tmp/day.tar.gz': [target]})
        monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_max_attempts', lambda: 2)
        monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_backoff_base_seconds', lambda: 0.0)
        monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_backoff_max_seconds', lambda: 0.0)
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        archive_pool = _FakeArchivePoolRetry()
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
        assert archive_pool.calls >= 2
    finally:
        shutdown_requested[0] = False

def test_retry_queue_dispatch_uses_retry_at_order_not_insertion(monkeypatch):
    """Archive retries should dispatch due items by earliest retry_at."""
    shutdown_requested[0] = False
    try:
        future_entry = {'task': st.ArchiveTask(archive_info=('/tmp/day-future.tar.gz', ['/tmp/future']), attempt=1), 'paths': ['/tmp/future'], 'retry_at': 10000.0}
        due_entry = {'task': st.ArchiveTask(archive_info=('/tmp/day-due.tar.gz', ['/tmp/due']), attempt=1), 'paths': ['/tmp/due'], 'retry_at': 0.0}
        monkeypatch.setattr(st, '_load_dead_letter_entries', lambda *_a, **_k: [future_entry, due_entry])
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st.time, 'time', lambda: 1.0)
        dispatched = []

        class _ArchivePoolOrder:

            def map_async(self, _fn, items):
                dispatched.append(list(items))
                return _fake_map_async_result([True for _ in items])
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), _ArchivePoolOrder(), run_once=True)
        assert dispatched
        first_dispatch = dispatched[0]
        assert first_dispatch == [('/tmp/day-due.tar.gz', ['/tmp/due'])]
    finally:
        shutdown_requested[0] = False

def test_nonblocking_finalize_queues_new_archive_work_when_busy(monkeypatch, tmp_path):
    """When archive job is not ready, new archive work should queue, not overwrite."""
    shutdown_requested[0] = False
    archive_dir = tmp_path / 'archive'
    archive_dir.mkdir()
    try:
        targets = ['/tmp/a', '/tmp/b']
        logs = []

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return list(targets)
            return []
        fake_rescan.calls = 0
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (_a[1], True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'chunk_size', 1)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda files, *_a, **_k: {'/tmp/%s.tar.gz' % files[0].split('/')[-1]: [files[0]]})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st.time, 'time', lambda: 1.0)
        monkeypatch.setattr(st, 'log_print', lambda *args, **kwargs: logs.append(' '.join((str(a) for a in args))))
        monkeypatch.setattr(st, 'async_result_get_watch_pool', lambda *_a, **_k: [True])
        dispatched = []

        class _ArchiveResult:

            def __init__(self, ready_initially):
                self._ready = ready_initially

            def ready(self):
                return self._ready

            def wait(self, _timeout):
                return None

            def get(self):
                self._ready = True
                return [True]

        class _ArchivePoolBusy:

            def __init__(self):
                self.calls = 0

            def map_async(self, _fn, items):
                self.calls += 1
                dispatched.append(list(items))
                if self.calls == 1:
                    return _ArchiveResult(False)
                return _ArchiveResult(True)
        archive_pool = _ArchivePoolBusy()
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), archive_pool, run_once=True)
        assert archive_pool.calls == 1
        assert len(dispatched) == 1
        assert dispatched[0][0][0].endswith('a.tar.gz')
    finally:
        shutdown_requested[0] = False

def test_archive_dispatch_by_tgz_groups_respects_archive_queue_max(monkeypatch):
    shutdown_requested[0] = False
    try:
        target = '/tmp/stats-q'

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            return []
        fake_rescan.calls = 0
        dispatched = []
        logs = []

        class _ArchivePoolCapture:

            def map_async(self, _fn, items):
                dispatched.append(list(items))
                return _fake_map_async_result([True for _ in items])
        mapping = {'/tmp/2026-03-03.tar.gz': ['/tmp/c'], '/tmp/2026-03-01.tar.gz': ['/tmp/a'], '/tmp/2026-03-02.tar.gz': ['/tmp/b']}
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_archive_queue_max_size', lambda: 2)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: mapping)
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'log_print', lambda *args, **kwargs: logs.append(' '.join((str(a) for a in args))))
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), _ArchivePoolCapture(), run_once=True)
        assert dispatched
        first_batch = dispatched[0]
        assert len(first_batch) == 2
        assert first_batch[0][0] == '/tmp/2026-03-01.tar.gz'
        assert first_batch[1][0] == '/tmp/2026-03-02.tar.gz'
        assert any(('Archive dispatch submitted=2 queued=1 inflight_slots=1' in ln for ln in logs))
    finally:
        shutdown_requested[0] = False

def test_periodic_maintenance_logs_deferred_when_archive_finalize_pending(monkeypatch, tmp_path):
    """Finalize stays soft-deferred; janitor replaces blocking supervisor maintenance."""
    shutdown_requested[0] = False
    archive_dir = tmp_path / 'archive'
    archive_dir.mkdir()
    try:
        _supervisor_startup_preflight_disabled(monkeypatch)
        logs = []
        clock = {'t': 2000.0}
        pending = ['/tmp/stats-a', '/tmp/stats-b', '/tmp/stats-c']
        archive_dispatched = [False]

        def fake_time():
            if not archive_dispatched[0]:
                return clock['t']
            clock['t'] += 0.25
            return clock['t']

        def fake_rescan(*_a, **_k):
            return list(pending)

        class _NeverReady:

            def ready(self):
                return False

            def wait(self, _timeout):
                return None

            def get(self):
                return [True]

        class _ArchivePoolNeverReady:

            def map_async(self, _fn, _items):
                return _NeverReady()
        log_lines = {'n': 0}

        def log_print_capture(*args, **kwargs):
            line = ' '.join((str(a) for a in args))
            logs.append(line)
            log_lines['n'] += 1
            if 'Archive dispatch submitted=' in line:
                archive_dispatched[0] = True
            if 'Archive finalize deferred' in line:
                shutdown_requested[0] = True
            if log_lines['n'] > 120:
                shutdown_requested[0] = True
        monkeypatch.setattr(st.time, 'time', fake_time)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (_a[1], True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda files, *_a, **_k: {'/tmp/day.tar.gz': list(files[:1])})
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'log_print', log_print_capture)
        monkeypatch.setattr(st, 'async_result_get_watch_pool', lambda *_a, **_k: [True])
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda _s: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _ArchivePoolNeverReady(), run_once=True)
        assert any(('Archive finalize deferred' in line for line in logs))
        assert not any(('Archive maintenance due but deferred' in line for line in logs))
        assert not any(('forced two-phase archive maintenance' in line for line in logs))
    finally:
        shutdown_requested[0] = False

def test_periodic_maintenance_runs_forced_two_phase_when_defer_cap_exceeded(monkeypatch, tmp_path):
    """Forced two-phase supervisor maintenance is retired; janitor signals continue."""
    shutdown_requested[0] = False
    archive_dir = tmp_path / 'archive'
    archive_dir.mkdir()
    try:
        _supervisor_startup_preflight_disabled(monkeypatch)
        logs = []
        janitor_signals = {'n': 0}
        original_signal = janitor_mod.ArchiveJanitor.signal_work_available

        def counting_signal(self):
            janitor_signals['n'] += 1
            return original_signal(self)
        rescan_calls = {'n': 0}

        def fake_rescan(*_a, **_k):
            if rescan_calls['n'] == 0:
                rescan_calls['n'] += 1
                return ['/tmp/stats-a']
            return []
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', counting_signal)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (_a[1], True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda files, *_a, **_k: {'/tmp/day.tar.zst': list(files[:1])})
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'log_print', lambda *args, **kwargs: logs.append(' '.join((str(a) for a in args))))
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda _s: None)

        class _ReadyArchivePool:

            def map_async(self, _fn, _items):

                class _R:

                    def ready(self):
                        return True

                    def get(self):
                        return [True]
                return _R()
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _ReadyArchivePool(), run_once=True)
        assert janitor_signals['n'] >= 1
        assert not any(('forced two-phase archive maintenance' in line for line in logs))
    finally:
        shutdown_requested[0] = False

def test_continuous_backlog_triggers_forced_maintenance(monkeypatch, tmp_path):
    """Continuous ingest backlog still signals the janitor without interval accrual."""
    shutdown_requested[0] = False
    archive_dir = tmp_path / 'archive'
    archive_dir.mkdir()
    try:
        logs = []
        backlog = ['/tmp/stats-a', '/tmp/stats-b', '/tmp/stats-c']

        def fake_rescan(*_a, **_k):
            return list(backlog)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (_a[1], True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda files, *_a, **_k: {'/tmp/day.tar.zst': list(files[:1])})
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')

        def log_print_capture(*args, **kwargs):
            line = ' '.join((str(a) for a in args))
            logs.append(line)
            if 'sync_timedb: maintenance pass reason=startup' in line:
                shutdown_requested[0] = True
        monkeypatch.setattr(st, 'log_print', log_print_capture)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda _s: None)

        class _NeverReady:

            def ready(self):
                return False

            def get(self):
                return [True]

        class _ArchivePoolNeverReady:

            def map_async(self, _fn, _items):
                return _NeverReady()
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _ArchivePoolNeverReady(), run_once=False)
        assert any(('sync_timedb: maintenance pass reason=startup' in line for line in logs))
        assert not any(('Archive debt accrual deferred' in line for line in logs))
        assert not any(('Archive janitor accrue reason=' in line for line in logs))
        assert not any(('forced two-phase archive maintenance' in line for line in logs))
    finally:
        shutdown_requested[0] = False

def test_transition_file_state_rejects_invalid_transition():
    file_states = {}
    assert st._transition_file_state(file_states, '/tmp/x', st.SyncFileState.DISCOVERED)
    assert not st._transition_file_state(file_states, '/tmp/x', st.SyncFileState.ARCHIVED)

def test_transition_archive_queued_to_written_allowed():
    path = '/tmp/archive-queued-reingest'
    file_states = {path: st.SyncFileState.ARCHIVE_QUEUED}
    assert st._transition_file_state(file_states, path, st.SyncFileState.WRITTEN)
    assert file_states[path] == st.SyncFileState.WRITTEN

def test_transition_archived_to_written_allowed_for_reingest():
    path = '/tmp/archived-db-complete-reingest'
    file_states = {path: st.SyncFileState.ARCHIVED}
    assert st._transition_file_state(file_states, path, st.SyncFileState.WRITTEN)
    assert file_states[path] == st.SyncFileState.WRITTEN

def test_transition_archived_to_archive_queued_is_idempotent():
    path = '/tmp/archived-replay-dispatch'
    file_states = {path: st.SyncFileState.ARCHIVED}
    assert st._transition_file_state(file_states, path, st.SyncFileState.ARCHIVE_QUEUED)
    assert file_states[path] == st.SyncFileState.ARCHIVED

def test_transition_still_rejects_discovered_to_archived():
    path = '/tmp/invalid-skip-to-archived'
    file_states = {path: st.SyncFileState.DISCOVERED}
    assert not st._transition_file_state(file_states, path, st.SyncFileState.ARCHIVED)
    assert file_states[path] == st.SyncFileState.DISCOVERED

def test_dead_letter_round_trip(tmp_path):
    dead_letter = tmp_path / '.sync_timedb_dead_letter.json'
    entries = [{'task': st.ArchiveTask(archive_info=('/tmp/day.tar.gz', ['/tmp/a']), attempt=3), 'paths': ['/tmp/a'], 'retry_at': 0.0}]
    st._save_dead_letter_entries(str(dead_letter), entries)
    loaded = st._load_dead_letter_entries(str(dead_letter))
    assert len(loaded) == 1
    assert loaded[0]['task'].archive_info[0].endswith('.tar.gz')
    assert loaded[0]['paths'] == ['/tmp/a']

def test_dead_letter_replay_runs_before_idle_sleep(monkeypatch):
    shutdown_requested[0] = False
    try:
        monkeypatch.setattr(st, '_load_dead_letter_entries', lambda *_a, **_k: [{'task': st.ArchiveTask(archive_info=('/tmp/day.tar.gz', ['/tmp/a']), attempt=3), 'paths': ['/tmp/a'], 'retry_at': 0.0}])
        monkeypatch.setattr(st, '_save_dead_letter_entries', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')

        class _ArchivePoolReplay:

            def __init__(self):
                self.calls = 0

            def map_async(self, _fn, _items):
                self.calls += 1
                return _fake_map_async_result([True])
        ap = _ArchivePoolReplay()
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), ap, run_once=True)
        assert ap.calls >= 1
    finally:
        shutdown_requested[0] = False

def test_parse_payload_marks_new_head_as_archival(monkeypatch):
    target = '/tmp/stats-new-head'
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st, 'parse_stats_file_path', lambda _p: ('h1', '123'))
    monkeypatch.setattr(st, 'stats_file_is_active_segment', lambda _p: False)
    monkeypatch.setattr(st, 'load_stats_file_lines', lambda *_a, **_k: (['100 job1 h1\n'], None))
    monkeypatch.setattr(st, 'parse_first_timestamp_line', lambda _lines: ('100', 'job1', 'h1'))
    monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: False)
    monkeypatch.setattr(st, 'parse_stats_lines', lambda *_a, **_k: ([{'k': 1}], []))
    stats_df = pd.DataFrame([{'host': 'h1', 'type': 'cpu', 'dev': '0', 'event': 'user', 'unit': '#', 'time': 100.0, 'value': 1.0, 'wid': 48, 'mult': 1}])
    proc_df = pd.DataFrame(columns=['jid', 'host', 'proc'])
    monkeypatch.setattr(st, 'build_stats_dataframes', lambda *_a, **_k: (stats_df, proc_df))
    monkeypatch.setattr(st, 'compute_deltas_and_arc', lambda s: s)
    stats_file, payload, need_archival, ingest_ok, parse_elapsed_s, _outcome_meta = st._unpack_parse_payload_result(st._parse_stats_file_payload(target))
    assert stats_file == target
    assert payload[0] is stats_df
    assert payload[1] is proc_df
    assert need_archival is True
    assert ingest_ok is True
    assert parse_elapsed_s >= 0.0

def test_parse_payload_marks_fully_duplicate_file_for_archival(monkeypatch):
    target = '/tmp/stats-dup'
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st, 'parse_stats_file_path', lambda _p: ('h1', '123'))
    monkeypatch.setattr(st, 'stats_file_is_active_segment', lambda _p: False)
    monkeypatch.setattr(st, 'load_stats_file_lines', lambda *_a, **_k: (['100 job1 h1\n'], None))
    monkeypatch.setattr(st, 'parse_first_timestamp_line', lambda _lines: ('100', 'job1', 'h1'))
    monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: True)
    monkeypatch.setattr(st.host_data, 'objects', type('_Mgr', (), {'filter': staticmethod(lambda **_k: type('_QS', (), {'values_list': staticmethod(lambda *a, **k: type('_V', (), {'distinct': staticmethod(lambda: type('_I', (), {'iterator': staticmethod(lambda: iter([]))})())})())})())})())
    monkeypatch.setattr(st, 'find_processing_start_index', lambda *_a, **_k: (-1, True))
    monkeypatch.setattr(st, 'raw_stats_path_tar_append_decision', lambda *_a, **_k: (True, ''))
    stats_file, payload, need_archival, ingest_ok, parse_elapsed_s, _outcome_meta = st._unpack_parse_payload_result(st._parse_stats_file_payload(target))
    assert stats_file == target
    assert payload is None
    assert need_archival is True
    assert ingest_ok is True
    assert parse_elapsed_s >= 0.0

def test_parse_stats_file_payload_need_archival_false_on_day_skip(monkeypatch):
    target = '/tmp/stats-day-skip'
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st, 'parse_stats_file_path', lambda _p: ('h1', '123'))
    monkeypatch.setattr(st, 'stats_file_is_active_segment', lambda _p: False)
    monkeypatch.setattr(st, 'load_stats_file_lines', lambda *_a, **_k: (['100 job1 h1\n'], None))
    monkeypatch.setattr(st, 'parse_first_timestamp_line', lambda _lines: ('100', 'job1', 'h1'))
    monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: True)
    monkeypatch.setattr(st.host_data, 'objects', type('_Mgr', (), {'filter': staticmethod(lambda **_k: type('_QS', (), {'values_list': staticmethod(lambda *a, **k: type('_V', (), {'distinct': staticmethod(lambda: type('_I', (), {'iterator': staticmethod(lambda: iter([]))})())})())})())})())
    monkeypatch.setattr(st, 'find_processing_start_index', lambda *_a, **_k: (-1, True))
    monkeypatch.setattr(
        st, 'raw_stats_path_tar_append_decision', lambda *_a, **_k: (False, 'member_exists'),
    )
    stats_file, payload, need_archival, ingest_ok, parse_elapsed_s, _outcome_meta = st._unpack_parse_payload_result(st._parse_stats_file_payload(target))
    assert stats_file == target
    assert payload is None
    assert need_archival is False
    assert ingest_ok is True
    assert parse_elapsed_s >= 0.0

def test_sync_timedb_exits_on_redis_unavailable_during_ingest(monkeypatch):
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import ArchiveMembersRedisUnavailableError
    shutdown_requested[0] = False
    try:
        target = '/fake/stats-redis-fatal'

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            return []
        fake_rescan.calls = 0

        def fake_combined(_lock, path, stats_file_contents=None):
            del _lock, stats_file_contents
            raise ArchiveMembersRedisUnavailableError('redis down mid-ingest')
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, '_ingest_parse_and_write_file', fake_combined)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1000)
        monkeypatch.setattr(st.cfg, 'get_sync_supervisor_rss_limit_mb', lambda: 0)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr('hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.verify_archive_members_redis_startup', lambda: None)
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            with pytest.raises(SystemExit) as excinfo:
                st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
            assert excinfo.value.code == 1
        finally:
            archive_pool.__exit__(None, None, None)
    finally:
        shutdown_requested[0] = False

def test_exit_handler_distinguishes_stall_from_connection(monkeypatch, capsys):
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import ArchiveMembersPopulateStalledError, ArchiveMembersRedisConnectionError
    monkeypatch.setattr(st.cfg, 'get_redis_location', lambda: 'redis://redis:6379/1')
    with pytest.raises(SystemExit) as excinfo:
        st._exit_on_archive_members_redis_unavailable(ArchiveMembersRedisConnectionError('Redis unreachable'))
    assert excinfo.value.code == 1
    connection_out = capsys.readouterr().out
    assert 'requires a reachable Redis' in connection_out
    assert 'populate stalled or timed out' not in connection_out
    fake_client = type('_FakeRedisClient', (), {'ping': lambda self: True})()
    monkeypatch.setattr('hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.get_archive_members_redis_client', lambda required=True: fake_client)
    with pytest.raises(SystemExit) as excinfo:
        st._exit_on_archive_members_redis_unavailable(ArchiveMembersPopulateStalledError('Archive members populate stalled (no progress for 120s): hash'))
    assert excinfo.value.code == 1
    stall_out = capsys.readouterr().out
    assert 'populate stalled or timed out' in stall_out
    assert 'is reachable' in stall_out
    assert 'requires a reachable Redis' not in stall_out

def test_chunk_prewarm_populates_redis_before_imap(monkeypatch, tmp_path):
    tgz_dir = tmp_path / 'daily'
    tgz_dir.mkdir()
    sealed = tgz_dir / '2026-05-20.tar.zst'
    sealed.write_bytes(b'zst')
    paths = ['/archive/host.hpc/1779274402', '/archive/host.hpc/1779274235']
    calls = []
    monkeypatch.setattr(st, 'archive_members_redis_enabled', lambda: True)
    monkeypatch.setattr(st, 'tgz_archive_dir', str(tgz_dir))
    monkeypatch.setattr(st, 'redis_members_cache_is_fully_warm', lambda keys: False)
    monkeypatch.setattr(st, 'get_existing_archive_members_for_daily_archive', lambda canonical: calls.append(canonical) or {'m': 1})
    st._prewarm_archive_members_redis_for_chunk(paths)
    assert len(calls) == 1

def test_chunk_prewarm_logs_begin_and_complete(monkeypatch, tmp_path, capsys):
    tgz_dir = tmp_path / 'daily'
    tgz_dir.mkdir()
    sealed = tgz_dir / '2026-05-20.tar.zst'
    sealed.write_bytes(b'zst')
    paths = ['/archive/host.hpc/1779274402']
    monkeypatch.setattr(st, 'archive_members_redis_enabled', lambda: True)
    monkeypatch.setattr(st, 'tgz_archive_dir', str(tgz_dir))
    monkeypatch.setattr(st, 'redis_members_cache_is_fully_warm', lambda keys: False)
    monkeypatch.setattr(st, 'get_existing_archive_members_for_daily_archive', lambda canonical: {'m': 1})
    st._prewarm_archive_members_redis_for_chunk(paths)
    out = capsys.readouterr().out
    begin_at = out.find('sync_timedb: chunk prewarm begin')
    complete_at = out.find('sync_timedb: chunk prewarm complete')
    assert begin_at >= 0
    assert complete_at > begin_at
    assert "days=['2026-05-20']" in out
    assert 'elapsed_s=' in out

def test_chunk_prewarm_includes_oldest_tar_day(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    tgz_dir = tmp_path / 'daily'
    tgz_dir.mkdir()
    (tgz_dir / '2026-05-31.tar.zst').write_bytes(b'zst')
    (tgz_dir / '2026-06-01.tar.zst').write_bytes(b'zst')
    june_ts = int(datetime(2026, 6, 1, 12, tzinfo=timezone.utc).timestamp())
    paths = ['/archive/host.hpc/%d' % june_ts]
    oldest_tar = str(tgz_dir / '2026-05-31.tar')
    calls = []
    monkeypatch.setattr(st, 'archive_members_redis_enabled', lambda: True)
    monkeypatch.setattr(st, 'tgz_archive_dir', str(tgz_dir))
    monkeypatch.setattr(st, 'redis_members_cache_is_fully_warm', lambda keys: False)
    monkeypatch.setattr(st, 'get_existing_archive_members_for_daily_archive', lambda canonical: calls.append(canonical) or {'m': 1})
    st._prewarm_archive_members_redis_for_chunk(paths, oldest_tar=oldest_tar)
    assert len(calls) == 2
    basenames = {os.path.basename(c) for c in calls}
    assert basenames == {'2026-05-31.tar.zst', '2026-06-01.tar.zst'}

def test_prewarm_gated_tar_restore_before_sealed_populate(monkeypatch, tmp_path, capsys):
    tgz_dir = tmp_path / 'daily'
    tgz_dir.mkdir()
    zst = tgz_dir / '2026-05-31.tar.zst'
    zst.write_bytes(b'zst')
    tar = tgz_dir / '2026-05-31.tar'
    restore_calls = []
    monkeypatch.setattr(st, 'archive_members_redis_enabled', lambda: True)
    monkeypatch.setattr(st, 'tgz_archive_dir', str(tgz_dir))
    monkeypatch.setattr(st, 'redis_members_cache_is_fully_warm', lambda keys: False)
    monkeypatch.setattr(st, 'ensure_daily_tar_restored_for_append', lambda tar_path, threads: restore_calls.append(tar_path) or True)
    monkeypatch.setattr(st, 'get_existing_archive_members_for_daily_archive', lambda canonical: {'m': 1})
    monkeypatch.setattr(st.cfg, 'get_archive_zstd_threads', lambda: 1)
    st._prewarm_archive_members_redis_for_days([(str(zst), '2026-05-31')], gated_tar_restore_day_tokens={'2026-05-31'})
    assert restore_calls == [str(tar)]
    assert 'populate_prewarm restored tar' in capsys.readouterr().out

def test_prewarm_runs_when_redis_complete_with_empty_hash(monkeypatch, tmp_path, capsys):
    tgz_dir = tmp_path / 'daily'
    tgz_dir.mkdir()
    sealed = tgz_dir / '2026-05-20.tar.zst'
    sealed.write_bytes(b'zst')
    paths = ['/archive/host.hpc/1779274402']
    calls = []
    monkeypatch.setattr(st, 'archive_members_redis_enabled', lambda: True)
    monkeypatch.setattr(st, 'tgz_archive_dir', str(tgz_dir))
    monkeypatch.setattr(st, 'redis_members_cache_is_fully_warm', lambda keys: False)
    monkeypatch.setattr(st, 'get_existing_archive_members_for_daily_archive', lambda canonical: calls.append(canonical) or {'host/a': 1})
    st._prewarm_archive_members_redis_for_chunk(paths)
    assert len(calls) == 1
    assert 'Prewarming archive members Redis' in capsys.readouterr().out

def test_prewarm_skips_when_redis_fully_warm(monkeypatch, tmp_path, capsys):
    tgz_dir = tmp_path / 'daily'
    tgz_dir.mkdir()
    (tgz_dir / '2026-05-20.tar.zst').write_bytes(b'zst')
    paths = ['/archive/host.hpc/1779274402']
    monkeypatch.setattr(st, 'archive_members_redis_enabled', lambda: True)
    monkeypatch.setattr(st, 'tgz_archive_dir', str(tgz_dir))
    monkeypatch.setattr(st, 'redis_members_cache_is_fully_warm', lambda keys: True)
    monkeypatch.setattr(st, 'get_existing_archive_members_for_daily_archive', lambda canonical: (_ for _ in ()).throw(AssertionError('should not populate')))
    st._prewarm_archive_members_redis_for_chunk(paths)
    assert ':redis_warm' in capsys.readouterr().out

def test_invalidation_hook_can_trigger_reprewarm(monkeypatch, tmp_path):
    prewarm_days = []
    monkeypatch.setattr(st, '_prewarm_archive_members_redis_for_day_token', lambda day_token: prewarm_days.append(day_token))

    def _hook(_canonical, day_token):
        if day_token:
            st._prewarm_archive_members_redis_for_day_token(day_token)
    archive_helpers.set_archive_members_invalidation_hook(_hook)
    try:
        zst = tmp_path / '2026-05-20.tar.zst'
        zst.write_bytes(b'sealed')
        archive_helpers.invalidate_daily_archive_members_cache(str(zst))
        assert prewarm_days == ['2026-05-20']
    finally:
        archive_helpers.reset_archive_members_invalidation_hook_for_tests()

def test_seal_reprewarm_skipped_when_verify_populates(monkeypatch, capsys):
    prewarm_days = []

    class _FakeDayRawRemoval:
        enabled = True
    st._reprewarm_archive_members_after_seal_phase('2026-05-21', day_raw_removal=_FakeDayRawRemoval(), prewarm_fn=lambda day_token: prewarm_days.append(day_token), log_fn=st.log_print)
    assert prewarm_days == []
    assert 'reason=verify_will_populate' in capsys.readouterr().out
    st._reprewarm_archive_members_after_seal_phase('2026-05-21', day_raw_removal=None, prewarm_fn=lambda day_token: prewarm_days.append(day_token), log_fn=st.log_print)
    assert prewarm_days == ['2026-05-21']

def _reraise_pool_worker_fatal(exc, **kwargs):
    del kwargs
    raise exc

def _parse_payload_quarantine_fixture(monkeypatch, tmp_path, *, lines, parse_side_effect=None):
    archive_dir = tmp_path / 'archive'
    host_dir = archive_dir / 'host.hpc'
    host_dir.mkdir(parents=True)
    raw_path = host_dir / '1778200758'
    raw_path.write_text(''.join(lines), encoding='utf-8')
    target = str(raw_path)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.cfg, 'get_archive_dir_path', lambda: str(archive_dir))
    monkeypatch.setattr(st, 'parse_stats_file_path', lambda _p: ('host.hpc', '1778200758'))
    monkeypatch.setattr(st, 'stats_file_is_active_segment', lambda _p: False)
    monkeypatch.setattr(st, 'load_stats_file_lines', lambda *_a, **_k: (list(lines), None))
    monkeypatch.setattr(st, 'parse_first_timestamp_line', lambda _lines: ('1778200758', 'job1', 'cn001'))
    monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: False)
    if parse_side_effect is not None:
        monkeypatch.setattr(st, 'parse_stats_lines', parse_side_effect)
    return (target, archive_dir, raw_path)

def test_parse_payload_quarantines_on_parse_exception(monkeypatch, tmp_path):
    lines = ['1778200758 job1 cn001\n', 'bad\n']
    target, archive_dir, raw_path = _parse_payload_quarantine_fixture(monkeypatch, tmp_path, lines=lines, parse_side_effect=lambda *_a, **_k: (_ for _ in ()).throw(ValueError('not enough values to unpack (expected 3, got 2)')))
    stats_file, payload, need_archival, ingest_ok, parse_elapsed_s, _outcome_meta = st._unpack_parse_payload_result(st._parse_stats_file_payload(target))
    assert stats_file == target
    assert payload is None
    assert need_archival is False
    assert ingest_ok is True
    assert parse_elapsed_s >= 0.0
    assert not raw_path.exists()
    quarantine_path = archive_dir / archive_helpers.SYNC_TIMEDB_UNPARSABLE_RAW_DIRNAME / 'host.hpc' / '1778200758'
    assert quarantine_path.is_file()

@pytest.mark.parametrize('failure_kind,lines,load_err,first_ts,host,empty_df', [('load_err', [], 'read failed', None, None, False), ('no_timestamp', ['not-a-stats-line\n'], None, None, None, False), ('no_host', ['1778200758 job1\n'], None, '1778200758', '', False), ('empty_df', ['1778200758 job1 cn001\n'], None, '1778200758', 'cn001', True)])
def test_parse_payload_quarantines_permanent_failures(monkeypatch, tmp_path, failure_kind, lines, load_err, first_ts, host, empty_df):
    _ = empty_df
    archive_dir = tmp_path / 'archive'
    host_dir = archive_dir / 'host.hpc'
    host_dir.mkdir(parents=True)
    raw_path = host_dir / 'bad_raw'
    raw_path.write_text(''.join(lines) if lines else 'x', encoding='utf-8')
    target = str(raw_path)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.cfg, 'get_archive_dir_path', lambda: str(archive_dir))
    monkeypatch.setattr(st, 'parse_stats_file_path', lambda _p: ('host.hpc', 'bad_raw'))
    monkeypatch.setattr(st, 'stats_file_is_active_segment', lambda _p: False)
    monkeypatch.setattr(st, 'load_stats_file_lines', lambda *_a, **_k: (list(lines), load_err))
    if first_ts is None and host is None:
        monkeypatch.setattr(st, 'parse_first_timestamp_line', lambda _lines: (None, None, None))
    elif host == '':
        monkeypatch.setattr(st, 'parse_first_timestamp_line', lambda _lines: (first_ts, 'job1', ''))
    else:
        monkeypatch.setattr(st, 'parse_first_timestamp_line', lambda _lines: (first_ts, 'job1', host))
        monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: False)
        monkeypatch.setattr(st, 'parse_stats_lines', lambda *_a, **_k: ([], []))
        empty_stats = pd.DataFrame()
        empty_proc = pd.DataFrame(columns=['jid', 'host', 'proc'])
        monkeypatch.setattr(st, 'build_stats_dataframes', lambda *_a, **_k: (empty_stats, empty_proc))
        monkeypatch.setattr(st, 'compute_deltas_and_arc', lambda s: s)
    stats_file, payload, need_archival, ingest_ok, _elapsed = st._parse_stats_file_payload(target)
    assert ingest_ok is True, failure_kind
    assert payload is None
    assert need_archival is False
    assert not raw_path.exists()

def test_parse_payload_per_file_timeout_returns_failure(monkeypatch):
    import signal
    import time
    if not hasattr(signal, 'SIGALRM'):
        pytest.skip('SIGALRM not available')
    target = '/fake/slow-stats'
    monkeypatch.setattr(st.cfg, 'get_sync_ingest_per_file_timeout_s', lambda: 0.05)

    def slow_impl(*_args, **_kwargs):
        time.sleep(1.0)
        return (target, None, False, True, 0.0)
    monkeypatch.setattr(st, '_parse_stats_file_payload_impl', slow_impl)
    stats_file, payload, need_archival, ingest_ok, elapsed_s = st._parse_stats_file_payload(target)
    assert stats_file == target
    assert payload is None
    assert need_archival is False
    assert ingest_ok is False
    assert elapsed_s >= 0.0

def test_ingest_in_flight_tracker_sample_and_complete():
    tracker = st._IngestPoolInFlightTracker(['/a/one', '/a/two', '/a/three'])
    tracker.complete('/a/two')
    assert tracker.sample_in_flight(max_n=10) == ['/a/one', '/a/three']

def test_parse_payload_skips_archival_when_db_complete_and_in_tar(monkeypatch):
    target = '/tmp/stats-in-tar'
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st, 'parse_stats_file_path', lambda _p: ('h1', '123'))
    monkeypatch.setattr(st, 'stats_file_is_active_segment', lambda _p: False)
    monkeypatch.setattr(st, 'load_stats_file_lines', lambda *_a, **_k: (['100 job1 h1\n'], None))
    monkeypatch.setattr(st, 'parse_first_timestamp_line', lambda _lines: ('100', 'job1', 'h1'))
    monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: True)
    monkeypatch.setattr(st, '_try_db_complete_head_tail_fast_path', lambda *_a, **_k: (-1, True))
    monkeypatch.setattr(
        st, 'raw_stats_path_tar_append_decision', lambda *_a, **_k: (False, 'member_exists'),
    )
    stats_file, payload, need_archival, ingest_ok, parse_elapsed_s, outcome_meta = (
        st._unpack_parse_payload_result(st._parse_stats_file_payload(target))
    )
    assert stats_file == target
    assert payload is None
    assert need_archival is False
    assert ingest_ok is True
    assert outcome_meta.get('archive_skip') == 'member_exists'

def test_parse_payload_logs_archive_skip_when_db_complete_in_tar(
    monkeypatch, capsys,
):
    target = '/tmp/stats-in-tar-log'
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st, 'parse_stats_file_path', lambda _p: ('h1', '123'))
    monkeypatch.setattr(st, 'stats_file_is_active_segment', lambda _p: False)
    monkeypatch.setattr(st, 'load_stats_file_lines', lambda *_a, **_k: (['100 job1 h1\n'], None))
    monkeypatch.setattr(st, 'parse_first_timestamp_line', lambda _lines: ('100', 'job1', 'h1'))
    monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: True)
    monkeypatch.setattr(st, '_try_db_complete_head_tail_fast_path', lambda *_a, **_k: (-1, True))
    monkeypatch.setattr(
        st, 'raw_stats_path_tar_append_decision', lambda *_a, **_k: (False, 'member_exists'),
    )
    monkeypatch.setattr(st, 'stats_file_size_bytes', lambda _p: 42)
    result = st._parse_stats_file_payload(target)
    (
        stats_file,
        payload,
        need_archival,
        ingest_ok,
        parse_elapsed_s,
        outcome_meta,
    ) = st._unpack_parse_payload_result(result)
    assert need_archival is False
    assert outcome_meta.get('archive_skip') == 'member_exists'
    packed = st._pack_ingest_worker_result(
        stats_file,
        need_archival,
        ingest_ok,
        parse_elapsed_s,
        outcome_meta,
    )
    st._log_ingest_worker_result(packed)
    out = capsys.readouterr().out
    assert 'archive=member_exists' in out
    assert 'archive=no' not in out


def test_empty_primary_mapping_falls_back_to_mtime_archive(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    try:
        target = str(tmp_path / 'stats0')
        Path(target).write_text('dummy')
        archive_calls = {'n': 0, 'items': []}

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            return []
        fake_rescan.calls = 0
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(tmp_path / 'daily'))

        class _ArchivePoolCapture:

            def map_async(self, _fn, items):
                archive_calls['n'] += 1
                archive_calls['items'].append(items)
                return _fake_map_async_result([True for _ in items])
        st.run_sync_timedb_supervisor_loop(str(tmp_path), 'all', None, '', object(), _ArchivePoolCapture(), run_once=True)
        assert archive_calls['n'] >= 1
        assert archive_calls['items'][0]
    finally:
        shutdown_requested[0] = False

def test_finally_path_finalizes_inflight_archive(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    try:
        target = str(tmp_path / 'stats-finalize')
        Path(target).write_text('dummy')
        processed = {'n': 0}

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            raise RuntimeError('forced-exit')
        fake_rescan.calls = 0
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {str(tmp_path / 'day.tar.gz'): [target]})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'tgz_archive_dir', str(tmp_path / 'daily'))
        real_add_processed = st._add_processed_path

        def wrapped_add(*args, **kwargs):
            processed['n'] += 1
            return real_add_processed(*args, **kwargs)
        monkeypatch.setattr(st, '_add_processed_path', wrapped_add)

        class _ArchivePoolDone:

            def map_async(self, _fn, _items):
                return _fake_map_async_result([True])
        with pytest.raises(RuntimeError):
            st.run_sync_timedb_supervisor_loop(str(tmp_path), 'all', None, '', object(), _ArchivePoolDone(), run_once=True)
        assert processed['n'] >= 1
    finally:
        shutdown_requested[0] = False

def test_archive_result_mismatch_retries_unmatched(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    try:
        target = str(tmp_path / 'stats-mismatch')
        Path(target).write_text('dummy')

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            shutdown_requested[0] = True
            return []
        fake_rescan.calls = 0
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {str(tmp_path / 'day.tar.gz'): [target]})
        monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_max_attempts', lambda: 2)
        monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_backoff_base_seconds', lambda: 0.0)
        monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_backoff_max_seconds', lambda: 0.0)
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(tmp_path / 'daily'))

        class _ArchivePoolMismatch:

            def __init__(self):
                self.calls = 0

            def map_async(self, _fn, _items):
                self.calls += 1
                call_no = self.calls
                return _fake_map_async_result([] if call_no == 1 else [True])
        ap = _ArchivePoolMismatch()
        st.run_sync_timedb_supervisor_loop(str(tmp_path), 'all', None, '', object(), ap, run_once=True)
        assert ap.calls >= 2
    finally:
        shutdown_requested[0] = False

def test_log_db_lock_wait_suppressed_under_30_seconds(monkeypatch):
    messages = []
    monkeypatch.setattr(st, 'log_print', lambda msg, flush=True: messages.append(msg))
    st._log_db_lock_wait('proc', '/tmp/stats0', 29.999)
    assert messages == []

def test_log_db_lock_wait_emits_over_30_seconds(monkeypatch):
    messages = []
    monkeypatch.setattr(st, 'log_print', lambda msg, flush=True: messages.append(msg))
    st._log_db_lock_wait('host', '/tmp/stats0', 30.001)
    assert len(messages) == 1
    assert 'DB lock wait host batch file=/tmp/stats0' in messages[0]

def test_insert_host_data_individually_uses_force_insert():
    """Fallback inserts must not use Django.save() UPDATE path on existing pk (Timescale decompress limit)."""
    mock_inst = MagicMock()
    row_time = pd.Timestamp('2026-04-05 07:40:44.301268101+0000', tz='UTC')
    df = pd.DataFrame([{'host': 'i615-154.vista.tacc.utexas.edu', 'jid': None, 'type': 'block', 'event': 'in_flight', 'unit': '#', 'time': row_time, 'value': 0.0, 'delta': 0.0, 'arc': 0.0}])
    with patch.object(st, 'host_data_instance_from_stats_row', return_value=mock_inst):
        st._insert_host_data_individually(df)
    mock_inst.save.assert_called_once_with(force_insert=True)

def test_sync_timedb_uses_fixed_batch_sizes_not_adaptive_helpers():
    """Ingest uses fixed chunk and bulk_create batch sizes (no runtime tuning)."""
    assert st.chunk_size == 1000
    assert st.bulk_create_batch_size() == 10000
    assert not hasattr(st, '_get_adaptive_bulk_create_batch_size')
    assert not hasattr(st, '_record_adaptive_batch_feedback')

def test_sync_timedb_supervisor_source_uses_watch_pool_not_raw_imap():
    """Ingest pool loops must use imap_unordered_watch_pool (OOM-safe)."""
    source_path = Path(st.__file__)
    text = source_path.read_text(encoding='utf-8')
    assert 'ingest_pool.imap_unordered' not in text
    assert 'imap_unordered_watch_pool' in text

def test_cap_pending_stats_files_list_truncates():
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import cap_pending_stats_file_list
    paths = ['/a/%d' % i for i in range(10)]
    capped = cap_pending_stats_file_list(paths, 4, log_fn=lambda *_a, **_k: None)
    assert capped == paths[:4]

def test_cap_pending_stats_file_list_retains_oldest_when_truncating():
    """Pending queue cap drops newer paths; oldest-first ingest order is preserved."""
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import cap_pending_stats_file_list
    base_ts = 1591123200
    paths = ['/host/%d' % (base_ts + i * 60) for i in range(5)]
    capped = cap_pending_stats_file_list(paths, 3, log_fn=lambda *_a, **_k: None)
    assert capped == paths[:3]
    assert int(os.path.basename(capped[0])) < int(os.path.basename(capped[-1]))

def test_supervisor_ingests_oldest_pending_paths_first(monkeypatch):
    """Supervisor chunks from the head of the oldest-first pending queue."""
    shutdown_requested[0] = False
    try:
        base_ts = 1591123200
        pending = ['/host/%d' % (base_ts + i * 60) for i in range(3)]
        ingested_order = []
        rescan_calls = {'n': 0}

        def fake_rescan(*_a, **_k):
            if rescan_calls['n'] == 0:
                rescan_calls['n'] += 1
                return list(pending)
            return []

        def fake_ingest(_lock, path, _c=None):
            ingested_order.append(path)
            return (path, True, True, 0.01)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_ingest)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'chunk_size', 1)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        archive_pool = _FakeArchivePool()
        archive_pool.__enter__()
        try:
            st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
        finally:
            archive_pool.__exit__(None, None, None)
        assert ingested_order == pending
    finally:
        shutdown_requested[0] = False

def test_rescan_pending_stats_files_reuses_set_without_copy(monkeypatch):
    from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers
    discovered = ['/pending/1', '/pending/2', '/done/1']
    monkeypatch.setattr(helpers, 'collect_stats_files_in_range', lambda *_a, **_k: list(discovered))
    processed = {'/done/1'}
    result = helpers.rescan_pending_stats_files('/arc', 'all', None, '.hpc', processed)
    assert result == ['/pending/1', '/pending/2']
    assert processed == {'/done/1'}

def test_ingest_pool_worker_exit_propagates_from_supervisor(monkeypatch):
    from hpcperfstats.dbload.lib.multiprocessing_pool_health import MultiprocessingWorkerExitError
    shutdown_requested[0] = False
    target = '/fake/stats-oom'

    def fake_rescan(*_a, **_k):
        if fake_rescan.calls == 0:
            fake_rescan.calls += 1
            return [target]
        return []
    fake_rescan.calls = 0

    def failing_watch_pool(pool, fn, iterable, *, context='', poll_timeout_s=None, on_stall_warning=None, on_stall_poll=None, pool_health_context=None):
        del pool, fn, iterable, context, poll_timeout_s, on_stall_warning, on_stall_poll, pool_health_context
        raise MultiprocessingWorkerExitError('worker dead', dead_pids=(999,), context='test')
    monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
    monkeypatch.setattr(st, 'imap_unordered_watch_pool', failing_watch_pool)
    monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: False)
    monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1000)
    monkeypatch.setattr(st.cfg, 'get_sync_supervisor_rss_limit_mb', lambda: 0)
    monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
    monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
    monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.connections, 'close_all', lambda: None)
    terminate_calls = []
    monkeypatch.setattr(st, 'terminate_pool_bounded', lambda pool, **kwargs: terminate_calls.append(pool) or True)
    monkeypatch.setattr(st, '_handle_pool_worker_exit_fatal', _reraise_pool_worker_fatal)

    class _Pool:
        pass
    ingest_pool = _Pool()

    class _SpawnCtx:

        def Pool(self, *args, **kwargs):
            del args, kwargs
            return ingest_pool
    monkeypatch.setattr(st.multiprocessing, 'get_context', lambda _name: _SpawnCtx())
    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
        with pytest.raises(MultiprocessingWorkerExitError) as excinfo:
            st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
    finally:
        archive_pool.__exit__(None, None, None)
    assert excinfo.value.exit_code == 137
    assert ingest_pool in terminate_calls
    assert archive_pool in terminate_calls

def test_stall_teardown_preserves_exit_124_not_137(monkeypatch):
    from hpcperfstats.dbload.lib.multiprocessing_pool_health import MultiprocessingPoolStallError, MultiprocessingWorkerExitError
    shutdown_requested[0] = False
    target = '/fake/stats-stall'

    def fake_rescan(*_a, **_k):
        if fake_rescan.calls == 0:
            fake_rescan.calls += 1
            return [target]
        return []
    fake_rescan.calls = 0

    def stall_watch_pool(pool, fn, iterable, *, context='', poll_timeout_s=None, on_stall_warning=None, on_stall_poll=None, pool_health_context=None, max_inflight=None, **extra):
        del pool, fn, iterable, context, poll_timeout_s, on_stall_warning, on_stall_poll, pool_health_context, max_inflight, extra
        raise MultiprocessingPoolStallError('pool imap stalled', dead_pids=(), context='sync_timedb ingest pool', exit_code=124)
    get_watch_calls = []

    def tracking_get_watch(*args, **kwargs):
        get_watch_calls.append(kwargs.get('context'))
        raise MultiprocessingWorkerExitError('worker dead', dead_pids=(999,), context='archive_finalize')
    monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
    monkeypatch.setattr(st, 'imap_sliding_window_watch_pool', stall_watch_pool)
    monkeypatch.setattr(st, 'async_result_get_watch_pool', tracking_get_watch)
    monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: False)
    monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1000)
    monkeypatch.setattr(st.cfg, 'get_sync_supervisor_rss_limit_mb', lambda: 0)
    monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
    monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
    monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.connections, 'close_all', lambda: None)
    monkeypatch.setattr(st, 'terminate_pool_bounded', lambda pool, **kwargs: True)
    monkeypatch.setattr(st, '_handle_pool_worker_exit_fatal', _reraise_pool_worker_fatal)

    class _Pool:
        pass
    ingest_pool = _Pool()

    class _SpawnCtx:

        def Pool(self, *args, **kwargs):
            del args, kwargs
            return ingest_pool
    monkeypatch.setattr(st.multiprocessing, 'get_context', lambda _name: _SpawnCtx())
    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
        with pytest.raises(MultiprocessingPoolStallError) as excinfo:
            st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
    finally:
        archive_pool.__exit__(None, None, None)
    assert excinfo.value.exit_code == 124
    assert get_watch_calls == []

def test_supervisor_stall_hard_exits_before_archive_pool_context(monkeypatch):
    from hpcperfstats.dbload.lib.multiprocessing_pool_health import MultiprocessingPoolStallError
    shutdown_requested[0] = False
    target = '/fake/stats-stall-hard-exit'
    exit_codes = []

    def fake_rescan(*_a, **_k):
        if fake_rescan.calls == 0:
            fake_rescan.calls += 1
            return [target]
        return []
    fake_rescan.calls = 0

    def stall_watch_pool(pool, fn, iterable, *, context='', poll_timeout_s=None, on_stall_warning=None, on_stall_poll=None, pool_health_context=None, max_inflight=None, **extra):
        del pool, fn, iterable, context, poll_timeout_s, on_stall_warning, on_stall_poll, pool_health_context, max_inflight, extra
        raise MultiprocessingPoolStallError('pool imap stalled', dead_pids=(), context='sync_timedb ingest pool', exit_code=124)
    monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
    monkeypatch.setattr(st, 'imap_sliding_window_watch_pool', stall_watch_pool)
    monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: False)
    monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1000)
    monkeypatch.setattr(st.cfg, 'get_sync_supervisor_rss_limit_mb', lambda: 0)
    monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
    monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
    monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.connections, 'close_all', lambda: None)
    monkeypatch.setattr(st, 'terminate_pool_bounded', lambda pool, **kwargs: True)
    monkeypatch.setattr('hpcperfstats.dbload.lib.multiprocessing_pool_health.os._exit', lambda code: exit_codes.append(code))

    class _Pool:
        pass
    ingest_pool = _Pool()

    class _SpawnCtx:

        def Pool(self, *args, **kwargs):
            del args, kwargs
            return ingest_pool
    monkeypatch.setattr(st.multiprocessing, 'get_context', lambda _name: _SpawnCtx())
    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
    finally:
        archive_pool.__exit__(None, None, None)
    assert exit_codes == [124]

def test_stall_teardown_uses_nonblocking_coordinator_shutdown(monkeypatch):
    from hpcperfstats.dbload.lib.multiprocessing_pool_health import MultiprocessingPoolStallError
    from hpcperfstats.dbload.lib.sync_timedb_day_close_manifest import DayCloseManifestCoordinator
    shutdown_requested[0] = False
    target = '/fake/stats-stall-shutdown'
    janitor_shutdown = []

    def janitor_shutdown_track(self, wait=True):
        janitor_shutdown.append(wait)
    monkeypatch.setattr(st.ArchiveJanitor, 'shutdown', janitor_shutdown_track)
    async_shutdown_waits = []

    def async_shutdown_track(self, wait=True):
        async_shutdown_waits.append(wait)
    monkeypatch.setattr(DayCloseManifestCoordinator, 'shutdown', async_shutdown_track)

    def fake_rescan(*_a, **_k):
        if fake_rescan.calls == 0:
            fake_rescan.calls += 1
            return [target]
        return []
    fake_rescan.calls = 0

    def stall_watch_pool(pool, fn, iterable, *, context='', poll_timeout_s=None, on_stall_warning=None, on_stall_poll=None, pool_health_context=None, max_inflight=None, **extra):
        del pool, fn, iterable, context, poll_timeout_s, on_stall_warning, on_stall_poll, pool_health_context, max_inflight, extra
        raise MultiprocessingPoolStallError('pool imap stalled', dead_pids=(), context='sync_timedb ingest pool', exit_code=124)
    monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
    monkeypatch.setattr(st, 'imap_sliding_window_watch_pool', stall_watch_pool)
    monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: False)
    monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1000)
    monkeypatch.setattr(st.cfg, 'get_sync_supervisor_rss_limit_mb', lambda: 0)
    monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
    monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
    monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.connections, 'close_all', lambda: None)
    monkeypatch.setattr(st, 'terminate_pool_bounded', lambda pool, **kwargs: True)
    monkeypatch.setattr(st, '_handle_pool_worker_exit_fatal', _reraise_pool_worker_fatal)

    class _Pool:
        pass
    ingest_pool = _Pool()

    class _SpawnCtx:

        def Pool(self, *args, **kwargs):
            del args, kwargs
            return ingest_pool
    monkeypatch.setattr(st.multiprocessing, 'get_context', lambda _name: _SpawnCtx())
    archive_pool = _FakeArchivePool()
    archive_pool.__enter__()
    try:
        with pytest.raises(MultiprocessingPoolStallError):
            st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), archive_pool, run_once=True)
    finally:
        archive_pool.__exit__(None, None, None)
    assert janitor_shutdown == [False]
    assert async_shutdown_waits == [False]

def test_finalize_invalidates_members_cache(monkeypatch):
    shutdown_requested[0] = False
    invalidated = []
    monkeypatch.setattr(st, 'invalidate_after_daily_tar_mutation', lambda path, **kw: invalidated.append(path))
    target = '/tmp/stats-inv'
    archive_compressed = '/tmp/2026-06-01.tar.gz'

    def fake_rescan(*_a, **_k):
        if fake_rescan.calls == 0:
            fake_rescan.calls += 1
            return [target]
        return []
    fake_rescan.calls = 0

    class _ArchivePoolSuccess:

        def map_async(self, _fn, items):

            class _R:

                def ready(self):
                    return True

                def get(self):
                    return [True for _ in items]
            return _R()
    monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
    monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
    monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {archive_compressed: [target]})
    monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.connections, 'close_all', lambda: None)
    monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
    try:
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), _ArchivePoolSuccess(), run_once=True)
    finally:
        shutdown_requested[0] = False
    assert archive_compressed in invalidated

def test_archive_finalize_skips_invalidate_when_tar_append_redis_merge_succeeded(monkeypatch):
    shutdown_requested[0] = False
    invalidated = []
    logs = []
    monkeypatch.setattr(st, 'invalidate_after_daily_tar_mutation', lambda path, **kw: invalidated.append((path, kw.get('reason'))))
    monkeypatch.setattr(st, 'log_print', lambda *args, **kwargs: logs.append(' '.join((str(a) for a in args))))
    target = '/tmp/stats-merge-warm'
    archive_compressed = '/tmp/2026-06-01.tar.gz'

    def fake_rescan(*_a, **_k):
        if fake_rescan.calls == 0:
            fake_rescan.calls += 1
            return [target]
        return []
    fake_rescan.calls = 0

    class _ArchivePoolMergeWarm:

        def map_async(self, _fn, items):

            class _R:

                def ready(self):
                    return True

                def get(self):
                    return [st.ArchiveAppendOutcome(redis_merge_ok=True, skip_finalize_invalidate=True) for _ in items]
            return _R()
    monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
    monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
    monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {archive_compressed: [target]})
    monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.connections, 'close_all', lambda: None)
    monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
    try:
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), _ArchivePoolMergeWarm(), run_once=True)
    finally:
        shutdown_requested[0] = False
    assert not invalidated
    assert any(('archive_finalize skip invalidate' in line for line in logs))
    assert any(('redis_merge_warm' in line for line in logs))

def test_invalidation_hook_defers_prewarm_on_archive_finalize(monkeypatch, tmp_path):
    prewarm_days = []
    flushed = []
    monkeypatch.setattr(st, '_prewarm_archive_members_redis_for_day_token', lambda day_token: prewarm_days.append(day_token))

    def _hook(_canonical, day_token, reason=None):
        if reason == 'archive_finalize':
            if day_token:
                deferred_days.add(day_token)
            return
        if day_token:
            st._prewarm_archive_members_redis_for_day_token(day_token)
    deferred_days = set()

    def _flush():
        for day in sorted(deferred_days):
            flushed.append(day)
            st._prewarm_archive_members_redis_for_day_token(day)
        deferred_days.clear()
    archive_helpers.set_archive_members_invalidation_hook(_hook)
    try:
        zst = tmp_path / '2026-05-22.tar.zst'
        zst.write_bytes(b'sealed')
        archive_helpers.invalidate_after_daily_tar_mutation(str(zst), reason='archive_finalize', log_fn=lambda *_a, **_k: None)
        assert prewarm_days == []
        assert deferred_days == {'2026-05-22'}
        _flush()
        assert prewarm_days == ['2026-05-22']
        assert flushed == ['2026-05-22']
    finally:
        archive_helpers.reset_archive_members_invalidation_hook_for_tests()

def test_handoff_reingest_allows_archived_to_written_transition():
    file_states = {'/tmp/handoff.raw': st.SyncFileState.ARCHIVED}
    handoff = {'/tmp/handoff.raw'}
    assert st._transition_file_state(file_states, '/tmp/handoff.raw', st.SyncFileState.WRITTEN, handoff_priority_paths=handoff)
    assert file_states['/tmp/handoff.raw'] == st.SyncFileState.WRITTEN
    file_states2 = {'/tmp/normal.raw': st.SyncFileState.ARCHIVED}
    assert st._transition_file_state(file_states2, '/tmp/normal.raw', st.SyncFileState.WRITTEN, handoff_priority_paths=handoff)
    assert file_states2['/tmp/normal.raw'] == st.SyncFileState.WRITTEN

def test_ingest_first_archive_abandoned_after_retries_exhausted(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    logs = []
    target = str(tmp_path / 'stats-abandon.hpc')
    open(target, 'wb').close()
    archive_compressed = str(tmp_path / '2026-06-01.tar.gz')
    dead_letter_saved = []
    checkpoint_saved = []

    class _DonePreflight:
        enabled = False

        def verification_complete(self):
            return True

        def needs_delete_phase(self):
            return False

        def delete_phase_done(self):
            return True

        def paths_pending_startup_delete(self):
            return set()

        def consumed_paths(self):
            return set()

        def start_async_verify(self):
            return None

        def shutdown(self, wait=True):
            del wait
    _supervisor_startup_preflight_patches(monkeypatch, _DonePreflight())
    monkeypatch.setattr(session_executor_mod, 'ThreadPoolExecutor', _InlineThreadPoolExecutor)

    def fake_rescan(*_a, **_k):
        if fake_rescan.calls == 0:
            fake_rescan.calls += 1
            return [target]
        shutdown_requested[0] = True
        return []
    fake_rescan.calls = 0
    monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
    monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, True, True, 0.0))
    monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
    monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {archive_compressed: [target]})
    monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_max_attempts', lambda: 1)
    monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_backoff_base_seconds', lambda: 0.0)
    monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_backoff_max_seconds', lambda: 0.0)
    monkeypatch.setattr(st.cfg, 'get_sync_enable_ingest_first_durability_mode', lambda: True)
    monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.connections, 'close_all', lambda: None)
    monkeypatch.setattr(st, 'tgz_archive_dir', str(tmp_path))
    monkeypatch.setattr(st, 'log_print', lambda msg, *a, **kw: logs.append(str(msg)))
    monkeypatch.setattr(st, '_save_dead_letter_entries', lambda _path, entries: dead_letter_saved.append(list(entries)))
    monkeypatch.setattr(st, '_save_sync_checkpoint', lambda _path, entries: checkpoint_saved.append(list(entries)))

    class _AlwaysFailArchive:

        def map_async(self, _fn, items):
            return _fake_map_async_result([False for _ in items])
    try:
        st.run_sync_timedb_supervisor_loop(str(tmp_path / 'archive'), 'all', None, '.hpc', object(), _AlwaysFailArchive(), run_once=True)
    finally:
        shutdown_requested[0] = False
    assert any(('ingest_first_archive_abandoned_raw' in line for line in logs)), logs[-30:]
    assert any(('Archive retries exhausted' in line for line in logs)), logs[-30:]
    assert dead_letter_saved
    assert checkpoint_saved
    checkpoint_paths = {entry['path'] for entry in checkpoint_saved[-1]}
    assert target in checkpoint_paths
    assert os.path.isfile(target)

def test_archive_finalize_cardinality_mismatch_retries_unmatched(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    logs = []
    target_a = str(tmp_path / 'stats-a.hpc')
    target_b = str(tmp_path / 'stats-b.hpc')
    open(target_a, 'wb').close()
    open(target_b, 'wb').close()
    archive_a = str(tmp_path / '2026-06-01.tar.gz')
    archive_b = str(tmp_path / '2026-06-02.tar.gz')

    class _DonePreflight:
        enabled = False

        def verification_complete(self):
            return True

        def needs_delete_phase(self):
            return False

        def delete_phase_done(self):
            return True

        def paths_pending_startup_delete(self):
            return set()

        def consumed_paths(self):
            return set()

        def start_async_verify(self):
            return None

        def shutdown(self, wait=True):
            del wait
    _supervisor_startup_preflight_patches(monkeypatch, _DonePreflight())
    monkeypatch.setattr(session_executor_mod, 'ThreadPoolExecutor', _InlineThreadPoolExecutor)

    def fake_rescan(*_a, **_k):
        if fake_rescan.calls == 0:
            fake_rescan.calls += 1
            return [target_a, target_b]
        shutdown_requested[0] = True
        return []
    fake_rescan.calls = 0

    class _ShortResultArchive:

        def map_async(self, _fn, items):
            del items
            return _fake_map_async_result([False])
    monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
    monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (_a[1], True, True, 0.0))
    monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
    monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {archive_a: [target_a], archive_b: [target_b]})
    monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_max_attempts', lambda: 3)
    monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_backoff_base_seconds', lambda: 0.0)
    monkeypatch.setattr(st.cfg, 'get_sync_archive_retry_backoff_max_seconds', lambda: 0.0)
    monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.connections, 'close_all', lambda: None)
    monkeypatch.setattr(st, 'tgz_archive_dir', str(tmp_path))
    monkeypatch.setattr(st, 'log_print', lambda msg, *a, **kw: logs.append(str(msg)))
    try:
        st.run_sync_timedb_supervisor_loop(str(tmp_path / 'archive'), 'all', None, '.hpc', object(), _ShortResultArchive(), run_once=True)
    finally:
        shutdown_requested[0] = False
    mismatch_lines = [line for line in logs if 'Archive result cardinality mismatch: deferred=2 results=1' in line]
    assert mismatch_lines, logs[-40:]
    retry_lines = [line for line in logs if 'Archive task retry scheduled' in line]
    assert len(retry_lines) >= 2, retry_lines

def test_checkpoint_flush_logs_oserror_and_preserves_dirty(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    logs = []
    flush_calls = {'n': 0}

    def failing_save(_path, _entries):
        flush_calls['n'] += 1
        raise OSError(28, 'No space left on device')
    target = str(tmp_path / 'stats-cp.hpc')
    open(target, 'wb').close()

    class _DonePreflight:
        enabled = False

        def verification_complete(self):
            return True

        def needs_delete_phase(self):
            return False

        def delete_phase_done(self):
            return True

        def paths_pending_startup_delete(self):
            return set()

        def consumed_paths(self):
            return set()

        def start_async_verify(self):
            return None

        def shutdown(self, wait=True):
            del wait
    _supervisor_startup_preflight_patches(monkeypatch, _DonePreflight())
    monkeypatch.setattr(session_executor_mod, 'ThreadPoolExecutor', _InlineThreadPoolExecutor)

    def fake_rescan(*_a, **_k):
        if fake_rescan.calls == 0:
            fake_rescan.calls += 1
            return [target]
        shutdown_requested[0] = True
        return []
    fake_rescan.calls = 0
    monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
    monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (target, False, True, 0.0))
    monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
    monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
    monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.connections, 'close_all', lambda: None)
    monkeypatch.setattr(st, 'tgz_archive_dir', str(tmp_path))
    monkeypatch.setattr(st, 'log_print', lambda msg, *a, **kw: logs.append(str(msg)))
    monkeypatch.setattr(st, '_save_sync_checkpoint', failing_save)
    monkeypatch.setattr(st, 'SYNC_TIMEDB_CHECKPOINT_FLUSH_EVERY_FILES', 1)
    try:
        st.run_sync_timedb_supervisor_loop(str(tmp_path / 'archive'), 'all', None, '.hpc', object(), object(), run_once=True)
    finally:
        shutdown_requested[0] = False
    assert flush_calls['n'] >= 1
    assert any(('ERROR: checkpoint flush failed' in line for line in logs))

def test_maybe_exit_on_supervisor_rss_limit_exits_137(monkeypatch, tmp_path):
    monkeypatch.setattr(st.cfg, 'get_sync_supervisor_rss_limit_mb', lambda: 1)
    monkeypatch.setattr(st.cfg, 'get_sync_supervisor_rss_check_every_n_chunks', lambda: 1)
    status = tmp_path / 'status'
    status.write_text('VmRSS:\t2048 kB\n', encoding='utf-8')
    monkeypatch.setattr(st, 'read_process_rss_bytes', lambda: 2048 * 1024)
    with pytest.raises(SystemExit) as excinfo:
        st._maybe_exit_on_supervisor_rss_limit(1)
    assert excinfo.value.code == 137

def test_maybe_apply_tree_rss_governor_exits_on_exit_cap(monkeypatch):
    monkeypatch.setattr(st.cfg, 'get_sync_process_tree_rss_limit_mb', lambda: 0)
    monkeypatch.setattr(st.cfg, 'get_sync_process_tree_rss_exit_mb', lambda: 1)
    monkeypatch.setattr(st.cfg, 'get_sync_process_tree_rss_check_every_n_chunks', lambda: 1)
    monkeypatch.setattr(st, 'read_sync_timedb_tree_rss_bytes', lambda *_a, **_k: 2 * 1024 * 1024)
    with pytest.raises(SystemExit) as excinfo:
        st._maybe_apply_tree_rss_governor(1, object(), None, object())
    assert excinfo.value.code == 137

def test_effective_ingest_imap_inflight_cap_equals_pool_size(monkeypatch):
    assert st._effective_ingest_imap_inflight_cap(24, 100) == 24
    assert st._effective_ingest_imap_inflight_cap(24, 10) == 10
    assert st._effective_ingest_imap_inflight_cap(1, 0) == 1

def test_spawn_pool_recycle_kwargs_when_maxtasks_set(monkeypatch):
    monkeypatch.setattr(st.cfg, 'get_sync_ingest_pool_maxtasksperchild', lambda: 25)
    assert st._spawn_pool_recycle_kwargs() == {'maxtasksperchild': 25}
    monkeypatch.setattr(st.cfg, 'get_sync_ingest_pool_maxtasksperchild', lambda: 0)
    assert st._spawn_pool_recycle_kwargs() == {}

def test_streaming_parse_path_avoids_readlines(monkeypatch, tmp_path):
    stats_file = tmp_path / 'host.example.com' / '1709123456'
    stats_file.parent.mkdir(parents=True)
    stats_file.write_text('1709123456 job1 host.example.com\n!cpu user sys\n1709123457 job1 host.example.com\ncpu 0 100 200\n', encoding='utf-8')
    readlines_calls = {'n': 0}
    orig_load = st.load_stats_file_lines

    def _counting_load(path, contents=None):
        readlines_calls['n'] += 1
        return orig_load(path, contents)
    monkeypatch.setattr(st, 'stats_file_size_bytes', lambda _p: 600 * 1024 * 1024)
    monkeypatch.setattr(st.cfg, 'get_sync_ingest_max_file_read_bytes', lambda: 512 * 1024 * 1024)
    monkeypatch.setattr(st, 'load_stats_file_lines', _counting_load)
    monkeypatch.setattr(st, 'stats_file_is_active_segment', lambda _p: False)
    monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: False)
    monkeypatch.setattr(
        st, 'raw_stats_path_tar_append_decision', lambda *_a, **_k: (False, 'member_exists'),
    )
    monkeypatch.setattr(st, 'compute_deltas_and_arc', lambda df: df)
    monkeypatch.setattr(st, 'build_stats_dataframes', lambda s, p: (pd.DataFrame(s), pd.DataFrame(p)))
    result = st._parse_stats_file_payload_impl(str(stats_file))
    assert readlines_calls['n'] == 0
    assert result[3] is True

def test_sync_worker_db_task_closes_connections(monkeypatch):
    close_calls = []
    monkeypatch.setattr(st, 'close_old_connections', lambda: close_calls.append('close_old'))
    monkeypatch.setattr(st.connections, 'close_all', lambda: close_calls.append('close_all'))
    with st._sync_worker_db_task():
        pass
    assert close_calls == ['close_old', 'close_all']

def test_host_recent_timestamps_cached_skips_oversized_cache_entry(monkeypatch):
    from datetime import timezone
    st._HOST_ITIMES_CACHE.clear()
    monkeypatch.setattr(st.cfg, 'get_sync_host_itimes_cache_max_timestamps_per_entry', lambda: 3)
    host = 'node1'
    ts_low = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ts_high = datetime(2026, 1, 3, tzinfo=timezone.utc)
    key = (host, int(ts_low.timestamp()), int(ts_high.timestamp()))

    class _FakeQS:

        def values_list(self, *_args, **_kwargs):
            return self

        def distinct(self):
            return self

        def iterator(self):
            for i in range(5):
                yield datetime(2026, 1, 2, 0, 0, i, tzinfo=timezone.utc)
    filter_calls = {'count': 0}

    def _fake_filter(**_kwargs):
        filter_calls['count'] += 1
        return _FakeQS()
    monkeypatch.setattr(st.host_data.objects, 'filter', _fake_filter)
    result = st._host_recent_timestamps_cached(host, ts_low, ts_high)
    assert result is st._HOST_ITIMES_SET_OVERFLOW
    assert key not in st._HOST_ITIMES_CACHE
    st._host_recent_timestamps_cached(host, ts_low, ts_high)
    assert filter_calls['count'] == 2
    assert key not in st._HOST_ITIMES_CACHE

def test_host_recent_timestamps_cached_statement_timeout_returns_overflow(monkeypatch):
    from datetime import timezone
    from django.db.utils import OperationalError
    from hpcperfstats.dbload.lib import sync_timedb_host_itimes as host_itimes
    st._HOST_ITIMES_CACHE.clear()
    host_itimes._ITIMES_TIMEOUT_WARNED.clear()
    host = 'node1'
    ts_low = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ts_high = datetime(2026, 1, 3, tzinfo=timezone.utc)

    class _FakeQS:

        def values_list(self, *_args, **_kwargs):
            return self

        def distinct(self):
            return self

        def iterator(self):
            raise OperationalError('canceling statement due to statement timeout')
    monkeypatch.setattr(st.host_data.objects, 'filter', lambda **_kwargs: _FakeQS())
    result = st._host_recent_timestamps_cached(host, ts_low, ts_high)
    assert result is st._HOST_ITIMES_SET_OVERFLOW

def test_add_processed_path_prunes_file_states(monkeypatch, tmp_path):
    monkeypatch.setattr(st, 'processed_files_max_size', 2)
    processed_files = set()
    processed_files_order = deque()
    checkpoint_entries = deque()
    file_states = {}
    checkpoint_path = str(tmp_path / 'cp.json')
    paths = []
    for i in range(3):
        path = str(tmp_path / ('file%d' % i))
        Path(path).write_text('x', encoding='utf-8')
        paths.append(path)
        file_states[path] = st.SyncFileState.ARCHIVED
        st._add_processed_path(path, processed_files, processed_files_order, checkpoint_entries, checkpoint_path, file_states=file_states)
    assert len(processed_files) == 2
    assert len(file_states) == 2
    assert paths[0] not in file_states
    assert paths[1] in file_states
    assert paths[2] in file_states

class _CapturingHygieneExecutor:
    """Records submitted callables instead of running them in a thread."""

    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.submitted = []

    def submit(self, fn, *args, **kwargs):
        del args, kwargs
        self.submitted.append(fn)

        class _Future:

            def done(self):
                return True

            def result(self, timeout=None):
                del timeout
                return None
        return _Future()

    def shutdown(self, *args, **kwargs):
        del args, kwargs

def test_post_chunk_hygiene_does_not_signal_chunk_boundary_maintenance(monkeypatch):
    """Chunk boundaries must not signal every_n_chunks maintenance (janitor tick discover)."""
    shutdown_requested[0] = False
    scheduled_reasons = []
    try:
        original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

        def spy_scheduled(self, *, reason):
            scheduled_reasons.append(reason)
            return original(self, reason=reason)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_scheduled_maintenance_pass', spy_scheduled)

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return ['/fake/statsA']
            shutdown_requested[0] = True
            return []
        fake_rescan.calls = 0

        def fake_add(_lock, path, _contents=None):
            return (path, True, True, 0.0)
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'chunk_size', 1)
        monkeypatch.setattr(st, 'rescan_every_chunks', 1)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert 'startup' in scheduled_reasons
        assert 'every_n_chunks' not in scheduled_reasons
        assert 'ingest_queue_empty' not in scheduled_reasons
    finally:
        shutdown_requested[0] = False

def test_supervisor_runs_startup_maintenance_with_all_flag(monkeypatch, capsys):
    """``startdate=all`` schedules janitor startup maintenance pass."""
    shutdown_requested[0] = False
    scheduled_reasons = []
    try:
        original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

        def spy_scheduled(self, *, reason):
            scheduled_reasons.append(reason)
            return original(self, reason=reason)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_scheduled_maintenance_pass', spy_scheduled)

        def fake_rescan(*_a, **_k):
            shutdown_requested[0] = True
            return []
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert 'startup' in scheduled_reasons
        out = capsys.readouterr().out
        assert 'sync_timedb: non-default settings:' in out
    finally:
        shutdown_requested[0] = False

def test_supervisor_skips_startup_maintenance_without_all_flag(monkeypatch, capsys):
    """Date-range runs skip startup maintenance and begin rescan/ingest immediately."""
    shutdown_requested[0] = False
    scheduled_reasons = []
    try:
        original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

        def spy_scheduled(self, *, reason):
            scheduled_reasons.append(reason)
            return original(self, reason=reason)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_scheduled_maintenance_pass', spy_scheduled)
        rescan_calls = {'n': 0}

        def fake_rescan(*_a, **_k):
            rescan_calls['n'] += 1
            if rescan_calls['n'] == 1:
                return ['/fake/statsA']
            shutdown_requested[0] = True
            return []
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda *_a, **_k: (_a[1], True, True, 0.0))
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        startdate = datetime(2026, 4, 1)
        enddate = datetime(2026, 4, 14)
        st.run_sync_timedb_supervisor_loop('/tmp/archive', startdate, enddate, '.hpc', object(), _FakeArchivePool(), run_once=True)
        out = capsys.readouterr().out
        assert 'startup' not in scheduled_reasons
        assert 'startup maintenance skipped' in out
        assert rescan_calls['n'] >= 1
    finally:
        shutdown_requested[0] = False

def test_rescan_without_all_skips_startup_snapshot_wait(monkeypatch):
    """Date-range rescans must not wait on or build the startup archive snapshot."""
    shutdown_requested[0] = False
    snapshot_waits = []
    try:
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        original_wait = StartupArchiveScanCoordinator.wait_for_snapshot

        def spy_wait(self, *, allow_build=True, build_fn=None):
            snapshot_waits.append(allow_build)
            return original_wait(self, allow_build=allow_build, build_fn=build_fn)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', spy_wait)

        def fake_rescan(*_a, **_k):
            shutdown_requested[0] = True
            return []
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp')
        st.run_sync_timedb_supervisor_loop('/tmp/archive', datetime(2026, 4, 1), datetime(2026, 4, 14), '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert snapshot_waits == []
    finally:
        shutdown_requested[0] = False

def test_supervisor_module_has_no_live_archive_maintenance_pipeline_calls():
    import inspect
    src = inspect.getsource(st.run_sync_timedb_supervisor_loop)
    forbidden = ('_run_scheduled_archive_maintenance', '_run_forced_two_phase_archive_maintenance', '_maybe_run_forced_maintenance_before_archive_dispatch', '_archive_maintenance_pipeline')
    for name in forbidden:
        assert name not in src

def test_supervisor_scheduled_day_close_at_startup(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    scheduled_calls = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

        def spy_scheduled(self, *, reason):
            scheduled_calls.append(reason)
            return original(self, reason=reason)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_scheduled_maintenance_pass', spy_scheduled)

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            shutdown_requested[0] = True
            return []
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert 'startup' in scheduled_calls
    finally:
        shutdown_requested[0] = False

def test_supervisor_startup_log_no_accrual_interval(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    logs = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            shutdown_requested[0] = True
            return []
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'log_print', lambda *args, **kwargs: logs.append(' '.join((str(a) for a in args))))
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert any(('sync_timedb: day_close immediate enqueue' in line for line in logs))
        assert not any(('archive janitor accrual interval' in line for line in logs))
    finally:
        shutdown_requested[0] = False

def test_supervisor_does_not_signal_ingest_queue_empty_at_chunk_drain(monkeypatch, tmp_path):
    """Queue drain at chunk boundary must not signal ingest_queue_empty maintenance."""
    shutdown_requested[0] = False
    scheduled_reasons = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

        def spy_scheduled(self, *, reason):
            scheduled_reasons.append(reason)
            return original(self, reason=reason)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_scheduled_maintenance_pass', spy_scheduled)

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return ['/fake/stats%d' % i for i in range(3)]
            shutdown_requested[0] = True
            return []
        fake_rescan.calls = 0

        def fake_add(_lock, path, _contents=None):
            return (path, True, True, 0.0)
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(st, 'rescan_every_chunks', 100)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert 'startup' in scheduled_reasons
        assert 'ingest_queue_empty' not in scheduled_reasons
        assert 'every_n_chunks' not in scheduled_reasons
    finally:
        shutdown_requested[0] = False

def test_supervisor_idle_loop_does_not_signal_queue_empty_pass(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    scheduled_reasons = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

        def spy_scheduled(self, *, reason):
            scheduled_reasons.append(reason)
            return original(self, reason=reason)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_scheduled_maintenance_pass', spy_scheduled)

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            return []
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert scheduled_reasons == ['startup']
    finally:
        shutdown_requested[0] = False

def test_supervisor_startup_empty_queue_no_duplicate_drain_pass(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    scheduled_reasons = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

        def spy_scheduled(self, *, reason):
            scheduled_reasons.append(reason)
            return original(self, reason=reason)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_scheduled_maintenance_pass', spy_scheduled)

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            shutdown_requested[0] = True
            return []
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert scheduled_reasons == ['startup']
    finally:
        shutdown_requested[0] = False

def _supervisor_startup_preflight_patches(monkeypatch, preflight_obj):
    del monkeypatch, preflight_obj


def _supervisor_day_raw_removal_patches(monkeypatch, coord_obj):
    import hpcperfstats.dbload.lib.sync_timedb_day_raw_removal as day_mod

    class _FakeDayCoord:

        def __init__(self, **_kwargs):
            self._delegate = coord_obj

        def __getattr__(self, name):
            return getattr(self._delegate, name)
    monkeypatch.setattr(day_mod, 'DayRawRemovalCoordinator', _FakeDayCoord)
    monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _FakeDayCoord)

def _supervisor_startup_preflight_disabled(monkeypatch):
    del monkeypatch

def test_supervisor_reconcile_cap_uses_coordinator_snapshot_no_live_collect(monkeypatch, capsys):
    """Handoff reconcile must use coordinator snapshot instead of live collect."""
    shutdown_requested[0] = False
    collect_calls = {'n': 0}
    from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot
    from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
    coordinator_snapshot = ArchiveMaintenanceSnapshot(
        closed_paths=['/tmp/archive/n.test/100'],
        mapping={'/tmp/daily/2020-01-01.tar.zst': ['/tmp/archive/n.test/100']},
        first_timestamp_by_path={'/tmp/archive/n.test/100': 100.0},
    )

    def boom_collect(*_a, **_k):
        collect_calls['n'] += 1
        return []

    def fake_live_unprocessed(*_a, **_k):
        return {}

    try:
        target = '/fake/statsA'

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            return [target]

        def fake_add(_lock, path, _contents=None):
            shutdown_requested[0] = True
            return (path, True, True, 0.0)

        monkeypatch.setattr(
            'hpcperfstats.dbload.lib.sync_timedb_archive_maint.build_archive_maintenance_snapshot',
            lambda *_a, **_k: coordinator_snapshot,
        )
        monkeypatch.setattr(
            'hpcperfstats.dbload.lib.sync_timedb_archive_helpers.collect_stats_files_in_range',
            boom_collect,
        )
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'get_snapshot', lambda self: coordinator_snapshot)
        monkeypatch.setattr(st, 'build_live_unprocessed_by_tar_for_reconcile', fake_live_unprocessed)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'chunk_size', 1)
        monkeypatch.setattr(st, 'rescan_every_chunks', 100)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp/daily')
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        st.run_sync_timedb_supervisor_loop(
            '/tmp/archive', 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True,
        )
        out = capsys.readouterr().out
        assert 'startup maintenance idle; ingest may begin' in out
    finally:
        shutdown_requested[0] = False

def test_supervisor_startup_blocks_ingest_until_maintenance_idle(monkeypatch):
    """Ingest must not run until janitor startup heavy maintenance pass completes (Fix A)."""
    shutdown_requested[0] = False
    ingest_calls = {'n': 0}
    heavy_pass_calls = {'n': 0}
    _orig_heavy = janitor_mod.ArchiveJanitor.run_heavy_maintenance_pass

    def track_heavy_pass(self, *, reason):
        heavy_pass_calls['n'] += 1
        assert ingest_calls['n'] == 0
        return _orig_heavy(self, reason=reason)
    monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'run_heavy_maintenance_pass', track_heavy_pass)
    try:
        target = '/fake/statsA'

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            shutdown_requested[0] = True
            return []
        fake_rescan.calls = 0

        def fake_add(_lock, path, _contents=None):
            ingest_calls['n'] += 1
            return (path, True, True, 0.0)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'chunk_size', 1)
        monkeypatch.setattr(st, 'rescan_every_chunks', 100)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp/daily')
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert heavy_pass_calls['n'] >= 1
        assert ingest_calls['n'] == 1
    finally:
        shutdown_requested[0] = False

def test_supervisor_ingest_proceeds_without_day_close_delete_gate(monkeypatch):
    """After startup drain clears, mid-run day-close deletes must not re-gate ingest."""
    shutdown_requested[0] = False
    ingest_calls = {'n': 0}

    class _DayCoord:
        enabled = True

        def __init__(self):
            self.block_startup_drain = False

        def any_needs_delete_phase(self):
            return self.block_startup_drain

        def any_active_raw_removal_work(self):
            return self.block_startup_drain

        def count_days_waiting_on_ingest(self):
            return 0

        def needs_delete_phase(self, tar_norm):
            del tar_norm
            return self.block_startup_drain

        def paths_pending_delete(self):
            return {'/fake/pending-delete'}

        def days_needing_delete_oldest_first(self):
            return ['/tmp/daily/fake.tar']

        def phase(self, tar_norm):
            del tar_norm
            return 'verification_complete'

        def begin_deleting(self, tar_norm):
            del tar_norm

        def apply_batch_delete(self, tar_norm):
            del tar_norm
            return 0

        def delete_phase_done(self, tar_norm):
            del tar_norm
            return False

        def consumed_paths(self):
            return set()

        def discover_manifest_handoffs(self):
            return []

        def discover_closed_raw_on_disk_handoffs(self):
            return []

        def any_needs_tar_drop_finish(self):
            return False

        def shutdown(self, wait=True):
            del wait
    try:
        target = '/fake/statsA'
        day_coord = _DayCoord()

        def fake_add(_lock, path, _contents=None):
            day_coord.block_startup_drain = True
            ingest_calls['n'] += 1
            return (path, True, True, 0.0)

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            shutdown_requested[0] = True
            return []
        fake_rescan.calls = 0
        _supervisor_startup_preflight_disabled(monkeypatch)
        _supervisor_day_raw_removal_patches(monkeypatch, day_coord)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'chunk_size', 1)
        monkeypatch.setattr(st, 'rescan_every_chunks', 100)
        monkeypatch.setattr(st, 'tgz_archive_dir', '/tmp/daily')
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        st.run_sync_timedb_supervisor_loop('/tmp/archive', 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert ingest_calls['n'] >= 1
    finally:
        shutdown_requested[0] = False

def test_supervisor_does_not_signal_every_n_chunks_maintenance(monkeypatch, tmp_path):
    """Steady-state day-close discover is janitor tick-driven, not every_n_chunks."""
    shutdown_requested[0] = False
    scheduled_reasons = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        original = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

        def spy_scheduled(self, *, reason):
            scheduled_reasons.append(reason)
            return original(self, reason=reason)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_scheduled_maintenance_pass', spy_scheduled)

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return ['/fake/stats%d' % i for i in range(10)]
            shutdown_requested[0] = True
            return []
        fake_rescan.calls = 0

        def fake_add(_lock, path, _contents=None):
            return (path, True, True, 0.0)
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(st, 'rescan_every_chunks', 10)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert 'startup' in scheduled_reasons
        assert 'every_n_chunks' not in scheduled_reasons
    finally:
        shutdown_requested[0] = False

def _patch_days_ingest_complete(monkeypatch, fn):
    monkeypatch.setattr('hpcperfstats.dbload.sync_timedb.days_ingest_complete_by_checkpoint', fn)
    monkeypatch.setattr(archive_helpers, 'days_ingest_complete_by_checkpoint', fn)
    monkeypatch.setattr(st, 'days_ingest_complete_by_checkpoint', fn)

def _immediate_day_close_contexts(*contexts):
    return tuple(('day_ingest_complete:%s' % ctx for ctx in contexts))
_DEFAULT_IMMEDIATE_DAY_CLOSE_CONTEXTS = _immediate_day_close_contexts('chunk_end', 'idle_finalize', 'archive_finalize')

def _assert_immediate_day_close_reason(events, *, tar_path=None, allowed_contexts=_DEFAULT_IMMEDIATE_DAY_CLOSE_CONTEXTS):
    allowed = set(allowed_contexts)
    hits = []
    for event in events:
        if isinstance(event, tuple) and len(event) == 2:
            tar, reason = event
        else:
            tar, reason = (None, event)
        if reason not in allowed:
            continue
        if tar_path is not None and tar is not None:
            if os.path.normpath(tar) != os.path.normpath(tar_path):
                continue
        hits.append(reason)
    assert hits, 'expected immediate day_close in %s, got %r' % (allowed, events)

def _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, *, paths, rescan_every_chunks=100, immediate_spy=None):
    archive_dir = tmp_path / 'archive'
    daily_dir = tmp_path / 'daily'
    archive_dir.mkdir()
    daily_dir.mkdir()
    calls = {'n': 0}

    def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            return list(paths)
        shutdown_requested[0] = True
        return []
    _supervisor_startup_preflight_disabled(monkeypatch)
    monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
    from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
    monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
    monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
    monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
    monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
    monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
    monkeypatch.setattr(st.cfg, 'get_sync_day_close_candidate_report', lambda: False)
    monkeypatch.setattr(st, 'rescan_every_chunks', rescan_every_chunks)
    monkeypatch.setattr(st, 'close_old_connections', lambda: None)
    monkeypatch.setattr(st.connections, 'close_all', lambda: None)
    monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
    monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
    monkeypatch.setattr(archive_helpers, 'build_archive_mapping', lambda *_a, **_k: ({}, {}))
    monkeypatch.setattr(st, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {})
    monkeypatch.setattr(archive_helpers, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {})
    monkeypatch.setattr('hpcperfstats.dbload.sync_timedb.days_ingest_complete_by_checkpoint', lambda *_a, **_k: [])
    monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
    if immediate_spy is not None:

        def spy_enqueue(self, tar_norm, *, reason, disqualified=None):
            immediate_spy(tar_norm, reason)
            return True
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, '_enqueue_eligible_day_close', spy_enqueue)
    return (str(archive_dir), str(daily_dir))

def test_supervisor_enqueues_immediate_day_close_on_day_drain(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    immediate_events = []
    try:
        day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
        day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=['/fake/stats/%d' % day1_epoch, '/fake/stats/%d' % day2_epoch], immediate_spy=lambda tar, reason: immediate_events.append((os.path.normpath(tar), reason)))
        tar_day1 = os.path.normpath(os.path.join(daily_dir, '2020-01-01.tar'))
        open(tar_day1, 'wb').close()
        _patch_days_ingest_complete(monkeypatch, lambda _unprocessed, **kwargs: [tar_day1])
        st.run_sync_timedb_supervisor_loop(archive_dir, 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        _assert_immediate_day_close_reason(immediate_events, tar_path=tar_day1)
    finally:
        shutdown_requested[0] = False

def test_supervisor_does_not_immediate_day_close_mid_day(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    immediate_events = []
    try:
        day_epoch1 = int(datetime(2020, 1, 1, 10, tzinfo=timezone.utc).timestamp())
        day_epoch2 = int(datetime(2020, 1, 1, 11, tzinfo=timezone.utc).timestamp())
        path_day1 = '/fake/stats/%d' % day_epoch1
        path_day2 = '/fake/stats/%d' % day_epoch2
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=[path_day1, path_day2], immediate_spy=lambda tar, reason: immediate_events.append((os.path.normpath(tar), reason)))
        original_add = st.add_stats_file_to_db

        def stop_after_first_ingest(_lock, path, **_k):
            if path == path_day1:
                shutdown_requested[0] = True
            return original_add(_lock, path, **_k)
        monkeypatch.setattr(st, 'add_stats_file_to_db', stop_after_first_ingest)
        tar_day1 = os.path.normpath(os.path.join(daily_dir, '2020-01-01.tar'))
        open(tar_day1, 'wb').close()
        st.run_sync_timedb_supervisor_loop(archive_dir, 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        chunk_end_hits = [event for event in immediate_events if event[0] == tar_day1 and event[1] == 'day_ingest_complete:chunk_end']
        assert not chunk_end_hits
    finally:
        shutdown_requested[0] = False

def test_supervisor_day_close_at_most_one_batch_late(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    immediate_events = []
    try:
        day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
        day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=['/fake/stats/%d' % day1_epoch, '/fake/stats/%d' % day2_epoch], immediate_spy=lambda tar, reason: immediate_events.append((os.path.normpath(tar), reason)))
        tar_day1 = os.path.normpath(os.path.join(daily_dir, '2020-01-01.tar'))
        open(tar_day1, 'wb').close()
        _patch_days_ingest_complete(monkeypatch, lambda _unprocessed, **kwargs: [tar_day1])
        st.run_sync_timedb_supervisor_loop(archive_dir, 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        immediate_indices = [idx for idx, event in enumerate(immediate_events) if event[0] == tar_day1 and event[1] in _DEFAULT_IMMEDIATE_DAY_CLOSE_CONTEXTS]
        assert immediate_indices
        assert immediate_indices[0] <= 1
    finally:
        shutdown_requested[0] = False

def test_supervisor_checks_day_close_every_batch(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    find_calls = []
    original_scan = archive_helpers.days_ingest_complete_by_checkpoint

    def counting_scan(unprocessed_by_tar, **kwargs):
        find_calls.append(1)
        return original_scan(unprocessed_by_tar, **kwargs)
    try:
        day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
        day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
        archive_dir, _daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=['/fake/stats/%d' % day1_epoch, '/fake/stats/%d' % day2_epoch])
        _patch_days_ingest_complete(monkeypatch, counting_scan)
        st.run_sync_timedb_supervisor_loop(archive_dir, 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert len(find_calls) >= 2
    finally:
        shutdown_requested[0] = False

def test_immediate_day_close_submits_checkpoint_complete_not_chunk_touched_only(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    submitted = []
    try:
        day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=['/fake/stats/%d' % day2_epoch])
        tar_day1 = os.path.normpath(os.path.join(daily_dir, '2020-01-01.tar'))
        tar_day2 = os.path.normpath(os.path.join(daily_dir, '2020-01-02.tar'))
        open(tar_day1, 'wb').close()
        open(tar_day2, 'wb').close()

        def fake_unprocessed(*_args, **_kwargs):
            return {tar_day2: ['/fake/stats/pending-day2']}
        monkeypatch.setattr(st, 'build_unprocessed_raw_by_daily_tar', fake_unprocessed)
        monkeypatch.setattr(archive_helpers, 'build_unprocessed_raw_by_daily_tar', fake_unprocessed)
        _patch_days_ingest_complete(monkeypatch, lambda _unprocessed, **_kwargs: [tar_day1])
        submitted = []

        def spy_enqueue(self, tar_norm, *, reason, disqualified=None):
            submitted.append((os.path.normpath(tar_norm), reason))
            return True
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, '_enqueue_eligible_day_close', spy_enqueue)
        st.run_sync_timedb_supervisor_loop(archive_dir, 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        _assert_immediate_day_close_reason(submitted, tar_path=tar_day1)
    finally:
        shutdown_requested[0] = False

def test_immediate_day_close_retries_after_transient_disqualify(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    enqueue_calls = []
    orig_enqueue = janitor_mod.ArchiveJanitor._enqueue_eligible_day_close

    def tracking_enqueue(self, tar_norm, *, reason, disqualified=None):
        tar_norm = os.path.normpath(tar_norm)
        enqueue_calls.append(tar_norm)
        if len(enqueue_calls) == 1:
            return False
        return orig_enqueue(self, tar_norm, reason=reason, disqualified=disqualified)
    try:
        day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
        day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=['/fake/stats/%d' % day1_epoch, '/fake/stats/%d' % day2_epoch])
        tar_day1 = os.path.normpath(os.path.join(daily_dir, '2020-01-01.tar'))
        open(tar_day1, 'wb').close()
        rescan_calls = {'n': 0}

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            rescan_calls['n'] += 1
            if rescan_calls['n'] <= 2:
                return ['/fake/stats/%d' % day1_epoch, '/fake/stats/%d' % day2_epoch]
            shutdown_requested[0] = True
            return []
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        _patch_days_ingest_complete(monkeypatch, lambda *_a, **_kwargs: [tar_day1])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, '_enqueue_eligible_day_close', tracking_enqueue)
        st.run_sync_timedb_supervisor_loop(archive_dir, 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert enqueue_calls.count(tar_day1) >= 2
    finally:
        shutdown_requested[0] = False

def test_day_close_unprocessed_build_passes_accrual_snapshot(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    seen_snapshots = []
    from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot
    fake_accrual = ArchiveMaintenanceSnapshot(closed_paths=[], mapping={}, remaining_raw_by_gz={}, ready_paths=set())
    _orig_janitor_init = janitor_mod.ArchiveJanitor.__init__

    def janitor_init_with_accrual(self, *args, **kwargs):
        _orig_janitor_init(self, *args, **kwargs)
        with self._accrual_snapshot_lock:
            self._accrual_snapshot = fake_accrual

    def recording_unprocessed(*_args, **kwargs):
        seen_snapshots.append(kwargs.get('maintenance_snapshot'))
        return {}
    try:
        day_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=['/fake/stats/%d' % day_epoch])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, '__init__', janitor_init_with_accrual)
        monkeypatch.setattr(st, 'build_unprocessed_raw_by_daily_tar', recording_unprocessed)
        monkeypatch.setattr(archive_helpers, 'build_unprocessed_raw_by_daily_tar', recording_unprocessed)
        open(os.path.join(daily_dir, '2020-01-01.tar'), 'wb').close()
        st.run_sync_timedb_supervisor_loop(archive_dir, 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert fake_accrual in seen_snapshots
    finally:
        shutdown_requested[0] = False

def test_immediate_day_close_coexists_with_startup_scheduled_pass(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    scheduled_reasons = []
    immediate_events = []
    try:
        day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
        day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=['/fake/stats/%d' % day1_epoch, '/fake/stats/%d' % day2_epoch], immediate_spy=lambda _tar, reason: immediate_events.append(reason))
        original_scheduled = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

        def spy_scheduled(self, *, reason):
            scheduled_reasons.append(reason)
            return original_scheduled(self, reason=reason)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_scheduled_maintenance_pass', spy_scheduled)
        open(os.path.join(daily_dir, '2020-01-01.tar'), 'wb').close()
        tar_day1 = os.path.normpath(os.path.join(daily_dir, '2020-01-01.tar'))
        _patch_days_ingest_complete(monkeypatch, lambda _unprocessed, **kwargs: [tar_day1])
        st.run_sync_timedb_supervisor_loop(archive_dir, 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert 'startup' in scheduled_reasons
        _assert_immediate_day_close_reason(immediate_events)
    finally:
        shutdown_requested[0] = False

def test_chunk_10_immediate_check_after_boundary_finalize(monkeypatch, tmp_path):
    """Immediate day_close runs at chunk_end without every_n_chunks maintenance."""
    shutdown_requested[0] = False
    order = []
    scheduled_reasons = []
    try:
        day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
        day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=['/fake/stats/%d' % day1_epoch, '/fake/stats/%d' % day2_epoch], rescan_every_chunks=1, immediate_spy=lambda _tar, reason: order.append(reason))
        original_scheduled = janitor_mod.ArchiveJanitor.signal_scheduled_maintenance_pass

        def spy_scheduled(self, *, reason):
            scheduled_reasons.append(reason)
            return original_scheduled(self, reason=reason)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_scheduled_maintenance_pass', spy_scheduled)
        open(os.path.join(daily_dir, '2020-01-01.tar'), 'wb').close()
        tar_day1 = os.path.normpath(os.path.join(daily_dir, '2020-01-01.tar'))
        _patch_days_ingest_complete(monkeypatch, lambda _unprocessed, **kwargs: [tar_day1])
        st.run_sync_timedb_supervisor_loop(archive_dir, 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        _assert_immediate_day_close_reason(order)
        assert 'every_n_chunks' not in scheduled_reasons
    finally:
        shutdown_requested[0] = False

def test_immediate_day_close_on_idle_finalize_without_chunk(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    immediate_reasons = []
    try:
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=[], immediate_spy=lambda _tar, reason: immediate_reasons.append(reason))
        tar_day1 = os.path.normpath(os.path.join(daily_dir, '2020-01-01.tar'))
        open(tar_day1, 'wb').close()
        _patch_days_ingest_complete(monkeypatch, lambda _unprocessed, **kwargs: [tar_day1])

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            shutdown_requested[0] = True
            return []
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert 'day_ingest_complete:idle_finalize' in immediate_reasons
    finally:
        shutdown_requested[0] = False

def test_supervisor_failed_ingest_requeues_at_pending_head(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    ingest_order = []
    try:
        fail_path = '/fake/stats/fail'
        ok_path = '/fake/stats/ok'

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            if not ingest_order:
                return [fail_path, ok_path]
            shutdown_requested[0] = True
            return []
        fail_attempts = {'n': 0}

        def fake_add(_lock, path, **_k):
            ingest_order.append(path)
            if path == fail_path:
                fail_attempts['n'] += 1
                if fail_attempts['n'] == 1:
                    return (path, True, False, 0.0)
            return (path, True, True, 0.0)
        archive_dir, _daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=[fail_path, ok_path])
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert ingest_order[:2] == [fail_path, fail_path]
        assert ok_path in ingest_order
    finally:
        shutdown_requested[0] = False

def test_supervisor_reconcile_prepends_oldest_blocked_before_chunk(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    ingest_order = []
    try:
        tar_norm = os.path.normpath(str(tmp_path / 'daily' / '2020-01-01.tar'))
        blocked_path = str(tmp_path / 'blocked_raw')
        open(blocked_path, 'wb').close()
        other_path = '/fake/stats/other'

        def live_unprocessed(*_a, **_k):
            return {tar_norm: [blocked_path]}
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=[other_path])
        open(os.path.join(str(daily_dir), '2020-01-01.tar'), 'wb').close()
        monkeypatch.setattr(st, 'build_live_unprocessed_by_tar_for_reconcile', live_unprocessed)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 2)

        def fake_add(_lock, path, **_k):
            ingest_order.append(path)
            return (path, True, True, 0.0)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert ingest_order[0] == blocked_path
        assert other_path in ingest_order
    finally:
        shutdown_requested[0] = False

def test_supervisor_startup_handoff_paths_ingested_at_queue_head(monkeypatch, tmp_path):
    """Day-close handoff paths are prepended ahead of unrelated pending backlog."""
    shutdown_requested[0] = False
    ingest_order = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        handoff_path = str(tmp_path / 'host' / '1000')
        backlog = [str(tmp_path / 'host' / str(2000 + i)) for i in range(5)]
        os.makedirs(os.path.dirname(handoff_path), exist_ok=True)
        for path in [handoff_path] + backlog:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('1000 job cn001\n')
        tar_norm = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        open(tar_norm, 'wb').close()

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return [(tar_norm, [handoff_path])]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 1

            def shutdown(self, wait=True):
                del wait
        rescan_calls = {'n': 0}

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            rescan_calls['n'] += 1
            if rescan_calls['n'] == 1:
                return list(backlog)
            shutdown_requested[0] = True
            return []

        def fake_add(_lock, path, **_kwargs):
            ingest_order.append(path)
            return (path, True, True, 0.0)
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_queue_max_size', lambda: 4)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(archive_helpers, 'build_archive_mapping', lambda *_a, **_k: ({}, {}))
        monkeypatch.setattr(st, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert ingest_order
        assert ingest_order[0] == handoff_path
        if backlog:
            backlog_positions = [idx for idx, path in enumerate(ingest_order) if path in backlog]
            if backlog_positions:
                assert min(backlog_positions) > 0
    finally:
        shutdown_requested[0] = False

def test_immediate_day_close_respects_sync_day_close_max_inflight(monkeypatch, tmp_path):
    shutdown_requested[0] = False
    submitted = []
    try:
        day1_epoch = int(datetime(2020, 1, 1, 12, tzinfo=timezone.utc).timestamp())
        day2_epoch = int(datetime(2020, 1, 2, 12, tzinfo=timezone.utc).timestamp())
        day3_epoch = int(datetime(2020, 1, 3, 12, tzinfo=timezone.utc).timestamp())
        archive_dir, daily_dir = _supervisor_two_day_ingest_patches(monkeypatch, tmp_path, paths=[])
        tar_paths = []
        for epoch in (day1_epoch, day2_epoch, day3_epoch):
            day = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y-%m-%d')
            tar = os.path.normpath(os.path.join(daily_dir, '%s.tar' % day))
            open(tar, 'wb').close()
            tar_paths.append(tar)
        monkeypatch.setattr(st, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {})
        _patch_days_ingest_complete(monkeypatch, lambda _unprocessed, **_kwargs: list(tar_paths))
        monkeypatch.setattr(async_day_close_mod.cfg, 'get_sync_day_close_max_inflight', lambda: 1)
        debt_active = set()

        def _enqueue(self, tar_norm, *, reason, disqualified=None):
            tar_norm = os.path.normpath(tar_norm)
            if len(debt_active) >= 1:
                return False
            submitted.append(tar_norm)
            debt_active.add(tar_norm)
            return True

        def _active(self):
            return set(debt_active)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, '_enqueue_eligible_day_close', _enqueue)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, '_day_close_active_tar_paths', _active)
        st.run_sync_timedb_supervisor_loop(archive_dir, 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert len(submitted) == 1
        assert submitted[0] == tar_paths[0]
    finally:
        shutdown_requested[0] = False

def test_apply_day_close_raw_removal_tar_drop_runs_during_chunk():
    """Class B regression: tar-drop runs before batch-delete chunk wait."""
    from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import run_supervisor_day_raw_removal_delete_pass
    delete_tar = '/tmp/daily/2025-12-03.tar'
    tar_drop_tar = '/tmp/daily/2025-12-28.tar'
    tar_drop_calls = []

    class _FakeAsync:

        def reconcile_supervisor_raw_delete_pending(self, reason=''):
            del reason

        def tar_paths_raw_delete_pending(self):
            return []

    class _FakeDayRaw:
        enabled = True

        def any_needs_delete_phase(self):
            return True

        def any_needs_tar_drop_finish(self):
            return True

        def days_needing_tar_drop_oldest_first(self):
            return [tar_drop_tar]

        def oldest_day_needing_delete(self):
            return delete_tar

        def days_needing_delete_oldest_first(self):
            return [delete_tar]

        def try_finish_tar_drop_if_ready(self, tar_norm):
            tar_drop_calls.append(tar_norm)
            return False

        def phase(self, tar_norm):
            del tar_norm
            return 'verification_complete'

        def begin_deleting(self, tar_norm):
            del tar_norm

        def apply_batch_delete(self, tar_norm):
            pytest.fail('batch delete must not run while chunk_in_progress')

        def delete_phase_done(self, tar_norm):
            del tar_norm
            return False

        def needs_delete_phase(self, tar_norm):
            del tar_norm
            return True
    spin = run_supervisor_day_raw_removal_delete_pass(_FakeDayRaw(), _FakeAsync(), chunk_in_progress=True, finalize_day_close_delete=lambda _t: None, sleep_fn=lambda _s: None)
    assert spin is True
    assert tar_drop_calls == [tar_drop_tar]

def test_handoff_batch_remove_processed_paths_single_checkpoint_pass(tmp_path, monkeypatch):
    """1578 paths: batch remove is one checkpoint scan; per-path loop scans N times."""
    from collections import deque as deque_cls
    n_paths = 1578
    paths = []
    for i in range(n_paths):
        p = tmp_path / 'host' / ('stats_%d' % i)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'x')
        paths.append(str(p))
    monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})

    def _counting_checkpoint_iter(entries):
        counts = {'n': 0}

        class _CountingDeque(deque_cls):

            def __iter__(self):
                counts['n'] += 1
                return super().__iter__()
        return (_CountingDeque(entries), counts)
    entries, batch_counts = _counting_checkpoint_iter([{'path': p, 'size': 1, 'mtime': 1} for p in paths])
    processed_files = set(paths)
    processed_order = deque_cls(paths)
    st._batch_remove_processed_paths(paths, processed_files, processed_order, entries, None, persist=False)
    assert len(entries) == 0
    assert processed_files == set()
    assert batch_counts['n'] == 1
    entries2, per_path_counts = _counting_checkpoint_iter([{'path': p, 'size': 1, 'mtime': 1} for p in paths])
    processed_files2 = set(paths)
    processed_order2 = deque_cls(paths)
    for path in paths:
        st._remove_processed_path(path, processed_files2, processed_order2, entries2, None, persist=False)
    assert len(entries2) == 0
    assert per_path_counts['n'] == n_paths

def test_recover_startup_handoff_incremental_one_tar_per_drain_spin(monkeypatch, tmp_path, capsys):
    """Boot recover enqueues handoffs; drain loop processes one tar per spin."""
    shutdown_requested[0] = False
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_a = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        tar_b = os.path.normpath(str(daily_dir / '2020-01-02.tar'))
        open(tar_a, 'wb').close()
        open(tar_b, 'wb').close()
        path_a = str(tmp_path / 'host' / 'a')
        path_b = str(tmp_path / 'host' / 'b')
        os.makedirs(os.path.dirname(path_a), exist_ok=True)
        for path in (path_a, path_b):
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('1000 job cn001\n')

        class _MultiHandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return [(tar_a, [path_a]), (tar_b, [path_b])]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator

        class _DoneTail:
            enabled = False

            def __init__(self, **_kwargs):
                pass

            def start_async_tail_ingest(self):
                return None

            def tail_ingest_done(self):
                return True

            def shutdown(self, wait=True):
                del wait
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _MultiHandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_queue_max_size', lambda: 100)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'get_snapshot', lambda self: type('Snap', (), {'closed_paths': []})())
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'is_startup_heavy_maintenance_idle', lambda self: True)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_startup_maintenance_idle', lambda self: True)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: shutdown_requested.__setitem__(0, True) or [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {})
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'shutdown', lambda self, wait=True: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        out = capsys.readouterr().out
        assert 'handoff_recover' in out
        requeue_lines = [line for line in out.splitlines() if 'day_close handoff requeue' in line and 'skip' not in line]
        assert len(requeue_lines) == 2
        assert 'startup maintenance idle; ingest may begin' in out
    finally:
        shutdown_requested[0] = False

def test_cap_pending_after_handoff_uses_snapshot_during_startup_gate(monkeypatch, tmp_path, capsys):
    """Handoff reconcile uses light cap when coordinator snapshot exists at startup."""
    shutdown_requested[0] = False
    live_reconcile_calls = {'n': 0}
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        open(tar_norm, 'wb').close()
        handoff_path = str(tmp_path / 'host' / '1000')
        os.makedirs(os.path.dirname(handoff_path), exist_ok=True)
        with open(handoff_path, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn001\n')

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return [(tar_norm, [handoff_path])]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        def fake_live_reconcile(*_a, **_k):
            live_reconcile_calls['n'] += 1
            return {}
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator

        class _DoneTail:
            enabled = False

            def __init__(self, **_kwargs):
                pass

            def start_async_tail_ingest(self):
                return None

            def tail_ingest_done(self):
                return True

            def shutdown(self, wait=True):
                del wait
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'get_snapshot', lambda self: type('Snap', (), {'closed_paths': []})())
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'is_startup_heavy_maintenance_idle', lambda self: True)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_startup_maintenance_idle', lambda self: True)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', fake_live_reconcile)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: shutdown_requested.__setitem__(0, True) or [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        out = capsys.readouterr().out
        assert 'handoff_light=1' in out
        assert live_reconcile_calls['n'] == 0
    finally:
        shutdown_requested[0] = False

def test_handoff_skips_duplicate_requeue_same_boot(monkeypatch, tmp_path, capsys):
    """Second handoff requeue for the same tar in one boot is a no-op."""
    shutdown_requested[0] = False
    coord_holder = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        open(tar_norm, 'wb').close()
        handoff_path = str(tmp_path / 'host' / '1000')
        os.makedirs(os.path.dirname(handoff_path), exist_ok=True)
        with open(handoff_path, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn001\n')

        class _HandoffDayRawRemoval:
            enabled = True
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                coord_holder.append(self)

            def discover_manifest_handoffs(self):
                return [(tar_norm, [handoff_path])]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def has_closed_raw_on_disk(self, tar_norm):
                del tar_norm
                return False

            def closed_raw_paths_on_disk(self, tar_norm):
                del tar_norm
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'get_snapshot', lambda self: type('Snap', (), {'closed_paths': []})())
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: shutdown_requested.__setitem__(0, True) or [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {})
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'shutdown', lambda self, wait=True: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        boot_out = capsys.readouterr().out
        assert boot_out.count('day_close handoff requeue') == 1
        assert coord_holder
        assert coord_holder[0].on_handoff_to_ingest is not None
        coord_holder[0].on_handoff_to_ingest(tar_norm, [handoff_path], 'janitor_closed_raw_submit_guard')
        dup_out = capsys.readouterr().out
        assert 'same_boot_duplicate' in dup_out
    finally:
        shutdown_requested[0] = False

def test_handoff_requeue_skips_same_boot_duplicate_after_first_handoff(monkeypatch, tmp_path, capsys):
    """Second handoff requeue for the same tar in one boot is skipped (same_boot_duplicate)."""
    shutdown_requested[0] = False
    coord_holder = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2026-05-26.tar'))
        open(tar_norm, 'wb').close()
        handoff_paths = [str(tmp_path / 'host-a' / '1782242314'), str(tmp_path / 'host-b' / '1782282524')]
        for path in handoff_paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('1000 job cn001\n')

        class _HandoffDayRawRemoval:
            enabled = True
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                coord_holder.append(self)

            def discover_manifest_handoffs(self):
                return [(tar_norm, handoff_paths[:1])]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def has_closed_raw_on_disk(self, tar_norm):
                return os.path.normpath(str(tar_norm or '')) == tar_norm

            def closed_raw_paths_on_disk(self, tar_norm):
                if os.path.normpath(str(tar_norm or '')) != tar_norm:
                    return []
                return [p for p in handoff_paths if os.path.isfile(p)]

            def paths_for_closed_raw_handoff_requeue(self, tar_norm):
                return self.closed_raw_paths_on_disk(tar_norm)

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'get_snapshot', lambda self: type('Snap', (), {'closed_paths': []})())
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: shutdown_requested.__setitem__(0, True) or [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {})
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'shutdown', lambda self, wait=True: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        boot_out = capsys.readouterr().out
        assert boot_out.count('day_close handoff requeue') == 1
        assert coord_holder
        assert coord_holder[0].on_handoff_to_ingest is not None
        coord_holder[0].on_handoff_to_ingest(tar_norm, handoff_paths, 'janitor_closed_raw_submit_guard')
        retry_out = capsys.readouterr().out
        assert 'detail=same_boot_duplicate' in retry_out
        assert 'handoff requeue retry' not in retry_out
        assert 'day_close handoff requeue day=' not in retry_out
    finally:
        shutdown_requested[0] = False

def test_prewarm_skips_sealed_scan_after_tar_append_merge(monkeypatch, tmp_path):
    """Warm Redis after tar_append merge must not repopulate from sealed scan."""
    from hpcperfstats.dbload.lib.archive_compress import normalize_daily_compressed_path
    from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis, build_archive_members_redis_keys, store_complete_members_in_redis
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import reset_archive_members_redis_client_for_tests
    fake = FakeRedis()
    monkeypatch.setattr('hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled', lambda: True)
    monkeypatch.setattr('hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.get_archive_members_redis_client', lambda required=True: fake)
    reset_archive_members_redis_client_for_tests()
    day = '2026-05-22'
    zst = tmp_path / ('%s.tar.zst' % day)
    tar = tmp_path / ('%s.tar' % day)
    zst.write_bytes(b'z')
    tar.write_bytes(b't')
    canonical = str(zst)
    cache_key = archive_helpers._daily_archive_members_cache_key(normalize_daily_compressed_path(canonical))
    keys = build_archive_members_redis_keys(cache_key)
    store_complete_members_in_redis(keys, {'m': 1}, saw_duplicates=False)
    populate_calls = {'n': 0}
    log_lines = []

    def _track_get_existing(*_a, **_k):
        populate_calls['n'] += 1
        return {'m': 1}
    monkeypatch.setattr(st, 'get_existing_archive_members_for_daily_archive', _track_get_existing)
    monkeypatch.setattr(st, 'log_print', lambda *args, **kw: log_lines.append(' '.join((str(a) for a in args))))
    summary = st._prewarm_archive_members_redis_for_days([(canonical, day)])
    assert '%s:redis_warm' % day in summary
    assert populate_calls['n'] == 0
    assert not any(('Prewarming archive members' in line for line in log_lines))

def test_supervisor_prewarm_delegates_to_populate_pool(monkeypatch, tmp_path):
    """MainThread prewarm enqueues populate-pool work; never runs sealed streams."""
    import threading
    import tarfile
    from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
        get_worker_pool_kind,
    )
    from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
        reset_populate_pool_controller_for_tests,
    )
    from hpcperfstats.tests.test_sync_timedb_archive import (
        _start_fake_populate_pool_worker,
    )
    from hpcperfstats.tests.test_sync_timedb_archive_members_redis import FakeRedis

    fake = FakeRedis()
    stop = threading.Event()
    _start_fake_populate_pool_worker(monkeypatch, fake, stop_event=stop)
    monkeypatch.setattr(
        'hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled',
        lambda: True,
    )
    day = '2026-05-23'
    day_gz = tmp_path / ('%s.tar.gz' % day)
    inner = tmp_path / 'raw.txt'
    inner.write_text('data')
    with tarfile.open(day_gz, 'w:gz') as tf:
        tf.add(str(inner), arcname='host/raw')
    canonical = str(day_gz)
    main_thread_stream = {'n': 0}
    populate_stream = {'n': 0}
    original_stream = archive_helpers._stream_compressed_archive_members

    def _counting_stream(compressed_path, on_member=None, **kwargs):
        kind = get_worker_pool_kind()
        if kind is None:
            main_thread_stream['n'] += 1
        elif kind == 'populate-pool':
            populate_stream['n'] += 1
        return original_stream(compressed_path, on_member, **kwargs)

    monkeypatch.setattr(
        archive_helpers, '_stream_compressed_archive_members', _counting_stream,
    )
    summary = st._prewarm_archive_members_redis_for_days([(canonical, day)])
    stop.set()
    reset_populate_pool_controller_for_tests()
    assert main_thread_stream['n'] == 0
    assert populate_stream['n'] == 1
    assert '%s:sealed_populated' % day in summary

def test_supervisor_oldest_day_chunk_gate_inflight_starvation(monkeypatch, tmp_path):
    """While oldest blocked tar has work, ingest chunk excludes newer-day backlog."""
    shutdown_requested[0] = False
    ingest_batches = []
    gate_logs = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
        d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
        tar_a = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        tar_b = os.path.normpath(str(daily_dir / '2020-01-02.tar'))
        open(tar_a, 'wb').close()
        open(tar_b, 'wb').close()
        handoff = []
        for i in range(8):
            path = tmp_path / ('h%d' % i)
            path.write_text('1000 job cn001\n')
            os.utime(path, (d1.timestamp(), d1.timestamp()))
            handoff.append(str(path))
        tail = []
        for i in range(10):
            path = tmp_path / ('t%d' % i)
            path.write_text('2000 job cn002\n')
            os.utime(path, (d2.timestamp(), d2.timestamp()))
            tail.append(str(path))

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return [(tar_a, handoff[:3])]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 1

            def shutdown(self, wait=True):
                del wait
        rescan_calls = {'n': 0}

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            rescan_calls['n'] += 1
            if rescan_calls['n'] == 1:
                return list(handoff[5:]) + list(tail)
            shutdown_requested[0] = True
            return []

        def fake_add(_lock, path, **_kwargs):
            ingest_batches.append(path)
            if len(ingest_batches) >= 3:
                shutdown_requested[0] = True
            return (path, True, True, 0.0)
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 3)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_queue_max_size', lambda: 2000)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(archive_helpers, 'build_archive_mapping', lambda *_a, **_k: ({}, {}))
        monkeypatch.setattr(st, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {tar_a: list(handoff)})
        monkeypatch.setattr(archive_helpers, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {tar_a: list(handoff)})
        monkeypatch.setattr(st, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        monkeypatch.setattr(st, 'log_print', lambda *args, **kw: gate_logs.append(' '.join((str(a) for a in args))) if args and 'oldest_day_chunk_gate' in str(args[0]) else None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert gate_logs
        assert ingest_batches
        assert all((path in handoff for path in ingest_batches))
        assert not any((path in tail for path in ingest_batches))
    finally:
        shutdown_requested[0] = False

def test_supervisor_chunk_gate_cross_day_stall_dispatches_pending_head(monkeypatch, tmp_path):
    """Cross-day blocked inflight defers gate; global pending head ingests first."""
    shutdown_requested[0] = False
    ingest_batches = []
    reclaim_logs = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
        tar_a = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        tar_b = os.path.normpath(str(daily_dir / '2020-01-02.tar'))
        open(tar_a, 'wb').close()
        open(tar_b, 'wb').close()
        blocked_obj = tmp_path / 'cross_day_blocked'
        tail_obj = tmp_path / 'tail'
        blocked_obj.write_text('1000 job cn001\n')
        tail_obj.write_text('2000 job cn002\n')
        os.utime(blocked_obj, (d2.timestamp(), d2.timestamp()))
        os.utime(tail_obj, (d2.timestamp(), d2.timestamp()))
        blocked = str(blocked_obj)

        class _DisabledDayRawRemoval:
            enabled = False
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        def fake_rescan(_directory, _start, _end, _ext, _processed, **_kwargs):
            return [str(tail_obj)]

        def fake_add(_lock, path, **_kwargs):
            ingest_batches.append(path)
            if path == str(tail_obj):
                shutdown_requested[0] = True
            return (path, True, True, 0.0)
        real_reconcile = st.reconcile_orphan_inflight_for_oldest_tar
        real_select = st.select_ingest_chunk_paths
        inflight_seeded = {'done': False}

        def seed_inflight_select(pending, **kwargs):
            inflight = kwargs.get('inflight_archive_paths')
            if inflight is not None and (not inflight_seeded['done']):
                inflight.add(blocked)
                inflight_seeded['done'] = True
            return real_select(pending, **kwargs)

        def track_reconcile(**kwargs):
            result = real_reconcile(**kwargs)
            if result:
                reclaim_logs.append('reclaimed')
            return result
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _DisabledDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 10)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_queue_max_size', lambda: 2000)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {tar_a: [blocked]})
        monkeypatch.setattr(st, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {tar_a: [blocked]})
        monkeypatch.setattr(archive_helpers, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {tar_a: [blocked]})
        monkeypatch.setattr(st, 'select_ingest_chunk_paths', seed_inflight_select)
        monkeypatch.setattr(st, 'reconcile_orphan_inflight_for_oldest_tar', track_reconcile)
        monkeypatch.setattr(st, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert str(tail_obj) in ingest_batches
    finally:
        shutdown_requested[0] = False

def test_supervisor_chunk_gate_unblocks_when_blocked_excluded_from_processed(monkeypatch, tmp_path):
    """Checkpoint-blocked paths in processed_files still ingest after reconcile."""
    shutdown_requested[0] = False
    ingest_batches = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
        d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
        tar_a = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        tar_b = os.path.normpath(str(daily_dir / '2020-01-02.tar'))
        open(tar_a, 'wb').close()
        open(tar_b, 'wb').close()
        blocked = []
        for i in range(2):
            path = tmp_path / ('blocked%d' % i)
            path.write_text('1000 job cn001\n')
            os.utime(path, (d1.timestamp(), d1.timestamp()))
            blocked.append(str(path))
        tail_path = tmp_path / 'tail'
        tail_path.write_text('2000 job cn002\n')
        os.utime(tail_path, (d2.timestamp(), d2.timestamp()))
        checkpoint_path = str(archive_dir / '.sync_timedb_state.json')
        completed = []
        for path in blocked:
            stat = os.stat(path)
            completed.append({'path': path, 'size': int(stat.st_size), 'mtime': int(stat.st_mtime)})
        st._save_sync_checkpoint(checkpoint_path, completed)

        class _DisabledDayRawRemoval:
            enabled = False
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        def fake_rescan(_directory, _start, _end, _ext, processed_files, **_kwargs):
            assert all((path in processed_files for path in blocked))
            return [str(tail_path)]

        def fake_add(_lock, path, **_kwargs):
            ingest_batches.append(path)
            if path in blocked:
                shutdown_requested[0] = True
            return (path, True, True, 0.0)
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _DisabledDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 10)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_queue_max_size', lambda: 2000)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {tar_a: list(blocked)})
        monkeypatch.setattr(st, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {tar_a: list(blocked)})
        monkeypatch.setattr(archive_helpers, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {tar_a: list(blocked)})
        monkeypatch.setattr(st, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert ingest_batches
        assert any((path in blocked for path in ingest_batches))
        assert not any((path == str(tail_path) for path in ingest_batches))
    finally:
        shutdown_requested[0] = False

def test_gate_chunk_no_respin_after_db_complete_checkpoint(monkeypatch, tmp_path):
    """Gate-blocked paths ingest once; memory checkpoint prevents re-dispatch spin."""
    shutdown_requested[0] = False
    ingest_batches = []
    reconcile_blocked_counts = []
    post_ingest_reconcile_pending = {'active': False}
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        d1 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)
        d2 = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
        tar_a = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        tar_b = os.path.normpath(str(daily_dir / '2020-01-02.tar'))
        open(tar_a, 'wb').close()
        open(tar_b, 'wb').close()
        blocked = []
        for i in range(2):
            path = tmp_path / ('blocked%d' % i)
            path.write_text('1000 job cn001\n')
            os.utime(path, (d1.timestamp(), d1.timestamp()))
            blocked.append(str(path))
        tail_path = tmp_path / 'tail'
        tail_path.write_text('2000 job cn002\n')
        os.utime(tail_path, (d2.timestamp(), d2.timestamp()))
        from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot
        accrual_snapshot = ArchiveMaintenanceSnapshot(closed_paths=blocked + [str(tail_path)], mapping={str(daily_dir / '2020-01-01.tar.zst'): list(blocked), str(daily_dir / '2020-01-02.tar.zst'): [str(tail_path)]}, first_timestamp_by_path={blocked[0]: d1.timestamp(), blocked[1]: d1.timestamp(), str(tail_path): d2.timestamp()})
        _real_build_live = archive_helpers.build_live_unprocessed_by_tar_for_reconcile

        def track_live_reconcile(*args, **kwargs):
            result = _real_build_live(*args, **kwargs)
            blocked_count = len(result.get(tar_a, []))
            reconcile_blocked_counts.append(blocked_count)
            if post_ingest_reconcile_pending['active'] and blocked_count == 0:
                shutdown_requested[0] = True
            return result

        class _DisabledDayRawRemoval:
            enabled = False
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        def fake_rescan(_directory, _start, _end, _ext, processed_files, **_kwargs):
            return [str(tail_path)]
        blocked_ingested = set()

        def fake_add(_lock, path, **_kwargs):
            ingest_batches.append(path)
            if path in blocked:
                blocked_ingested.add(path)
                if len(blocked_ingested) == len(blocked):
                    post_ingest_reconcile_pending['active'] = True
            return (path, False, True, 0.0)
        _supervisor_startup_preflight_disabled(monkeypatch)
        _orig_janitor_init = janitor_mod.ArchiveJanitor.__init__

        def janitor_init_with_accrual(self, *args, **kwargs):
            _orig_janitor_init(self, *args, **kwargs)
            with self._accrual_snapshot_lock:
                self._accrual_snapshot = accrual_snapshot
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, '__init__', janitor_init_with_accrual)
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _DisabledDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 10)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_queue_max_size', lambda: 2000)
        monkeypatch.setattr(st.cfg, 'get_sync_checkpoint_flush_batch_size', lambda: 100)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'build_live_unprocessed_by_tar_for_reconcile', track_live_reconcile)
        monkeypatch.setattr(st, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), '2020-01-01', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        blocked_ingest_counts = {path: ingest_batches.count(path) for path in blocked}
        assert all((count == 1 for count in blocked_ingest_counts.values()))
        assert reconcile_blocked_counts
        assert reconcile_blocked_counts[0] == 2
        assert any((count == 0 for count in reconcile_blocked_counts))
    finally:
        shutdown_requested[0] = False

def test_handoff_giant_day_slice_ingest(monkeypatch, tmp_path):
    """Handoff requeue slices giant days via _run_startup_tail_ingest_batch."""
    shutdown_requested[0] = False
    ingest_calls = []
    log_lines = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        open(tar_norm, 'wb').close()
        paths = []
        for i in range(250):
            path = tmp_path / ('p%d' % i)
            path.write_text('1000 job cn001\n')
            paths.append(str(path))

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return [(tar_norm, paths)]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 1

            def shutdown(self, wait=True):
                del wait

        def fake_tail_add(_lock, path, **_kwargs):
            ingest_calls.append(path)
            return (path, True, True, 0.0)
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 100)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_tail_add)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: [])
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'log_print', lambda *args, **kw: log_lines.append(' '.join((str(a) for a in args))))
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(archive_helpers, 'build_archive_mapping', lambda *_a, **_k: ({}, {}))
        monkeypatch.setattr(st, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'build_unprocessed_raw_by_daily_tar', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert len(ingest_calls) == 250
        begin_logs = [line for line in log_lines if 'startup handoff giant-day ingest begin' in line]
        assert begin_logs
        assert 'slices=3' in begin_logs[0]
    finally:
        shutdown_requested[0] = False

def test_closed_raw_handoff_uses_steady_chunk_not_giant_day_slice(monkeypatch, tmp_path, capsys):
    """Steady-state closed_raw submit guard enqueues handoff_priority, not giant-day slices."""
    shutdown_requested[0] = False
    ingest_calls = []
    coord_holder = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2026-05-22.tar'))
        open(tar_norm, 'wb').close()
        paths = []
        for i in range(250):
            path = tmp_path / ('steady-%d' % i)
            path.write_text('1000 job cn001\n')
            paths.append(str(path))

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                coord_holder.append(self)

            def discover_manifest_handoffs(self):
                return []

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def has_closed_raw_on_disk(self, tar_norm):
                return os.path.normpath(str(tar_norm or '')) == tar_norm

            def closed_raw_paths_on_disk(self, tar_norm):
                if os.path.normpath(str(tar_norm or '')) != tar_norm:
                    return []
                return [p for p in paths if os.path.isfile(p)]

            def paths_for_closed_raw_handoff_requeue(self, tar_norm):
                if os.path.normpath(str(tar_norm or '')) != tar_norm:
                    return []
                return [p for p in paths if os.path.isfile(p)]

            def needs_verify_for_closed_raw_block(self, tar_norm):
                del tar_norm
                return False

            def _closed_raw_path_is_quarantine_skip(self, path):
                del path
                return False

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: ingest_calls.append(path) or (path, True, True, 0.0))
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 100)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert coord_holder
        coord_holder[0].on_handoff_to_ingest(tar_norm, paths, 'closed_raw_submit_guard')
        out = capsys.readouterr().out
        assert ingest_calls == []
        assert 'handoff_mode=steady_chunk' in out
        assert 'startup handoff giant-day ingest begin' not in out
        assert 'day_close handoff requeue' in out
    finally:
        shutdown_requested[0] = False

def test_giant_day_slice_gated_to_startup_handoff_recover_only(monkeypatch, tmp_path, capsys):
    """Giant-day synchronous slices run at startup recover only."""
    shutdown_requested[0] = False
    ingest_calls = []
    coord_holder = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        open(tar_norm, 'wb').close()
        paths = []
        for i in range(250):
            path = tmp_path / ('p%d' % i)
            path.write_text('1000 job cn001\n')
            paths.append(str(path))

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                coord_holder.append(self)

            def discover_manifest_handoffs(self):
                return [(tar_norm, paths)]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 1

            def shutdown(self, wait=True):
                del wait
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 100)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: ingest_calls.append(path) or (path, True, True, 0.0))
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: [])
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(archive_helpers, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        boot_out = capsys.readouterr().out
        assert len(ingest_calls) == 250
        assert 'startup handoff giant-day ingest begin' in boot_out
        assert 'handoff_mode=giant_day_slice' in boot_out
        assert 'handoff_mode=steady_chunk' not in boot_out
    finally:
        shutdown_requested[0] = False

def test_finalize_day_close_deferred_when_handoff_priority_pending(monkeypatch, tmp_path, capsys):
    """archive_finalize defers immediate day_close when handoff_priority_paths pending."""
    shutdown_requested[0] = False
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2026-06-01.tar'))
        open(tar_norm, 'wb').close()
        handoff_path = str(tmp_path / 'host-handoff' / '1782242314')
        os.makedirs(os.path.dirname(handoff_path), exist_ok=True)
        with open(handoff_path, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn001\n')
        target = str(tmp_path / 'host-archive' / '1782242315')
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn002\n')
        archive_compressed = str(daily_dir / '2026-06-01.tar.gz')
        open(archive_compressed, 'wb').close()

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return [(tar_norm, [handoff_path])]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        class _ArchivePoolSuccess:

            def map_async(self, _fn, items):

                class _R:

                    def ready(self):
                        return True

                    def get(self):
                        return [st.ArchiveAppendOutcome(redis_merge_ok=False) for _ in items]
                return _R()

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            shutdown_requested.__setitem__(0, True)
            return []
        fake_rescan.calls = 0
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {archive_compressed: [target]})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: False)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [tar_norm])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _ArchivePoolSuccess(), run_once=True)
        out = capsys.readouterr().out
        assert 'archive_finalize defer immediate day_close reason=handoff_priority' in out
        assert 'post_finalize_reconcile' in out
        assert 'day_close handoff requeue' in out
    finally:
        shutdown_requested[0] = False

def test_post_finalize_reconcile_clears_blocked_before_handoff(monkeypatch, tmp_path, capsys):
    """Archive finalize checkpoints paths; post_finalize_reconcile runs before next chunk."""
    shutdown_requested[0] = False
    reconcile_calls = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2026-06-04.tar'))
        open(tar_norm, 'wb').close()
        target = str(tmp_path / 'host-archive' / '1782242315')
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn002\n')
        archive_compressed = str(daily_dir / '2026-06-04.tar.gz')
        open(archive_compressed, 'wb').close()

        class _DisabledDayRawRemoval:
            enabled = False
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        class _ArchivePoolSuccess:

            def map_async(self, _fn, items):

                class _R:

                    def ready(self):
                        return True

                    def get(self):
                        return [st.ArchiveAppendOutcome(redis_merge_ok=False) for _ in items]
                return _R()

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            shutdown_requested.__setitem__(0, True)
            return []
        fake_rescan.calls = 0

        def fake_unprocessed(*_a, **_k):
            reconcile_calls.append(1)
            if len(reconcile_calls) == 1:
                return {tar_norm: [target]}
            return {}
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _DisabledDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {archive_compressed: [target]})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: False)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', fake_unprocessed)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _ArchivePoolSuccess(), run_once=True)
        out = capsys.readouterr().out
        assert 'post_finalize_reconcile' in out
        assert 'incomplete_n=0' in out
        assert 'chunk ingest summary' in out
    finally:
        shutdown_requested[0] = False

def test_handoff_giant_day_does_not_block_main_on_immediate_day_close(monkeypatch, tmp_path, capsys):
    """Immediate day_close handoff with many paths returns without synchronous slices."""
    shutdown_requested[0] = False
    ingest_calls = []
    coord_holder = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2026-05-22.tar'))
        open(tar_norm, 'wb').close()
        paths = []
        for i in range(250):
            path = tmp_path / ('block-%d' % i)
            path.write_text('1000 job cn001\n')
            paths.append(str(path))

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                coord_holder.append(self)

            def discover_manifest_handoffs(self):
                return []

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: ingest_calls.append(path) or (path, True, True, 0.0))
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 100)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert coord_holder
        coord_holder[0].on_handoff_to_ingest(tar_norm, paths, 'closed_raw_submit_guard')
        out = capsys.readouterr().out
        assert ingest_calls == []
        assert 'startup handoff giant-day ingest begin' not in out
        assert 'handoff_mode=steady_chunk' in out
    finally:
        shutdown_requested[0] = False

def test_startup_handoff_no_giant_slice_manifest_done_0522(monkeypatch, tmp_path, capsys):
    """Boot discover for manifest-done closed raw must not giant-day slice."""
    shutdown_requested[0] = False
    coord_holder = []
    kick_calls = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2026-05-22.tar'))
        open(tar_norm, 'wb').close()

        class _HandoffDayRawRemoval:
            enabled = True
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                coord_holder.append(self)

            def discover_manifest_handoffs(self):
                return []

            def discover_closed_raw_on_disk_handoffs(self):
                return [(tar_norm, [])]

            def requeue_closed_raw_paths_for_ingest(self, tar_norm, *, reason, paths=None):
                del tar_norm, paths
                kick_calls.append(reason)
                return []

            def kick_closed_raw_unblock(self, tar_norm, *, reason):
                del tar_norm
                kick_calls.append(reason)
                return 'delete_reopen'

            def has_closed_raw_on_disk(self, tar_norm):
                return os.path.normpath(str(tar_norm or '')) == tar_norm

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'get_snapshot', lambda self: type('Snap', (), {'closed_paths': []})())
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: shutdown_requested.__setitem__(0, True) or [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {})
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        out = capsys.readouterr().out
        assert kick_calls
        assert 'startup handoff giant-day ingest begin' not in out
        assert 'startup_handoff_recover summary' in out
    finally:
        shutdown_requested[0] = False

def test_startup_same_boot_duplicate_kicks_delete_not_skip(monkeypatch, tmp_path, capsys):
    """Second same-boot handoff with delete-only blockers kicks, not silent skip."""
    shutdown_requested[0] = False
    coord_holder = []
    kick_calls = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2026-05-26.tar'))
        open(tar_norm, 'wb').close()
        handoff_paths = [str(tmp_path / 'host-a' / '1782242314')]
        os.makedirs(os.path.dirname(handoff_paths[0]), exist_ok=True)
        with open(handoff_paths[0], 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn001\n')

        class _HandoffDayRawRemoval:
            enabled = True
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                coord_holder.append(self)

            def discover_manifest_handoffs(self):
                return [(tar_norm, handoff_paths)]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def kick_closed_raw_unblock(self, tar_norm, *, reason):
                del tar_norm
                kick_calls.append(reason)
                return 'delete_reopen'

            def paths_for_closed_raw_handoff_requeue(self, tar_norm):
                del tar_norm
                return []

            def needs_verify_for_closed_raw_block(self, tar_norm):
                del tar_norm
                return True

            def has_closed_raw_on_disk(self, tar_norm):
                return os.path.normpath(str(tar_norm or '')) == tar_norm

            def closed_raw_paths_on_disk(self, tar_norm):
                if os.path.normpath(str(tar_norm or '')) != tar_norm:
                    return []
                return list(handoff_paths)

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'get_snapshot', lambda self: type('Snap', (), {'closed_paths': []})())
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: shutdown_requested.__setitem__(0, True) or [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {})
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        boot_out = capsys.readouterr().out
        assert boot_out.count('day_close handoff requeue') == 1
        assert coord_holder
        coord_holder[0].on_handoff_to_ingest(tar_norm, handoff_paths, 'janitor_closed_raw_submit_guard')
        retry_out = capsys.readouterr().out
        assert 'same_boot_duplicate' in retry_out
        assert not kick_calls
    finally:
        shutdown_requested[0] = False

def test_gate_blocked_n_excludes_db_complete_paths(monkeypatch, tmp_path, capsys):
    """Gate incomplete_n drops to zero when paths are checkpoint-complete in memory."""
    shutdown_requested[0] = False
    gate_logs = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_a = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        open(tar_a, 'wb').close()
        blocked = []
        for i in range(2):
            path = tmp_path / ('blocked%d' % i)
            path.write_text('1000 job cn001\n')
            blocked.append(str(path))
        from hpcperfstats.dbload.lib.sync_timedb_archive_maint import ArchiveMaintenanceSnapshot
        accrual_snapshot = ArchiveMaintenanceSnapshot(closed_paths=blocked, mapping={str(daily_dir / '2020-01-01.tar.zst'): list(blocked)}, first_timestamp_by_path={blocked[0]: 1.0, blocked[1]: 1.0})

        class _DisabledDayRawRemoval:
            enabled = False
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return []

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        def fake_rescan(_directory, _start, _end, _ext, processed_files, **_kwargs):
            shutdown_requested[0] = True
            return list(blocked)
        checkpoint_seeded = {'done': False}

        def fake_add(_lock, path, **_kwargs):
            if not checkpoint_seeded['done'] and path in blocked:
                fp = st._path_fingerprint(path)
                if fp is not None:
                    st.run_sync_timedb_supervisor_loop
            return (path, False, True, 0.0)
        _orig_janitor_init = janitor_mod.ArchiveJanitor.__init__

        def janitor_init_with_accrual(self, *args, **kwargs):
            _orig_janitor_init(self, *args, **kwargs)
            with self._accrual_snapshot_lock:
                self._accrual_snapshot = accrual_snapshot
        orig_log = st.log_print

        def capture_log(*args, **kwargs):
            msg = ' '.join((str(a) for a in args))
            if 'oldest_day_chunk_gate' in msg:
                gate_logs.append(msg)
            return orig_log(*args, **kwargs)
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, '__init__', janitor_init_with_accrual)
        monkeypatch.setattr(st, 'log_print', capture_log)
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _DisabledDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 10)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_queue_max_size', lambda: 2000)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'resolved_checkpoint_path_set', lambda _checkpoint_path, checkpoint_entries=None: set(blocked))
        real_build_live = archive_helpers.build_live_unprocessed_by_tar_for_reconcile

        def live_with_checkpoint_merge(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs['checkpoint_paths'] = set(blocked)
            return real_build_live(*args, **kwargs)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', live_with_checkpoint_merge)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), '2020-01-01', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        out = capsys.readouterr().out
        combined = out + '\n'.join(gate_logs)
        assert 'oldest_day_chunk_gate_all_db_complete' in combined or any(('incomplete_n=0' in line for line in gate_logs)) or 'incomplete_n=0' in combined
    finally:
        shutdown_requested[0] = False

def test_chunk_reconcile_single_live_scan_per_chunk(monkeypatch, tmp_path, capsys):
    """Each ingest chunk performs at most one live reconcile scan (S2 cache)."""
    shutdown_requested[0] = False
    live_reconcile_calls = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_a = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        open(tar_a, 'wb').close()
        blocked_path = tmp_path / 'blocked0'
        blocked_path.write_text('1000 job cn001\n')
        blocked = [str(blocked_path)]

        class _DisabledDayRawRemoval:
            enabled = False
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        def fake_rescan(_directory, _start, _end, _ext, processed_files, **_kwargs):
            return blocked

        def fake_add(_lock, path, **_kwargs):
            shutdown_requested[0] = True
            return (path, False, True, 0.0)

        def track_live_reconcile(*_a, **_k):
            live_reconcile_calls.append(1)
            return {tar_a: list(blocked)}
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _DisabledDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'add_stats_file_to_db', fake_add)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 10)
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_queue_max_size', lambda: 2000)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'build_archive_mapping', lambda *_a, **_k: {})
        monkeypatch.setattr(st, 'build_live_unprocessed_by_tar_for_reconcile', track_live_reconcile)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', track_live_reconcile)
        monkeypatch.setattr(st, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        st.run_sync_timedb_supervisor_loop(str(archive_dir), '2020-01-01', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        assert live_reconcile_calls == [1, 1]
    finally:
        shutdown_requested[0] = False

def test_handoff_light_when_drain_disabled(monkeypatch, tmp_path, capsys):
    """handoff_light uses startup snapshot even when drain-before-ingest is off (S3)."""
    shutdown_requested[0] = False
    live_reconcile_calls = {'n': 0}
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2020-01-01.tar'))
        open(tar_norm, 'wb').close()
        handoff_path = str(tmp_path / 'host' / '1000')
        os.makedirs(os.path.dirname(handoff_path), exist_ok=True)
        with open(handoff_path, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn001\n')

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return [(tar_norm, [handoff_path])]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        def fake_live_reconcile(*_a, **_k):
            live_reconcile_calls['n'] += 1
            return {}
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator

        class _DoneTail:
            enabled = False

            def __init__(self, **_kwargs):
                pass

            def start_async_tail_ingest(self):
                return None

            def tail_ingest_done(self):
                return True

            def shutdown(self, wait=True):
                del wait
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'get_snapshot', lambda self: type('Snap', (), {'closed_paths': []})())
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'is_startup_heavy_maintenance_idle', lambda self: True)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_startup_maintenance_idle', lambda self: True)
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', fake_live_reconcile)
        monkeypatch.setattr(st, 'rescan_pending_stats_files', lambda *_a, **_k: shutdown_requested.__setitem__(0, True) or [])
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(st, '_path_fingerprint', lambda p: {'path': p, 'size': 1, 'mtime': 1})
        monkeypatch.setattr(st, 'ensure_persistence_contract', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st.cfg, 'get_sync_ingest_chunk_size', lambda: 1)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _FakeArchivePool(), run_once=True)
        out = capsys.readouterr().out
        assert 'handoff_light=1' in out
        assert live_reconcile_calls['n'] == 0
    finally:
        shutdown_requested[0] = False

def test_pipeline_complete_rescan_excludes_active_handoff_paths(tmp_path):
    """RC-G: rescan exclude set includes coordinator handoff paths."""
    from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import PHASE_DONE, DayRawRemovalCoordinator, _save_manifest
    seg = tmp_path / 'host' / '1782242314'
    seg.parent.mkdir(parents=True)
    seg.write_text('1000 job cn001\n', encoding='utf-8')
    tar_path = str(tmp_path / 'daily' / '2026-06-01.tar')
    os.makedirs(os.path.dirname(tar_path), exist_ok=True)
    open(tar_path, 'wb').close()
    zst = str(tmp_path / 'daily' / '2026-06-01.tar.zst')
    open(zst, 'wb').close()
    coord = DayRawRemovalCoordinator(archive_data_dir=str(tmp_path / 'archive'), host_name_ext='.hpc', tgz_archive_dir=str(tmp_path / 'daily'), log_fn=None, get_quarantine_skip_paths=lambda: set(), ingest_ready_fn=lambda _p: False)
    state = coord._get_or_create_day(tar_path)
    state._record_entry(str(seg), zst, 'skipped_not_in_archive', 'not_in_sealed_archive')
    with state._lock:
        state._manifest['phase'] = PHASE_DONE
        state._manifest['skipped_count'] = 1
        _save_manifest(state._manifest_path, state._manifest)
    excluded = coord.rescan_exclude_paths()
    assert str(seg) in excluded

def test_chunk_end_defers_immediate_day_close_when_handoff_pending(monkeypatch, tmp_path, capsys):
    """RC-F: chunk_end must not synchronously submit day_close while handoff drains."""
    shutdown_requested[0] = False
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2026-06-01.tar'))
        open(tar_norm, 'wb').close()
        handoff_path = str(tmp_path / 'host-handoff' / '1782242314')
        os.makedirs(os.path.dirname(handoff_path), exist_ok=True)
        with open(handoff_path, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn001\n')
        target = str(tmp_path / 'host-archive' / '1782242315')
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn002\n')
        archive_compressed = str(daily_dir / '2026-06-01.tar.gz')
        open(archive_compressed, 'wb').close()

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return [(tar_norm, [handoff_path])]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        class _ArchivePoolSuccess:

            def map_async(self, _fn, items):

                class _R:

                    def ready(self):
                        return True

                    def get(self):
                        return [st.ArchiveAppendOutcome(redis_merge_ok=False) for _ in items]
                return _R()

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            shutdown_requested.__setitem__(0, True)
            return []
        fake_rescan.calls = 0
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {archive_compressed: [target]})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: False)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [tar_norm])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _ArchivePoolSuccess(), run_once=True)
        out = capsys.readouterr().out
        assert 'immediate day_close defer context=chunk_end reason=handoff_priority' in out
        assert 'chunk ingest summary' in out
        assert 'day_close handoff requeue' in out
    finally:
        shutdown_requested[0] = False

def test_chunk_end_submits_immediate_day_close_despite_closed_raw_on_disk(monkeypatch, tmp_path, capsys):
    """Checkpoint-complete day with closed raw on disk still submits day_close at chunk_end."""
    shutdown_requested[0] = False
    submit_calls = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2026-06-01.tar'))
        open(tar_norm, 'wb').close()
        target = str(tmp_path / 'host-archive' / '1782242315')
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn002\n')
        archive_compressed = str(daily_dir / '2026-06-01.tar.gz')
        open(archive_compressed, 'wb').close()

        class _ClosedRawDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return []

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def has_closed_raw_on_disk(self, _tar):
                return True

            def shutdown(self, wait=True):
                del wait

        class _ArchivePoolSuccess:

            def map_async(self, _fn, items):

                class _R:

                    def ready(self):
                        return True

                    def get(self):
                        return [st.ArchiveAppendOutcome(redis_merge_ok=False) for _ in items]
                return _R()

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            shutdown_requested.__setitem__(0, True)
            return []
        fake_rescan.calls = 0
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _ClosedRawDayRawRemoval)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {archive_compressed: [target]})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: False)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {})
        # Force a checkpoint-complete candidate; patch the name bound in sync_timedb.
        monkeypatch.setattr(st, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [tar_norm])
        monkeypatch.setattr(st, 'daily_tar_eligible_for_day_close_submit', lambda *_a, **_k: (True, ''))

        import hpcperfstats.dbload.lib.sync_timedb_day_close_manifest as async_day_close_mod

        def _record_submit(self, tar, *, reason, disqualified_daily_tars=None):
            submit_calls.append((tar, reason))
            return True

        monkeypatch.setattr(
            async_day_close_mod.DayCloseManifestCoordinator,
            'enqueue_day_close',
            _record_submit,
        )
        monkeypatch.setattr(
            async_day_close_mod.DayCloseManifestCoordinator,
            'enqueue_day_close',
            lambda self, tar, reason, *, disqualified_daily_tars=None: _record_submit(
                self, tar, reason=reason, disqualified_daily_tars=disqualified_daily_tars,
            ),
        )
        # Do not silence janitor: autouse inline executors run ticks without real threads.
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _ArchivePoolSuccess(), run_once=True)
        out = capsys.readouterr().out
        assert 'closed_raw_guard' not in out
        assert submit_calls
        assert 'chunk ingest summary' in out
    finally:
        shutdown_requested[0] = False

def test_arch_june04_handoff_after_giant_finalize_dispatches_chunk(monkeypatch, tmp_path, capsys):
    """june04 replay: archive finalize with handoff pending defers day_close; chunk ingests."""
    shutdown_requested[0] = False
    chunk_summaries = []
    try:
        archive_dir = tmp_path / 'archive'
        daily_dir = tmp_path / 'daily'
        archive_dir.mkdir()
        daily_dir.mkdir()
        tar_norm = os.path.normpath(str(daily_dir / '2026-06-04.tar'))
        open(tar_norm, 'wb').close()
        handoff_path = str(tmp_path / 'host-handoff' / '1782242314')
        os.makedirs(os.path.dirname(handoff_path), exist_ok=True)
        with open(handoff_path, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn001\n')
        target = str(tmp_path / 'host-archive' / '1782242315')
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', encoding='utf-8') as fh:
            fh.write('1000 job cn002\n')
        archive_compressed = str(daily_dir / '2026-06-04.tar.gz')
        open(archive_compressed, 'wb').close()

        class _HandoffDayRawRemoval:
            enabled = True
            on_handoff_to_ingest = None
            on_pipeline_complete = None

            def __init__(self, **_kwargs):
                pass

            def discover_manifest_handoffs(self):
                return [(tar_norm, [handoff_path])]

            def discover_closed_raw_on_disk_handoffs(self):
                return []

            def any_active_raw_removal_work(self):
                return False

            def consumed_paths(self):
                return set()

            def paths_pending_delete(self):
                return set()

            def any_needs_delete_phase(self):
                return False

            def any_needs_tar_drop_finish(self):
                return False

            def days_needing_delete_oldest_first(self):
                return []

            def days_needing_tar_drop_oldest_first(self):
                return []

            def count_days_waiting_on_ingest(self):
                return 0

            def shutdown(self, wait=True):
                del wait

        class _ArchivePoolSuccess:

            def map_async(self, _fn, items):

                class _R:

                    def ready(self):
                        return True

                    def get(self):
                        return [st.ArchiveAppendOutcome(redis_merge_ok=False) for _ in items]
                return _R()
        real_log_print = st.log_print

        def counting_log_print(*args, **kwargs):
            msg = ' '.join((str(a) for a in args))
            if 'chunk ingest summary' in msg:
                chunk_summaries.append(msg)
            return real_log_print(*args, **kwargs)

        def fake_rescan(*_a, **_k):
            if fake_rescan.calls == 0:
                fake_rescan.calls += 1
                return [target]
            shutdown_requested.__setitem__(0, True)
            return []
        fake_rescan.calls = 0
        from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import StartupArchiveScanCoordinator
        _supervisor_startup_preflight_disabled(monkeypatch)
        monkeypatch.setattr(st, 'DayRawRemovalCoordinator', _HandoffDayRawRemoval)
        monkeypatch.setattr(st, 'log_print', counting_log_print)
        monkeypatch.setattr(st, 'sleep_until_shutdown', lambda *_a, **_k: None)
        monkeypatch.setattr(st, '_sync_timedb_ingest_inline_requested', lambda: True)
        monkeypatch.setattr(st, 'add_stats_file_to_db', lambda _lock, path, **_k: (path, True, True, 0.0))
        monkeypatch.setattr(st, 'rescan_pending_stats_files', fake_rescan)
        monkeypatch.setattr(st, 'build_archive_mapping', lambda *_a, **_k: {archive_compressed: [target]})
        monkeypatch.setattr(st, 'seal_dirty_daily_archives', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'remove_verified_archived_raw_files', lambda *a, **k: None, raising=False)
        monkeypatch.setattr(st, 'close_old_connections', lambda: None)
        monkeypatch.setattr(st.connections, 'close_all', lambda: None)
        monkeypatch.setattr(st, 'head_timestamp_present_in_db', lambda *_a, **_k: False)
        monkeypatch.setattr(st, 'tgz_archive_dir', str(daily_dir))
        monkeypatch.setattr(StartupArchiveScanCoordinator, 'wait_for_snapshot', lambda self, *, allow_build=False: None)
        monkeypatch.setattr(archive_helpers, 'build_live_unprocessed_by_tar_for_reconcile', lambda *_a, **_k: {})
        monkeypatch.setattr(archive_helpers, 'days_ingest_complete_by_checkpoint', lambda *_a, **_k: [tar_norm])
        monkeypatch.setattr(janitor_mod.ArchiveJanitor, 'signal_work_available', lambda self: None)
        st.run_sync_timedb_supervisor_loop(str(archive_dir), 'all', None, '.hpc', object(), _ArchivePoolSuccess(), run_once=True)
        out = capsys.readouterr().out
        assert 'archive_finalize defer immediate day_close reason=handoff_priority' in out or 'immediate day_close defer context=chunk_end reason=handoff_priority' in out
        assert chunk_summaries
        assert 'day_close handoff requeue' in out
    finally:
        shutdown_requested[0] = False

def test_light_pass_archive_finalize_deferred_no_longer_exists():
    """Archive finalize defer must signal janitor work, not archive_finalize_deferred maintenance."""
    import inspect
    import hpcperfstats.dbload.sync_timedb as sync_mod
    source = inspect.getsource(sync_mod)
    assert 'archive_finalize_deferred' not in source
    assert 'archive_finalize_day_close_deferred' not in source
    assert 'signal_work_available' in source
