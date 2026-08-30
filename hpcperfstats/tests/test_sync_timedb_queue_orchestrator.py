"""Host unit tests for sync_timedb queue orchestrator cutover (slice 4)."""
from __future__ import annotations

import inspect
import os
import time
from datetime import date, datetime
from multiprocessing import Process
from pathlib import Path

import pytest

import hpcperfstats.dbload.sync_timedb as st
from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_progress_report as pr
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
from hpcperfstats.dbload.lib.sync_timedb_archive_dir_lock import (
  exclusive_archive_dir_flock,
  orchestrator_lock_path,
)
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


def test_drain_records_ingest_marks_before_ack(monkeypatch):
  """P0-1: drain must persist file-complete/zero-host marks before ACK."""
  recorded = []

  def _record(result, **_kwargs):
    recorded.append(result)

  monkeypatch.setattr(st, "_record_ingest_marks_from_worker_result", _record)
  acked = []

  def _ack(*a, **k):
    acked.append(k.get("identity"))
    return True

  monkeypatch.setattr(jq, "ack_job", _ack)

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      del timeout
      return ("/a", True, True, 0.1, {"outcome": "ingested"})

  client = FakeRedis()
  inflight = {"/a": _Ready()}
  claims = {
      "/a": jq.ClaimedJob(
          kind=jq.JOB_KIND_INGEST,
          identity="/a",
          owner_token="n:h:b:1",
          deadline=1.0,
          score=5.0,
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
  assert recorded
  assert acked == ["/a"]
  worker_src = inspect.getsource(qo._ingest_worker)
  assert "_record_ingest_marks_from_worker_result" in worker_src
  jid_src = inspect.getsource(st.run_sync_timedb_jid_ingest)
  assert "_record_ingest_marks_from_worker_result" in jid_src


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
  assert outcome == "complete"
  assert not tar.exists()
  assert zst.exists()


def test_day_close_dc01_stage_order(tmp_path, monkeypatch):
  """DC-01 order: reconcile → pre-seal verify → dedupe → seal → post-seal → delete."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import ReconcileResult

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
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.reconcile_open_tar_with_sealed_zst",
      lambda *a, **k: stages.append("reconcile") or ReconcileResult(
          True, "noop", "already_equivalent",
      ),
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

    def remaining_raw_paths_blocking_tar_drop(self, _tar_path):
      return {}

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
  assert outcome == "complete"
  assert stages == ["reconcile", "pre_seal", "dedupe", "seal", "post_seal", "delete"]


def test_day_close_reconcile_invoked_before_dedupe():
  """Regression: reconcile hook runs before dedupe in day_close."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  src = inspect.getsource(qo._run_day_close_job)
  assert src.index("reconcile_open_tar_with_sealed_zst(") < src.index(
      "dedupe_tar_keep_largest_file_per_member(",
  )


def test_day_close_job_returns_complete_after_tar_drop(tmp_path, monkeypatch):
  """Tar-path identity returns complete after tar-drop with no remaining raw."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / "2020-01-01.tar"
  zst = daily / "2020-01-01.tar.zst"
  tar.write_bytes(b"tar")
  zst.write_bytes(b"zst")
  ident = str(tar)

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
      ident,
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
  )
  assert outcome == "complete"
  assert not tar.exists()


def test_day_close_job_remaining_raw_returns_incomplete_raw_not_fake_sealed(
    tmp_path, monkeypatch,
):
  """No-op seal with remaining raw must not report sealed."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / "2020-01-01.tar"
  tar.write_bytes(b"tar")

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
      return True

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
      _Coord,
  )
  outcome = qo._run_day_close_job(
      "2020-01-01",
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
  )
  assert outcome == "incomplete_raw"
  assert tar.exists()


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
      force=True,
  )
  assert calls["boot"] == 1
  assert n >= 1
  assert any(str(v).startswith("rescan") for _k, v in calls["rpush"])


def test_idle_reconstruct_does_not_log_work_total():
  """Day-line reconstruct_enq owns visibility; no undated work= INFO."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  src = inspect.getsource(qo._idle_reconstruct_pass)
  assert "idle reconstruct work=" not in src


def test_idle_reconstruct_off_main_does_not_run_boot_inline(monkeypatch):
  """P1-10: periodic reconstruct must not block MainThread on GNU find."""
  from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  calls = {"boot": 0, "submit": 0}

  class _C:
    def rpush(self, *a, **k):
      del a, k
      return 1

  monkeypatch.setattr(
      qo, "_boot_stream_discover", lambda *a, **k: calls.__setitem__("boot", 1),
  )
  monkeypatch.setattr(qo, "_enqueue_day_closes_for_daily_dir", lambda *a, **k: 0)
  monkeypatch.setattr(
      jq, "enqueue_list_job", lambda *a, **k: True,
  )
  monkeypatch.setattr(
      qo,
      "_submit_background_discover",
      lambda *a, **k: calls.__setitem__("submit", calls["submit"] + 1),
  )
  qo._last_idle_reconstruct_mono = 0.0
  n = qo._idle_reconstruct_pass(
      _C(),
      "/archive",
      tgz_archive_dir="/daily",
      force=False,
  )
  assert calls["boot"] == 0
  assert calls["submit"] == 1
  assert n == 0


def test_cli_backlog_current_retired():
  """Dual-mode backlog/current argv must fail closed."""
  with pytest.raises(SystemExit) as ei:
    st.parse_sync_timedb_argv(["sync_timedb.py", "backlog"])
  assert "retired" in str(ei.value).lower()
  with pytest.raises(SystemExit) as ei:
    st.parse_sync_timedb_argv(["sync_timedb.py", "current"])
  assert "retired" in str(ei.value).lower()


def test_cli_no_arg_does_not_default_five_day_window():
  """P0-2: no-arg and `once` must stream the full find (startdate=enddate=None)."""
  run_once, start, end = st.parse_sync_timedb_argv(["sync_timedb.py"])
  assert run_once is False
  assert start is None and end is None
  run_once, start, end = st.parse_sync_timedb_argv(["sync_timedb.py", "once"])
  assert run_once is True
  assert start is None and end is None
  run_once, start, end = st.parse_sync_timedb_argv(
      ["sync_timedb.py", "2026-08-01"],
  )
  assert run_once is False
  assert start is not None and start.date() == date(2026, 8, 1)


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


def test_orchestrator_starts_pools_before_boot_discover_submit():
  """Boot populate stall: populate.start before _submit_background_discover."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  i_pop = src.find("populate.start")
  i_sub = src.find("_submit_background_discover")
  assert i_pop > 0 and i_sub > 0 and i_pop < i_sub
  assert "boot discover submitted" in src
  # Sync discover must not run on MainThread before fill; only via background.
  before_submit = src.split("_submit_background_discover", 1)[0]
  assert "_boot_stream_discover(" not in before_submit


def test_orchestrator_boot_discover_submitted_log_after_submit():
  """Post-boot silence: 'submitted' must not log before _submit returns."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  i_sub = src.find("_submit_background_discover(")
  i_log = src.find("boot discover submitted")
  assert i_sub > 0 and i_log > i_sub


def test_submit_background_discover_does_not_deadlock_on_lock():
  """Nested Lock+_discover_executor must not hang MainThread (hpcperfstats03)."""
  import threading

  qo._shutdown_background_discover()
  client = FakeRedis()
  done = {"ok": False}

  def _run() -> None:
    qo._submit_background_discover(
        client,
        "/tmp/archive-does-not-need-to-exist",
        tgz_archive_dir="/tmp/daily",
        log_fn=None,
        mtime_days=None,
        startdate=None,
        enddate=None,
    )
    done["ok"] = True

  thr = threading.Thread(target=_run, name="submit-deadlock-probe")
  thr.start()
  thr.join(timeout=5.0)
  assert not thr.is_alive(), "submit deadlocked on nested discover_bg Lock"
  assert done["ok"] is True
  assert qo._discover_bg_future is not None
  qo._shutdown_background_discover()


def test_fill_append_slots_missing_paths_ack_and_bounded(monkeypatch, tmp_path):
  """Missing append identities ACK-drop with skip budget (not unbounded requeue)."""
  client = FakeRedis()
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_APPEND,
      identity="/no/such/raw/file",
      owner_token="n:h:b:1",
      deadline=1060.0,
      score=0.0,
  )
  calls = {"claim": 0, "ack": 0, "requeue": 0}

  def _claim(*a, **k):
    calls["claim"] += 1
    return claim

  def _ack(*a, **k):
    calls["ack"] += 1
    return True

  def _requeue(*a, **k):
    calls["requeue"] += 1
    return True

  monkeypatch.setattr(jq, "claim_list_job", _claim)
  monkeypatch.setattr(jq, "ack_job", _ack)
  monkeypatch.setattr(jq, "requeue_job", _requeue)
  monkeypatch.setattr(os.path, "isfile", lambda p: False)

  class _Pool:
    def apply_async(self, *a, **k):
      raise AssertionError("must not submit missing path")

  qo._fill_append_slots(
      client,
      cap=8,
      inflight={},
      claims={},
      archive_pool=_Pool(),
      tgz_archive_dir=str(tmp_path),
  )
  assert calls["claim"] <= qo.APPEND_FILL_SKIP_BUDGET
  assert calls["ack"] >= 1
  assert calls["requeue"] == 0


def test_boot_stream_discover_uses_discover_append_complete():
  """Discover skip-complete must use discover_append_is_complete (no sealed wait)."""
  src = inspect.getsource(qo._boot_stream_discover)
  assert "discover_append_is_complete" in src


def test_discover_raw_needs_tar_append_cold_never_calls_populate_wait(
  tmp_path, monkeypatch,
):
  """Cold Redis: discover probe enqueues append without populate_and_wait."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah
  from hpcperfstats.dbload.lib import sync_timedb_archive_members_redis as amr
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  daily = tmp_path / "daily"
  daily.mkdir()
  raw = tmp_path / "host" / "2026-06-03.123456"
  raw.parent.mkdir(parents=True)
  raw.write_bytes(b"x" * 64)
  sealed = daily / "2026-06-03.tar.zst"
  sealed.write_bytes(b"fake-zst")

  calls = {"wait": 0, "enqueue": 0}

  monkeypatch.setattr(ah, "stats_file_is_active_segment", lambda _p: False)
  monkeypatch.setattr(
      ah, "_derive_stats_path_date",
      lambda _p, _ts=None: date(2026, 6, 3),
  )
  monkeypatch.setattr(ah, "daily_archive_populate_source_exists", lambda _c: True)
  monkeypatch.setattr(ah, "_lookup_daily_archive_members_cache", lambda _c: None)
  monkeypatch.setattr(amr, "archive_members_redis_enabled", lambda: True)
  monkeypatch.setattr(
      amr, "build_archive_members_redis_keys",
      lambda key: type("K", (), {"hash_key": "h", "complete_key": "c"})(),
  )
  monkeypatch.setattr(
      amr, "get_archive_members_redis_client", lambda required=True: object(),
  )
  monkeypatch.setattr(amr, "redis_member_match_when_warm", lambda *a, **k: None)
  monkeypatch.setattr(
      amr,
      "request_archive_members_populate_and_wait",
      lambda *a, **k: calls.__setitem__("wait", calls["wait"] + 1),
  )
  monkeypatch.setattr(
      amr,
      "enqueue_archive_members_populate",
      lambda *a, **k: calls.__setitem__("enqueue", calls["enqueue"] + 1) or True,
  )

  needs = jr.discover_raw_needs_tar_append(str(raw), str(daily))
  assert needs is True
  assert calls["wait"] == 0
  assert calls["enqueue"] == 1
  assert jr.discover_append_is_complete(path=str(raw), tgz_archive_dir=str(daily)) is False


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


def test_catchup_dispatch_cap_expands_when_hot_submitted_zero():
  """RC1: unused pool slots go to catchup when hot is queued but unsubmittable."""
  assert qo.catchup_dispatch_cap(
      hot_queued=531,
      catchup_queued=2010,
      hot_cap=16,
      catchup_cap=8,
      pool=24,
      hot_submitted=0,
  ) == 24
  assert qo.catchup_dispatch_cap(
      hot_queued=531,
      catchup_queued=2010,
      hot_cap=16,
      catchup_cap=8,
      pool=24,
      hot_submitted=3,
  ) == 8


def test_rc7_unused_slot_catchup_when_hot_submitted_nonzero(monkeypatch, tmp_path):
  """RC7: after one hot submit, unused slots still expand catchup (and elevated hot)."""
  pool = 3
  hot_cap = 2
  catchup_cap = 1
  inflight: dict[str, object] = {}
  fill_log: list[dict[str, object]] = []

  class _Client:
    def zcount(self, key, lo, hi):
      del key
      hi_f = float(hi)
      # Hot scores are below the catchup floor (1e15).
      if hi_f < 1e15:
        return 50
      return 200

    def zcard(self, key):
      del key
      return 250

  def _fake_fill(
      client,
      *,
      band,
      cap,
      inflight,
      claims,
      submitted,
      ingest_pool,
      manager_lock,
      band_cap=None,
      **kw,
  ):
    del client, claims, submitted, ingest_pool, manager_lock, cap
    fill_log.append({
        "band": band,
        "band_cap": band_cap,
        "probe_depth": kw.get("probe_depth"),
        "inflight_before": len(inflight),
    })
    # Reserved hot: one successful submit then stop.
    if (
        band == "hot"
        and band_cap == hot_cap
        and not any(k.startswith("hot-res-") for k in inflight)
    ):
      inflight["hot-res-0"] = object()
      return 1
    # Reserved catchup + spillover: claim nothing (hot still queued).
    if band == "catchup" and band_cap == catchup_cap:
      return 0
    if band == "hot" and band_cap is None and int(kw.get("probe_depth") or 0) < 32:
      return 0
    # Elevated hot retry: still nothing claimable.
    if band == "hot" and int(kw.get("probe_depth") or 0) >= 32:
      return 0
    # Expanded catchup into remaining pool slots (RC7).
    if band == "catchup" and band_cap == pool:
      if "catch-expand-0" not in inflight:
        inflight["catch-expand-0"] = object()
        return 1
      return 0
    return 0

  monkeypatch.setattr(qo, "_fill_ingest_band", _fake_fill)
  did, hot_q, zcard, hot_n = qo._ingest_coordinator_fill_tick(
      client=_Client(),
      pool_ref=qo.AtomicPoolRef(object()),
      manager_lock=None,
      directory=str(tmp_path),
      tgz_archive_dir=str(tmp_path),
      hot_cap=hot_cap,
      catchup_cap=catchup_cap,
      ingest_pool_size=pool,
      ingest_inflight=inflight,
      ingest_leases={},
      ingest_submitted={},
      skip_budget=8,
      fill_stats=qo._empty_ingest_fill_stats(),
  )
  assert hot_n >= 1
  assert did >= 2
  assert "catch-expand-0" in inflight
  assert any(
      c["band"] == "catchup" and c["band_cap"] == pool for c in fill_log
  ), fill_log
  assert any(
      c["band"] == "hot" and int(c.get("probe_depth") or 0) >= 32 for c in fill_log
  ), fill_log
  assert hot_q == 50
  assert zcard == 250


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
  """T2 retired: submit-age abandon is a no-op (idle stall owns soft-kill)."""
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
  inflight = {"/raw/a": _Pending()}
  claims = {"/raw/a": claim}
  submitted = {"/raw/a": 0.0}
  abandoned = qo._abandon_timed_out_ingest(
      client,
      inflight=inflight,
      claims=claims,
      submitted=submitted,
      archive_data_dir="/archive",
      now=10.0,
      log_fn=lambda *a, **k: None,
  )
  assert abandoned == []
  assert "/raw/a" in inflight
  assert client.get(jq.job_lease_key("ingest", "/raw/a")) is not None


def test_ingest_deadline_requeues():
  """T3 retired: abandon helper must not soft-requeue via retry/dead-letter."""
  src = inspect.getsource(qo._abandon_timed_out_ingest)
  assert "return []" in src
  assert "_retry_or_dead_letter" not in src
  assert "_abandon_timed_out_ingest" not in inspect.getsource(
      qo._ingest_coordinator_loop,
  )

def test_day_close_failure_requeues(tmp_path, monkeypatch):
  """S3: deferred_age requeues without burning a retry attempt."""
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
  assert jq.read_job_attempt(
      client, kind="day_close", identity="2026-08-01",
  ) == 0


def test_day_close_verify_failed_bumps_attempt(tmp_path, monkeypatch):
  """P0-7: verify_failed is a real error and must increment attempts."""
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 5)
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.enqueue_list_job(client, kind="day_close", identity="2026-08-02")
  claim = jq.claim_list_job(
      client, kind="day_close", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )

  class _Done:
    def done(self):
      return True

    def result(self):
      return "verify_failed"

  n = qo._drain_day_close_ready(
      client,
      inflight={"2026-08-02": _Done()},
      leases={"2026-08-02": claim},
      archive_data_dir=str(tmp_path),
  )
  assert n == 1
  assert jq.read_job_attempt(
      client, kind="day_close", identity="2026-08-02",
  ) == 1


def test_day_close_path_identity_records_complete_on_calendar_day(tmp_path):
  """Tar-path identity ACKs complete onto the calendar day, not undated."""
  pr.reset_progress_state_for_tests()
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  ident = "/d/2026-06-07.tar"
  jq.enqueue_list_job(client, kind="day_close", identity=ident)
  claim = jq.claim_list_job(
      client, kind="day_close", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )

  class _Done:
    def done(self):
      return True

    def result(self):
      return "complete"

  n = qo._drain_day_close_ready(
      client,
      inflight={ident: _Done()},
      leases={ident: claim},
      archive_data_dir=str(tmp_path),
  )
  assert n == 1
  assert client.llen(jq.job_queue_key("day_close")) == 0
  days = pr.get_progress_state().snapshot_days()
  assert days["2026-06-07"].counters["complete"] == 1
  line = pr.format_day_progress_line("2026-06-07", days["2026-06-07"])
  assert "complete=1" in line
  undated = pr.get_progress_state().window.undated
  assert int(undated.get("complete", 0) or 0) == 0


def test_day_close_incomplete_raw_requeues_without_ack(tmp_path):
  """Remaining-raw outcome stays on the LIST with attempt 0 and no sealed=."""
  pr.reset_progress_state_for_tests()
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  ident = "/d/2026-06-07.tar"
  jq.enqueue_list_job(client, kind="day_close", identity=ident)
  claim = jq.claim_list_job(
      client, kind="day_close", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )

  class _Done:
    def done(self):
      return True

    def result(self):
      return "incomplete_raw"

  n = qo._drain_day_close_ready(
      client,
      inflight={ident: _Done()},
      leases={ident: claim},
      archive_data_dir=str(tmp_path),
  )
  assert n == 1
  assert client.llen(jq.job_queue_key("day_close")) == 1
  assert jq.read_job_attempt(
      client, kind="day_close", identity=ident,
  ) == 0
  days = pr.get_progress_state().snapshot_days()
  assert days["2026-06-07"].counters["incomplete_raw"] == 1
  assert days["2026-06-07"].counters["sealed"] == 0
  assert days["2026-06-07"].counters["complete"] == 0


def test_day_close_fake_sealed_requeues_without_ack(tmp_path):
  """Leftover sealed outcome must not ACK (hpcperfstats03 fake-sealed)."""
  pr.reset_progress_state_for_tests()
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  ident = "/d/2026-07-15.tar"
  jq.enqueue_list_job(client, kind="day_close", identity=ident)
  claim = jq.claim_list_job(
      client, kind="day_close", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )

  class _Done:
    def done(self):
      return True

    def result(self):
      return "sealed"

  n = qo._drain_day_close_ready(
      client,
      inflight={ident: _Done()},
      leases={ident: claim},
      archive_data_dir=str(tmp_path),
  )
  assert n == 1
  assert client.llen(jq.job_queue_key("day_close")) == 1
  assert jq.read_job_attempt(
      client, kind="day_close", identity=ident,
  ) == 0
  days = pr.get_progress_state().snapshot_days()
  assert days["2026-07-15"].counters["complete"] == 0
  assert days["2026-07-15"].counters["sealed"] == 0
  assert days["2026-07-15"].counters["incomplete_raw"] == 1


def test_fill_day_close_records_dc_run_for_tar_path_identity(
    tmp_path, monkeypatch,
):
  """Fill records dc_run= on the calendar day before the worker finishes."""
  from concurrent.futures import ThreadPoolExecutor

  pr.reset_progress_state_for_tests()
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  ident = "/d/2026-06-07.tar"
  jq.enqueue_list_job(client, kind="day_close", identity=ident)
  monkeypatch.setattr(qo, "_run_day_close_job", lambda *a, **k: "deferred_age")
  monkeypatch.setattr(qo.cfg, "get_sync_day_close_max_inflight", lambda: 1)
  inflight = {}
  leases = {}
  with ThreadPoolExecutor(max_workers=1) as ex:
    n = qo._fill_day_close_slots(
        client,
        executor=ex,
        inflight=inflight,
        leases=leases,
        tgz_archive_dir=str(tmp_path),
        archive_data_dir=str(tmp_path),
        log_fn=lambda *a, **k: None,
    )
  assert n == 1
  days = pr.get_progress_state().snapshot_days()
  assert days["2026-06-07"].counters["dc_run"] == 1
  line = pr.format_day_progress_line("2026-06-07", days["2026-06-07"])
  assert "dc_run=1" in line


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


def test_day_close_claim_vacate_and_stage_enter_logged(tmp_path, monkeypatch):
  """Fill/drain and job body emit claim/vacate/stage_enter breadcrumbs."""
  from concurrent.futures import Future

  from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation import (
      DayCloseYieldError,
  )

  logs = []
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
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.dedupe_tar_keep_largest_file_per_member",
      lambda *a, **k: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.reconcile_open_tar_with_sealed_zst",
      lambda *a, **k: (_ for _ in ()).throw(
          DayCloseYieldError(str(tar), phase="tar_merge", reason="stall_confirmed"),
      ),
  )

  class _Coord:
    def __init__(self, **_kw):
      pass

    def run_pre_seal_verify_sync(self, _tar_path, **_kw):
      return True

    def run_post_seal_verify_sync(self, _tar_path):
      return True

    def apply_batch_delete(self, _tar_path):
      return 0

    def has_closed_raw_on_disk(self, _tar_path):
      return False

    def remaining_raw_paths_blocking_tar_drop(self, _tar_path):
      return {}

    def update_reconcile_progress(self, *_a, **_k):
      pass

    def should_handoff_to_ingest(self, _tar_path):
      return False

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
      _Coord,
  )
  outcome = qo._run_day_close_job(
      day,
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda msg, **k: logs.append(str(msg)),
  )
  assert outcome == "yielded"
  joined = "\n".join(logs)
  assert "stage_enter" in joined and "reconcile_merge" in joined
  assert "stall_confirmed" in joined
  assert "stage_exit" in joined and "result=yielded" in joined

  client = FakeRedis()
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_DAY_CLOSE,
      identity=str(tar),
      owner_token="owner-test",
      deadline=time.time() + 3600,
      score=None,
  )
  fut = Future()
  fut.set_result("yielded")
  qo._drain_day_close_ready(
      client,
      inflight={str(tar): fut},
      leases={str(tar): claim},
      archive_data_dir=str(tmp_path),
      log_fn=lambda msg, **k: logs.append(str(msg)),
  )
  assert any("day_close vacate" in line for line in logs)

  class _Ex:
    def submit(self, fn, *a, **k):
      f = Future()
      f.set_result("complete")
      return f

  monkeypatch.setattr(
      jq,
      "claim_list_job",
      lambda *a, **k: claim,
  )
  monkeypatch.setattr(qo.cfg, "get_sync_day_close_max_inflight", lambda: 1)
  fill_logs = []
  submitted = qo._fill_day_close_slots(
      client,
      executor=_Ex(),
      inflight={},
      leases={},
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda msg, **k: fill_logs.append(str(msg)),
  )
  assert submitted == 1
  assert any("day_close claim" in line for line in fill_logs)


def test_day_close_wait_on_ingest_yield(tmp_path, monkeypatch):
  """Only-waiting-on-ingest must handoff and return yielded (release slot)."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import ReconcileResult

  daily = tmp_path / "daily"
  daily.mkdir()
  day = "2020-01-01"
  tar = daily / ("%s.tar" % day)
  zst = daily / ("%s.tar.zst" % day)
  tar.write_bytes(b"tar")
  zst.write_bytes(b"zst")
  handoffs = []
  logs = []

  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
      lambda *a, **k: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.reconcile_open_tar_with_sealed_zst",
      lambda *a, **k: ReconcileResult(True, "noop", "already_equivalent"),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.dedupe_tar_keep_largest_file_per_member",
      lambda *a, **k: True,
  )

  class _Coord:
    def __init__(self, **_kw):
      pass

    def run_pre_seal_verify_sync(self, _tar_path, **_kw):
      return True

    def should_handoff_to_ingest(self, _tar_path):
      return True

    def complete_handoff_to_ingest(self, tar_path, reason=""):
      handoffs.append((tar_path, reason))

    def remaining_raw_paths_blocking_tar_drop(self, _tar_path):
      return {}

    def update_reconcile_progress(self, *_a, **_k):
      pass

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
      _Coord,
  )
  outcome = qo._run_day_close_job(
      day,
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda msg, **k: logs.append(str(msg)),
  )
  assert outcome == "yielded"
  assert handoffs and handoffs[0][1] == "day_close_wait_on_ingest"
  assert any("wait_on_ingest" in line for line in logs)


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
  """B6: reconstruct is interval-gated on reconstruct-coordinator, not idle-only."""
  src = inspect.getsource(qo._reconstruct_coordinator_loop)
  assert "_idle_reconstruct_pass" in src
  assert "run_once" in src
  run_src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "_reconstruct_coordinator_loop" in run_src
  assert "_fill_ingest_band" not in run_src


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

  def _claim_jobs(*a, **k):
    one = _claim(*a, **k)
    return [one] if one is not None else []

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
  monkeypatch.setattr(jq, "claim_ingest_jobs", _claim_jobs)
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


def test_missing_path_acks_when_ingest_complete(monkeypatch, tmp_path):
  """P0-3: gone + complete predicates → terminal ack, not infinite requeue."""
  client = FakeRedis()
  identity = "/gone/but/complete"
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

  def _claim_jobs(*a, **k):
    one = _claim(*a, **k)
    return [one] if one is not None else []

  requeued = []
  acked = []
  monkeypatch.setattr(
      jq, "requeue_job", lambda *a, **k: requeued.append(k.get("identity")),
  )
  monkeypatch.setattr(
      jq, "ack_job", lambda *a, **k: acked.append(k.get("identity")) or True,
  )
  monkeypatch.setattr(jq, "claim_ingest_job", _claim)
  monkeypatch.setattr(jq, "claim_ingest_jobs", _claim_jobs)
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
      ingest_is_complete_fn=lambda *_a, **_k: True,
  )
  assert acked == [identity]
  assert not requeued


def test_fill_ingest_ack_drops_fnctl_lock_sidecar(monkeypatch, tmp_path):
  """H6: fill must ACK-drop claimed *.fnctl.lock identities, not submit."""
  lock_path = tmp_path / "host" / "123.fnctl.lock"
  lock_path.parent.mkdir()
  lock_path.write_bytes(b"x")
  identity = str(lock_path)
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

  def _claim_jobs(*a, **k):
    one = _claim(*a, **k)
    return [one] if one is not None else []

  acked = []
  monkeypatch.setattr(
      jq, "ack_job", lambda *a, **k: acked.append(k.get("identity")) or True,
  )
  monkeypatch.setattr(jq, "claim_ingest_job", _claim)
  monkeypatch.setattr(jq, "claim_ingest_jobs", _claim_jobs)
  monkeypatch.setattr(
      jq, "requeue_job", lambda *a, **k: (_ for _ in ()).throw(
          AssertionError("must not requeue lock sidecar"),
      ),
  )

  class _Pool:
    def apply_async(self, *a, **k):
      raise AssertionError("must not submit lock sidecar")

  n = qo._fill_ingest_band(
      FakeRedis(),
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
  assert n == 0
  assert acked == [identity]


def test_fill_ingest_skip_budget_breaks(monkeypatch, tmp_path):
  """P0-3: missing-path skips must not busy-spin the MainThread tick."""
  client = FakeRedis()
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity="/no/such/raw/file",
      owner_token="n:h:b:1",
      deadline=1060.0,
      score=5.0,
  )
  calls = {"n": 0}

  def _claim(*a, **k):
    calls["n"] += 1
    return claim

  def _claim_jobs(*a, **k):
    max_n = int(k.get("max_n", 1))
    out = []
    for _ in range(max_n):
      calls["n"] += 1
      out.append(claim)
    return out

  monkeypatch.setattr(jq, "claim_ingest_job", _claim)
  monkeypatch.setattr(jq, "claim_ingest_jobs", _claim_jobs)
  monkeypatch.setattr(jq, "requeue_job", lambda *a, **k: True)
  monkeypatch.setattr(jq, "ack_job", lambda *a, **k: True)
  monkeypatch.setattr(os.path, "isfile", lambda p: False)

  class _Pool:
    def apply_async(self, *a, **k):
      raise AssertionError("must not submit missing path")

  qo._fill_ingest_band(
      client,
      band="hot",
      cap=8,
      inflight={},
      claims={},
      submitted={},
      ingest_pool=_Pool(),
      manager_lock=None,
      tgz_archive_dir=str(tmp_path),
      ingest_is_complete_fn=lambda *_a, **_k: False,
  )
  assert calls["n"] <= qo.INGEST_FILL_SKIP_BUDGET


def test_request_shutdown_sets_list_flag():
  """P0-4: SIGTERM Event and shutdown_utils.shutdown_requested[0] stay in sync."""
  from hpcperfstats.dbload.lib.shutdown_utils import (
    shutdown_requested as list_flag,
  )

  qo.reset_shutdown_for_tests()
  try:
    assert qo.shutdown_requested() is False
    assert list_flag[0] is False
    qo.request_shutdown()
    assert qo.shutdown_requested() is True
    assert list_flag[0] is True
  finally:
    qo.reset_shutdown_for_tests()
    assert list_flag[0] is False


def test_retry_or_dead_letter_claim_none_not_silent_requeued():
  """F15: missing claim must not report success as requeued."""
  assert qo._retry_or_dead_letter(
      None, kind="ingest", claim=None, archive_data_dir="/a", reason="x",
  ) == "dropped_no_claim"


def test_drain_timeout_terminates_before_requeue():
  """P0-8: kill in-flight workers before Redis requeue (dirty-tar then recover)."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  timeout_idx = src.find("drain timeout")
  assert timeout_idx != -1
  # Include the cancel/shutdown lines immediately above the log, stop before finally.
  arm_start = src.rfind("if drain_deadline", 0, timeout_idx)
  finally_idx = src.find("\n    finally:", timeout_idx)
  window = src[
      arm_start if arm_start != -1 else max(0, timeout_idx - 400)
      : finally_idx if finally_idx != -1 else timeout_idx + 800
  ]
  assert "day_executor.shutdown(wait=False" in window
  assert window.find("day_executor.shutdown") < window.find(
      "_release_claims_on_shutdown",
  )


def test_ingest_timeout_requeues_without_attempt_bump(tmp_path, monkeypatch):
  """P1-18: timeout/lookup_budget must not burn the poison attempt counter."""
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 5)
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  identity = "/raw/timeout"
  jq.zadd_ingest_job(client, identity=identity, score=1.0)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      del timeout
      return (identity, False, False, 1.0, {"outcome": "timeout"})

  n = qo._drain_ingest_ready(
      client,
      inflight={identity: _Ready()},
      claims={identity: claim},
      tgz_archive_dir="/daily",
      archive_data_dir=str(tmp_path),
  )
  assert n == 1
  assert jq.read_job_attempt(client, kind="ingest", identity=identity) == 0


def test_zcount_failure_is_fail_closed():
  """P1-7: Redis zcount errors must not invent hot_queued=1."""
  loop_src = inspect.getsource(qo._ingest_coordinator_loop)
  fill_src = inspect.getsource(qo._ingest_coordinator_fill_tick)
  assert "hot_queued = 1" not in loop_src
  assert "hot_queued = 1" not in fill_src
  assert "queue orchestrator redis zcount failed" in fill_src


def test_ingest_coordinator_idle_sleep_deep_queue():
  """B1: deep ZSET uses short poll, not sync_pool_poll_timeout_s."""
  assert qo._ingest_coordinator_idle_sleep_s(zcard=452, poll_s=5.0) == 0.05


def test_ingest_coordinator_idle_sleep_empty_queue():
  """B1: empty ZSET keeps long idle poll."""
  assert qo._ingest_coordinator_idle_sleep_s(zcard=0, poll_s=5.0) == 5.0


def test_ingest_fill_skip_budget_scales_with_zcard():
  """B2: deep queue escalates skip budget with a hard cap."""
  assert qo._ingest_fill_skip_budget_for_zcard(0) == qo.INGEST_FILL_SKIP_BUDGET
  assert qo._ingest_fill_skip_budget_for_zcard(500) == 10
  assert qo._ingest_fill_skip_budget_for_zcard(5000) == qo.INGEST_FILL_SKIP_BUDGET_MAX


def test_ingest_claim_probe_depth_elevates_when_hot_deep():
  """B2: claim probe depth rises when hot queue exceeds pool."""
  assert jq.ingest_claim_probe_depth(hot_q=0, pool=16) == 8
  assert jq.ingest_claim_probe_depth(hot_q=500, pool=16) == 31
  fill_src = inspect.getsource(qo._ingest_coordinator_fill_tick)
  assert "min(64, max(int(probe_depth), 32))" in fill_src
  assert "hot_submitted=0" in fill_src


def test_dominant_ingest_fill_block_picks_max():
  """B4: telemetry chooses the highest-count fill-block reason."""
  stats = qo._empty_ingest_fill_stats()
  stats["skip_missing"] = 2
  stats["claim_none"] = 5
  assert qo._dominant_ingest_fill_block(stats) == "claim_none"


def test_ingest_coordinator_uses_runtime_steal_on_fill_empty():
  """B3/RC2: under-capacity deep queue steals and reconciles this-owner leases."""
  src = inspect.getsource(qo._ingest_coordinator_loop)
  assert "_ingest_runtime_lease_hygiene" in src
  assert "reconcile_this_owner_orphan_leases" not in src
  hy = inspect.getsource(qo._ingest_runtime_lease_hygiene)
  assert "steal_dead_owner_leases" in hy
  assert "ingest runtime steal" in hy
  assert "reconcile_this_owner_orphan_leases" in hy
  assert "redis_underfull" in hy or "redis_hlen" in hy
  assert "zcard" in hy
  fill_src = inspect.getsource(qo._ingest_coordinator_loop)
  first_fill = fill_src.find(
      "did, hot_queued, zcard, _hot_n = _ingest_coordinator_fill_tick",
  )
  drain_at = fill_src.find("did += _drain_ingest_ready")
  second_fill = fill_src.find(
      "extra, hot_queued, zcard, _hot_n2 = _ingest_coordinator_fill_tick",
  )
  assert 0 < first_fill < drain_at < second_fill
  assert "fill under-capacity" in fill_src
  assert "set_fill_block(None)" in fill_src
  assert "len(ingest_inflight) < ingest_pool_size" in fill_src


def test_ingest_coordinator_loop_uses_zcard_for_idle_sleep():
  """B1: idle sleep branch must consult ZSET depth."""
  src = inspect.getsource(qo._ingest_coordinator_loop)
  assert "_ingest_coordinator_idle_sleep_s" in src
  assert "time.sleep(max(0.05, poll_s))" not in src.split(
      "elif did == 0 and not ingest_inflight:",
  )[1].split("else:")[0]


def test_renew_helper_not_called_from_loop():
  """P1-21/OQ-1: production loop must not heartbeat-renew leases."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "_renew_active_claims(" not in src


def test_orchestrator_log_fallback_uses_log_print(monkeypatch):
  """``_log`` without ``log_fn`` must use ``log_print``, not bare ``print``."""
  writes: list[str] = []
  buf = __import__("io").StringIO()
  real_write = __import__("io").StringIO.write

  def tracking_write(data):
    writes.append(data)
    return real_write(buf, data)

  buf.write = tracking_write  # type: ignore[method-assign]
  monkeypatch.setattr(__import__("sys"), "stdout", buf)

  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(__import__("sys").modules, "__main__", DummyMain)
  from hpcperfstats.dbload.lib.print_utils import set_log_role

  set_log_role(None)
  qo._log("queue_orchestrator drained; exiting")
  assert len(writes) == 1
  assert writes[0].startswith("[sync_timedb:main] ")
  assert "queue_orchestrator drained; exiting" in writes[0]


def test_sync_timedb_modules_have_no_bare_print():
  """Drift guard: sync_timedb entry + lib must not call bare ``print``."""
  import ast

  dbload = Path(__file__).resolve().parents[1] / "dbload"
  paths = [dbload / "sync_timedb.py"]
  paths.extend(sorted((dbload / "lib").glob("sync_timedb_*.py")))
  offenders: list[str] = []
  for path in paths:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
      if not isinstance(node, ast.Call):
        continue
      func = node.func
      if isinstance(func, ast.Name) and func.id == "print":
        offenders.append("%s:%d" % (path.name, node.lineno))
  assert not offenders, "bare print() in sync_timedb modules: %s" % (
      ", ".join(offenders),
  )


def test_drain_append_no_find_uses_cheap_day_close(monkeypatch):
  """Append drain must not call remaining-raw / filesystem_complete find."""
  calls: list[tuple] = []

  def _cheap(client, tar, **kwargs):
    calls.append((tar, kwargs))
    return True

  monkeypatch.setattr(qo.jr, "enqueue_cheap_day_close_if_needed", _cheap)

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      return None

  class _Claim:
    identity = "/raw/a"
    owner_token = "tok"

  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  n = qo._drain_append_ready(
      client,
      inflight={"/d/2026-07-17.tar": _Ready()},
      claims={"/d/2026-07-17.tar": _Claim()},
      tgz_archive_dir="/d",
      archive_data_dir="/a",
  )
  assert n == 1
  assert calls == [("/d/2026-07-17.tar", {})]
  src = inspect.getsource(qo._drain_append_ready)
  assert "enqueue_cheap_day_close_if_needed" in src
  assert "enqueue_day_close_if_needed(client, tar)" not in src


def test_drain_append_gate_skip_handoffs_before_ack(monkeypatch):
  """Gate-skipped append must handoff to ingest before ACK."""
  from hpcperfstats.dbload.sync_timedb import ArchiveAppendOutcome

  handoffs: list[tuple] = []
  acks: list[str] = []

  def _handoff(client, tar, paths, **kwargs):
    handoffs.append((tar, tuple(paths), kwargs.get("reason")))
    return len(paths)

  def _ack(client, *, kind, identity, owner_token):
    acks.append(identity)

  monkeypatch.setattr(qo, "_handoff_retryable_paths_to_ingest", _handoff)
  monkeypatch.setattr(qo.jq, "ack_job", _ack)
  monkeypatch.setattr(
      qo.jr, "enqueue_cheap_day_close_if_needed", lambda *a, **k: True,
  )

  skipped = ("/raw/a",)
  outcome = ArchiveAppendOutcome(
      ok=False,
      gate_skipped=True,
      skipped_paths=skipped,
      skip_finalize_invalidate=True,
  )

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      return outcome

  class _Claim:
    identity = "/raw/a"
    owner_token = "tok"

  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  n = qo._drain_append_ready(
      client,
      inflight={"/d/2026-07-17.tar": _Ready()},
      claims={"/d/2026-07-17.tar": _Claim()},
      tgz_archive_dir="/d",
      archive_data_dir="/a",
  )
  assert n == 1
  assert handoffs == [("/d/2026-07-17.tar", skipped, "gate_skip")]
  assert acks == ["/raw/a"]


def test_drain_append_soft_requeue_requeues_without_ack(monkeypatch):
  """Restore soft_requeue must requeue append without ACK."""
  from hpcperfstats.dbload.sync_timedb import ArchiveAppendOutcome

  requeues: list[str] = []
  acks: list[str] = []

  def _requeue(client, *, kind, identity, owner_token):
    requeues.append(identity)

  def _ack(client, *, kind, identity, owner_token):
    acks.append(identity)

  monkeypatch.setattr(qo.jq, "requeue_job", _requeue)
  monkeypatch.setattr(qo.jq, "ack_job", _ack)

  outcome = ArchiveAppendOutcome(
      ok=False,
      soft_requeue=True,
      skip_finalize_invalidate=True,
  )

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      return outcome

  class _Claim:
    identity = "/raw/a"
    owner_token = "tok"

  client = FakeRedis()
  n = qo._drain_append_ready(
      client,
      inflight={"/d/2026-07-17.tar": _Ready()},
      claims={"/d/2026-07-17.tar": _Claim()},
      tgz_archive_dir="/d",
      archive_data_dir="/a",
  )
  assert n == 1
  assert requeues == ["/raw/a"]
  assert acks == []


def test_reconstruct_coordinator_reaps_discover_kind():
  """Discover orphan inflight leases are reaped on reconstruct-coordinator."""
  src = inspect.getsource(qo._reconstruct_coordinator_loop)
  assert "JOB_KIND_DISCOVER" in src
  assert "_reap_stale_inflight" in src


def test_coordinator_roles_no_double_thread_prefix():
  """set_daemon_thread_title already prefixes thread: — role= must not repeat."""
  for fn_name in (
      "_ingest_coordinator_loop",
      "_append_coordinator_loop",
      "_day_close_coordinator_loop",
      "_reconstruct_coordinator_loop",
  ):
    src = inspect.getsource(getattr(qo, fn_name))
    assert 'role="thread:' not in src, fn_name


def test_cheap_day_close_helper_rejects_blocking_filesystem_complete():
  """Cheap helper always injects filesystem_complete=False (no archive find)."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  seen: list[dict] = []

  def _capture(client, tar_path, **kwargs):
    seen.append(kwargs)
    return True

  # Call through real helper with patched underlying enqueue.
  import hpcperfstats.dbload.lib.sync_timedb_job_reconstruct as jrmod

  orig = jrmod.enqueue_day_close_if_needed

  def _wrapped(client, tar_path, **kwargs):
    seen.append(dict(kwargs))
    return True

  jrmod.enqueue_day_close_if_needed = _wrapped  # type: ignore[assignment]
  try:
    assert jr.enqueue_cheap_day_close_if_needed(
        object(), "/d/2026-08-01.tar", calendar_day=date(2026, 8, 1),
    ) is True
  finally:
    jrmod.enqueue_day_close_if_needed = orig  # type: ignore[assignment]
  assert seen and seen[0].get("filesystem_complete") is False


def test_tar_dedup_day_close_uses_list_dedupe(monkeypatch):
  """Burst appends for one tar enqueue day_close at most once (Redis dedupe)."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  tar = "/d/2026-07-17.tar"
  assert jr.enqueue_cheap_day_close_if_needed(client, tar) is True
  # Second enqueue is dedupe-skipped at Redis; complete check still True-path
  # returns True from enqueue_day_close_if_needed only when push happens.
  # With FakeRedis dedupe Lua may no-op; assert LIST depth stays 1.
  jr.enqueue_cheap_day_close_if_needed(client, tar)
  jr.enqueue_cheap_day_close_if_needed(client, tar)
  depth = int(client.llen(jq.job_queue_key(jq.JOB_KIND_DAY_CLOSE)) or 0)
  assert depth <= 1


def test_cheap_day_close_age_skips_today_and_yesterday(monkeypatch):
  """Cheap enqueue must not RPUSH days younger than min-age (find-free)."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  now = datetime(2026, 8, 27, 12, 0, 0)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_day_close_min_age_hours",
      lambda: 32.0,
  )
  assert jr.enqueue_cheap_day_close_if_needed(
      client, "/d/2026-08-27.tar", now=now,
  ) is False
  assert jr.enqueue_cheap_day_close_if_needed(
      client, "/d/2026-08-26.tar", now=now,
  ) is False
  assert int(client.llen(jq.job_queue_key(jq.JOB_KIND_DAY_CLOSE)) or 0) == 0
  assert jr.enqueue_cheap_day_close_if_needed(
      client, "/d/2026-08-01.tar", now=now,
  ) is True
  assert int(client.llen(jq.job_queue_key(jq.JOB_KIND_DAY_CLOSE)) or 0) == 1


def test_pause_protocol_AtomicPoolRef_recycle():
  """C1: MainThread publishes new pool only via AtomicPoolRef after pause."""
  ref = qo.AtomicPoolRef("old")
  assert ref.get() == "old"
  ref.set("new")
  assert ref.get() == "new"
  gate = qo.IngestRecycleGate()
  assert not gate.recycle_requested.is_set()
  gate.recycle_requested.set()
  gate.paused.set()
  # Simulate MainThread recycle handoff.
  ref.set("recycled")
  gate.recycle_requested.clear()
  gate.paused.clear()
  assert ref.get() == "recycled"
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "recycle_gate.paused.is_set()" in src
  assert "_recycle_ingest_pool" in src
  assert "pool_ref.set" in src


def test_fail_closed_coordinator_death_no_empty_map_restart(monkeypatch):
  """C2: coordinator death must fail-closed, not silently restart."""
  exits: list[int] = []
  qo.reset_shutdown_for_tests()
  try:
    qo.fail_closed_on_coordinator_death(
        role="ingest-coordinator",
        log_fn=lambda *a, **k: None,
        exit_fn=exits.append,
    )
  finally:
    qo.reset_shutdown_for_tests()
  assert exits == [1]
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "fail_closed_on_coordinator_death" in src
  # No empty-map restart helper in MainThread allowlist path.
  assert "threading.Thread(" in src
  assert "empty_map" not in src.lower()


def test_kind_scoped_reaper_skips_local_inflight(monkeypatch):
  """C3: reaper protects local identities before Redis reclaim."""
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  protected: list[str] = []

  def _fake_protect(client, *, kind, identities, extend_s=600.0):
    protected.extend(str(x) for x in identities)
    return len(protected)

  calls: list[str] = []

  def _fake_reap(client, *, kind):
    calls.append(kind)
    return ["remote-b"]

  monkeypatch.setattr(qo, "_protect_local_inflight_deadlines", _fake_protect)
  monkeypatch.setattr(jq, "reap_expired_inflight", _fake_reap)
  n = qo._reap_stale_inflight(
      client,
      kinds=(jq.JOB_KIND_INGEST,),
      skip_identities=("local-a",),
      log_fn=lambda *a, **k: None,
  )
  assert "local-a" in protected
  assert calls == [jq.JOB_KIND_INGEST]
  assert n == 1
  append_src = inspect.getsource(qo._append_coordinator_loop)
  assert "JOB_KIND_APPEND" in append_src
  assert "skip_identities" in append_src


def test_mainthread_forbid_fill_drain():
  """MainThread maintenance must not call fill/drain helpers directly."""
  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  for banned in (
      "_fill_ingest_band(",
      "_fill_append_slots(",
      "_fill_day_close_slots(",
      "_drain_append_ready(",
      "_drain_ingest_ready(",
      "_idle_reconstruct_pass(",
  ):
    assert banned not in src, banned
  assert "populate.reap_and_restart" in src
  assert "_ingest_coordinator_loop" in src
  assert "_append_coordinator_loop" in src



def test_drain_bare_TimeoutError_leaves_inflight_no_soft_requeue(
  tmp_path, monkeypatch,
):
  """Bare TimeoutError on get must not soft-requeue or clear local inflight."""
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 5)
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  identity = "/raw/timeout_escape"
  jq.zadd_ingest_job(client, identity=identity, score=1.0)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  logs = []

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      del timeout
      raise TimeoutError("pickled per-file timeout")

  async_res = _Ready()
  inflight = {identity: async_res}
  claims = {identity: claim}
  submitted = {identity: 1.0}
  n = qo._drain_ingest_ready(
      client,
      inflight=inflight,
      claims=claims,
      submitted=submitted,
      tgz_archive_dir="/daily",
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: logs.append(" ".join(str(x) for x in a)),
  )
  assert n == 0
  assert identity in inflight
  assert identity in claims
  assert identity in submitted
  assert client.get(jq.job_lease_key("ingest", identity)) is not None
  assert client.zscore(jq.job_queue_key("ingest"), identity) is None
  joined = "\n".join(logs)
  assert "ingest fail" not in joined
  assert "queue_orchestrator ingest timeout" not in joined


def test_drain_rich_TimeoutError_soft_requeues_without_fail(tmp_path, monkeypatch):
  """Rich IngestPerFileTimeoutError escape still soft-requeues (Wave 1)."""
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 5)
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  identity = "/raw/timeout_rich"
  jq.zadd_ingest_job(client, identity=identity, score=1.0)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  logs = []

  class _RichTimeout(TimeoutError):
    def __init__(self):
      super().__init__("rich")
      self.elapsed_s = 12.5
      self.stage = "write"
      self.size_bytes = 99

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      del timeout
      raise _RichTimeout()

  n = qo._drain_ingest_ready(
      client,
      inflight={identity: _Ready()},
      claims={identity: claim},
      tgz_archive_dir="/daily",
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: logs.append(" ".join(str(x) for x in a)),
  )
  assert n == 1
  assert jq.read_job_attempt(client, kind="ingest", identity=identity) == 0
  joined = "\n".join(logs)
  assert "ingest timeout" in joined
  assert "ingest fail" not in joined
  assert "stage=write" in joined
  assert "err=" in joined


def test_drain_packed_timeout_rich_log(tmp_path, monkeypatch):
  """Packed outcome=timeout soft-requeue must emit rich coordinator timeout line."""
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 5)
  monkeypatch.setattr(st, "stats_file_size_bytes", lambda _p: 42)
  monkeypatch.setattr(st, "resolve_ingest_per_file_timeout_s", lambda _p: 3600.0)
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  identity = "/raw/packed_timeout"
  jq.zadd_ingest_job(client, identity=identity, score=1.0)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  logs = []

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      del timeout
      return (
          identity,
          False,
          False,
          12.5,
          {
              "outcome": "timeout",
              "fail_reason": "write",
              "timeout_s": 8000.2,
              "db_shard_lock_s": 3.0,
              "postgres_s": 4.0,
              "parse_elapsed_s": 1.5,
          },
      )

  n = qo._drain_ingest_ready(
      client,
      inflight={identity: _Ready()},
      claims={identity: claim},
      tgz_archive_dir="/daily",
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: logs.append(" ".join(str(x) for x in a)),
  )
  assert n == 1
  assert jq.read_job_attempt(client, kind="ingest", identity=identity) == 0
  joined = "\n".join(logs)
  assert "queue_orchestrator ingest timeout" in joined
  assert "timeout_s=8000.2" in joined
  assert "stage=write" in joined
  assert "db_shard_lock_s=3.0" in joined
  assert "postgres_s=4.0" in joined
  assert "parse_elapsed_s=1.5" in joined


def test_ingest_worker_logs_outcome_before_marks():
  """Orchestrator ingest worker must log outcome then record marks."""
  src = inspect.getsource(qo._ingest_worker)
  assert "_log_ingest_outcome_from_packed_result" in src
  assert src.index("_log_ingest_outcome_from_packed_result") < src.index(
      "_record_ingest_marks_from_worker_result",
  )
  assert "_log_ingest_worker_result" not in src


def test_drain_ingest_marks_quiet_log_fn_none(monkeypatch, tmp_path):
  """Coordinator mark record must pass log_fn=None (one INFO from worker)."""
  recorded = []

  def _record(result, *, log_fn=st.log_print):
    recorded.append({"result": result, "log_fn": log_fn})

  monkeypatch.setattr(st, "_record_ingest_marks_from_worker_result", _record)
  monkeypatch.setattr(jq, "ack_job", lambda *a, **k: True)

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      del timeout
      return ("/a", True, True, 0.1, {"outcome": "ingested"})

  client = FakeRedis()
  inflight = {"/a": _Ready()}
  claims = {
      "/a": jq.ClaimedJob(
          kind=jq.JOB_KIND_INGEST,
          identity="/a",
          owner_token="n:h:b:1",
          deadline=1.0,
          score=5.0,
      ),
  }
  done = qo._drain_ingest_ready(
      client,
      inflight=inflight,
      claims=claims,
      tgz_archive_dir="/daily",
      archive_data_dir=str(tmp_path),
  )
  assert done == 1
  assert recorded
  assert recorded[0]["log_fn"] is None
  src = inspect.getsource(qo._drain_ingest_ready)
  assert "log_fn=None" in src


def test_rc8_reconcile_prunes_local_when_redis_hlen_low():
  """RC8: phantom local maps prune when Redis has no inflight/lease."""
  class _Client:
    def hget(self, key, field):
      del key, field
      return None

    def get(self, key):
      del key
      return None

  class _NotReady:
    def ready(self):
      return False

  inflight = {"/phantom": _NotReady()}
  leases = {
      "/phantom": type("C", (), {"score": 1.0})(),
  }
  submitted = {"/phantom": 1.0}
  band_used = {"hot": 1, "catchup": 0}
  pruned = qo._reconcile_local_ingest_maps_to_redis(
      _Client(),
      ingest_inflight=inflight,
      ingest_leases=leases,
      ingest_submitted=submitted,
      band_used=band_used,
  )
  assert pruned == 1
  assert inflight == {}
  assert leases == {}
  assert submitted == {}
  assert band_used["hot"] == 0


def test_rc8_hygiene_runs_when_local_full_redis_underfull(monkeypatch):
  """RC8: hygiene must not skip when local looks full but Redis HLEN is low."""
  calls = {"steal": 0}

  def fake_steal(client):
    del client
    calls["steal"] += 1
    return 0

  monkeypatch.setattr(jq, "steal_dead_owner_leases", fake_steal)
  monkeypatch.setattr(
      jq, "reconcile_this_owner_orphan_leases", lambda **k: 0,
  )
  class _Ready:
    pass

  inflight = {("/x%d" % i): _Ready() for i in range(24)}
  now = qo._ingest_runtime_lease_hygiene(
      client=object(),
      ingest_inflight=inflight,
      ingest_leases={},
      ingest_pool_size=24,
      zcard=100,
      last_runtime_steal=0.0,
      log_fn=None,
      redis_hlen=5,
  )
  assert now > 0
  assert calls["steal"] == 1


def test_rc8_band_cap_uses_counters_not_full_scan():
  """RC8 #9: fill loop must not walk claims via _count_ingest_band_inflight."""
  src = inspect.getsource(qo._fill_ingest_band)
  assert "band_used" in src
  # Per-claim recount removed from the hot loop body.
  assert src.count("_count_ingest_band_inflight(claims)") <= 1


def test_rc8e_no_50ms_sleep_on_zero_submit_deep_zset():
  """RC8e: 0-submit deep ZSET under pool sleeps <<50ms."""
  s = qo._ingest_coordinator_tick_sleep_s(
      zcard=100, poll_s=5.0, fill_submitted=0, local_n=2, pool=24,
  )
  assert s <= 0.005
  s2 = qo._ingest_coordinator_tick_sleep_s(
      zcard=100, poll_s=5.0, fill_submitted=3, local_n=5, pool=24,
  )
  assert s2 == 0.05


def test_rc8e_census_wired_in_fill_tick():
  """RC8e: fill tick uses pipelined ingest_zset_census."""
  src = inspect.getsource(qo._ingest_coordinator_fill_tick)
  assert "ingest_zset_census" in src
  loop = inspect.getsource(qo._ingest_coordinator_loop)
  assert "_ingest_coordinator_tick_sleep_s" in loop
  assert "_reconcile_local_ingest_maps_to_redis" in loop
