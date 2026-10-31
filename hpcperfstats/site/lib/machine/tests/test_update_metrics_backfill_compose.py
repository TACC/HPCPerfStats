"""Compose-backed (real PostgreSQL) regression for the update_metrics backfill lane.

Run inside the dev container::

  tests/run_dev_container_pytest.sh \
    hpcperfstats/site/lib/machine/tests/test_update_metrics_backfill_compose.py -q
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from django.utils import timezone

from hpcperfstats.analysis.metrics import update_metrics as um
from hpcperfstats.analysis.metrics.lib import metrics as metrics_module


def _compose_network() -> bool:
  return os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
    "1",
    "yes",
    "true",
  )


pytestmark = pytest.mark.skipif(
  not _compose_network(),
  reason=(
    "Requires the compose network (PostgreSQL at host 'db'). "
    "Run: tests/run_dev_container_pytest.sh <this file>"
  ),
)


def _job(jid: str, end: timezone.datetime, **fields):
  from hpcperfstats.site.lib.machine.models import job_data

  return job_data.objects.create(
    jid=jid,
    submit_time=end - timedelta(hours=3),
    start_time=end - timedelta(hours=2),
    end_time=end,
    runtime=7200.0,
    username="bf",
    host_list=["bfhost1"],
    **fields,
  )


@pytest.mark.django_db(transaction=True)
def test_backfill_lane_lists_dirty_first_then_stale_with_cursor(monkeypatch):
  """Seeded old jobs: dirty one first, then version-stale newest first; the
  cursor row is written per consumed page and cleared at lap end; skips and
  the census see real rows."""
  from hpcperfstats.site.lib.machine.models import (
    job_data,
    metrics_scheduler_state,
  )

  monkeypatch.setattr(
    um.cfg, "get_metrics_backfill_quiescence_days", lambda: 7.0
  )
  now = timezone.now()
  before = now - timedelta(days=8)
  stale_new = _job("bf-stale-new", now - timedelta(days=20))
  stale_old = _job("bf-stale-old", now - timedelta(days=21))
  dirty = _job(
    "bf-dirty",
    now - timedelta(days=22),
    metrics_dirty_at=now - timedelta(hours=1),
    metrics_version=metrics_module.METRICS_CONTRACT_VERSION,
  )
  awaiting = _job(
    "bf-awaiting",
    now - timedelta(days=23),
    metrics_skipped_since=now - timedelta(days=1),
  )
  quiescent = _job(
    "bf-quiescent",
    now - timedelta(days=24),
    metrics_skipped_since=now - timedelta(days=10),
  )
  current = _job(
    "bf-current",
    now - timedelta(days=25),
    metrics_version=metrics_module.METRICS_CONTRACT_VERSION,
    metrics_coverage=0.5,
  )
  del current
  metrics_scheduler_state.objects.filter(key=um.BACKFILL_CURSOR_KEY).delete()

  gen = um._iter_backfill_pks(before, 300, 1)
  pages = []
  first, _ = next(gen)
  pages.append([r.jid for r in first])
  assert pages == [["bf-dirty"]]
  assert first[0].lane == um.BACKFILL_LANE
  second, _ = next(gen)
  pages.append([r.jid for r in second])
  assert pages[-1] == ["bf-stale-new"]
  assert not metrics_scheduler_state.objects.filter(
    key=um.BACKFILL_CURSOR_KEY
  ).exists()
  third, _ = next(gen)
  pages.append([r.jid for r in third])
  assert pages[-1] == ["bf-stale-old"]
  cursor = um._load_backfill_cursor()
  assert cursor is not None and cursor[1] == "bf-stale-new"
  fourth, _ = next(gen)
  assert [r.jid for r in fourth] == ["bf-quiescent"]
  with pytest.raises(StopIteration):
    next(gen)
  # Lap wrapped: awaiting (quiescence not elapsed) and current were never
  # listed and the cursor is gone.
  assert um._load_backfill_cursor() is None

  # A skip stamps the clock and coverage and consumes the dirty stamp.
  checked_at = timezone.now()
  assert (
    metrics_module.record_backfill_skips(
      [(stale_new.jid, 0.5), (dirty.jid, 0.0)], checked_at
    )
    == 2
  )
  stale_new.refresh_from_db()
  dirty.refresh_from_db()
  assert stale_new.metrics_skipped_since is not None
  assert stale_new.metrics_coverage == 0.5
  assert dirty.metrics_dirty_at is None

  census = um._backfill_census(before, 300)
  assert census["remaining_dirty"] == 0
  # stale_old (never skipped) + quiescent (skip clock elapsed)
  assert census["remaining_stale"] == 2
  # awaiting (1 day) + stale_new (just skipped)
  assert census["awaiting"] == 2
  # bf-current only: bf-dirty's skip recorded coverage 0.0 under the current
  # version but its skip clock is set, so it is awaiting, not partial_final.
  assert census["partial_final"] == 1

  # Persist stamps the version, coverage and clears the skip clock.
  metrics_module._persist_metrics_batch(
    [
      {
        "jid": quiescent.jid,
        "type": "cpu",
        "metric": "avg_cpuusage",
        "units": "#cores",
        "value": 1.0,
        "no_data_reason": None,
      }
    ],
    5,
    metrics_coverage=0.5,
  )
  quiescent.refresh_from_db()
  assert quiescent.metrics_version == metrics_module.METRICS_CONTRACT_VERSION
  assert quiescent.metrics_skipped_since is None
  assert quiescent.metrics_coverage == 0.5

  # failed_final closes a job out under the current version.
  assert metrics_module.persist_metrics_failed_final(stale_old.jid, "x") is True
  stale_old.refresh_from_db()
  assert stale_old.metrics_version == metrics_module.METRICS_CONTRACT_VERSION
  assert um._backfill_census(before, 300)["remaining_stale"] == 0
  del awaiting
  job_data.objects.filter(jid__startswith="bf-").delete()


@pytest.mark.django_db(transaction=True)
def test_mark_jids_metrics_dirty_widened_against_real_rows():
  """Stage 2 on PostgreSQL: a never-processed job older than the window is
  stamped; a never-processed in-window job is not; a running job is not."""
  from hpcperfstats.site.lib.machine import cache_utils
  from hpcperfstats.site.lib.machine.models import job_data

  now = timezone.now()
  old_never = _job("bf-old-never", now - timedelta(days=20))
  young_never = _job("bf-young-never", now - timedelta(days=1))
  young_done = _job(
    "bf-young-done", now - timedelta(days=1), metrics_distinct_time_count=3
  )
  running = _job("bf-running", now + timedelta(hours=1))
  try:
    assert (
      cache_utils.mark_jids_metrics_dirty(
        [old_never.jid, young_never.jid, young_done.jid, running.jid]
      )
      == 2
    )
    stamped = set(
      job_data.objects.filter(
        jid__startswith="bf-", metrics_dirty_at__isnull=False
      ).values_list("jid", flat=True)
    )
    assert stamped == {"bf-old-never", "bf-young-done"}
  finally:
    job_data.objects.filter(jid__startswith="bf-").delete()
