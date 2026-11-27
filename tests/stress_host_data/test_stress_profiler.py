"""Regression coverage for stress report serialization."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path

from .stress_profiler import DEFAULT_REPORT_DIR, StressProfiler


def test_build_report_serializes_postgresql_temporal_values(tmp_path) -> None:
  """PostgreSQL timestamps in introspection snapshots remain JSON-safe."""
  profiler = StressProfiler(tmp_path)
  profiler.pg_snapshots.append({
      "last_autovacuum": datetime(2026, 9, 17, 1, 2, tzinfo=timezone.utc),
      "sample_date": date(2026, 9, 17),
      "sample_time": time(1, 2, 3),
  })

  report = profiler.build_report()

  assert report["pg_snapshots"][0] == {
      "last_autovacuum": "2026-09-17T01:02:00+00:00",
      "sample_date": "2026-09-17",
      "sample_time": "01:02:03",
  }
  json.dumps(report)


def test_default_report_dir_is_checkout_test_runs_stress(
  tmp_path, monkeypatch
) -> None:
  """Unset report dir writes to <checkout>/test_runs/stress, not ./artifacts."""
  monkeypatch.delenv("HPCPERFSTATS_STRESS_REPORT_DIR", raising=False)
  monkeypatch.chdir(tmp_path)
  checkout = Path(__file__).resolve().parents[2]

  profiler = StressProfiler()

  assert profiler.report_dir == checkout / "test_runs" / "stress"
  assert profiler.report_dir == DEFAULT_REPORT_DIR
  assert profiler.report_dir.is_dir()
  assert not (tmp_path / "artifacts").exists()
