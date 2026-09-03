"""Host unit tests for in-process sync_timedb job-store queue helpers."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib import sync_timedb_job_store as jq
from hpcperfstats.dbload.lib.invalidate_archive_members_ops import (
    JOB_STORE_SNAPSHOT_RELPATH,
    invalidate_archive_members_sidecars,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


@pytest.fixture(autouse=True)
def _reset_job_queue_scripts():
  jq.reset_job_queue_script_cache_for_tests()
  yield
  jq.reset_job_queue_script_cache_for_tests()


def _store() -> SyncTimedbJobStore:
  return SyncTimedbJobStore("")


def test_job_key_names_are_store_local():
  ingest = jq.job_queue_key("ingest")
  assert ingest == "hps:job:queue:ingest"
  assert ":archive_members:" not in ingest
  assert jq.job_queue_key("append").endswith(":queue:append")
  assert jq.job_lease_key("ingest", "id").endswith(":lease:ingest:id")
  assert jq.job_payload_key("day_close", "2026-08-01").endswith(
      ":payload:day_close:2026-08-01",
  )


def test_operator_census_uses_disk_sidecars_and_thread_titles():
  """Live T0 census must use job-store sidecars and populate thread titles."""
  root = Path(__file__).resolve().parents[2]
  stall = (root / "docs" / "OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md").read_text()
  assert ".sync_timedb_job_store.json" in stall
  assert "[thread:populate-pool]" in stall
  assert "job:v1:queue:ingest" not in stall
  assert "redis-cli -n 1 ZCARD" not in stall
  rules_path = (
      root / "hpcperfstats" / "cursor-rules"
      / "compose-operator-terminal-commands.mdc"
  )
  rules = rules_path.read_text()
  assert ".sync_timedb_job_store.json" in rules
  assert "archive_members:hash:v1" not in rules


def test_ingest_identity_normpath_size_mtime():
  """Lease identity is the stable path; size/mtime live in the fingerprint."""
  assert jq.ingest_identity("/tmp/a/../b", 12, 34) == "/tmp/b"
  assert jq.ingest_fingerprint(12, 34) == "12|34"


def test_encode_decode_hot_newest_first_catchup_oldest_first():
  today = date(2026, 8, 24)
  newer = date(2026, 8, 23)
  older = date(2026, 8, 20)
  id_new = "new|1|1"
  id_old = "old|1|1"
  hot_new = jq.encode_ingest_score(
      band="hot", day=newer, today=today, identity=id_new,
  )
  hot_old = jq.encode_ingest_score(
      band="hot", day=older, today=today, identity=id_old,
  )
  assert hot_new < hot_old
  assert jq.decode_ingest_band(hot_new) == "hot"
  catch_old = jq.encode_ingest_score(
      band="catchup", day=older, today=today, identity=id_old,
  )
  catch_new = jq.encode_ingest_score(
      band="catchup", day=newer, today=today, identity=id_new,
  )
  assert catch_old < catch_new
  assert jq.decode_ingest_band(catch_old) == "catchup"
  assert catch_old >= jq.CATCHUP_SCORE_BASE


def test_ingest_band_slot_caps_pool16_is_10_and_6():
  assert jq.ingest_band_slot_caps(16) == (10, 6)
  assert jq.ingest_band_slot_caps(2) == (1, 1)
  assert jq.ingest_band_slot_caps(1) == (1, 0)
  hot, catch = jq.ingest_band_slot_caps(3)
  assert hot + catch == 3
  assert catch >= 1


def test_claim_is_exclusive_and_non_owner_ack_fails():
  store = _store()
  jq.zadd_ingest_job(store, identity="p|1|2", score=5)
  owner = jq.make_lease_owner_token(pid=111, hostname="h", boot_id="b")
  claim = jq.claim_ingest_job(
      store, band="hot", owner_token=owner, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  assert store.get(jq.job_lease_key("ingest", "p|1|2")) == owner
  assert not jq.ack_job(
      store, kind="ingest", identity="p|1|2", owner_token="other:h:b:2",
  )
  assert store.get(jq.job_lease_key("ingest", "p|1|2")) == owner
  assert jq.ack_job(
      store, kind="ingest", identity="p|1|2", owner_token=owner,
  )
  assert store.get(jq.job_lease_key("ingest", "p|1|2")) is None


def test_steal_lease_when_owner_pid_dead():
  store = _store()
  jq.enqueue_list_job(store, kind="append", identity="day")
  claim = jq.claim_list_job(
      store, kind="append", owner_token="n:host1:boot1:9", ttl_s=60,
      now_s=1000.0,
  )
  assert claim is not None
  assert jq.steal_job_lease_if_owner_dead(
      store,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _p: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert store.get(jq.job_lease_key("append", "day")) is None
  assert store.llen(jq.job_queue_key("append")) == 1


def test_reconcile_this_owner_orphan_lease_requeues():
  store = _store()
  identity = "/raw/a"
  jq.zadd_ingest_job(store, identity=identity, score=5)
  owner = "n:h:b:1"
  claim = jq.claim_ingest_job(
      store, band="hot", owner_token=owner, ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  assert store.zcard(jq.job_queue_key("ingest")) == 0
  kept = jq.reconcile_this_owner_orphan_leases(
      store,
      kind=jq.JOB_KIND_INGEST,
      local_identities=(identity,),
      owner_token=owner,
  )
  assert kept == 0
  assert store.get(jq.job_lease_key("ingest", identity)) is not None
  n = jq.reconcile_this_owner_orphan_leases(
      store,
      kind=jq.JOB_KIND_INGEST,
      local_identities=(),
      owner_token=owner,
  )
  assert n == 1
  assert store.get(jq.job_lease_key("ingest", identity)) is None
  assert store.zscore(jq.job_queue_key("ingest"), identity) is not None


def test_ranged_claim_prefers_hot_range_without_starving_catchup():
  store = _store()
  today = date(2026, 8, 24)
  hot_id = "/hot"
  catch_id = "/catch"
  jq.zadd_ingest_job(
      store,
      identity=hot_id,
      score=jq.encode_ingest_score(
          band="hot", day=date(2026, 8, 23), today=today, identity=hot_id,
      ),
  )
  jq.zadd_ingest_job(
      store,
      identity=catch_id,
      score=jq.encode_ingest_score(
          band="catchup", day=date(2026, 6, 1), today=today, identity=catch_id,
      ),
  )
  hot = jq.claim_ingest_job(store, band="hot", owner_token="n:h:b:1")
  assert hot is not None and hot.identity == hot_id
  assert jq.claim_ingest_job(store, band="hot", owner_token="n:h:b:2") is None
  catch = jq.claim_ingest_job(store, band="catchup", owner_token="n:h:b:3")
  assert catch is not None and catch.identity == catch_id
  assert store.zcard(jq.job_queue_key("ingest")) == 0


def test_zadd_same_member_reband_overwrites_score():
  store = _store()
  ident = "/raw/a"
  today = date(2026, 8, 24)
  hot = jq.encode_ingest_score(
      band="hot", day=date(2026, 8, 23), today=today, identity=ident,
  )
  catch = jq.encode_ingest_score(
      band="catchup", day=date(2026, 6, 1), today=today, identity=ident,
  )
  jq.zadd_ingest_job(store, identity=ident, score=hot)
  jq.zadd_ingest_job(store, identity=ident, score=catch)
  assert store.zcard(jq.job_queue_key("ingest")) == 1
  assert jq.claim_ingest_job(store, band="hot", owner_token="n:h:b:1") is None
  claim = jq.claim_ingest_job(store, band="catchup", owner_token="n:h:b:1")
  assert claim is not None and claim.identity == ident


def test_list_queue_fifo_for_append():
  store = _store()
  jq.enqueue_list_job(store, kind="append", identity="a")
  jq.enqueue_list_job(store, kind="append", identity="b")
  first = jq.claim_list_job(store, kind="append", owner_token="n:h:b:1")
  second = jq.claim_list_job(store, kind="append", owner_token="n:h:b:2")
  assert first is not None and first.identity == "a"
  assert second is not None and second.identity == "b"
  assert jq.claim_list_job(store, kind="append", owner_token="n:h:b:3") is None


def test_invalidate_protects_job_store_sidecar(tmp_path):
  archive = tmp_path / "archive"
  members = archive / ".sync_timedb_archive_members"
  members.mkdir(parents=True)
  job = archive / JOB_STORE_SNAPSHOT_RELPATH
  job.write_text("{}", encoding="utf-8")
  day = members / "2026-08-01.json"
  day.write_text("{}", encoding="utf-8")
  other = members / "2026-08-02.json"
  other.write_text("{}", encoding="utf-8")
  result = invalidate_archive_members_sidecars(
      archive_dir=str(archive),
      day_tokens=["2026-08-01"],
  )
  assert result["deleted"] == 1
  assert not day.exists()
  assert other.exists()
  assert job.exists()


def test_reconstruct_laws_empty_queues_and_checkpoint_not_sot():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  assert jr.empty_job_queues_mean_caught_up() is False
  assert jr.checkpoint_sidecar_is_reconstruct_source_of_truth() is False
  assert jr.RECONSTRUCT_CHECKPOINT_BASENAME == ".sync_timedb_state.json"
  assert "marks" in jr.reconstruct_sources_of_truth()
  assert ".sync_timedb_state.json" not in jr.reconstruct_sources_of_truth()


def test_reconstruct_never_ingested_enqueues_ingest_and_append():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = _store()
  plan = jr.classify_closed_raw_path(
      "/archive/host/raw",
      tgz_archive_dir="/daily",
      size=100,
      mtime_ns=200,
      calendar_day=date(2026, 8, 20),
      ingest_is_complete_fn=lambda **_k: False,
      append_is_complete_fn=lambda **_k: False,
  )
  assert plan.kinds_to_enqueue() == ("ingest", "append")
  enqueued = jr.enqueue_reconstruct_jobs_for_closed_path(
      client,
      plan,
      today=date(2026, 8, 24),
  )
  assert enqueued == {"ingest": True, "append": True}
  ingest_key = jq.job_queue_key("ingest")
  append_key = jq.job_queue_key("append")
  assert client.zscore(ingest_key, plan.identity) is not None
  assert client.lrange(append_key, 0, -1) == [plan.path]


def test_reconstruct_ingested_not_in_tar_enqueues_append_only():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = _store()
  plan = jr.classify_closed_raw_path(
      "/archive/host/raw",
      tgz_archive_dir="/daily",
      size=100,
      mtime_ns=200,
      calendar_day=date(2026, 6, 2),
      ingest_is_complete_fn=lambda **_k: True,
      append_is_complete_fn=lambda **_k: False,
  )
  assert plan.kinds_to_enqueue() == ("append",)
  enqueued = jr.enqueue_reconstruct_jobs_for_closed_path(
      client,
      plan,
      today=date(2026, 8, 24),
  )
  assert enqueued == {"ingest": False, "append": True}
  assert client.zscore(jq.job_queue_key("ingest"), plan.identity) is None
  assert client.lrange(jq.job_queue_key("append"), 0, -1) == [plan.path]


def test_reconstruct_skips_zadd_when_both_complete():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = _store()
  plan = jr.classify_closed_raw_path(
      "/archive/host/raw",
      tgz_archive_dir="/daily",
      size=100,
      mtime_ns=200,
      calendar_day=date(2026, 8, 20),
      ingest_is_complete_fn=lambda **_k: True,
      append_is_complete_fn=lambda **_k: True,
  )
  assert plan.kinds_to_enqueue() == ()
  enqueued = jr.enqueue_reconstruct_jobs_for_closed_path(
      client,
      plan,
      today=date(2026, 8, 24),
  )
  assert enqueued == {"ingest": False, "append": False}
  assert not client.ingest_identities()
  assert client.llen(jq.job_queue_key("append")) == 0


def test_reconstruct_ghost_phase_done_still_enqueues_day_close():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = _store()
  tar = "/daily/2026-08-01.tar"
  did = jr.enqueue_day_close_if_needed(
      client,
      tar,
      calendar_day=date(2026, 8, 1),
      phase_name="done",
      filesystem_complete=False,
      min_age_elapsed=True,
  )
  assert did is True
  assert client.lrange(jq.job_queue_key("day_close"), 0, -1) == [tar]
  client2 = _store()
  skipped = jr.enqueue_day_close_if_needed(
      client2,
      tar,
      calendar_day=date(2026, 8, 1),
      phase_name="done",
      filesystem_complete=True,
      min_age_elapsed=True,
  )
  assert skipped is False
  assert client2.llen(jq.job_queue_key("day_close")) == 0


def test_reconstruct_ingest_complete_ignores_head_tail_when_listend_on():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  assert (
      jr.ingest_is_complete(
          "/x",
          listend_enabled=True,
          has_file_complete_fn=lambda _p: False,
          has_zero_host_fn=lambda _p: False,
          head_tail_ready_fn=lambda _p: True,
      )
      is False
  )
  assert (
      jr.ingest_is_complete(
          "/x",
          listend_enabled=False,
          has_file_complete_fn=lambda _p: False,
          has_zero_host_fn=lambda _p: False,
          head_tail_ready_fn=lambda _p: True,
      )
      is True
  )


def test_streaming_parse_yields_before_final_chunk():
  from hpcperfstats.dbload.lib.sync_timedb_stats_find import (
      iter_find_printf_records_streaming,
  )

  chunks = [
      b"/a\x001.0\x0010\x001\x00",
      b"/b\x002.0\x0020\x002\x00",
  ]
  it = iter_find_printf_records_streaming(iter(chunks))
  first = next(it)
  assert first.path == "/a"
  assert first.size == 10
  second = next(it)
  assert second.path == "/b"


def test_streaming_discover_enqueues_before_iterator_exhausts():
  from hpcperfstats.dbload.lib import sync_timedb_job_discover as jd
  from hpcperfstats.dbload.lib.sync_timedb_stats_find import FindStatsRecord

  client = _store()
  mid_seen_ingest = []

  def _gen():
    yield FindStatsRecord(path="/archive/h/a", mtime=1.0, size=10, inode=1)
    assert client.ingest_identities(), "ingest empty mid-stream"
    mid_seen_ingest.append(True)
    yield FindStatsRecord(path="/archive/h/b", mtime=2.0, size=20, inode=2)

  stats = jd.stream_enqueue_ingest_from_find_records(
      client,
      _gen(),
      tgz_archive_dir="/daily",
      today=date(2026, 8, 24),
      calendar_day_fn=lambda _r: date(2026, 8, 20),
      ingest_is_complete_fn=lambda **_k: False,
      append_is_complete_fn=lambda **_k: True,
  )
  assert mid_seen_ingest == [True]
  assert stats.seen == 2
  assert stats.enqueued_ingest == 2
  assert stats.skipped_complete == 0
  assert len(client.ingest_identities()) == 2


def test_streaming_discover_skips_complete_identities():
  from hpcperfstats.dbload.lib import sync_timedb_job_discover as jd

  client = _store()
  stats = jd.stream_enqueue_ingest_from_find_stdout_chunks(
      client,
      [b"/archive/h/done\x001.0\x0010\x001\x00"],
      tgz_archive_dir="/daily",
      today=date(2026, 8, 24),
      ingest_is_complete_fn=lambda **_k: True,
      append_is_complete_fn=lambda **_k: True,
  )
  assert stats.seen == 1
  assert stats.enqueued_ingest == 0
  assert stats.skipped_complete == 1
  assert not client.ingest_identities()


def test_claim_is_atomic_pop_and_lease():
  """A claim must move the member to in-flight under a lease in one step."""
  store = _store()
  jq.zadd_ingest_job(store, identity="/raw/a", score=5)
  claim = jq.claim_ingest_job(
      store, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  assert claim.identity == "/raw/a"
  assert store.zcard(jq.job_queue_key("ingest")) == 0
  assert store.get(jq.job_lease_key("ingest", "/raw/a")) == "n:h:b:1"
  assert "/raw/a" in jq.read_inflight_entries(store, kind="ingest")


def test_lease_conflict_leaves_identity_on_map():
  store = _store()
  jq.zadd_ingest_job(store, identity="held", score=5)
  jq.zadd_ingest_job(store, identity="free", score=9)
  store._leases[(jq.JOB_KIND_INGEST, "held")] = "other:h:b:2"
  store._inflight[jq.JOB_KIND_INGEST]["held"] = (2000.0, "other:h:b:2", 5.0)
  store._ingest.pop("held", None)
  claim = jq.claim_ingest_job(
      store, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  assert claim is not None and claim.identity == "free"
  assert "held" in jq.read_inflight_entries(store, kind="ingest")


def test_inflight_reaped_on_expired_deadline():
  store = _store()
  jq.zadd_ingest_job(store, identity="/raw/a", score=5)
  jq.claim_ingest_job(
      store, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  recovered = jq.reap_expired_inflight(
      store, kind="ingest", now_s=2000.0, ttl_s=60,
  )
  assert recovered == ["/raw/a"]
  assert store.zscore(jq.job_queue_key("ingest"), "/raw/a") is not None


def test_owner_token_host_scoped():
  token = jq.make_lease_owner_token()
  parsed = jq.parse_lease_owner(token)
  assert parsed.hostname
  assert parsed.pid is not None


def test_steal_refuses_foreign_host():
  store = _store()
  store._leases[(jq.JOB_KIND_INGEST, "/raw/a")] = "n:other:boot:9"
  assert not jq.steal_job_lease_if_owner_dead(
      store,
      kind="ingest",
      identity="/raw/a",
      pid_alive_fn=lambda _p: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert store.get(jq.job_lease_key("ingest", "/raw/a")) == "n:other:boot:9"


def test_lease_ttl_matches_oq1_per_file_max(monkeypatch):
  monkeypatch.setattr(
      jq.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 86400,
  )
  assert jq.job_lease_ttl_seconds() == 86400
  monkeypatch.setattr(
      jq.cfg, "get_sync_ingest_per_file_timeout_max_s", lambda: 10,
  )
  assert jq.job_lease_ttl_seconds() == jq.JOB_LEASE_TTL_FLOOR_S


def test_renew_lease_extends_deadline():
  store = _store()
  jq.zadd_ingest_job(store, identity="/raw/a", score=5)
  claim = jq.claim_ingest_job(
      store, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  assert jq.renew_job_lease(
      store,
      kind="ingest",
      identity="/raw/a",
      owner_token="n:h:b:1",
      ttl_s=60,
      now_s=1150.0,
  )
  assert jq.reap_expired_inflight(
      store, kind="ingest", now_s=1200.0, ttl_s=60,
  ) == []


def test_lease_identity_excludes_fingerprint():
  path = "/raw/same"
  first = jq.ingest_identity(path, 10, 1)
  second = jq.ingest_identity(path, 20, 2)
  assert first == second
  store = _store()
  jq.zadd_ingest_job(
      store, identity=first, score=1, fingerprint=jq.ingest_fingerprint(10, 1),
  )
  jq.zadd_ingest_job(
      store, identity=second, score=2, fingerprint=jq.ingest_fingerprint(20, 2),
  )
  assert store.zcard(jq.job_queue_key("ingest")) == 1


def test_fingerprint_revalidated_at_dispatch(tmp_path):
  raw = tmp_path / "host" / "1"
  raw.parent.mkdir()
  raw.write_bytes(b"x")
  st = raw.stat()
  fp = jq.ingest_fingerprint(st.st_size, st.st_mtime_ns)
  assert jq.fingerprint_matches_path(str(raw), fp)
  raw.write_bytes(b"xy")
  assert not jq.fingerprint_matches_path(str(raw), fp)


def test_future_day_score_is_poppable():
  today = date(2026, 8, 24)
  score = jq.encode_ingest_score(
      band="hot", day=date(2026, 8, 25), today=today, identity="x",
  )
  lo, hi = jq.ingest_score_range("hot")
  assert lo <= score <= hi
  assert score >= jq.HOT_SCORE_BASE


def test_discover_resolves_calendar_day_and_bands_catchup():
  score = jq.encode_ingest_score(
      band="catchup",
      day=date(2026, 6, 1),
      today=date(2026, 8, 24),
      identity="/archive/h/a",
  )
  assert jq.decode_ingest_band(score) == "catchup"


def test_unresolved_day_skips_ingest_enqueue():
  from hpcperfstats.dbload.lib import sync_timedb_job_discover as jd
  from hpcperfstats.dbload.lib.sync_timedb_stats_find import FindStatsRecord

  store = _store()
  stats = jd.stream_enqueue_ingest_from_find_records(
      store,
      [FindStatsRecord(path="/archive/h/a", mtime=1.0, size=10, inode=1)],
      tgz_archive_dir="/daily",
      today=date(2026, 8, 24),
      calendar_day_fn=lambda _r: None,
      ingest_is_complete_fn=lambda **_k: False,
      append_is_complete_fn=lambda **_k: True,
  )
  assert stats.enqueued_ingest == 0
  assert not store.ingest_identities()


def test_persist_omits_inflight_and_leases(tmp_path):
  store = SyncTimedbJobStore(str(tmp_path / "archive"))
  jq.zadd_ingest_job(store, identity="/raw/a", score=1)
  jq.enqueue_list_job(store, kind="append", identity="/raw/a", dedupe=True)
  jq.claim_ingest_job(
      store, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  store.persist(force=True)
  reloaded = SyncTimedbJobStore(str(tmp_path / "archive"))
  assert reloaded.llen(jq.job_queue_key("append")) == 1
  assert reloaded.inflight_count("ingest") == 0
  assert reloaded.get(jq.job_lease_key("ingest", "/raw/a")) is None


def test_queue_max_size_blocks_new_zadd(monkeypatch):
  monkeypatch.setattr(jq, "queue_capacity_limit", lambda: 2)
  store = _store()
  assert jq.zadd_ingest_job(store, identity="/a", score=1) == 1
  assert jq.zadd_ingest_job(store, identity="/b", score=2) == 1
  assert jq.zadd_ingest_job(store, identity="/c", score=3) == 0
  assert store.zcard(jq.job_queue_key("ingest")) == 2
  assert jq.zadd_ingest_job(store, identity="/a", score=9) >= 0
  assert store.zscore(jq.job_queue_key("ingest"), "/a") == 9.0


def test_append_list_dedupe_skips_queued_identity():
  store = _store()
  assert jq.enqueue_list_job(
      store, kind="append", identity="/raw/a", dedupe=True,
  ) == 1
  assert jq.enqueue_list_job(
      store, kind="append", identity="/raw/a", dedupe=True,
  ) == 0
  assert store.llen(jq.job_queue_key("append")) == 1


def test_census_counts_queued_and_inflight():
  store = _store()
  jq.zadd_ingest_job(store, identity="/a", score=1)
  jq.claim_ingest_job(
      store, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  census = jq.queue_census(store)
  assert census["ingest"]["inflight"] == 1
  assert "ingest=" in jq.format_queue_census(census)


def test_live_redis_lua_module_is_retired():
  """Hard cutover: live Redis Lua claim tests must not return."""
  path = Path(__file__).with_name("test_sync_timedb_job_queue_redis.py")
  assert not path.is_file()


def test_list_claim_lease_conflict_keeps_other_identity():
  store = _store()
  jq.enqueue_list_job(store, kind="append", identity="a")
  jq.enqueue_list_job(store, kind="append", identity="b")
  first = jq.claim_list_job(
      store, kind="append", owner_token="n:other:boot:1", ttl_s=60,
      now_s=1000.0,
  )
  assert first is not None and first.identity == "a"
  claim = jq.claim_list_job(
      store, kind="append", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  assert claim is not None and claim.identity == "b"
  remaining = store.lrange(jq.job_queue_key("append"), 0, -1)
  assert remaining == []


def test_reconstruct_append_dedupes_list():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  store = _store()
  plan = jr.classify_closed_raw_path(
      "/archive/host/raw",
      tgz_archive_dir="/daily",
      size=100,
      mtime_ns=200,
      calendar_day=date(2026, 6, 2),
      ingest_is_complete_fn=lambda **_k: True,
      append_is_complete_fn=lambda **_k: False,
  )
  jr.enqueue_reconstruct_jobs_for_closed_path(
      store, plan, today=date(2026, 8, 24),
  )
  jr.enqueue_reconstruct_jobs_for_closed_path(
      store, plan, today=date(2026, 8, 24),
  )
  assert store.llen(jq.job_queue_key("append")) == 1


def test_rc8b_claim_returns_fingerprint(tmp_path):
  raw = tmp_path / "host" / "1"
  raw.parent.mkdir()
  raw.write_bytes(b"abc")
  st = raw.stat()
  fp = jq.ingest_fingerprint(st.st_size, st.st_mtime_ns)
  store = _store()
  identity = jq.ingest_identity(str(raw), st.st_size, st.st_mtime_ns)
  jq.zadd_ingest_job(store, identity=identity, score=1.0, fingerprint=fp)
  claim = jq.claim_ingest_job(
      store, band="hot",
      owner_token=jq.make_lease_owner_token(pid=1, hostname="h", boot_id="b"),
  )
  assert claim is not None
  assert claim.fingerprint == fp


def test_rc8c_multi_claim_fills_free_slots(tmp_path):
  store = _store()
  for idx in range(3):
    raw = tmp_path / "host" / str(idx)
    raw.parent.mkdir(exist_ok=True)
    raw.write_bytes(b"x" * (idx + 1))
    st = raw.stat()
    jq.zadd_ingest_job(
        store,
        identity=jq.ingest_identity(str(raw), st.st_size, st.st_mtime_ns),
        score=float(idx),
        fingerprint=jq.ingest_fingerprint(st.st_size, st.st_mtime_ns),
    )
  claims = jq.claim_ingest_jobs(
      store,
      band="hot",
      owner_token=jq.make_lease_owner_token(pid=1, hostname="h", boot_id="b"),
      max_n=2,
  )
  assert len(claims) == 2


def test_rc8d_steal_does_not_require_hgetall():
  store = _store()
  identity = "/raw/steal"
  jq.zadd_ingest_job(store, identity=identity, score=1.0)
  owner = "n:host1:boot1:9"
  jq.claim_ingest_job(
      store, band="hot", owner_token=owner, ttl_s=60, now_s=1000.0,
  )
  assert jq.steal_job_lease_if_owner_dead(
      store,
      kind="ingest",
      identity=identity,
      pid_alive_fn=lambda _p: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert store.zscore(jq.job_queue_key("ingest"), identity) is not None


def test_rc8e_census_uses_pipeline():
  store = _store()
  jq.zadd_ingest_job(store, identity="/a", score=1.0)
  hot, catch, zcard = jq.ingest_zset_census(store)
  assert zcard == 1
  assert hot + catch == 1
