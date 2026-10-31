"""Dropped-at-cap files are rediscovered; Codon ingests report parsed rows.

hpcperfstats04 2026-10-29: 732k raw files sat on disk. Discover drops a
non-hot incomplete file while the ingest queue is at the cap
(``sync_ingest_queue_max_size``), nothing remembers it, and the full-archive
walk only runs at boot, so those files waited for the next restart. Separately
the Codon COPY-frame branch never counted ``stats_rows_parsed``, so every
Codon ingest logged ``stats_rows_parsed=0`` and recorded a zero-host mark.
A file with any host or proc lines ingests; when no host rows reach the DB it
gets a zero-host mark so it can archive. Only a file with no lines at all is
quarantined (``empty``).
"""

from __future__ import annotations

import time
from datetime import date

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import (
  conf_parser as cfg,
  sync_timedb_job_discover as jd,
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore
from hpcperfstats.dbload.lib.sync_timedb_stats_find import FindStatsRecord

TODAY = date(2026, 9, 14)


def _discover(client, records, day_for):
  return jd.stream_enqueue_ingest_from_find_records(
    client,
    records,
    tgz_archive_dir="/daily",
    today=TODAY,
    hot_days=3,
    calendar_day_fn=lambda rec: day_for(rec.path),
    ingest_is_complete_fn=lambda **_k: False,
    append_is_complete_fn=lambda **_k: True,
  )


def _full_store(monkeypatch):
  monkeypatch.setattr(jq, "queue_capacity_limit", lambda: 2)
  client = SyncTimedbJobStore("")
  for ident in ("/old/a", "/old/b"):
    score = jq.encode_ingest_score(
      band="catchup", day=date(2026, 6, 1), today=TODAY, identity=ident
    )
    jq.zadd_ingest_job(client, identity=ident, score=score)
  return client


def test_discover_counts_catchup_files_it_dropped_at_the_cap(monkeypatch):
  client = _full_store(monkeypatch)
  records = [
    FindStatsRecord(path="/archive/h/hot", mtime=1.0, size=10, inode=1),
    FindStatsRecord(path="/archive/h/old1", mtime=1.0, size=10, inode=2),
    FindStatsRecord(path="/archive/h/old2", mtime=1.0, size=10, inode=3),
  ]
  stats = _discover(
    client,
    records,
    lambda p: TODAY if p.endswith("hot") else date(2026, 6, 1),
  )
  assert stats.stopped_at_capacity is True
  assert stats.dropped_at_capacity == 2


def test_discover_drops_nothing_when_the_queue_has_room(monkeypatch):
  monkeypatch.setattr(jq, "queue_capacity_limit", lambda: 100)
  stats = _discover(
    SyncTimedbJobStore(""),
    [FindStatsRecord(path="/archive/h/old1", mtime=1.0, size=10, inode=2)],
    lambda _p: date(2026, 6, 1),
  )
  assert stats.dropped_at_capacity == 0


@pytest.fixture
def _pending(monkeypatch):
  qo._FULL_REDISCOVER_PENDING.clear()
  yield qo._FULL_REDISCOVER_PENDING
  qo._FULL_REDISCOVER_PENDING.clear()


def _run_bg_discover(monkeypatch, dropped):
  client = SyncTimedbJobStore("")
  jq.enqueue_list_job(
    client, kind=jq.JOB_KIND_DISCOVER, identity="rescan:1", dedupe=True
  )
  stats = jd.StreamingDiscoverStats(
    seen=10,
    enqueued_ingest=0,
    enqueued_append=0,
    skipped_complete=0,
    stopped_at_capacity=bool(dropped),
    dropped_at_capacity=dropped,
  )
  monkeypatch.setattr(qo, "_boot_stream_discover", lambda *a, **k: stats)
  qo._run_background_discover(
    client,
    "/archive",
    tgz_archive_dir="/daily",
    log_fn=lambda *a, **k: None,
    mtime_days=1,
    startdate=None,
    enddate=None,
  )


def test_background_discover_flags_a_full_rediscover_after_drops(
  monkeypatch, _pending
):
  _run_bg_discover(monkeypatch, dropped=5)
  assert _pending.is_set()


def test_background_discover_without_drops_flags_nothing(monkeypatch, _pending):
  _run_bg_discover(monkeypatch, dropped=0)
  assert not _pending.is_set()


def test_failed_whole_archive_walk_is_logged_and_retried(monkeypatch, _pending):
  # hpcperfstats01 2026-10-31: the boot whole-archive walk raised inside the
  # discover-bg Future, nothing read it, and every later line said seen=0.
  client = SyncTimedbJobStore("")
  ident = qo.discover_job_identity("/archive", None)
  jq.enqueue_list_job(client, kind=jq.JOB_KIND_DISCOVER, identity=ident)
  qo._BOOT_FULL_DISCOVER_PENDING.set()

  def _boom(*_a, **_k):
    raise RuntimeError("classify blew up on /archive/h/1700000000")

  logged: list[str] = []
  monkeypatch.setattr(qo, "_boot_stream_discover", _boom)
  qo._run_background_discover(
    client,
    "/archive",
    tgz_archive_dir="/daily",
    log_fn=lambda msg, **_k: logged.append(str(msg)),
    mtime_days=1,
    startdate=None,
    enddate=None,
  )
  failed = [m for m in logged if "queue_orchestrator discover failed" in m]
  assert len(failed) == 1
  assert "window=all" in failed[0]
  assert "RuntimeError: classify blew up on /archive/h/1700000000" in failed[0]
  assert _pending.is_set()  # next idle pass reruns the whole-archive walk
  assert not qo._BOOT_FULL_DISCOVER_PENDING.is_set()  # seals not held forever
  census = jq.queue_census(client)[jq.JOB_KIND_DISCOVER]
  assert census["queued"] == 0 and census["inflight"] == 0


def _idle_pass(
  monkeypatch,
  *,
  catchup_queued,
  cap=3000,
  interval_hours=6,
  last_full_age_h=0,
):
  submitted = []
  monkeypatch.setattr(qo, "_last_idle_reconstruct_mono", 0.0)
  monkeypatch.setattr(qo, "_discover_bg_is_busy", lambda: False)
  monkeypatch.setattr(
    qo,
    "_submit_background_discover",
    lambda *a, **k: submitted.append(k["mtime_days"]),
  )
  monkeypatch.setattr(
    qo, "_enqueue_day_closes_for_daily_dir", lambda *a, **k: 0
  )
  monkeypatch.setattr(qo, "_readmit_due_dead_letters", lambda *a, **k: 0)
  monkeypatch.setattr(qo, "sealed_integrity_sweep_once", lambda *a, **k: None)
  monkeypatch.setattr(qo, "sweep_dead_archive_scratch", lambda *a, **k: 0)
  monkeypatch.setattr(jq, "enqueue_list_job", lambda *a, **k: True)
  monkeypatch.setattr(
    jq, "ingest_queue_census", lambda _c: (0, catchup_queued, catchup_queued)
  )
  monkeypatch.setattr(jd, "discover_ingest_capacity_limit", lambda: cap)
  monkeypatch.setattr(
    cfg, "get_sync_ingest_full_rescan_interval_hours", lambda: interval_hours
  )
  monkeypatch.setattr(
    qo,
    "_last_full_rediscover_mono",
    time.monotonic() - last_full_age_h * 3600 if last_full_age_h else 0.0,
  )
  logs: list[str] = []
  qo._idle_reconstruct_pass(
    SyncTimedbJobStore(""),
    "/archive",
    tgz_archive_dir="/daily",
    log_fn=lambda msg, **_k: logs.append(msg),
    mtime_days=1,
  )
  return submitted, logs


def test_idle_pass_runs_a_full_walk_once_the_queue_is_below_low_water(
  monkeypatch, _pending
):
  _pending.set()
  submitted, logs = _idle_pass(monkeypatch, catchup_queued=1000)
  assert submitted == [None]
  assert not _pending.is_set()
  assert any("full rediscover" in m for m in logs)


def test_idle_pass_waits_while_the_queue_is_above_low_water(
  monkeypatch, _pending
):
  _pending.set()
  submitted, _logs = _idle_pass(monkeypatch, catchup_queued=2000)
  assert submitted == [1]
  assert _pending.is_set()


def test_idle_pass_keeps_the_incremental_window_without_drops(
  monkeypatch, _pending
):
  submitted, _logs = _idle_pass(monkeypatch, catchup_queued=0)
  assert submitted == [1]


def test_idle_pass_walks_the_whole_archive_every_interval(
  monkeypatch, _pending
):
  submitted, logs = _idle_pass(
    monkeypatch, catchup_queued=10, last_full_age_h=7
  )
  assert submitted == [None]
  assert any("reason=periodic" in m for m in logs)


def test_idle_pass_skips_the_periodic_walk_inside_the_interval(
  monkeypatch, _pending
):
  submitted, _logs = _idle_pass(
    monkeypatch, catchup_queued=10, last_full_age_h=2
  )
  assert submitted == [1]


def test_periodic_walk_waits_for_the_queue_to_drain(monkeypatch, _pending):
  submitted, _logs = _idle_pass(
    monkeypatch, catchup_queued=2900, last_full_age_h=7
  )
  assert submitted == [1]


def test_periodic_walk_can_be_disabled(monkeypatch, _pending):
  submitted, _logs = _idle_pass(
    monkeypatch, catchup_queued=0, interval_hours=0, last_full_age_h=999
  )
  assert submitted == [1]


def test_dropped_files_are_still_rediscovered_when_the_periodic_walk_is_off(
  monkeypatch, _pending
):
  _pending.set()
  submitted, _logs = _idle_pass(monkeypatch, catchup_queued=0, interval_hours=0)
  assert submitted == [None]


def _codon_ingest(
  monkeypatch, tmp_path, host_copy, proc_copy, parsed=None, fake_parse=None
):
  """Run the streaming ingest on one fake Codon frame; return (meta, details).

  ``details`` collects quarantine ``error_detail`` strings (the quarantine
  move itself is faked as successful).
  """
  stats_file = tmp_path / "host.example.com" / "1789000000"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text("x\n", encoding="utf-8")
  frame = {"host_copy": host_copy, "proc_copy": proc_copy}
  if parsed is not None:
    frame["parsed_host_rows"] = parsed

  def _copy_frame_parse(_path, *, on_chunk, **_kw):
    on_chunk(frame, {})

  details: list[str] = []

  def _fake_quarantine(_stats_file, error_detail=None):
    details.append(str(error_detail))
    return True

  monkeypatch.setattr(st, "_ensure_ingest_codon_sticky_slot", lambda: None)
  monkeypatch.setattr(st, "update_worker_substage", lambda *a, **k: None)
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st, "_release_ingest_worker_heap", lambda *a, **k: None)
  monkeypatch.setattr(st, "_quarantine_failed_ingest_parse", _fake_quarantine)
  monkeypatch.setattr(
    st, "_resolve_streaming_ingest_start", lambda *_a, **_k: (False, (0, True))
  )
  monkeypatch.setattr(
    st,
    "parse_stats_file_streaming_incremental",
    fake_parse or _copy_frame_parse,
  )
  monkeypatch.setattr(
    st, "_submit_stats_payload_write", lambda *a, **k: (None, True, True)
  )
  monkeypatch.setattr(
    st, "_drain_ingest_write_pipeline", lambda _o: (True, True)
  )
  result = st._add_stats_file_to_db_streaming_incremental(
    str(stats_file), time.time()
  )
  return st._unpack_ingest_worker_result(result)[-1], details


def _quarantine_class(details) -> str:
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    quarantine_failure_class_for_detail,
  )

  assert len(details) == 1, details
  return quarantine_failure_class_for_detail(details[0])


def _kernel_parsed_host_rows(lines) -> int:
  import importlib.util
  from pathlib import Path

  path = Path(st.__file__).parent / "codon" / "feed_collapse.py"
  spec = importlib.util.spec_from_file_location("fc_parsed_under_test", path)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod.feed_delta_collapse_arc(lines, {}, {})[-1]


def test_codon_ingest_reports_the_host_rows_it_parsed(monkeypatch, tmp_path):
  """``stats_rows_parsed`` is the kernel's pre-collapse count, not COPY rows."""
  meta, details = _codon_ingest(
    monkeypatch, tmp_path, b"r1\nr2\nr3\n", b"p1\np2\n", parsed=9
  )
  assert meta["outcome"] == "ingested" and details == []
  assert meta["stats_rows"] == 3
  assert meta["stats_rows_parsed"] == 9
  assert meta["proc_rows"] == 2
  assert _marks_recorded(monkeypatch, meta) == []


def test_codon_frame_without_parsed_field_falls_back_to_host_rows(
  monkeypatch, tmp_path
):
  meta, _details = _codon_ingest(monkeypatch, tmp_path, b"r1\nr2\n", b"")
  assert meta["stats_rows_parsed"] == 2


def test_host_rows_without_proc_rows_ingest_and_archive_normally(
  monkeypatch, tmp_path
):
  """Host>0, proc=0 (e.g. an idle node) ingests; head/tail proves it."""
  meta, details = _codon_ingest(monkeypatch, tmp_path, b"r1\nr2\n", b"")
  assert meta["outcome"] == "ingested" and details == []
  assert meta["stats_rows"] == 2 and meta["proc_rows"] == 0
  assert _marks_recorded(monkeypatch, meta) == []


@pytest.mark.parametrize(
  ("parsed", "proc_copy"),
  [(3, b"p1\n"), (3, b""), (0, b"p1\n")],
  ids=["host_lines_and_proc", "host_lines_only", "proc_only"],
)
def test_file_with_host_or_proc_lines_ingests_and_is_zero_host_marked(
  monkeypatch, tmp_path, parsed, proc_copy
):
  """Any host or proc lines: never quarantined; no host rows written -> mark.

  The mark lets the raw archive and delete without a head/tail DB proof
  (which needs host rows). ``stats_rows_parsed`` stays in the ingest line.
  """
  meta, details = _codon_ingest(
    monkeypatch, tmp_path, b"", proc_copy, parsed=parsed
  )
  assert meta["outcome"] == "ingested" and details == []
  assert meta["stats_rows"] == 0 and meta["stats_rows_parsed"] == parsed
  assert _marks_recorded(monkeypatch, meta) == ["/x/1789000000"]


def test_kernel_proc_only_input_parses_no_host_rows(monkeypatch, tmp_path):
  parsed = _kernel_parsed_host_rows(
    [
      "!host_proc uid,W=32 vm_peak,W=64",
      "1000.0 J1 hostA",
      "host_proc 12/a/0/0 @full 7 100",
    ]
  )
  assert parsed == 0
  meta, details = _codon_ingest(
    monkeypatch, tmp_path, b"", b"p1\n", parsed=parsed
  )
  assert meta["outcome"] == "ingested" and details == []


def test_file_with_no_lines_at_all_is_quarantined_empty(monkeypatch, tmp_path):
  """Only a file with no host and no proc lines is quarantined."""
  meta, details = _codon_ingest(monkeypatch, tmp_path, b"", b"", parsed=0)
  assert meta["outcome"] == "quarantine"
  assert _quarantine_class(details) == "empty"


def test_codon_soa_frame_uses_the_kernel_parsed_count_from_carry(
  monkeypatch, tmp_path
):
  """emit=insert / line-batch SoA columns are post-collapse; carry has parsed."""

  def fake_parse(_path, *, on_chunk, delta_carry, **_kw):
    delta_carry.codon_finished_chunk = True
    delta_carry.codon_parsed_host_rows = 4
    on_chunk({"time": []}, {})

  meta, details = _codon_ingest(
    monkeypatch, tmp_path, b"", b"", fake_parse=fake_parse
  )
  assert meta["outcome"] == "ingested" and details == []
  assert meta["stats_rows"] == 0 and meta["stats_rows_parsed"] == 4


def _marks_recorded(monkeypatch, meta) -> list[str]:
  """Zero-host marks the production call site records for one outcome meta."""
  from hpcperfstats.dbload.lib import (
    sync_timedb_file_complete_ingest_mark as fc,
    sync_timedb_zero_host_ingest_mark as zh,
  )

  recorded: list[str] = []
  monkeypatch.setattr(
    zh,
    "record_zero_host_ingest_mark",
    lambda path, **_k: recorded.append(path) or True,
  )
  monkeypatch.setattr(
    fc,
    "maybe_record_file_complete_ingest_mark_from_outcome",
    lambda *a, **k: None,
  )
  st._record_ingest_marks_from_worker_result(
    ("/x/1789000000", True, True, 1.0, meta), log_fn=None
  )
  return recorded


def test_worker_result_marks_every_ingest_with_no_host_rows_written(
  monkeypatch,
):
  """Production call site: 0 host rows written marks, whatever was parsed."""
  base = {"outcome": "ingested", "stats_rows": 0}
  for extra in (
    {"stats_rows_parsed": 12, "proc_rows": 3},
    {"stats_rows_parsed": 12, "proc_rows": 0},
    {"stats_rows_parsed": 0, "proc_rows": 3},
  ):
    assert _marks_recorded(monkeypatch, {**base, **extra}) == [
      "/x/1789000000"
    ], extra
  assert (
    _marks_recorded(
      monkeypatch, {"outcome": "ingested", "stats_rows": 4, "proc_rows": 3}
    )
    == []
  )
