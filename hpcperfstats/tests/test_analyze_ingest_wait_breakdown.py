"""Tests for scripts/analyze_ingest_wait_breakdown.py (wait/CPU wall split)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
  Path(__file__).resolve().parents[2]
  / "scripts"
  / "analyze_ingest_wait_breakdown.py"
)
_spec = importlib.util.spec_from_file_location(
  "analyze_ingest_wait_breakdown", _SCRIPT
)
mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(mod)


def _line(**kw: object) -> str:
  base = {
    "path": "/a/host1/1792900000",
    "outcome": "ingested",
    "elapsed_s": 100.0,
    "size_bytes": 20 * 1024 * 1024,
    "parse_elapsed_s": 100.0,
    "postgres_s": 20.0,
    "stages_sum_s": 40.0,
    "thr_cpu_s": 30.0,
    "thr_runq_s": 25.0,
    "thr_sleep_s": 45.0,
    "wq_join_s": 15.0,
    "wq_resid_s": 4.0,
    "wq_put_s": 0.5,
    "wq_n": 3,
    "w_exec_s": 12.0,
    "w_cpu_s": 8.0,
    "w_runq_s": 3.0,
    "stages_cpu_s": 28.0,
    "arc_s": 10.0,
    "arc_cpu_s": 6.0,
    "slot_wait_s": 0.2,
  }
  base.update(kw)
  toks = " ".join(f"{k}={v}" for k, v in base.items())
  return (
    f"2026-10-25T05:00:00.000000Z [x:thread:ingest-pool] ingest file {toks}\n"
  )


def test_parse_kv_line_extracts_numeric_and_text_tokens():
  kv = mod.parse_kv_line(_line(fail_reason="none"))
  assert kv["outcome"] == "ingested"
  assert kv["thr_runq_s"] == "25.0"


def test_tier_of_size_boundaries():
  mib = 1024 * 1024
  assert mod.tier_of(1 * mib) == "lt8"
  assert mod.tier_of(8 * mib) == "8_64"
  assert mod.tier_of(64 * mib) == "64_256"
  assert mod.tier_of(256 * mib) == "ge256"


def test_load_records_skips_db_skip_and_lines_without_telemetry(tmp_path):
  log = tmp_path / "child.log"
  log.write_text(
    _line()
    + _line(outcome="db_skip")
    + "2026-10-25T05:00:01Z [x] ingest file path=/b outcome=ingested elapsed_s=5.0 size_bytes=10\n"
  )
  recs = mod.load_records(log)
  assert len(recs) == 1
  assert recs[0]["thr_cpu_s"] == 30.0


def test_summarize_shares_sum_to_one_and_split_sleep():
  recs = [mod.record_from_kv(mod.parse_kv_line(_line()))]
  s = mod.summarize(recs)["8_64"]
  total = (
    s["cpu_share"]
    + s["runq_share"]
    + s["write_wait_share"]
    + s["other_sleep_share"]
  )
  assert total == pytest.approx(1.0, abs=1e-6)
  assert s["write_wait_share"] == pytest.approx(0.15)
  assert s["other_sleep_share"] == pytest.approx(0.30)
  assert s["n"] == 1


def test_summarize_write_wait_is_capped_by_sleep():
  # join wait cannot exceed thread sleep (clock skew between sources).
  rec = mod.record_from_kv(
    mod.parse_kv_line(_line(thr_sleep_s=5.0, wq_join_s=9.0))
  )
  s = mod.summarize([rec])["8_64"]
  assert s["other_sleep_share"] >= 0.0
  assert s["write_wait_share"] == pytest.approx(5.0 / 60.0)


def test_stage_cpu_ratio_reports_cpu_over_wall():
  s = mod.summarize([mod.record_from_kv(mod.parse_kv_line(_line()))])["8_64"]
  assert s["arc_cpu_over_wall"] == pytest.approx(0.6)


def test_census_summary_reads_window_tokens():
  lines = [
    "x queue_orchestrator census ingest=64/0 psi_cpu10=40.00 sys_runnable=30 cpu_ingest=20.0 runq_ingest=10.0 pygc_n=2 pygc_s=0.020 pygc_max_ms=15 wq_depth=3/32 w_busy=9/16 boehm_gc_no=5\n",
    "x queue_orchestrator census ingest=64/0 psi_cpu10=60.00 sys_runnable=50 cpu_ingest=24.0 runq_ingest=14.0 pygc_n=0 pygc_s=0.000 pygc_max_ms=0 wq_depth=5/32 w_busy=16/16 boehm_gc_no=9\n",
  ]
  c = mod.census_summary(lines)
  assert c["psi_cpu10"]["mean"] == pytest.approx(50.0)
  assert c["psi_cpu10"]["max"] == 60.0
  assert c["cpu_ingest"]["mean"] == pytest.approx(22.0)
  assert c["w_busy"]["max"] == 16.0
  assert c["pygc_s"]["sum"] == pytest.approx(0.02)


def test_render_markdown_has_tier_rows_and_headline(tmp_path):
  log = tmp_path / "child.log"
  log.write_text(_line() + _line(size_bytes=100 * 1024 * 1024))
  text = mod.render_markdown(mod.summarize(mod.load_records(log)), {})
  assert "| 8_64 |" in text
  assert "| 64_256 |" in text
  assert "cpu" in text and "write_wait" in text


def test_main_prints_report_and_returns_zero(tmp_path, capsys):
  log = tmp_path / "child.log"
  log.write_text(_line())
  assert mod.main(["--log-file", str(log)]) == 0
  assert "8_64" in capsys.readouterr().out


def test_main_returns_nonzero_when_no_telemetry_lines(tmp_path, capsys):
  log = tmp_path / "child.log"
  log.write_text("nothing here\n")
  assert mod.main(["--log-file", str(log)]) == 1
