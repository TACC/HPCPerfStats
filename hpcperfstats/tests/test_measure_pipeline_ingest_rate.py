"""Tests for scripts/measure_pipeline_ingest_rate.py."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "measure_pipeline_ingest_rate.py"


def _load_module():
  name = "measure_pipeline_ingest_rate"
  spec = importlib.util.spec_from_file_location(name, _SCRIPT)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  sys.modules[name] = mod
  spec.loader.exec_module(mod)
  return mod


def test_script_imports_with_python_313_grammar():
  """Analyzer syntax must remain valid on production Python before 3.14."""
  source = _SCRIPT.read_text(encoding="utf-8")
  ast.parse(source, filename=str(_SCRIPT), feature_version=(3, 13))


@pytest.fixture
def mod():
  return _load_module()


def _ts(minutes_from_start: int, second: int = 0) -> str:
  hour = 10 + minutes_from_start // 60
  minute = minutes_from_start % 60
  return (
    f"2026-07-08T{hour:02}:{minute:02}:{second:02}Z hpcperfstats_pipeline_1 | "
  )


def _census(q: int) -> str:
  return (
    "[sync_timedb:thread:reconstruct-coordinator] queue_orchestrator census "
    f"ingest=3/{q} discover=0/0 append=0/0 day_close=0/0 total_ingested=0 "
    "total_completed=0"
  )


FIXTURE_WINNING = [
  _ts(0)
  + "Messages consumed in the last 10 minutes: 100; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 5",
  _ts(0) + _census(1000),
]
FIXTURE_WINNING += [
  _ts(10 + i)
  + f"ingest file path=/arch/host/{100 + i} outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10"
  for i in range(12)
]
FIXTURE_WINNING += [
  _ts(50) + _census(900),
  _ts(55)
  + "queue_orchestrator progress day=2026-07-08 ingest=12 ingested=12 archive=8",
  _ts(56) + "queue_orchestrator progress day=2026-07-07 archive=4 gate_skip=2",
  _ts(60)
  + "Messages consumed in the last 10 minutes: 100; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 5",
]


FIXTURE_LOSING = [
  _ts(0) + _census(500),
  _ts(10)
  + "Messages consumed in the last 10 minutes: 50; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 50",
  _ts(70)
  + "ingest file path=/arch/host/200 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
  _ts(71)
  + "ingest file path=/arch/host/201 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
  _ts(80) + _census(550),
]


def test_parse_leading_rfc3339_timestamp(mod):
  line = (
    "2026-07-07T08:38:46.381101000-05:00 [update_metrics:thread:readiness-producer] "
    "metrics_deferred_coverage jid=809643 start_ok=False"
  )
  ts, body = mod._strip_log_prefix(line)
  assert ts is not None
  assert ts.year == 2026 and ts.month == 7 and ts.day == 7
  assert "[update_metrics:thread:readiness-producer]" in body


def test_parse_nanosecond_timestamp_python39_compatible(mod):
  ts = mod._parse_log_timestamp("2026-07-07T08:38:46.381101000-05:00")
  assert ts is not None
  assert ts.microsecond == 381101


def test_parse_container_pipe_timestamp(mod):
  line = (
    "hpcperfstats_pipeline_1  | 2026-07-07T08:38:46.381101000-05:00 "
    "[sync_timedb:thread:reconstruct-coordinator] queue_orchestrator census "
    "ingest=0/1000 discover=0/0"
  )
  ts, body = mod._strip_log_prefix(line)
  assert ts is not None
  assert "census ingest=0/1000" in body


def test_parse_container_first_timestamp(mod):
  line = (
    "hpcperfstats_pipeline_1 2026-07-07T08:38:51.004115000-05:00 "
    "[sync_timedb:thread:reconstruct-coordinator] queue_orchestrator census "
    "ingest=0/1000 discover=0/0"
  )
  ts, body = mod._strip_log_prefix(line)
  assert ts is not None
  assert "census ingest=0/1000" in body
  metrics = mod.parse_log_lines([line])
  assert metrics.census_ingest_queued[0][1] == 1000


def test_parse_full_ingest_and_listend(mod):
  metrics = mod.parse_log_lines(FIXTURE_WINNING)
  assert metrics.listend_unlink_sum == 10
  assert metrics.full_ingest_count == 12
  assert metrics.archive_batches_sum == 12


def test_unknown_backlog_eta_is_na(mod):
  """Unknown disk backlog must not invent a zero-hour finish at log end."""
  lines = [
    _ts(0)
    + "Messages consumed in the last 10 minutes: 1; messages waiting to be "
    "consumed: 0; current file unlinks (last 10 minutes): 1",
    _ts(10)
    + (
      "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 "
      "ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10"
    ),
  ]
  outcomes = mod.analyze_lines(lines)
  assert outcomes["backlog_at_start"] == "N/A"
  assert outcomes["backlog_latest"] == "N/A"
  assert outcomes["eta_hours_empirical"] == "N/A"
  assert outcomes["eta_hours_full_ingest"] == "N/A"
  assert outcomes["estimated_finish_local"] == "N/A"
  assert outcomes["estimated_finish_basis"] == "N/A"


def test_overlapping_listend_windows_not_double_counted(mod):
  """Rolling last-10-minute listend reports must not be summed as arrivals."""
  lines = [_ts(0) + _census(1000)]
  # Every minute for an hour, listend reports the same rolling 10-minute count.
  for minute in range(61):
    lines.append(
      _ts(minute)
      + "Messages consumed in the last 10 minutes: 0; messages waiting "
      "to be consumed: 0; current file unlinks (last 10 minutes): 10"
    )
  lines.append(_ts(60) + _census(940))
  for i in range(60):
    lines.append(
      _ts(1 + i)
      + f"ingest file path=/arch/host/{1000 + i} outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10"
    )
  outcomes = mod.analyze_lines(lines, since_minutes=None, exclude_startup=False)
  # Non-overlapping keep: t=0,10,20,30,40,50,60 -> 7 * 10 = 70 over ~60 min.
  assert float(outcomes["window_minutes"]) == pytest.approx(60.0, abs=0.1)
  assert float(outcomes["listend_closed_per_min"]) == pytest.approx(
    70.0 / 60.0,
    rel=1e-3,
  )
  # Naive sum of every overlapping report would be 61*10=610 -> ~10.17/min.
  assert float(outcomes["listend_closed_per_min"]) < 2.0


def test_winning_verdict_and_eta(mod):
  outcomes = mod.analyze_lines(FIXTURE_WINNING)
  assert outcomes["verdict_full_ingest"] == "WINNING"
  assert float(outcomes["ratio_listend_over_full_ingest"]) < 1.0
  assert outcomes["backlog_at_start"] == "1000"
  assert outcomes["backlog_latest"] == "900"
  assert outcomes["backlog_drained_since_start"] == "100"
  assert outcomes["ingest_queue_depth_at_start"] == "1000"
  assert outcomes["ingest_queue_depth_latest"] == "900"
  assert outcomes["eta_hours_full_ingest"] != "N/A"
  assert float(outcomes["eta_hours_full_ingest"]) > 0
  assert outcomes["estimated_finish_local"] != "N/A"
  assert outcomes["estimated_finish_basis"] in (
    "eta_hours_empirical",
    "eta_hours_full_ingest",
  )
  # Local stamp: YYYY-MM-DD HH:MM:SS ±HHMM
  assert re.match(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}$",
    outcomes["estimated_finish_local"],
  )


def test_losing_verdict_and_eta_na(mod):
  outcomes = mod.analyze_lines(FIXTURE_LOSING)
  assert outcomes["verdict_full_ingest"] == "LOSING"
  assert float(outcomes["ratio_listend_over_full_ingest"]) > 1.0
  assert outcomes["eta_hours_full_ingest"] == "N/A"
  assert outcomes["estimated_finish_local"] == "N/A"
  assert outcomes["estimated_finish_basis"] == "N/A"


def test_db_skip_not_counted_as_full_ingest(mod):
  lines = [
    _ts(0)
    + "ingest file path=/arch/host/1 outcome=db_skip elapsed_s=0.1 ingest_ok=yes archive=member_exists db_skip=head_tail size_bytes=1000",
    _ts(61)
    + "ingest file path=/arch/host/2 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
  ]
  metrics = mod.parse_log_lines(lines)
  assert metrics.full_ingest_count == 1


def test_boot_only_skips_pre_boot_lines(mod):
  lines = [
    _ts(0) + _census(9999),
    _ts(5)
    + "queue_orchestrator ingest write stage started write_pool=4 parse_queue_max=8",
    _ts(10) + _census(100),
    _ts(10)
    + "Messages consumed in the last 10 minutes: 0; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 1",
    _ts(40) + _census(80),
    _ts(71)
    + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
    _ts(72)
    + "ingest file path=/arch/host/2 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
  ]
  # Default excludes startup; --boot-only remains an explicit alias.
  outcomes = mod.analyze_lines(lines)
  assert outcomes["backlog_at_start"] == "100"
  assert outcomes["backlog_latest"] == "80"
  assert outcomes["ingest_queue_depth_latest"] == "80"
  assert "T10:05:00" in outcomes["ingest_start_utc"] or outcomes[
    "ingest_start_utc"
  ].endswith("10:05:00+00:00")


def test_measurement_window_starts_at_ingest_gate_not_startup(mod):
  """Elapsed window must begin at gate cleared, not supervisor startup lines."""
  lines = [
    _ts(0) + "sync_timedb: startup maintenance pass reason=startup",
    _ts(30) + _census(5000),
    _ts(60)
    + "queue_orchestrator ingest write stage started write_pool=4 parse_queue_max=8",
    _ts(61) + _census(4000),
    _ts(90) + _census(3500),
    _ts(120) + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 "
    "ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
  ]
  outcomes = mod.analyze_lines(lines)
  # 60 min of startup must not inflate the window: gate@60 -> last@120 = 60 min.
  assert float(outcomes["window_minutes"]) == pytest.approx(60.0, abs=0.1)
  assert outcomes["backlog_at_start"] == "4000"
  assert float(outcomes["elapsed_hours"]) == pytest.approx(1.0, abs=0.01)


def test_later_pending_rescans_do_not_move_ingest_start(mod):
  """Catch-up pending rescans must not reset the measurement window."""
  lines = [
    _ts(0)
    + "queue_orchestrator ingest write stage started write_pool=4 parse_queue_max=8",
    _ts(1) + _census(1000),
    _ts(30) + _census(800),
    _ts(60) + _census(600),
    _ts(90) + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 "
    "ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
  ]
  outcomes = mod.analyze_lines(lines)
  assert float(outcomes["window_minutes"]) == pytest.approx(90.0, abs=0.1)
  assert outcomes["backlog_at_start"] == "1000"


def test_include_startup_keeps_pre_gate_window(mod):
  lines = [
    _ts(0) + _census(9999),
    _ts(60)
    + "queue_orchestrator ingest write stage started write_pool=4 parse_queue_max=8",
    _ts(90) + _census(100),
    _ts(120) + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 "
    "ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
  ]
  outcomes = mod.analyze_lines(lines, exclude_startup=False)
  assert float(outcomes["window_minutes"]) == pytest.approx(120.0, abs=0.1)
  assert outcomes["backlog_at_start"] == "9999"


def test_since_minutes_window(mod):
  lines = [
    _ts(0) + _census(1000),
  ]
  lines += [
    _ts(130 + i)
    + f"ingest file path=/arch/host/{300 + i} outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10"
    for i in range(12)
  ]
  lines.append(
    _ts(200)
    + "Messages consumed in the last 10 minutes: 0; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 1",
  )
  outcomes = mod.analyze_lines(lines, since_minutes=90)
  assert float(outcomes["window_minutes"]) == 90.0
  assert float(outcomes["sync_full_ingest_per_min"]) == pytest.approx(
    12 / 90.0, rel=1e-3
  )


def test_empty_log_raises(mod):
  with pytest.raises(ValueError, match="insufficient log window"):
    mod.analyze_lines([])


def test_no_timestamps_fallback_window(mod):
  lines = [
    "Messages consumed in the last 10 minutes: 1; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 5",
    "Messages consumed in the last 10 minutes: 1; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 5",
  ]
  outcomes = mod.analyze_lines(lines)
  assert float(outcomes["window_minutes"]) == 20.0
  assert float(outcomes["listend_closed_per_min"]) == 0.5


def test_stdout_only_key_count(mod):
  outcomes = mod.analyze_lines(FIXTURE_WINNING)
  text = mod.format_stdout(outcomes)
  lines = text.splitlines()
  assert all("=" in line for line in lines)
  assert lines[0].startswith("window_minutes=")
  # Base keys + mid/decision pack + per-tier elapsed/postgres/frac + selected phases
  assert len(lines) >= 42
  assert "decision_next=" in text
  assert "mid_tier_median_postgres_frac=" in text
  assert "sync_full_ingest_mib_per_min=" in text
  assert "tier_lt_8mib_count=" in text
  assert "tier_8mib_64mib_count=" in text
  assert "ingest_queue_depth_latest=" in text
  assert "ingest_queue_depth_at_start=" in text
  assert "ingest_start_utc=" in text
  assert "estimated_finish_local=" in text
  assert "estimated_finish_basis=" in text
  assert "container_start_utc=" not in text


def test_full_ingest_mib_per_min_and_size_tiers(mod):
  """MiB/min and size-tier medians from known size_bytes / elapsed / postgres."""
  mib = 1024 * 1024
  gib = 1024 * mib
  lines = [
    _ts(0) + "Messages consumed in the last 10 minutes: 1; messages waiting to "
    "be consumed: 0; current file unlinks (last 10 minutes): 1",
    _ts(0) + _census(10),
    # lt_8mib - unordered tokens (postgres before size before elapsed)
    _ts(10)
    + (
      f"ingest file path=/arch/a outcome=ingested postgres_s=10.0 ingest_ok=yes db_skip=no size_bytes={4 * mib} elapsed_s=40.0 archive=yes"
    ),
    # 8mib_64mib (Horizon dense mid-tier)
    _ts(15)
    + (
      f"ingest file path=/arch/a2 outcome=ingested postgres_s=12.0 ingest_ok=yes db_skip=no size_bytes={16 * mib} elapsed_s=45.0 archive=yes"
    ),
    # 64mib_1gib
    _ts(20)
    + (
      f"ingest file path=/arch/b outcome=ingested elapsed_s=100.0 ingest_ok=yes archive=yes db_skip=no size_bytes={128 * mib} postgres_s=50.0"
    ),
    # 1_4gib
    _ts(30)
    + (
      f"ingest file path=/arch/c outcome=ingested size_bytes={2 * gib} ingest_ok=yes db_skip=no elapsed_s=200.0 postgres_s=80.0 archive=yes"
    ),
    # ge_4gib
    _ts(40)
    + (
      f"ingest file path=/arch/d outcome=ingested elapsed_s=300.0 postgres_s=120.0 size_bytes={5 * gib} ingest_ok=yes archive=yes db_skip=no"
    ),
    _ts(60)
    + "Messages consumed in the last 10 minutes: 1; messages waiting to "
    "be consumed: 0; current file unlinks (last 10 minutes): 1",
    _ts(60) + _census(6),
  ]
  outcomes = mod.analyze_lines(lines)
  # (4+16+128) MiB + 2 GiB + 5 GiB = 148 MiB + 7168 MiB = 7316 MiB / 60 min
  expected_mib = (4 + 16 + 128 + 2 * 1024 + 5 * 1024) / 60.0
  assert float(outcomes["sync_full_ingest_mib_per_min"]) == pytest.approx(
    expected_mib,
    rel=1e-6,
  )
  assert outcomes["tier_lt_8mib_count"] == "1"
  assert outcomes["tier_lt_8mib_median_elapsed_s"] == "40.000"
  assert outcomes["tier_lt_8mib_median_postgres_s"] == "10.000"
  assert outcomes["tier_8mib_64mib_count"] == "1"
  assert outcomes["tier_8mib_64mib_median_elapsed_s"] == "45.000"
  assert outcomes["tier_8mib_64mib_median_postgres_s"] == "12.000"
  assert outcomes["tier_64mib_1gib_count"] == "1"
  assert outcomes["tier_64mib_1gib_median_elapsed_s"] == "100.000"
  assert outcomes["tier_64mib_1gib_median_postgres_s"] == "50.000"
  assert outcomes["tier_1_4gib_count"] == "1"
  assert outcomes["tier_ge_4gib_count"] == "1"
  assert outcomes["tier_ge_4gib_median_elapsed_s"] == "300.000"
  assert outcomes["tier_ge_4gib_median_postgres_s"] == "120.000"


def test_unordered_size_elapsed_postgres_tokens(mod):
  """Named-field regex must not require size_bytes before elapsed_s."""
  body = (
    "ingest file path=/x outcome=ingested postgres_s=1.5 "
    "ingest_ok=yes db_skip=no elapsed_s=9.0 size_bytes=1024 archive=yes"
  )
  metrics = mod.LogMetrics()
  mod._record_full_ingest(metrics, body)
  assert metrics.full_ingest_count == 1
  assert metrics.full_ingest_bytes == 1024
  assert metrics.full_ingest_count_by_tier["lt_8mib"] == 1
  assert metrics.full_ingest_elapsed_by_tier["lt_8mib"] == [9.0]
  assert metrics.full_ingest_postgres_by_tier["lt_8mib"] == [1.5]


def test_overnight_pack_prefers_dense_8mib_when_64mib_empty(mod):
  """When 64mib_1gib is empty, decision pack must use 8mib_64mib (not lt_8mib)."""
  mib = 1024 * 1024
  lines = [
    _ts(0) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
    _ts(0) + _census(1000),
  ]
  for i in range(6):
    # Tiny files - must not drive mid_tier when dense samples exist.
    lines.append(
      _ts(5 + i)
      + (
        f"ingest file path=/arch/tiny/{i} outcome=ingested elapsed_s=20.0 ingest_ok=yes archive=yes db_skip=no size_bytes={2 * mib} postgres_s=1.0 db_execute_s=0.5 copy_s=0.2 orm_materialize_s=0.2 feed_s=15.0 collapse_gpu_s=1.0 parse_unaccounted_s=1.0"
      ),
    )
  for i in range(12):
    lines.append(
      _ts(20 + i * 5)
      + (
        f"ingest file path=/arch/dense/{i} outcome=ingested elapsed_s=100.0 ingest_ok=yes archive=yes db_skip=no size_bytes={16 * mib} postgres_s=5.0 db_execute_s=2.0 copy_s=1.0 orm_materialize_s=1.0 feed_s=10.0 collapse_gpu_s=70.0 parse_unaccounted_s=5.0"
      ),
    )
  lines.append(
    _ts(90) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
  )
  lines.append(
    _ts(90) + _census(900),
  )
  outcomes = mod.analyze_lines(lines)
  assert outcomes["mid_tier_name"] == "8mib_64mib"
  assert outcomes["decision_next"] == "parse_hold_collapse_gpu_s"
  assert outcomes["mid_tier_top_parse_hold"] == "collapse_gpu_s"
  assert outcomes["tier_lt_8mib_count"] == "6"
  assert outcomes["tier_8mib_64mib_count"] == "12"


def test_dense_tier_below_min_samples_warns_not_decision_grade(mod, capsys):
  """Tail admit-starvation (n<12 dense) must label decision_next_dense not decision-grade."""
  mib = 1024 * 1024
  lines = [
    _ts(0) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
    _ts(0) + _census(1000),
  ]
  for i in range(3):
    lines.append(
      _ts(10 + i * 5)
      + (
        f"ingest file path=/arch/dense/{i} outcome=ingested elapsed_s=100.0 ingest_ok=yes archive=yes db_skip=no size_bytes={16 * mib} postgres_s=5.0 feed_s=80.0 collapse_gpu_s=70.0 parse_unaccounted_s=2.0"
      ),
    )
  lines.append(
    _ts(40) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
  )
  lines.append(
    _ts(40) + _census(900),
  )
  outcomes = mod.analyze_lines(lines)
  assert outcomes["tier_8mib_64mib_count"] == "3"
  assert outcomes["decision_next_dense"] == "parse_hold_feed_s"
  err = capsys.readouterr().err
  assert "tier_8mib_64mib_count=3 below min 12" in err
  assert "not decision-grade" in err


def test_overnight_pack_prefers_dense_when_64mib_sparse(mod):
  """Four 64mib files must not beat twelve dense 8-64 MiB samples."""
  mib = 1024 * 1024
  lines = [
    _ts(0) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
    _ts(0) + _census(1000),
  ]
  for i in range(4):
    lines.append(
      _ts(5 + i)
      + (
        f"ingest file path=/arch/big/{i} outcome=ingested elapsed_s=2000.0 ingest_ok=yes archive=yes db_skip=no size_bytes={128 * mib} postgres_s=1000.0 db_execute_s=100.0 copy_s=10.0 orm_materialize_s=900.0 feed_s=50.0"
      ),
    )
  for i in range(12):
    lines.append(
      _ts(30 + i * 5)
      + (
        f"ingest file path=/arch/dense/{i} outcome=ingested elapsed_s=800.0 ingest_ok=yes archive=yes db_skip=no size_bytes={16 * mib} postgres_s=200.0 db_execute_s=50.0 copy_s=5.0 orm_materialize_s=30.0 feed_s=40.0 delta_s=120.0 jid_invalidate_s=130.0 collapse_gpu_s=90.0 parse_unaccounted_s=2.0"
      ),
    )
  lines.append(
    _ts(120)
    + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
  )
  lines.append(
    _ts(120) + _census(900),
  )
  outcomes = mod.analyze_lines(lines)
  assert outcomes["mid_tier_name"] == "8mib_64mib"
  assert outcomes["decision_next_dense"] == "parse_hold_jid_invalidate_s"
  assert outcomes["dense_tier_top_parse_hold"] == "jid_invalidate_s"


def test_overnight_pack_decision_next_write_path(mod):
  """High mid-tier postgres_frac must yield write_timescale_path."""
  mib = 1024 * 1024
  lines = [
    _ts(0) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
    _ts(0) + _census(1000),
  ]
  for i in range(12):
    lines.append(
      _ts(10 + i * 5)
      + (
        f"ingest file path=/arch/h/{i} outcome=ingested elapsed_s=100.0 ingest_ok=yes archive=yes db_skip=no size_bytes={128 * mib} postgres_s=50.0 db_execute_s=40.0 copy_s=25.0 conflict_insert_s=15.0 orm_materialize_s=5.0 feed_s=10.0"
      ),
    )
  lines.append(
    _ts(70) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
  )
  lines.append(
    _ts(70) + _census(900),
  )
  outcomes = mod.analyze_lines(lines)
  assert outcomes["decision_next"] == "write_timescale_path"
  assert outcomes["mid_tier_write_exec_dominates"] == "yes"
  assert float(outcomes["mid_tier_median_postgres_frac"]) == pytest.approx(
    0.5,
    rel=1e-3,
  )


def test_overnight_pack_decision_next_parse_hold(mod):
  """Low postgres frac with dominant feed_s must yield parse_hold_feed_s."""
  mib = 1024 * 1024
  lines = [
    _ts(0) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
    _ts(0) + _census(1000),
  ]
  for i in range(12):
    lines.append(
      _ts(10 + i * 5)
      + (
        f"ingest file path=/arch/h/{i} outcome=ingested elapsed_s=100.0 ingest_ok=yes archive=yes db_skip=no size_bytes={128 * mib} postgres_s=5.0 db_execute_s=2.0 copy_s=1.0 orm_materialize_s=1.0 feed_s=70.0 collapse_s=10.0 parse_unaccounted_s=5.0"
      ),
    )
  lines.append(
    _ts(70) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
  )
  lines.append(
    _ts(70) + _census(900),
  )
  outcomes = mod.analyze_lines(lines)
  assert outcomes["decision_next"] == "parse_hold_feed_s"
  assert outcomes["mid_tier_top_parse_hold"] == "feed_s"
  assert outcomes["mid_tier_parse_unaccounted_dominates"] == "no"


def test_overnight_pack_decision_next_parse_unaccounted(mod):
  """Unaccounted median >= top named hold -> parse_unaccounted_investigate."""
  mib = 1024 * 1024
  lines = [
    _ts(0) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
    _ts(0) + _census(1000),
  ]
  for i in range(12):
    lines.append(
      _ts(10 + i * 5)
      + (
        f"ingest file path=/arch/h/{i} outcome=ingested elapsed_s=100.0 ingest_ok=yes archive=yes db_skip=no size_bytes={128 * mib} postgres_s=5.0 db_execute_s=2.0 copy_s=1.0 orm_materialize_s=1.0 feed_s=20.0 collapse_s=10.0 parse_unaccounted_s=80.0"
      ),
    )
  lines.append(
    _ts(70) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
  )
  lines.append(
    _ts(70) + _census(900),
  )
  outcomes = mod.analyze_lines(lines)
  assert outcomes["decision_next"] == "parse_unaccounted_investigate"
  assert outcomes["mid_tier_parse_unaccounted_dominates"] == "yes"
  assert outcomes["mid_tier_top_parse_hold"] == "feed_s"


def test_overnight_pack_decision_next_telem_incomplete(mod, capsys):
  """Parse holds without write-phase tokens -> telem_incomplete_re_soak."""
  mib = 1024 * 1024
  lines = [
    _ts(0) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
    _ts(0) + _census(1000),
  ]
  for i in range(12):
    lines.append(
      _ts(10 + i * 5)
      + (
        f"ingest file path=/arch/h/{i} outcome=ingested elapsed_s=100.0 ingest_ok=yes archive=yes db_skip=no size_bytes={128 * mib} postgres_s=5.0 feed_s=70.0 collapse_s=10.0 parse_unaccounted_s=5.0"
      ),
    )
  lines.append(
    _ts(70) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
  )
  lines.append(
    _ts(70) + _census(900),
  )
  outcomes = mod.analyze_lines(lines)
  assert outcomes["decision_next"] == "telem_incomplete_re_soak"
  assert outcomes["mid_tier_telem_incomplete"] == "yes"
  err = capsys.readouterr().err
  assert (
    "telem_incomplete_re_soak" in err
    or "write-phase" in err
    or "telem incomplete" in err
  )


def test_overnight_pack_decision_next_telem_all_phases_absent(mod, capsys):
  """Mid-tier files with no parse and no write phase tokens -> incomplete."""
  mib = 1024 * 1024
  lines = [
    _ts(0) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
    _ts(0) + _census(1000),
  ]
  for i in range(12):
    lines.append(
      _ts(10 + i * 5)
      + (
        f"ingest file path=/arch/h/{i} outcome=ingested elapsed_s=100.0 ingest_ok=yes archive=yes db_skip=no size_bytes={128 * mib} postgres_s=5.0"
      ),
    )
  lines.append(
    _ts(70) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 60",
  )
  lines.append(
    _ts(70) + _census(900),
  )
  outcomes = mod.analyze_lines(lines)
  assert outcomes["decision_next"] == "telem_incomplete_re_soak"
  assert outcomes["mid_tier_telem_incomplete"] == "yes"
  err = capsys.readouterr().err
  assert "telem_incomplete" in err or "telem incomplete" in err


def test_cli_script_runs_from_repo(tmp_path):
  log_path = tmp_path / "pipeline.log"
  log_path.write_text("\n".join(FIXTURE_WINNING) + "\n", encoding="utf-8")
  proc = subprocess.run(
    [sys.executable, str(_SCRIPT), "--log-file", str(log_path)],
    cwd=str(_REPO),
    check=False,
    capture_output=True,
    text=True,
  )
  assert proc.returncode == 0, proc.stderr
  assert "verdict_full_ingest=WINNING" in proc.stdout
  assert "ERROR:" not in proc.stdout


def test_tier_stdout_prints_all_hold_medians(mod):
  """Stdout tier lines carry every write and parse hold median once."""
  mib = 1024 * 1024
  lines = [
    _ts(0)
    + "queue_orchestrator ingest write stage started write_pool=4 parse_queue_max=8",
    _ts(1)
    + (
      "ingest file path=/arch/dense outcome=ingested ingest_ok=yes "
      "db_skip=no "
      f"size_bytes={16 * mib} elapsed_s=45.0 postgres_s=12.0 "
      "feed_s=1.0 collapse_host_sum_s=2.0 parse_unaccounted_s=0.5 "
      "orm_materialize_s=3.0"
    ),
  ]
  outcomes = mod.analyze_lines(lines, exclude_startup=False)
  text = mod.format_stdout(outcomes)
  keys = [line.split("=", 1)[0] for line in text.splitlines()]
  assert len(keys) == len(set(keys))
  hold_names = list(
    dict.fromkeys(mod._WRITE_PHASE_TOKEN_NAMES + mod._PARSE_HOLD_TOKEN_NAMES)
  )
  for tok in hold_names:
    assert f"tier_8mib_64mib_median_{tok}" in keys
  for tok in (
    "collapse_host_sum_s",
    "feed_s",
    "parse_unaccounted_s",
    "orm_materialize_s",
  ):
    assert f"tier_8mib_64mib_median_{tok}" in keys


def test_fresh_campaign_tokens_median_outside_write_share(mod):
  """F1/F2 tokens median on the dense tier and do not enter write share."""
  mib = 1024 * 1024
  lines = [
    _ts(0)
    + "queue_orchestrator ingest write stage started write_pool=4 parse_queue_max=8",
    _ts(1)
    + (
      "ingest file path=/arch/dense outcome=ingested ingest_ok=yes "
      "db_skip=no "
      f"size_bytes={16 * mib} elapsed_s=45.0 postgres_s=12.0 "
      "orm_materialize_s=3.0 overlap_headroom_s=9.0 orm_rows=4000"
    ),
  ]
  outcomes = mod.analyze_lines(lines, exclude_startup=False)
  assert outcomes["tier_8mib_64mib_median_overlap_headroom_s"] == "9.000"
  assert outcomes["tier_8mib_64mib_median_orm_rows"] == "4000.000"
  metrics = mod.parse_log_lines(lines, exclude_startup=False)
  phases = metrics.full_ingest_phases_by_tier["8mib_64mib"]
  write_sum = sum(
    sum(phases.get(tok, [])) for tok in mod._WRITE_PHASE_TOKEN_NAMES
  )
  assert write_sum == 3.0
  assert "orm_rows" not in mod._WRITE_PHASE_TOKEN_NAMES
  assert "overlap_headroom_s" not in mod._WRITE_PHASE_TOKEN_NAMES


def _winning_ratio_lines(census: list[str] | None = None) -> list[str]:
  lines = [
    _ts(0) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 1",
    _ts(30) + "ingest file path=/arch/a outcome=ingested elapsed_s=1.0 "
    "ingest_ok=yes archive=yes db_skip=no size_bytes=1000",
    _ts(60) + "ingest file path=/arch/b outcome=ingested elapsed_s=1.0 "
    "ingest_ok=yes archive=yes db_skip=no size_bytes=1000",
    _ts(90) + "Messages consumed in the last 10 minutes: 100; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 1",
  ]
  if census:
    lines[1:1] = census
  return lines


def test_rising_census_queue_does_not_stop(mod):
  """A winning listend ratio must not stop while census queued depth rises."""
  lines = _winning_ratio_lines(
    [
      _ts(10) + "queue_orchestrator census ingest=4/1000 discover=1/1",
      _ts(80) + "queue_orchestrator census ingest=4/1800 discover=1/1",
    ],
  )
  outcomes = mod.analyze_lines(lines, exclude_startup=False)
  assert float(outcomes["queued_slope_per_min"]) > 0
  assert outcomes["decision_next"] != "stop_ingest_rate_watch_archive"


def test_missing_census_slope_does_not_stop(mod):
  """No census samples must not claim the ingest-rate stop token."""
  outcomes = mod.analyze_lines(
    _winning_ratio_lines(),
    exclude_startup=False,
  )
  assert outcomes["queued_slope_per_min"] == "N/A"
  assert outcomes["decision_next"] != "stop_ingest_rate_watch_archive"


def test_nonpositive_census_slope_stops_when_ratio_wins(mod):
  """Stop only when the listend ratio wins and queued depth is not rising."""
  lines = _winning_ratio_lines(
    [
      _ts(10) + "queue_orchestrator census ingest=4/1800 discover=1/1",
      _ts(80) + "queue_orchestrator census ingest=4/1000 discover=1/1",
    ],
  )
  outcomes = mod.analyze_lines(lines, exclude_startup=False)
  assert float(outcomes["queued_slope_per_min"]) < 0
  assert outcomes["decision_next"] == "stop_ingest_rate_watch_archive"


def test_write_setup_median_is_reported(mod):
  """write_setup_s stays a per-tier median on ingest outcome lines."""
  mib = 1024 * 1024
  lines = [
    _ts(0)
    + "queue_orchestrator ingest write stage started write_pool=4 parse_queue_max=8",
    _ts(1)
    + (
      "ingest file path=/arch/dense outcome=ingested ingest_ok=yes "
      "db_skip=no "
      f"size_bytes={16 * mib} elapsed_s=100.0 postgres_s=1.0 "
      "write_setup_s=8.75"
    ),
  ]
  outcomes = mod.analyze_lines(lines, exclude_startup=False)
  assert outcomes["tier_8mib_64mib_median_write_setup_s"] == "8.750"


def test_campaign_detail_and_supervisor_meters_outside_write_share(mod):
  """Detail clocks and supervisor meters median without entering write share."""
  mib = 1024 * 1024
  lines = [
    _ts(0)
    + "queue_orchestrator ingest write stage started write_pool=4 parse_queue_max=8",
    _ts(1)
    + (
      "ingest file path=/arch/dense outcome=ingested ingest_ok=yes "
      "db_skip=no "
      f"size_bytes={16 * mib} elapsed_s=100.0 postgres_s=1.0 "
      "stage_ddl_s=50.0 reconnect_s=2.0"
    ),
    _ts(2)
    + (
      "queue_orchestrator status ingest_hot=0/0 "
      "job_store_hold_s=1.500 census_s=0.040 rss_mib=12 "
      "admit_probes=3 dead_letter_ingest=2 dead_letter_append=1"
    ),
    _ts(3) + "queue_orchestrator boot discover seen=1 rescan_s=4.500",
    _ts(4) + "INFO: populate complete day=2026-07-08 populate_s=8.000",
  ]
  outcomes = mod.analyze_lines(lines, exclude_startup=False)
  assert outcomes["tier_8mib_64mib_median_stage_ddl_s"] == "50.000"
  assert outcomes["tier_8mib_64mib_median_reconnect_s"] == "2.000"
  assert outcomes["median_job_store_hold_s"] == "1.500"
  assert outcomes["median_census_s"] == "0.040"
  assert outcomes["median_rss_mib"] == "12.000"
  assert outcomes["median_admit_probes"] == "3.000"
  assert outcomes["median_dead_letter"] == "3.000"
  assert outcomes["median_rescan_s"] == "4.500"
  assert outcomes["median_populate_s"] == "8.000"
  metrics = mod.parse_log_lines(lines, exclude_startup=False)
  pack = mod._tier_decision_pack(
    metrics,
    "8mib_64mib",
    clear_top_parse_when_write_heavy=False,
  )
  assert pack["mid_write_dominates"] is False
  assert pack["write_sample_n"] == 0
  phases = metrics.full_ingest_phases_by_tier["8mib_64mib"]
  write_sum = sum(
    sum(phases.get(tok, [])) for tok in mod._WRITE_PHASE_TOKEN_NAMES
  )
  assert write_sum == 0.0
  assert "stage_ddl_s" not in mod._WRITE_PHASE_TOKEN_NAMES
  assert "write_setup_s" in mod._WRITE_PHASE_TOKEN_NAMES
  assert "reconnect_s" not in mod._WRITE_PHASE_TOKEN_NAMES
  assert "census_s" not in mod._WRITE_PHASE_TOKEN_NAMES


def test_listend_unlink_bytes_parsed_and_deduped(mod):
  """Non-overlapping byte sum must follow the same keep policy as counts."""
  lines = [
    _ts(0) + _census(1000),
  ]
  for minute in (0, 1, 9, 10, 19, 20):
    lines.append(
      _ts(minute)
      + "Messages consumed in the last 10 minutes: 0; messages waiting "
      "to be consumed: 0; current file unlinks (last 10 minutes): 2; "
      "unlink_bytes_last_10=2000000000; average unlink file size: 1000000000 bytes"
    )
  lines.append(_ts(20) + _census(900))
  outcomes = mod.analyze_lines(lines, since_minutes=None, exclude_startup=False)
  # Kept at t=0,10,20 -> 3 * 2e9 bytes over ~20 min window.
  assert float(outcomes["listend_sealed_mib_per_min"]) == pytest.approx(
    (3 * 2_000_000_000) / mod._MIB_BYTES / 20.0,
    rel=1e-3,
  )


def test_verdict_full_ingest_mib_can_differ_from_file_verdict(mod):
  """MiB/min compares sealed bytes to ingest bytes, not file counts."""
  lines = [
    _ts(0) + _census(100),
    _ts(0) + "Messages consumed in the last 10 minutes: 1; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 100; "
    "unlink_bytes_last_10=1000000; average unlink file size: 10000 bytes",
    _ts(10) + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 "
    "ingest_ok=yes archive=yes db_skip=no size_bytes=10000000000 stats_rows=10",
    _ts(20) + _census(90),
  ]
  outcomes = mod.analyze_lines(lines, since_minutes=None, exclude_startup=False)
  assert outcomes["verdict_full_ingest"] == "LOSING"
  assert outcomes["verdict_full_ingest_mib"] == "WINNING"


def test_verdict_full_ingest_mib_na_without_byte_fields(mod, capsys):
  lines = [
    _ts(0) + _census(100),
    _ts(0) + "Messages consumed in the last 10 minutes: 1; messages waiting "
    "to be consumed: 0; current file unlinks (last 10 minutes): 5",
    _ts(10) + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 "
    "ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
    _ts(20) + _census(90),
  ]
  outcomes = mod.analyze_lines(lines, since_minutes=None, exclude_startup=False)
  assert outcomes["verdict_full_ingest_mib"] == "N/A"
  assert outcomes["ratio_listend_over_full_ingest_mib"] == "N/A"
  captured = capsys.readouterr()
  assert "verdict_full_ingest_mib is N/A" in captured.err


def test_legacy_backlog_lines_are_ignored(mod):
  """Removed pending-rescan / truncate / throughput lines never set backlog."""
  lines = [
    _ts(0) + "sync_timedb: pending rescan done pending=1000 elapsed_s=1.0",
    _ts(1) + "Pending stats file list truncated pending=900 max=2000",
    _ts(2)
    + "Throughput telemetry: active_workers=4 backlog=900 chunk_size=1000",
    _ts(61)
    + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
  ]
  outcomes = mod.analyze_lines(lines)
  assert outcomes["backlog_latest"] == "N/A"
  assert outcomes["ingest_queue_depth_latest"] == "N/A"


def test_census_backlog_drain_and_eta(mod):
  lines = [
    _ts(0) + _census(5000),
    _ts(30) + _census(4000),
    _ts(60) + _census(3000),
    _ts(61)
    + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
  ]
  outcomes = mod.analyze_lines(lines)
  assert outcomes["backlog_at_start"] == "5000"
  assert outcomes["backlog_latest"] == "3000"
  assert outcomes["backlog_drained_since_start"] == "2000"
  assert outcomes["pct_complete_since_start"] == "40.00"
  assert outcomes["ingest_queue_depth_latest"] == "3000"
  assert outcomes["eta_hours_empirical"] != "N/A"


def test_archive_batches_sum_progress_archive_token_only(mod):
  lines = [
    _ts(0)
    + "queue_orchestrator progress day=2026-07-08 ingest=5 archive=3 gate_skip=1",
    _ts(1) + "queue_orchestrator progress day=2026-07-07 archive=4",
    _ts(2) + "queue_orchestrator progress day=2026-07-06 tar_delete=9 sealed=2",
    _ts(3) + "sync_timedb: checkpoint deferred archive finalize count=50",
  ]
  assert mod.parse_log_lines(lines).archive_batches_sum == 7


def test_only_write_stage_started_marks_ingest_start(mod):
  lines = [
    _ts(0) + "startup ingest gate cleared; ingest may begin",
    _ts(5) + "chunk imap start",
    _ts(9)
    + "queue_orchestrator ingest write stage started write_pool=4 parse_queue_max=8",
    _ts(20) + "queue_orchestrator status ingest_hot=0/0",
  ]
  assert mod._find_ingest_start_cutoff(lines) == 2
  assert mod._find_ingest_start_cutoff(lines[:2]) is None


def test_archive_batches_field_replaces_file_unit_comparisons(mod):
  """Batches are not files: no ratio/verdict/gap/ETA built on them."""
  outcomes = mod.analyze_lines(FIXTURE_WINNING)
  assert float(outcomes["sync_archive_batches_per_min"]) > 0
  for gone in (
    "sync_archive_done_per_min",
    "ratio_listend_over_archive_done",
    "verdict_archive_done",
    "backlog_gap_archive_done_per_min",
    "eta_hours_archive_done",
  ):
    assert gone not in outcomes
  assert "sync_archive_batches_per_min=" in mod.format_stdout(outcomes)


_WAIT_LINE = (
  "ingest file path=/arch/host/9 outcome=ingested elapsed_s=40.0 "
  "timeout_s=0.0 ingest_ok=yes archive=yes db_skip=no size_bytes=20000000 "
  "postgres_s=10.0 copy_encode_s=2.5 thr_cpu_s=11.0 thr_runq_s=1.5 "
  "thr_sleep_s=3.0 stages_cpu_s=6.0 feed_cpu_s=4.0 build_df_cpu_s=1.0 "
  "delta_cpu_s=0.5 collapse_cpu_s=0.5 arc_cpu_s=0.2 wq_put_s=0.3 "
  "wq_resid_s=0.4 wq_join_s=0.6 wq_n=7 w_exec_s=8.0 w_cpu_s=7.0 "
  "w_runq_s=0.9 slot_wait_s=1.1 codon_primary=1 emit_start=1 "
  "collapse_concat_s=5.0 concat_s=0.7 stats_rows=10"
)


def test_wait_counters_reported_per_tier(mod):
  outcomes = mod.analyze_lines([_ts(0) + _WAIT_LINE, _ts(61) + _WAIT_LINE])
  tier = "tier_8mib_64mib_median_"
  expect = {
    "copy_encode_s": "2.500",
    "thr_cpu_s": "11.000",
    "thr_runq_s": "1.500",
    "thr_sleep_s": "3.000",
    "stages_cpu_s": "6.000",
    "feed_cpu_s": "4.000",
    "build_df_cpu_s": "1.000",
    "delta_cpu_s": "0.500",
    "collapse_cpu_s": "0.500",
    "arc_cpu_s": "0.200",
    "wq_put_s": "0.300",
    "wq_resid_s": "0.400",
    "wq_join_s": "0.600",
    "wq_n": "7.000",
    "w_exec_s": "8.000",
    "w_cpu_s": "7.000",
    "w_runq_s": "0.900",
    "slot_wait_s": "1.100",
    "codon_primary": "1.000",
    "emit_start": "1.000",
  }
  for tok, want in expect.items():
    assert outcomes[tier + tok] == want, tok
  text = mod.format_stdout(outcomes)
  assert f"{tier}thr_cpu_s=11.000" in text


def test_token_regexes_do_not_match_inside_longer_names(mod):
  """``concat_s`` must not read ``collapse_concat_s``; ``rss_mib`` not tree_rss."""
  outcomes = mod.analyze_lines([_ts(0) + _WAIT_LINE, _ts(61) + _WAIT_LINE])
  assert outcomes["tier_8mib_64mib_median_concat_s"] == "0.700"
  assert outcomes["tier_8mib_64mib_median_collapse_concat_s"] == "5.000"
  metrics = mod.parse_log_lines(
    [
      _ts(0)
      + "queue_orchestrator census ingest=0/0 tree_rss_mib=9999 rss_mib=12"
    ],
  )
  assert metrics.supervisor_samples["rss_mib"] == [12.0]
  metrics = mod.parse_log_lines(
    [_ts(0) + "census ingest=0/0 tree_rss_mib=9999 ingest_pool_rss_mib=8888"],
  )
  assert metrics.supervisor_samples["rss_mib"] == []
