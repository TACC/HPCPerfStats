"""The census line reports the calendar day of the last backfill file ingested.

Operator request 2026-10-29 (hpcperfstats04): discover now hands the ingest
queue the oldest backfill files first, so the day of the most recent backfill
ingest shows how far the catch-up has got. Hot-band files ingest all the time
and would pin the token to today, so only backfill (non-hot) files set it.
``last_ingested_backfill_day=`` is a plain module global, overwritten by each
ingest without a lock, and absent until the first backfill ingest.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime

import pytest

import hpcperfstats.dbload.sync_timedb as st
from hpcperfstats.dbload.lib import (
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
  monkeypatch.setattr(qo, "_LAST_INGESTED_BACKFILL_DAY", None)
  monkeypatch.setattr(qo, "_local_today", lambda: date(2026, 10, 29))
  monkeypatch.setattr(qo, "_hot_days", lambda: 3)
  monkeypatch.setattr(
    st, "_record_ingest_marks_from_worker_result", lambda *a, **k: None
  )
  monkeypatch.setattr(jq, "ack_job", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_retry_or_dead_letter", lambda *a, **k: "requeued")


def _path(day: date) -> str:
  epoch = int(datetime(day.year, day.month, day.day, 12).timestamp())
  return f"/archive/h/{epoch}"


class _Ready:
  def __init__(self, path: str, outcome: str, ingest_ok: bool = True):
    self._row = (path, True, ingest_ok, 0.1, {"outcome": outcome})

  def ready(self):
    return True

  def get(self, timeout=0):
    del timeout
    return self._row


def _drain(tmp_path, path: str, outcome: str, ingest_ok: bool = True) -> None:
  claim = jq.ClaimedJob(
    kind=jq.JOB_KIND_INGEST,
    identity=path,
    owner_token="n:h:b:1",
    deadline=1.0,
    score=5.0,
  )
  qo._drain_ingest_ready(
    SyncTimedbJobStore(""),
    inflight={path: _Ready(path, outcome, ingest_ok)},
    claims={path: claim},
    tgz_archive_dir="/daily",
    archive_data_dir=str(tmp_path),
  )


def test_backfill_ingest_overwrites_the_last_ingested_day(tmp_path):
  _drain(tmp_path, _path(date(2026, 8, 25)), "ingested")
  assert qo._LAST_INGESTED_BACKFILL_DAY == "2026-08-25"
  _drain(tmp_path, _path(date(2026, 8, 24)), "db_skip")
  assert qo._LAST_INGESTED_BACKFILL_DAY == "2026-08-24"


def test_hot_and_failed_ingests_leave_the_day_alone(tmp_path):
  _drain(tmp_path, _path(date(2026, 8, 25)), "ingested")
  _drain(tmp_path, _path(date(2026, 10, 29)), "ingested")
  _drain(tmp_path, _path(date(2026, 8, 30)), "parse_fail", ingest_ok=False)
  assert qo._LAST_INGESTED_BACKFILL_DAY == "2026-08-25"


def test_census_line_carries_the_token():
  src = inspect.getsource(qo._reconstruct_coordinator_loop)
  assert "last_ingested_backfill_day=" in src
  assert "_LAST_INGESTED_BACKFILL_DAY" in src
