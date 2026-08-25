"""Host FakeRedis unit tests for sync_timedb job:v1 queue helpers (slice 1)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib.invalidate_archive_members_ops import (
    _KEY_PREFIX,
    _PROTECTED_COORD_PREFIXES,
    _is_protected_coord_redis_key,
    invalidate_archive_members_redis_bulk,
)
from hpcperfstats.tests.fake_redis_queue import FakeRedis


@pytest.fixture(autouse=True)
def _reset_job_queue_scripts():
  jq.reset_job_queue_script_cache_for_tests()
  yield
  jq.reset_job_queue_script_cache_for_tests()


def test_job_v1_key_names_disjoint_from_archive_members():
  ingest = jq.job_queue_key("ingest")
  assert ingest == "hpcperfstats:sync_timedb:job:v1:queue:ingest"
  assert ":archive_members:" not in ingest
  assert jq.job_queue_key("append").endswith(":queue:append")
  assert jq.job_lease_key("ingest", "id").endswith(":lease:ingest:id")
  assert jq.job_payload_key("day_close", "2026-08-01").endswith(
      ":payload:day_close:2026-08-01",
  )


def test_operator_job_v1_census_uses_prefixed_queue_keys():
  """Prefixed job:v1 queue keys must appear in operator census docs."""
  root = Path(__file__).resolve().parents[2]
  ingest = jq.job_queue_key("ingest")
  append = jq.job_queue_key("append")
  stall = (root / "docs" / "OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md").read_text()
  assert ingest in stall
  assert append in stall
  assert "redis-cli -n 1 ZCARD job:v1:ingest" not in stall
  rules_path = (
      root / "hpcperfstats" / "cursor-rules"
      / "compose-operator-terminal-commands.mdc"
  )
  rules = rules_path.read_text()
  assert ingest in rules
  assert "ZCOUNT" in rules


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


def test_set_nx_ex_lease_and_non_owner_release(monkeypatch):
  monkeypatch.setattr(jq, "job_lease_ttl_seconds", lambda: 86400)
  client = FakeRedis()
  token = jq.try_acquire_job_lease(
      client, kind="ingest", identity="p|1|2", owner_token="abc:111",
  )
  assert token == "abc:111"
  assert client.get(jq.job_lease_key("ingest", "p|1|2")) == "abc:111"
  assert (
      jq.try_acquire_job_lease(
          client, kind="ingest", identity="p|1|2", owner_token="other:222",
      )
      == ""
  )
  assert not jq.release_job_lease(
      client, kind="ingest", identity="p|1|2", owner_token="other:222",
  )
  assert client.get(jq.job_lease_key("ingest", "p|1|2")) == "abc:111"
  assert jq.release_job_lease(
      client, kind="ingest", identity="p|1|2", owner_token="abc:111",
  )
  assert client.get(jq.job_lease_key("ingest", "p|1|2")) is None


def test_steal_lease_when_owner_pid_dead():
  client = FakeRedis()
  key = jq.job_lease_key("append", "day")
  client.set(key, "deadtoken:host1:boot1:99999", nx=True, ex=86400)
  assert jq.steal_job_lease_if_owner_dead(
      client,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: False,
      hostname="host1",
      boot_id="boot1",
  )
  assert client.get(key) is None
  client.set(key, "livetoken:host1:boot1:1", nx=True, ex=86400)
  assert not jq.steal_job_lease_if_owner_dead(
      client,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: True,
      hostname="host1",
      boot_id="boot1",
  )
  assert client.get(key) == "livetoken:host1:boot1:1"


def test_ranged_lua_pop_prefers_hot_range_without_starving_catchup():
  client = FakeRedis()
  today = date(2026, 8, 24)
  hot_id = "hot|1|1"
  catch_id = "catch|1|1"
  jq.zadd_ingest_job(
      client,
      identity=hot_id,
      score=jq.encode_ingest_score(
          band="hot", day=date(2026, 8, 23), today=today, identity=hot_id,
      ),
  )
  jq.zadd_ingest_job(
      client,
      identity=catch_id,
      score=jq.encode_ingest_score(
          band="catchup",
          day=date(2026, 6, 1),
          today=today,
          identity=catch_id,
      ),
  )
  assert jq.pop_ingest_job_ranged(client, band="hot") == hot_id
  assert jq.pop_ingest_job_ranged(client, band="hot") is None
  assert jq.pop_ingest_job_ranged(client, band="catchup") == catch_id
  assert client.zcard(jq.job_queue_key("ingest")) == 0


def test_zadd_same_member_reband_overwrites_score():
  client = FakeRedis()
  today = date(2026, 8, 24)
  ident = "p|9|9"
  hot = jq.encode_ingest_score(
      band="hot", day=date(2026, 8, 23), today=today, identity=ident,
  )
  catch = jq.encode_ingest_score(
      band="catchup", day=date(2026, 8, 23), today=today, identity=ident,
  )
  jq.zadd_ingest_job(client, identity=ident, score=hot)
  jq.zadd_ingest_job(client, identity=ident, score=catch)
  assert client.zcard(jq.job_queue_key("ingest")) == 1
  assert jq.pop_ingest_job_ranged(client, band="hot") is None
  assert jq.pop_ingest_job_ranged(client, band="catchup") == ident


def test_list_queue_fifo_for_append():
  client = FakeRedis()
  jq.enqueue_list_job(client, kind="append", identity="a")
  jq.enqueue_list_job(client, kind="append", identity="b")
  assert jq.pop_list_job(client, kind="append") == "a"
  assert jq.pop_list_job(client, kind="append") == "b"
  assert jq.pop_list_job(client, kind="append") is None


def test_invalidate_protects_job_v1_keys():
  job_prefix = "%s:job:v1" % _KEY_PREFIX
  assert job_prefix in _PROTECTED_COORD_PREFIXES
  assert _is_protected_coord_redis_key("%s:queue:ingest" % job_prefix)
  assert _is_protected_coord_redis_key("%s:lease:ingest:x" % job_prefix)
  client = FakeRedis()
  hash_key = "%s:archive_members:hash:v1:2026-08-01:id" % _KEY_PREFIX
  job_key = "%s:queue:ingest" % job_prefix
  client.set(hash_key, "1")
  client.set(job_key, "should-survive-if-scanned")
  # Force-protection path: scan would not normally find job keys; inject via
  # a membership-shaped key is N/A — assert protected helper + bulk leaves
  # explicit job key when only membership patterns delete.
  result = invalidate_archive_members_redis_bulk(
      day_tokens=["2026-08-01"], dry_run=False, client=client,
  )
  assert result["deleted"] >= 1
  assert client.get(job_key) == "should-survive-if-scanned"


# --- Slice 2: brownfield reconstruct predicates (library-only) ---


def test_reconstruct_laws_empty_redis_and_checkpoint_not_sot():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  assert jr.empty_job_queues_mean_caught_up() is False
  assert jr.checkpoint_sidecar_is_reconstruct_source_of_truth() is False
  assert jr.RECONSTRUCT_CHECKPOINT_BASENAME == ".sync_timedb_state.json"
  assert "marks" in jr.reconstruct_sources_of_truth()
  assert ".sync_timedb_state.json" not in jr.reconstruct_sources_of_truth()


def test_reconstruct_never_ingested_enqueues_ingest_and_append():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = FakeRedis()
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
  assert plan.identity in client._zsets[ingest_key]
  assert client._lists[append_key] == [plan.path]


def test_reconstruct_ingested_not_in_tar_enqueues_append_only():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = FakeRedis()
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
  assert not client._zsets.get(jq.job_queue_key("ingest"))
  assert client._lists[jq.job_queue_key("append")] == [plan.path]


def test_reconstruct_skips_zadd_when_both_complete():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = FakeRedis()
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
  assert not client._zsets
  assert not client._lists


def test_reconstruct_ghost_phase_done_still_enqueues_day_close():
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = FakeRedis()
  tar = "/daily/2026-08-01.tar"
  # Ghost phase=done with incomplete filesystem must still enqueue.
  did = jr.enqueue_day_close_if_needed(
      client,
      tar,
      calendar_day=date(2026, 8, 1),
      phase_name="done",
      filesystem_complete=False,
      min_age_elapsed=True,
  )
  assert did is True
  assert client._lists[jq.job_queue_key("day_close")] == [tar]
  # Same phase with FS+age complete → skip enqueue.
  client2 = FakeRedis()
  skipped = jr.enqueue_day_close_if_needed(
      client2,
      tar,
      calendar_day=date(2026, 8, 1),
      phase_name="done",
      filesystem_complete=True,
      min_age_elapsed=True,
  )
  assert skipped is False
  assert not client2._lists


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


# --- Slice 3: streaming discover → ZADD before scan exhaust ---


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

  client = FakeRedis()
  mid_seen_ingest = []

  def _gen():
    yield FindStatsRecord(path="/archive/h/a", mtime=1.0, size=10, inode=1)
    ingest_key = jq.job_queue_key("ingest")
    # First path must already be ZADDed before the generator finishes.
    assert client._zsets.get(ingest_key), "ingest empty mid-stream"
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
  ingest_key = jq.job_queue_key("ingest")
  assert len(client._zsets[ingest_key]) == 2


def test_streaming_discover_skips_complete_identities():
  from hpcperfstats.dbload.lib import sync_timedb_job_discover as jd

  client = FakeRedis()
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
  assert not client._zsets


def test_claim_is_atomic_pop_and_lease():
  """Q1: a claim must move the member to in-flight under a lease in one step."""
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.zadd_ingest_job(client, identity="/raw/a", score=5)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  assert claim.identity == "/raw/a"
  assert client.zcard(jq.job_queue_key("ingest")) == 0
  assert jq.job_lease_key("ingest", "/raw/a") in client._kv
  assert "/raw/a" in jq.read_inflight_entries(client, kind="ingest")


def test_lease_conflict_leaves_identity_on_zset():
  """Q1: a contended claim must not drop the durable ZSET member."""
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.zadd_ingest_job(client, identity="held", score=5)
  jq.zadd_ingest_job(client, identity="free", score=9)
  client.set(jq.job_lease_key("ingest", "held"), "other:h:b:2", nx=True, ex=60)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  assert claim is not None and claim.identity == "free"
  assert client.zscore(jq.job_queue_key("ingest"), "held") is not None


def test_inflight_zset_reaped_on_expired_deadline():
  """Q2: expired in-flight work must return to the durable queue."""
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.zadd_ingest_job(client, identity="/raw/a", score=5)
  jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  recovered = jq.reap_expired_inflight(
      client, kind="ingest", now_s=2000.0, limit=10,
  )
  assert recovered == ["/raw/a"]
  assert client.zscore(jq.job_queue_key("ingest"), "/raw/a") is not None


def test_owner_token_host_scoped():
  """Q11: owner tokens embed hostname and boot id, not just pid."""
  token = jq.make_lease_owner_token()
  parsed = jq.parse_lease_owner(token)
  assert parsed.hostname
  assert parsed.boot_id
  assert parsed.pid and parsed.pid > 0


def test_steal_refuses_foreign_host():
  """Q11: a local steal must not clear another host's live lease."""
  client = FakeRedis()
  key = jq.job_lease_key("ingest", "/raw/a")
  client.set(key, "n:otherhost:boot1:1", nx=True, ex=60)
  assert not jq.steal_job_lease_if_owner_dead(
      client,
      kind="ingest",
      identity="/raw/a",
      pid_alive_fn=lambda _pid: False,
      hostname="localhost",
      boot_id="boot1",
  )
  assert client.get(key) == "n:otherhost:boot1:1"


def test_lease_ttl_is_short():
  """Q6: lease TTL is a short renewable window, not the per-file max."""
  assert jq.JOB_LEASE_TTL_CEILING_S <= 900
  assert jq.job_lease_ttl_seconds() <= jq.JOB_LEASE_TTL_CEILING_S


def test_renew_lease_extends_deadline():
  """Q6: compare-and-extend renewal keeps a live owner off the reaper."""
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.zadd_ingest_job(client, identity="/raw/a", score=5)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  assert jq.renew_job_lease(
      client, kind="ingest", identity=claim.identity,
      owner_token=claim.owner_token, ttl_s=60, now_s=1030.0,
  )


def test_release_failure_is_reported():
  """Q6: lease release errors must surface, not be swallowed."""
  class _Boom:
    def evalsha(self, *a, **k):
      raise RuntimeError("redis down")

    def script_load(self, source):
      del source
      return "sha"

    def eval(self, *a, **k):
      raise RuntimeError("redis down")

  with pytest.raises(RuntimeError, match="redis down"):
    jq.release_job_lease(
        _Boom(), kind="ingest", identity="x", owner_token="n:h:b:1",
    )


def test_lease_identity_excludes_fingerprint():
  """Q4: two fingerprints of one path cannot both hold a lease."""
  path = "/raw/growing"
  first = jq.ingest_identity(path, 10, 1)
  second = jq.ingest_identity(path, 20, 2)
  assert first == second == path
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.zadd_ingest_job(
      client, identity=first, score=1, fingerprint=jq.ingest_fingerprint(10, 1),
  )
  jq.zadd_ingest_job(
      client, identity=second, score=2, fingerprint=jq.ingest_fingerprint(20, 2),
  )
  assert client.zcard(jq.job_queue_key("ingest")) == 1
  token = jq.try_acquire_job_lease(
      client, kind="ingest", identity=first, owner_token="a:h:b:1", ttl_s=60,
  )
  assert token
  assert jq.try_acquire_job_lease(
      client, kind="ingest", identity=second, owner_token="b:h:b:2", ttl_s=60,
  ) == ""


def test_fingerprint_revalidated_at_dispatch(tmp_path):
  """Q4: dispatch re-stats the path and rejects a stale fingerprint."""
  raw = tmp_path / "host" / "stats"
  raw.parent.mkdir()
  raw.write_bytes(b"abc")
  st = raw.stat()
  fp = jq.ingest_fingerprint(st.st_size, st.st_mtime_ns)
  assert jq.fingerprint_matches_path(str(raw), fp)
  raw.write_bytes(b"abcdef")
  assert not jq.fingerprint_matches_path(str(raw), fp)


def test_future_day_score_is_poppable():
  """B4: a future-dated hot score must still fall in the hot claim range."""
  today = date(2026, 8, 24)
  future = date(2026, 8, 30)
  score = jq.encode_ingest_score(
      band="hot", day=future, today=today, identity="/raw/a",
  )
  lo, hi = jq.ingest_score_range("hot")
  assert lo <= score <= hi
  assert score >= jq.HOT_SCORE_BASE


def test_discover_resolves_calendar_day_and_bands_catchup():
  """B1: an old calendar day must land in the catchup score range."""
  from hpcperfstats.dbload.lib import sync_timedb_job_discover as jd
  from hpcperfstats.dbload.lib.sync_timedb_stats_find import FindStatsRecord

  client = FakeRedis()
  rec = FindStatsRecord(path="/archive/h/a", mtime=1.0, size=10, inode=1)
  stats = jd.stream_enqueue_ingest_from_find_records(
      client,
      [rec],
      tgz_archive_dir="/daily",
      today=date(2026, 8, 24),
      hot_days=8,
      calendar_day_fn=lambda _r: date(2026, 6, 1),
      ingest_is_complete_fn=lambda **_k: False,
      append_is_complete_fn=lambda **_k: True,
  )
  assert stats.enqueued_ingest == 1
  member = jq.ingest_identity("/archive/h/a", 10, 0)
  score = client.zscore(jq.job_queue_key("ingest"), member)
  assert jq.decode_ingest_band(score) == "catchup"


def test_unresolved_day_skips_ingest_enqueue():
  """B1: ingest must not be banded with a substituted today."""
  from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr

  client = FakeRedis()
  plan = jr.ClosedPathReconstructPlan(
      path="/raw/a",
      identity="/raw/a",
      needs_ingest=True,
      needs_append=True,
      calendar_day=None,
      tar_path=None,
  )
  enqueued = jr.enqueue_reconstruct_jobs_for_closed_path(
      client, plan, today=date(2026, 8, 24),
  )
  assert enqueued["ingest"] is False
  assert enqueued["append"] is True
  assert not client._zsets.get(jq.job_queue_key("ingest"))


def test_queue_structural_keys_have_no_ttl():
  """Q9: durable queue keys must be written without EX so volatile-* is safe."""
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.zadd_ingest_job(
      client, identity="/raw/a", score=1, fingerprint="1|2",
  )
  jq.enqueue_list_job(client, kind="append", identity="/raw/a", dedupe=True)
  claim = jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  assert claim is not None
  ingest_ttl = client.ttl(jq.job_queue_key("ingest"))
  # Redis deletes empty ZSETs; missing (ttl -2) is still TTL-free.
  assert ingest_ttl in (-1, -2)
  assert client.ttl(jq.job_inflight_key("ingest")) == -1
  assert client.ttl(jq.job_payload_key("ingest", "/raw/a")) == -1
  assert client.ttl(jq.job_queue_key("append")) == -1
  assert client.ttl(jq.job_lease_key("ingest", "/raw/a")) > 0


def test_allkeys_eviction_is_unsafe():
  """Q9: allkeys-* with a memory ceiling is unsafe for durable job:v1 keys."""
  assert jq.unsafe_eviction_policy("allkeys-lru")
  assert jq.unsafe_eviction_policy("allkeys-random")
  assert not jq.unsafe_eviction_policy("noeviction")
  assert not jq.unsafe_eviction_policy("volatile-lru")


def test_queue_max_size_blocks_new_zadd(monkeypatch):
  """Q9: producers must not grow the ingest ZSET past the configured cap."""
  monkeypatch.setattr(jq, "queue_capacity_limit", lambda: 2)
  client = FakeRedis()
  assert jq.zadd_ingest_job(client, identity="/a", score=1) == 1
  assert jq.zadd_ingest_job(client, identity="/b", score=2) == 1
  assert jq.zadd_ingest_job(client, identity="/c", score=3) == 0
  assert client.zcard(jq.job_queue_key("ingest")) == 2
  # Overwrite of an existing member stays allowed at capacity.
  assert jq.zadd_ingest_job(client, identity="/a", score=9) >= 0
  assert client.zscore(jq.job_queue_key("ingest"), "/a") == 9.0


def test_append_list_dedupe_skips_queued_identity():
  """Q10: append LIST enqueue must not grow unbounded on rediscover."""
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  assert jq.enqueue_list_job(
      client, kind="append", identity="/raw/a", dedupe=True,
  ) == 1
  assert jq.enqueue_list_job(
      client, kind="append", identity="/raw/a", dedupe=True,
  ) == 0
  assert client.llen(jq.job_queue_key("append")) == 1


def test_census_counts_queued_and_inflight():
  """Q10: census reports queued vs in-flight per kind."""
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  jq.zadd_ingest_job(client, identity="/a", score=1)
  jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  census = jq.queue_census(client)
  assert census["ingest"]["inflight"] == 1
  assert "ingest=" in jq.format_queue_census(census)


def test_live_redis_lua_module_covers_loss_and_reap():
  """Compose Lua tests must exist so FakeRedis cannot be the only claim oracle."""
  path = Path(__file__).with_name("test_sync_timedb_job_queue_redis.py")
  text = path.read_text(encoding="utf-8")
  assert "HPCPERFSTATS_PYTEST_LIVE_REDIS" in text
  assert "from hpcperfstats.dbload.lib import conf_parser" in text
  assert "test_live_claim_is_atomic_pop_and_lease" in text
  assert "test_live_expired_inflight_reaped_back_onto_queue" in text
  assert "test_live_hot_range_does_not_starve_catchup" in text
  assert "test_live_concurrent_claim_does_not_drop_identity" in text
