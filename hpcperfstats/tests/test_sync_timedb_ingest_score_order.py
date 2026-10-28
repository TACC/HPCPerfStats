"""Ingest queue order must stay oldest-day-first however often jobs are penalized.

hpcperfstats04 2026-10-28: every queued catchup file carried one of three
score-days in 2027 (job-store snapshot dump), so the oldest-day-first claim
order degraded to hash order across 60 days and no day ever drained, which
kept every day-close yielding at the live-ingest gate. A penalty used to add
a whole day (``SCORE_STRIDE``) per requeue and the sum was persisted.
"""

from __future__ import annotations

from datetime import date

from hpcperfstats.dbload.lib import (
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore

TODAY = date(2026, 10, 28)
OWNER = "n:h:b:1"


def _day_floor(score: float) -> int:
  value = int(score)
  return value - value % jq.SCORE_STRIDE


def _score(day: date, identity: str) -> int:
  return jq.encode_ingest_score(
    band="catchup", day=day, today=TODAY, identity=identity
  )


def test_penalty_never_leaves_the_day_however_often_it_is_applied():
  score = float(_score(date(2026, 9, 10), "/a/1788000000"))
  floor = _day_floor(score)
  for _ in range(10_000):
    score = jq.penalize_ingest_score(score)
    assert _day_floor(score) == floor
  assert floor <= score < floor + jq.SCORE_STRIDE


def test_penalized_file_still_sorts_before_the_next_day():
  worst_old = float(_score(date(2026, 9, 10), "/a/1788000000"))
  for _ in range(100):
    worst_old = jq.penalize_ingest_score(worst_old)
  assert worst_old < _score(date(2026, 9, 11), "/b/1788100000")


def test_penalty_defers_behind_same_day_peers_when_there_is_room():
  early = float(_score(date(2026, 9, 10), "/a/early"))
  assert jq.penalize_ingest_score(early) > early


def test_penalty_of_none_starts_from_the_catchup_base():
  assert jq.penalize_ingest_score(None) >= float(jq.CATCHUP_SCORE_BASE)


def test_reaped_expired_lease_is_penalized_within_its_day():
  store = SyncTimedbJobStore("")
  ident = "/a/1788000000"
  score = float(_score(date(2026, 9, 10), ident))
  store.zadd_ingest(ident, score)
  for _ in range(50):
    claimed = store.claim_ingest(
      band="catchup", owner_token=OWNER, max_n=1, now_s=1000.0
    )
    assert [c.identity for c in claimed] == [ident]
    reaped = jq.reap_expired_inflight(
      store, kind="ingest", now_s=10**9, ttl_s=60
    )
    assert reaped == [ident]
  assert _day_floor(store.ingest_score(ident)) == _day_floor(score)


def test_repeated_fill_skips_keep_the_queue_oldest_day_first():
  store = SyncTimedbJobStore("")
  days = [date(2026, 9, 1 + i) for i in range(5)]
  idents = {d: f"/a/{1787000000 + i}" for i, d in enumerate(days)}
  for day, ident in idents.items():
    store.zadd_ingest(ident, float(_score(day, ident)))
  for _ in range(500):  # the head file is skipped over and over
    head = store.claim_ingest(band="catchup", owner_token=OWNER, max_n=1)[0]
    qo._requeue_ingest_fill_skip(
      store, claim=head, archive_data_dir="/a", reason="skip_cgroup_headroom"
    )
  order = [
    c.identity
    for c in store.claim_ingest(band="catchup", owner_token=OWNER, max_n=5)
  ]
  assert order == [idents[d] for d in days]


def _flattened_store():
  """Queue whose scores all sit on one far-future day (the prod symptom)."""
  store = SyncTimedbJobStore("")
  days = [date(2026, 8, 24), date(2026, 9, 14), date(2026, 10, 2)]
  idents = {
    d: f"/hpcperfstats/archive/c1-001/{1787000000 + 86400 * i}"
    for i, d in enumerate(days)
  }
  for ident in idents.values():
    store.zadd_ingest(ident, float(_score(date(2027, 1, 26), ident)))
  return store, days, idents


def test_normalize_rederives_scores_so_claims_are_oldest_day_first(
  monkeypatch,
):
  store, days, idents = _flattened_store()
  by_path = {path: day for day, path in idents.items()}
  monkeypatch.setattr(
    qo, "_calendar_day_for_ingest_path", lambda path, _dir: by_path[path]
  )
  fixed = qo._normalize_queued_ingest_scores(
    store, tgz_archive_dir="/d", today=TODAY, hot_days=3
  )
  assert fixed == 3
  order = [
    c.identity
    for c in store.claim_ingest(band="catchup", owner_token=OWNER, max_n=3)
  ]
  assert order == [idents[d] for d in days]


def test_normalize_keeps_an_in_day_penalty_and_is_idempotent(monkeypatch):
  store = SyncTimedbJobStore("")
  day = date(2026, 9, 10)
  ident = "/a/1788000000"
  penalized = jq.penalize_ingest_score(float(_score(day, ident)))
  store.zadd_ingest(ident, penalized)
  monkeypatch.setattr(qo, "_calendar_day_for_ingest_path", lambda *_a: day)
  fixed = qo._normalize_queued_ingest_scores(
    store, tgz_archive_dir="/d", today=TODAY, hot_days=3
  )
  assert fixed == 0
  assert store.ingest_score(ident) == penalized


def test_normalize_skips_paths_with_no_resolvable_day(monkeypatch):
  store = SyncTimedbJobStore("")
  store.zadd_ingest("/a/odd", 123.0)
  monkeypatch.setattr(qo, "_calendar_day_for_ingest_path", lambda *_a: None)
  fixed = qo._normalize_queued_ingest_scores(
    store, tgz_archive_dir="/d", today=TODAY, hot_days=3
  )
  assert fixed == 0
  assert store.ingest_score("/a/odd") == 123.0


def test_normalize_runs_at_boot_before_the_first_fill():
  import inspect

  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert src.index("_normalize_queued_ingest_scores(") < src.index(
    "ingest_pool_size = max(1"
  )
