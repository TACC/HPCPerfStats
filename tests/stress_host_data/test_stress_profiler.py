"""Regression coverage for stress report serialization."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone

from .stress_profiler import StressProfiler


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
