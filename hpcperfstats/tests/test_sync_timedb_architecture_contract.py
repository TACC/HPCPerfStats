"""Predicate architecture contracts for the sync_timedb queue orchestrator.

B-09 (slice 4): lock rediscovered → ingest job; remaining-raw → ingest/append
job; day_close = filesystem complete + 32h min-age. Do not freeze two-queue
threads as sacred law.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr


class _FakeRedis:
  """Minimal Redis stand-in for predicate enqueue tests."""

  def __init__(self):
    self.z = {}
    self.lists = {}

  def zadd(self, key, mapping):
    self.z.setdefault(key, {}).update(mapping)
    return len(mapping)

  def rpush(self, key, *values):
    bucket = self.lists.setdefault(key, [])
    bucket.extend(values)
    return len(bucket)


def test_arch_predicate_discovered_incomplete_enqueues_ingest_job():
  """Discovered closed raw with incomplete ingest must create an ingest job."""
  client = _FakeRedis()
  plan = jr.classify_closed_raw_path(
      "/raw/host/1",
      tgz_archive_dir="/daily",
      size=10,
      mtime_ns=20,
      calendar_day=date(2026, 8, 20),
      ingest_is_complete_fn=lambda **k: False,
      append_is_complete_fn=lambda **k: True,
  )
  assert plan.needs_ingest is True
  enqueued = jr.enqueue_reconstruct_jobs_for_closed_path(
      client, plan, today=date(2026, 8, 24), hot_days=8
  )
  assert enqueued["ingest"] is True
  ingest_key = jq.job_queue_key(jq.JOB_KIND_INGEST)
  assert plan.identity in client.z.get(ingest_key, {})


def test_arch_predicate_remaining_raw_enqueues_ingest_or_append_job():
  """Remaining-raw incomplete append must leave an append LIST job."""
  client = _FakeRedis()
  plan = jr.classify_closed_raw_path(
      "/raw/host/2",
      tgz_archive_dir="/daily",
      size=11,
      mtime_ns=21,
      calendar_day=date(2026, 6, 1),
      ingest_is_complete_fn=lambda **k: True,
      append_is_complete_fn=lambda **k: False,
  )
  assert plan.needs_append is True
  enqueued = jr.enqueue_reconstruct_jobs_for_closed_path(
      client, plan, today=date(2026, 8, 24), hot_days=2
  )
  assert enqueued["append"] is True
  assert plan.path in client.lists.get(
      jq.job_queue_key(jq.JOB_KIND_APPEND), []
  )


def test_arch_predicate_day_close_is_filesystem_plus_min_age():
  """day_close complete requires filesystem complete and 32h min-age."""
  assert jr.day_close_is_complete(
      "/daily/2026-08-01.tar",
      calendar_day=date(2026, 8, 1),
      filesystem_complete=True,
      min_age_elapsed=True,
      phase_name="done",
  )
  assert not jr.day_close_is_complete(
      "/daily/2026-08-01.tar",
      calendar_day=date(2026, 8, 1),
      filesystem_complete=True,
      min_age_elapsed=False,
      phase_name="done",
  )
  assert not jr.day_close_is_complete(
      "/daily/2026-08-01.tar",
      calendar_day=date(2026, 8, 1),
      filesystem_complete=False,
      min_age_elapsed=True,
  )
  assert jr.day_close_min_age_elapsed(
      date(2026, 8, 1),
      now=datetime(2026, 8, 3, 12, 0, 0),
      min_age_hours=32.0,
  )
  assert not jr.day_close_min_age_elapsed(
      date(2026, 8, 1),
      now=datetime(2026, 8, 2, 0, 0, 0),
      min_age_hours=32.0,
  )


def test_arch_empty_job_queues_do_not_mean_caught_up():
  """Empty Redis job structures never imply archive catch-up."""
  assert jr.empty_job_queues_mean_caught_up() is False


def test_arch_checkpoint_sidecar_not_reconstruct_source_of_truth():
  """``.sync_timedb_state.json`` is not reconstruct source of truth."""
  assert jr.checkpoint_sidecar_is_reconstruct_source_of_truth() is False
  assert "disk" in jr.reconstruct_sources_of_truth()
