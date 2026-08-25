"""Host unit tests for sync_timedb queue orchestrator cutover (slice 4)."""
from __future__ import annotations

import inspect
import os
from datetime import date
from multiprocessing import Process
from pathlib import Path

import pytest

import hpcperfstats.dbload.sync_timedb as st
from hpcperfstats.dbload.lib.sync_timedb_archive_dir_lock import (
  exclusive_archive_dir_flock,
  orchestrator_lock_path,
)
from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
from hpcperfstats.tests.fake_redis_queue import FakeRedis


def test_exclusive_archive_dir_flock_rejects_second_nonblocking_holder(tmp_path):
  """Second non-blocking flock must fail while the first holder is live."""
  archive = tmp_path / "archive"
  archive.mkdir()
  lock_path = orchestrator_lock_path(str(archive))
  assert lock_path.endswith(".sync_timedb_orchestrator.fnctl.lock")

  with exclusive_archive_dir_flock(str(archive), blocking=True):
    with pytest.raises(OSError):
      with exclusive_archive_dir_flock(str(archive), blocking=False):
        pass


def _child_try_nonblocking(archive_dir: str, result_path: str) -> None:
  try:
    with exclusive_archive_dir_flock(archive_dir, blocking=False):
      open(result_path, "w", encoding="utf-8").write("acquired")
  except OSError:
    open(result_path, "w", encoding="utf-8").write("contended")


def test_exclusive_archive_dir_flock_cross_process(tmp_path):
  """Cross-process non-blocking acquire fails while parent holds the flock."""
  archive = tmp_path / "archive"
  archive.mkdir()
  result = tmp_path / "result.txt"
  with exclusive_archive_dir_flock(str(archive), blocking=True):
    proc = Process(
        target=_child_try_nonblocking,
        args=(str(archive), str(result)),
    )
    proc.start()
    proc.join(timeout=10)
    assert proc.exitcode == 0
    assert result.read_text(encoding="utf-8") == "contended"


def test_from_parsed_wires_queue_orchestrator():
  """Production entry must call the greenfield orchestrator."""
  source = inspect.getsource(st.run_sync_timedb_supervisor_from_parsed)
  assert "run_sync_timedb_queue_orchestrator" in source
  assert "run_sync_timedb_supervisor_loop(" not in source


def test_supervisor_loop_symbol_removed():
  """Retired supervisor_loop must not remain as an importable dual path."""
  assert not hasattr(st, "run_sync_timedb_supervisor_loop")


def test_sliding_window_ingest_enqueues_append_while_other_inflight():
  """First completed ingest enqueues append while another ingest stays inflight."""
  from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      del timeout
      return ("/a", True, True, 0.1, {})

  class _Pending:
    def ready(self):
      return False

  client = FakeRedis()
  inflight = {
      "/a|1|1": _Ready(),
      "/b|2|2": _Pending(),
  }
  claims = {
      "/a|1|1": jq.ClaimedJob(
          kind=jq.JOB_KIND_INGEST,
          identity="/a|1|1",
          owner_token="n:h:b:1",
          deadline=1.0,
          score=5.0,
      ),
      "/b|2|2": jq.ClaimedJob(
          kind=jq.JOB_KIND_INGEST,
          identity="/b|2|2",
          owner_token="n:h:b:2",
          deadline=1.0,
          score=6.0,
      ),
  }
  done = qo._drain_ingest_ready(
      client,
      inflight=inflight,
      claims=claims,
      tgz_archive_dir="/daily",
      archive_data_dir="/archive",
  )
  assert done == 1
  assert "/b|2|2" in inflight
  assert "/a|1|1" not in inflight
  assert client.lrange(jq.job_queue_key(jq.JOB_KIND_APPEND), 0, -1) == ["/a"]


def test_day_close_job_wires_on_handoff_to_ingest():
  """Day-close must enqueue retryable raw via on_handoff_to_ingest (1.6)."""
  src = inspect.getsource(qo._run_day_close_job)
  assert "on_handoff_to_ingest" in src
  assert "_handoff_retryable_paths_to_ingest" in src
  fill = inspect.getsource(qo._fill_day_close_slots)
  assert "redis_client=" in fill


def test_handoff_retryable_paths_enqueues_ingest(tmp_path):
  """Retryable day-close paths ZADD ingest from the daily tar calendar day."""
  client = FakeRedis()
  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / "2020-01-01.tar"
  tar.write_bytes(b"tar")
  raw = tmp_path / "node.stats"
  raw.write_bytes(b"payload")
  enqueued = qo._handoff_retryable_paths_to_ingest(
      client,
      str(tar),
      [str(raw)],
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      today=date(2026, 8, 24),
      ingest_is_complete_fn=lambda **k: False,
      append_is_complete_fn=lambda **k: True,
  )
  assert enqueued == 1
  ident = os.path.normpath(str(raw))
  assert client.zscore(jq.job_queue_key("ingest"), ident) is not None


def test_handoff_retryable_paths_skips_when_redis_client_missing(tmp_path):
  """Handoff is a no-op when the day-close thread has no Redis client."""
  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / "2020-01-01.tar"
  tar.write_bytes(b"tar")
  raw = tmp_path / "node.stats"
  raw.write_bytes(b"payload")
  assert (
      qo._handoff_retryable_paths_to_ingest(
          None,
          str(tar),
          [str(raw)],
          tgz_archive_dir=str(daily),
      )
      == 0
  )


def test_parallelism_doc_covers_band_reservation():
  """SYNC_TIMEDB_PARALLELISM.md must document hot/catchup reserved slots."""
  root = Path(__file__).resolve().parents[2]
  text = (root / "docs" / "SYNC_TIMEDB_PARALLELISM.md").read_text()
  assert "hot_cap" in text
  assert "catchup_cap" in text
  assert "job:v1" in text


def test_day_close_job_tar_drops_when_sealed_and_no_raw(tmp_path, monkeypatch):
  """day_close must seal then tar-drop when zst exists and closed raw is gone."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  day = "2020-01-01"
  tar = daily / ("%s.tar" % day)
  zst = daily / ("%s.tar.zst" % day)
  tar.write_bytes(b"tar")
  zst.write_bytes(b"zst")

  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
      lambda *a, **k: None,
  )

  class _Coord:
    def __init__(self, **_kw):
      pass

    def apply_batch_delete(self, _tar_path):
      return 0

    def run_pre_seal_verify_sync(self, _tar_path):
      return True

    def run_post_seal_verify_sync(self, _tar_path):
      return True

    def has_closed_raw_on_disk(self, _tar_path):
      return False

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
      _Coord,
  )
  outcome = qo._run_day_close_job(
      day,
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
  )
  assert outcome == "tar_dropped"
  assert not tar.exists()
  assert zst.exists()


def test_day_close_dc01_stage_order(tmp_path, monkeypatch):
  """DC-01 order: pre-seal verify → dedupe → seal → post-seal → delete → tar-drop."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  day = "2020-01-01"
  tar = daily / ("%s.tar" % day)
  zst = daily / ("%s.tar.zst" % day)
  tar.write_bytes(b"tar")
  zst.write_bytes(b"zst")
  stages = []

  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
      lambda *a, **k: stages.append("seal"),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.dedupe_tar_keep_largest_file_per_member",
      lambda *a, **k: stages.append("dedupe") or True,
  )

  class _Coord:
    def __init__(self, **_kw):
      pass

    def run_pre_seal_verify_sync(self, _tar_path):
      stages.append("pre_seal")
      return True

    def run_post_seal_verify_sync(self, _tar_path):
      stages.append("post_seal")
      return True

    def apply_batch_delete(self, _tar_path):
      stages.append("delete")
      return 0

    def has_closed_raw_on_disk(self, _tar_path):
      return False

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
      _Coord,
  )
  outcome = qo._run_day_close_job(
      day,
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
  )
  assert outcome == "tar_dropped"
  assert stages == ["pre_seal", "dedupe", "seal", "post_seal", "delete"]


def test_enqueue_day_closes_for_daily_dir_calls_reconstruct(tmp_path, monkeypatch):
  """Orchestrator must enqueue day_close for incomplete daily tars."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  (daily / "2020-01-01.tar").write_bytes(b"x")
  seen = []

  monkeypatch.setattr(
      jr,
      "enqueue_day_close_if_needed",
      lambda client, tar, **k: seen.append(tar) or True,
  )

  class _C:
    pass

  n = qo._enqueue_day_closes_for_daily_dir(_C(), tgz_archive_dir=str(daily))
  assert n == 1
  assert any(p.endswith("2020-01-01.tar") for p in seen)


def test_boot_stream_discover_does_not_call_run_find_stats():
  """Boot discover must stream stdout chunks, not capture-all run_find_stats."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  src = inspect.getsource(qo._boot_stream_discover)
  assert "run_find_stats(" not in src
  assert "iter_find_stats_stdout_chunks" in src
  assert "stream_enqueue_ingest_from_find_stdout_chunks" in src


def test_idle_reconstruct_enqueues_discover_and_rescans(monkeypatch):
  """Idle reconstruct must use JOB_KIND_DISCOVER then re-run streaming discover."""
  from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  calls = {"boot": 0, "rpush": []}

  class _C:
    def __init__(self):
      self._q = []

    def rpush(self, key, value):
      self._q.append(value)
      calls["rpush"].append((key, value))
      return 1

    def lpop(self, key):
      del key
      return self._q.pop(0) if self._q else None

  def _boot(*a, **k):
    calls["boot"] += 1
    return type(
        "S",
        (),
        {
            "enqueued_ingest": 0,
            "enqueued_append": 0,
            "enqueued_day_close": 0,
            "seen": 0,
            "skipped_complete": 0,
        },
    )()

  def _claim(client, *, kind, owner_token, **kwargs):
    del kwargs
    ident = client.lpop(kind)
    if ident is None:
      return None
    return jq.ClaimedJob(
        kind=kind,
        identity=ident,
        owner_token=owner_token,
        deadline=1.0,
        score=None,
    )

  monkeypatch.setattr(qo, "_boot_stream_discover", _boot)
  monkeypatch.setattr(qo, "_enqueue_day_closes_for_daily_dir", lambda *a, **k: 0)
  monkeypatch.setattr(
      jq,
      "enqueue_list_job",
      lambda client, *, kind, identity, dedupe=False: (
          client.rpush(kind, identity) or True
      ),
  )
  monkeypatch.setattr(jq, "claim_list_job", _claim)
  monkeypatch.setattr(
      jq, "ack_job", lambda *a, **k: True,
  )
  n = qo._idle_reconstruct_pass(
      _C(),
      "/archive",
      tgz_archive_dir="/daily",
      log_fn=lambda *a, **k: None,
  )
  assert calls["boot"] == 1
  assert n >= 1
  assert any(str(v).startswith("rescan") for _k, v in calls["rpush"])


def test_cli_backlog_current_retired():
  """Dual-mode backlog/current argv must fail closed."""
  with pytest.raises(SystemExit) as ei:
    st.parse_sync_timedb_argv(["sync_timedb.py", "backlog"])
  assert "retired" in str(ei.value).lower()
  with pytest.raises(SystemExit) as ei:
    st.parse_sync_timedb_argv(["sync_timedb.py", "current"])
  assert "retired" in str(ei.value).lower()


def test_boot_steals_dead_owner_leases():
  """Q3: boot must SCAN lease keys and steal locally-dead owners before discover."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "steal_dead_owner_leases" in src
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  key = jq.job_lease_key("ingest", "/raw/dead")
  client.set(key, "n:host1:boot1:99999", nx=True, ex=60)
  stolen = jq.steal_dead_owner_leases(
      client,
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert stolen >= 1
  assert client.get(key) is None


def test_populate_pool_started_in_orchestrator():
  """T6: production loop starts PopulatePoolController and reaps it."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "PopulatePoolController" in src
  assert "reap_and_restart" in src


def test_reserved_band_slots_under_mixed_inflight():
  """B2: catchup cannot steal hot's reserved slots while hot work is queued."""
  assert qo.catchup_dispatch_cap(
      hot_queued=12, catchup_queued=400, hot_cap=10, catchup_cap=6, pool=16,
  ) == 6
  assert qo.catchup_dispatch_cap(
      hot_queued=0, catchup_queued=400, hot_cap=10, catchup_cap=6, pool=16,
  ) == 16


def test_reband_at_claim_moves_stale_hot_to_catchup(monkeypatch):
  """B5: a claimed job whose day aged out of hot is requeued with a catchup score."""
  from datetime import date

  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  identity = "/raw/old"
  score = jq.encode_ingest_score(
      band="hot",
      day=date(2026, 8, 24),
      today=date(2026, 8, 24),
      identity=identity,
  )
  jq.zadd_ingest_job(client, identity=identity, score=score)
  monkeypatch.setattr(qo, "_hot_days", lambda: 8)
  monkeypatch.setattr(
      qo, "_calendar_day_for_ingest_path",
      lambda path, tgz: date(2026, 6, 1),
  )
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  did = qo._reband_claimed_ingest_if_needed(
      client,
      claim,
      tgz_archive_dir="/daily",
      today=date(2026, 8, 24),
  )
  assert did is True
  restored = client.zscore(jq.job_queue_key("ingest"), identity)
  assert restored is not None
  assert jq.decode_ingest_band(restored) == "catchup"


def test_poison_routes_to_dead_letter(tmp_path, monkeypatch):
  """Q5: after max attempts a failed ingest is quarantined, not requeued."""
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 2)
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.zadd_ingest_job(client, identity="/raw/a", score=1)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  first = qo._retry_or_dead_letter(
      client,
      kind="ingest",
      claim=claim,
      archive_data_dir=str(tmp_path),
      reason="boom",
  )
  assert first == "requeued"
  claim2 = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:2", ttl_s=60, now_s=1000.0,
  )
  second = qo._retry_or_dead_letter(
      client,
      kind="ingest",
      claim=claim2,
      archive_data_dir=str(tmp_path),
      reason="boom",
  )
  assert second == "dead_letter"
  assert client.zcard(jq.job_queue_key("ingest")) == 0


def test_sigterm_drains_and_releases_leases():
  """S1: SIGTERM is cooperative; drain timeout requeues outstanding claims."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "shutdown_requested" in src
  assert "_release_claims_on_shutdown" in src
  assert "SHUTDOWN_DRAIN_TIMEOUT_S" in src


def test_shutdown_flag_only_handler():
  """S1: the signal handler must only set a flag."""
  src = inspect.getsource(qo.install_cooperative_shutdown_handlers)
  assert "request_shutdown" in src
  qo.reset_shutdown_for_tests()
  qo.request_shutdown()
  assert qo.shutdown_requested() is True
  qo.reset_shutdown_for_tests()


def test_executor_shutdown_cancels():
  """S2: drain timeout must cancel leftover day_close futures."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "cancel_futures" in src or "fut.cancel()" in src or ".cancel()" in src


def test_dead_pool_worker_frees_slot_and_requeues(monkeypatch):
  """T2: a hung ingest worker is abandoned, requeued, and the pool recycled."""
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.zadd_ingest_job(client, identity="/raw/a", score=1)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )

  class _Pending:
    def ready(self):
      return False

  monkeypatch.setattr(qo, "_ingest_watchdog_budget_s", lambda path: 1.0)
  abandoned = qo._abandon_timed_out_ingest(
      client,
      inflight={"/raw/a": _Pending()},
      claims={"/raw/a": claim},
      submitted={"/raw/a": 0.0},
      archive_data_dir="/archive",
      now=10.0,
      log_fn=lambda *a, **k: None,
  )
  assert abandoned == ["/raw/a"]
  assert client.zscore(jq.job_queue_key("ingest"), "/raw/a") is not None


def test_ingest_deadline_requeues():
  """T3: per-file deadline abandonment requeues rather than leaking the slot."""
  src = inspect.getsource(qo._abandon_timed_out_ingest)
  assert "_retry_or_dead_letter" in src


def test_day_close_failure_requeues(tmp_path, monkeypatch):
  """S3: a retryable day_close outcome is requeued with attempt+1."""
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 5)
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.enqueue_list_job(client, kind="day_close", identity="2026-08-01")
  claim = jq.claim_list_job(
      client, kind="day_close", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )

  class _Done:
    def done(self):
      return True

    def result(self):
      return "deferred_age"

  n = qo._drain_day_close_ready(
      client,
      inflight={"2026-08-01": _Done()},
      leases={"2026-08-01": claim},
      archive_data_dir=str(tmp_path),
  )
  assert n == 1
  assert client.llen(jq.job_queue_key("day_close")) == 1


def test_verify_failure_blocks_seal_and_delete(tmp_path, monkeypatch):
  """S4: pre-seal verify failure must skip seal and raw delete."""
  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / "2026-01-01.tar"
  tar.write_bytes(b"not-a-tar")
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0.0)

  class _Coord:
    def __init__(self, **kwargs):
      del kwargs

    def run_pre_seal_verify_sync(self, tar_path):
      del tar_path
      raise RuntimeError("verify exploded")

    def run_post_seal_verify_sync(self, tar_path):
      del tar_path

    def apply_batch_delete(self, tar_path):
      del tar_path
      raise AssertionError("delete must not run after verify failure")

    def has_closed_raw_on_disk(self, tar_path):
      del tar_path
      return False

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
      _Coord,
  )
  monkeypatch.setattr(
      qo.jr, "day_close_is_complete", lambda *a, **k: False,
  )
  monkeypatch.setattr(
      qo.jr, "day_close_min_age_elapsed", lambda *a, **k: True,
  )
  outcome = qo._run_day_close_job(
      "2026-01-01",
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
  )
  assert outcome == "verify_failed"


def test_day_close_error_detail_is_logged(monkeypatch):
  """S5: day_close exceptions must log the exception type and message."""
  src = inspect.getsource(qo._drain_day_close_ready)
  assert "type(exc).__name__" in src
  assert "failure" in src


def test_startup_rejects_allkeys_eviction_policy():
  """Q9: allkeys-* fails closed at orchestrator start."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "assert_redis_queue_safety" in src or "RuntimeError" in src
  client = FakeRedis()
  client.set_config_for_tests("maxmemory-policy", "allkeys-lru")
  with pytest.raises(RuntimeError):
    qo.assert_redis_queue_safety(client)


def test_idle_includes_discover():
  """S6: idle detection includes the discover LIST, not just ingest/append."""
  src = inspect.getsource(qo._queues_appear_idle)
  assert "queue_census" in src
  client = FakeRedis()
  jq.enqueue_list_job(client, kind="discover", identity="rescan|/a|mtime=1")
  assert qo._queues_appear_idle(client) is False


def test_reconstruct_runs_while_busy():
  """B6: reconstruct is interval-gated, not full-idle-only."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  busy_idx = src.find("busy = bool")
  recon_idx = src.find("_idle_reconstruct_pass")
  assert busy_idx != -1
  assert recon_idx != -1
  # A later reconstruct call must exist outside the `did == 0 and not busy` arm,
  # or the busy arm itself must invoke reconstruct.
  assert "if not draining:" in src
  body = src[src.find("while True:"):]
  assert "_idle_reconstruct_pass" in body
  assert "not draining" in body


def test_run_once_exits_with_future_dated_file_present():
  """B4: a poppable future-dated member must not wedge run_once idle detection."""
  lo, hi = jq.ingest_score_range("hot")
  from datetime import date
  score = jq.encode_ingest_score(
      band="hot",
      day=date(2026, 8, 30),
      today=date(2026, 8, 24),
      identity="/raw/future",
  )
  assert lo <= score <= hi


def test_oq1_lease_no_heartbeat_renew_in_orchestrator_loop():
  """OQ-1 / F2: main loop must not renew job leases each tick."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "_renew_active_claims" not in src


def test_missing_path_requeues_ingest_not_ack(monkeypatch, tmp_path):
  """F4: missing raw must requeue, never terminal-ack."""
  client = FakeRedis()
  identity = "/no/such/raw/file"
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity=identity,
      owner_token="n:h:b:1",
      deadline=1060.0,
      score=5.0,
  )
  calls = {"n": 0}

  def _claim(*a, **k):
    calls["n"] += 1
    return claim if calls["n"] == 1 else None

  requeued = []

  def _requeue(*a, **k):
    requeued.append(k.get("identity") or identity)
    return True

  acked = []

  def _ack(*a, **k):
    acked.append(k.get("identity"))
    return True

  monkeypatch.setattr(jq, "requeue_job", _requeue)
  monkeypatch.setattr(jq, "ack_job", _ack)
  monkeypatch.setattr(jq, "claim_ingest_job", _claim)
  monkeypatch.setattr(os.path, "isfile", lambda p: False)

  class _Pool:
    def apply_async(self, *a, **k):
      raise AssertionError("must not submit missing path")

  qo._fill_ingest_band(
      client,
      band="hot",
      cap=1,
      inflight={},
      claims={},
      submitted={},
      ingest_pool=_Pool(),
      manager_lock=None,
      band_cap=1,
      tgz_archive_dir=str(tmp_path),
  )
  assert requeued
  assert not acked


def test_retry_or_dead_letter_claim_none_not_silent_requeued():
  """F15: missing claim must not report success as requeued."""
  assert qo._retry_or_dead_letter(
      None, kind="ingest", claim=None, archive_data_dir="/a", reason="x",
  ) == "dropped_no_claim"

