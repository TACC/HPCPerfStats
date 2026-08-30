"""Unit tests for sync_timedb 10-minute progress report helpers."""

from __future__ import annotations

from datetime import date, datetime, timezone

from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_progress_report as pr


def test_format_day_progress_omits_zeros():
  ledger = pr.DayActivityLedger()
  ledger.add("gate_skip", 50)
  ledger.add("ingest_handoff", 50)
  ledger.add("ingest", 0)
  line = pr.format_day_progress_line("2025-05-05", ledger)
  assert "gate_skip=50" in line
  assert "ingest_handoff=50" in line
  assert "ingest=" not in line


def test_format_day_progress_empty_when_idle():
  assert pr.format_day_progress_line("2025-05-05", pr.DayActivityLedger()) == ""


def test_format_status_idle_and_busy_includes_discover():
  idle = pr.format_status_line(
      band_ratios={},
      queue_deltas={},
      busy_kinds=[],
      orphan_inflight={},
  )
  assert idle == "queue_orchestrator status idle"
  line = pr.format_status_line(
      band_ratios={"ingest_hot": {"inflight": 8, "queued": 200}},
      queue_deltas={"append": 0, "ingest_catchup": 3},
      busy_kinds=["ingest", "discover"],
      orphan_inflight={"day_close": 2, "ingest": 1},
      oldest_day="2025-05-05",
      oldest_age_s=86400,
  )
  assert "ingest_hot=8/200" in line
  assert "ingest_catchup_q_delta=3" in line
  assert "append_q_delta=" not in line
  assert "busy=ingest,discover" in line
  assert "orphan_inflight=day_close:2" in line
  assert "oldest_day=2025-05-05" in line


def test_format_status_includes_fill_block():
  line = pr.format_status_line(
      band_ratios={"ingest_hot": {"inflight": 0, "queued": 452}},
      queue_deltas={},
      busy_kinds=["append"],
      orphan_inflight={},
      fill_block="claim_none",
  )
  assert "fill_block=claim_none" in line
  assert "ingest_hot=0/452" in line


def test_progress_state_set_fill_block():
  state = pr.reset_progress_state_for_tests()
  state.set_fill_block("skip_missing")
  lines = state.emit_lines(
      band_ratios={},
      busy_kinds=[],
      census_inflight={},
      queue_depth_now={},
  )
  assert any("fill_block=skip_missing" in line for line in lines)


def test_format_queue_census_is_current_over_queued():
  census = {
      "ingest": {"queued": 2, "inflight": 1},
      "append": {"queued": 0, "inflight": 0},
      "discover": {"queued": 0, "inflight": 0},
      "day_close": {"queued": 0, "inflight": 0},
  }
  text = jq.format_queue_census(census)
  assert "ingest=1/2" in text


def test_record_prefer_day_and_emit_reset():
  state = pr.reset_progress_state_for_tests()
  state.record_day("2025-05-05", "gate_skip", 2)
  state.record_day(None, "attempt_bump", 1)
  lines = state.emit_lines(
      band_ratios={},
      busy_kinds=[],
      census_inflight={},
      queue_depth_now={},
  )
  assert any("gate_skip=2" in line for line in lines)
  assert any("attempt_bump=1" in line for line in lines)
  emitted = []
  assert state.maybe_emit_and_reset(
      now_mono=state._window_started_mono + 601.0,
      interval_s=600.0,
      band_ratios={},
      busy_kinds=[],
      census_inflight={},
      queue_depth_now={"append": 5},
      log_fn=lambda msg, flush=False: emitted.append(msg),
  )
  assert emitted
  assert not state.snapshot_days()


def test_resolve_oldest_queued_day_from_catchup_score():
  class _C:
    def zrangebyscore(self, *a, **k):
      d = date(2025, 5, 5)
      score = jq.encode_ingest_score(
          band="catchup", day=d, today=d, identity="p|1|2",
      )
      return [("p|1|2", float(score))]

    def lindex(self, *a, **k):
      return None

  day, age = pr.resolve_oldest_queued_day(
      _C(),
      now=datetime(2025, 5, 6, tzinfo=timezone.utc),
  )
  assert day == "2025-05-05"
  assert age == 86400


def test_decode_catchup_calendar_day_roundtrip():
  d = date(2025, 5, 5)
  score = jq.encode_ingest_score(
      band="catchup", day=d, today=d, identity="a",
  )
  assert jq.decode_catchup_calendar_day(score) == d
  assert jq.decode_catchup_calendar_day(0) is None


def test_day_token_from_day_close_identity_parses_tar_path():
  assert pr.day_token_from_day_close_identity(
      "/hpcperfstats/daily_archive/2026-06-07.tar",
  ) == "2026-06-07"
  assert pr.day_token_from_day_close_identity("2026-06-07") == "2026-06-07"
  assert pr.day_token_from_day_close_identity("/hpcperfst") is None


def test_resolve_oldest_queued_day_from_day_close_tar_path():
  class _C:
    def zrangebyscore(self, *a, **k):
      return []

    def lindex(self, *a, **k):
      return "/hpcperfstats/daily_archive/2026-07-15.tar"

  day, age = pr.resolve_oldest_queued_day(
      _C(),
      now=datetime(2026, 7, 16, tzinfo=timezone.utc),
  )
  assert day == "2026-07-15"
  assert age == 86400
