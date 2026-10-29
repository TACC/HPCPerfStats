"""Discover classify skips the two per-file costs that made the walk crawl.

hpcperfstats04 2026-10-29: the oldest-first whole-archive walk classified about
7 files/s. py-spy of the discover thread, three samples: two in
``get_mutable_tar_authority_member_map`` re-listing the 21 GB 2026-08-25 open
tar (appends kept changing it) for files that still needed ingest, whose append
answer nothing reads; one in ``stats_file_head_ingested_in_db``, a head/tail
query per unmarked file against a DB saturated by ingest (listend live DB
ingest is off there).

Now classify looks up the tar only once ingest is complete, discover checks
ingest completeness from the marks only, and a head/tail ``db_skip`` writes the
file-complete mark when listend live DB ingest is off (where head+tail is
already the archive/delete proof), so the walk does not requeue it forever.
"""

from __future__ import annotations

from datetime import date

import pytest

from hpcperfstats.dbload.lib import (
  sync_timedb_file_complete_ingest_mark as fcm,
  sync_timedb_ingest_readiness as ready,
  sync_timedb_job_discover as jd,
  sync_timedb_job_reconstruct as jr,
  sync_timedb_queue_orchestrator as qo,
  sync_timedb_zero_host_ingest_mark as zhm,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


def _classify(ingest_complete: bool, append_calls: list):
  return jr.classify_closed_raw_path(
    "/archive/h/1787000000",
    tgz_archive_dir="/daily",
    size=1,
    mtime_ns=1,
    calendar_day=date(2026, 8, 25),
    ingest_is_complete_fn=lambda **_k: ingest_complete,
    append_is_complete_fn=lambda **_k: append_calls.append(1) or False,
  )


# --- A: no open-tar lookup for a file that still needs ingest ---------------


def test_classify_skips_the_tar_lookup_while_ingest_is_incomplete():
  calls = []
  plan = _classify(False, calls)
  assert calls == []
  assert plan.kinds_to_enqueue() == ("ingest",)


def test_classify_checks_the_tar_once_ingest_is_complete():
  calls = []
  plan = _classify(True, calls)
  assert calls == [1]
  assert plan.kinds_to_enqueue() == ("append",)


# --- B: discover reads the marks only ---------------------------------------


@pytest.fixture
def marks(monkeypatch):
  def _no_db(_path):
    raise AssertionError("discover must not query the DB per file")

  monkeypatch.setattr(ready, "stats_file_head_ingested_in_db", _no_db)
  monkeypatch.setattr(
    fcm, "has_file_complete_ingest_mark", lambda p, **_k: p == "/marked"
  )
  monkeypatch.setattr(
    zhm, "has_zero_host_ingest_mark", lambda p, **_k: p == "/zero"
  )


def test_discover_ingest_check_reads_marks_only(marks):
  for path, done in (("/marked", True), ("/zero", True), ("/none", False)):
    assert (
      jr.discover_ingest_is_complete(
        path=path, archive_data_dir="/a", listend_enabled=False
      )
      is done
    )


def test_boot_walk_uses_the_marks_only_ingest_check(monkeypatch):
  seen = {}

  def _stream(*_a, **kw):
    seen.update(kw)
    return jd.StreamingDiscoverStats(0, 0, 0, 0)

  monkeypatch.setattr(qo, "iter_find_stats_stdout_chunks", lambda *a, **k: [])
  monkeypatch.setattr(
    jd, "stream_enqueue_ingest_from_find_stdout_chunks", _stream
  )
  monkeypatch.setattr(
    qo, "_enqueue_day_closes_for_daily_dir", lambda *a, **k: 0
  )
  qo._boot_stream_discover(
    SyncTimedbJobStore(""),
    "/archive",
    tgz_archive_dir="/daily",
    log_fn=lambda *a, **k: None,
  )
  assert seen["ingest_is_complete_fn"] is jr.discover_ingest_is_complete


# --- B: a head/tail db_skip is marked when listend live ingest is off -------


def _seg(tmp_path):
  path = tmp_path / "host" / "1710000000"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text("payload")
  return str(path)


def _record(tmp_path, seg, skip, **kw):
  return fcm.maybe_record_file_complete_ingest_mark_from_outcome(
    seg,
    ingest_ok=True,
    outcome="db_skip",
    db_skip=skip,
    archive_data_dir=str(tmp_path),
    **kw,
  )


@pytest.mark.parametrize("skip", ["head_tail", "tail_window"])
def test_head_tail_db_skip_is_marked_when_listend_is_off(tmp_path, skip):
  seg = _seg(tmp_path)
  assert _record(tmp_path, seg, skip, listend_enabled=False) is True
  assert fcm.has_file_complete_ingest_mark(seg, archive_data_dir=str(tmp_path))


@pytest.mark.parametrize("skip", ["head_tail", "tail_window"])
def test_head_tail_db_skip_is_not_marked_when_listend_is_on(tmp_path, skip):
  seg = _seg(tmp_path)
  assert _record(tmp_path, seg, skip, listend_enabled=True) is False


def test_mark_reads_listend_from_conf_and_fails_closed(tmp_path, monkeypatch):
  seg = _seg(tmp_path)
  key = "hpcperfstats.dbload.lib.conf_parser.get_listend_db_ingest_enabled"
  monkeypatch.setattr(key, lambda: True)
  assert _record(tmp_path, seg, "head_tail") is False

  def _broken():
    raise RuntimeError("no conf")

  monkeypatch.setattr(key, _broken)
  assert _record(tmp_path, seg, "head_tail") is False
  monkeypatch.setattr(key, lambda: False)
  assert _record(tmp_path, seg, "head_tail") is True
