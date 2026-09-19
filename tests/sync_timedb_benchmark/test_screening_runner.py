"""Unit tests for ingest-width screening helpers."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.scaling_selection import select_thread_winner
from tests.sync_timedb_benchmark.screening_runner import (
    SCREENING_WIDTHS,
    build_screening_manifest,
    parse_replicates_env,
    parse_widths_env,
    plant_corpus_into_archive,
    run_width_matrix,
    summarize_width_replicates,
    write_screening_artifact,
)


def test_parse_widths_env_defaults_to_campaign_matrix():
  assert parse_widths_env("") == SCREENING_WIDTHS
  assert 96 in SCREENING_WIDTHS


def test_parse_widths_env_accepts_csv():
  assert parse_widths_env("8,2,4") == (2, 4, 8)


def test_parse_replicates_env_defaults_to_two():
  assert parse_replicates_env("") == 2
  assert parse_replicates_env("3") == 3


def test_plant_corpus_into_archive_copies_host_trees(tmp_path):
  corpus = tmp_path / "corpus"
  host = corpus / "benchhost0000.cluster_name.domain.edu"
  host.mkdir(parents=True)
  (host / "1700000000").write_text("payload\n", encoding="utf-8")
  archive = tmp_path / "archive"
  planted = plant_corpus_into_archive(corpus, archive)
  assert planted == ["benchhost0000.cluster_name.domain.edu"]
  assert (archive / planted[0] / "1700000000").read_text(encoding="utf-8") == (
      "payload\n"
  )


def test_summarize_and_winner_integration():
  points = [
      summarize_width_replicates(16, [8.0, 8.5]),
      summarize_width_replicates(32, [10.0, 10.2]),
      summarize_width_replicates(96, [11.0, 11.1], long_lock_wait=True),
  ]
  winner = select_thread_winner(points)
  assert winner["threads"] == 32


def test_run_width_matrix_times_out_when_ingest_hangs():
  """A hung ingest must raise TimeoutError instead of blocking forever."""
  with pytest.raises(TimeoutError, match="timed out"):
    run_width_matrix(
        widths=(1,),
        replicates=1,
        file_count=1,
        set_width=lambda _n: None,
        reset_and_plant=lambda: None,
        run_ingest=lambda: time.sleep(5),
        ingest_timeout_s=0.2,
    )


def test_run_width_matrix_records_files_per_s(tmp_path):
  calls: list[int] = []

  def set_width(n: int) -> None:
    calls.append(n)

  points = run_width_matrix(
      widths=(1, 2),
      replicates=2,
      file_count=4,
      set_width=set_width,
      reset_and_plant=lambda: None,
      run_ingest=lambda: None,
      seed=1,
  )
  assert sorted(calls) == [1, 1, 2, 2]
  assert {point["threads"] for point in points} == {1, 2}
  assert all(point["replicates"] == 2 for point in points)


def test_empty_screen_corpus_env_falls_back_to_default():
  """Empty SCREEN_CORPUS env must not resolve to the process cwd."""
  from tests.sync_timedb_benchmark import test_ingest_width_screening as mod

  assert mod.DEFAULT_SMOKE_CORPUS.name == "corpus_smoke"
  assert mod.DEFAULT_STEADY_CORPUS.name == "corpus_steady"


def test_knee_mode_defaults_widths_and_replicates(monkeypatch):
  from tests.sync_timedb_benchmark.screening_runner import (
      DEFAULT_KNEE_REPLICATES,
      KNEE_WIDTHS,
      parse_replicates_env,
      parse_widths_env,
  )

  monkeypatch.setenv("HPCPERFSTATS_SYNC_TIMEDB_KNEE", "1")
  monkeypatch.delenv("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES", raising=False)
  assert parse_widths_env() == KNEE_WIDTHS
  assert parse_replicates_env() == DEFAULT_KNEE_REPLICATES
  assert KNEE_WIDTHS == (48, 64, 80, 96)
  assert DEFAULT_KNEE_REPLICATES == 5


def test_workflow_knee_unsets_ambient_screen_width_env():
  """--knee must clear leftover SCREEN_WIDTHS/REPLICATES unless explicitly allowed."""
  text = (
      Path(__file__).resolve().parents[1]
      / "run_sync_timedb_benchmark_workflow.sh"
  ).read_text(encoding="utf-8")
  assert "HPCPERFSTATS_SYNC_TIMEDB_KNEE_ALLOW_SCREEN_ENV" in text
  assert "unset HPCPERFSTATS_SYNC_TIMEDB_SCREEN_WIDTHS" in text
  assert "unset HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES" in text


def test_screening_watcher_uses_ingest_timeout_not_fixed_90s():
  """Large-file knee hangs if the early-shutdown watcher dies at 90s."""
  text = (
      Path(__file__).resolve().parent / "test_ingest_width_screening.py"
  ).read_text(encoding="utf-8")
  assert "time() + 90.0" not in text
  assert "ingest_timeout_s" in text
  assert "has_file_complete_ingest_mark" in text


def test_write_knee_artifact_prefix(tmp_path, monkeypatch):
  from tests.sync_timedb_benchmark.screening_runner import (
      build_screening_manifest,
      write_screening_artifact,
  )

  monkeypatch.setenv("HPCPERFSTATS_SYNC_TIMEDB_KNEE", "1")
  payload = build_screening_manifest(
      points=[{"threads": 64}],
      winner={"threads": 64},
      corpus_manifest_path=Path("manifest.json"),
      widths=(48, 64, 80, 96),
      replicates=5,
      python_abi="3.14t",
      run_id="kneeid",
  )
  assert payload["kind"] == "ingest_width_knee"
  out = write_screening_artifact(payload, repo_root=tmp_path)
  assert out.name == "knee_kneeid.json"


def test_reset_screening_state_wipes_foreign_hosts(tmp_path):
  from tests.sync_timedb_benchmark.screening_runner import reset_screening_state

  corpus = tmp_path / "corpus"
  host = corpus / "benchhost0000.cluster_name.domain.edu"
  host.mkdir(parents=True)
  (host / "1700000000").write_text("payload\n", encoding="utf-8")
  archive = tmp_path / "archive"
  foreign = archive / "other.cluster_name.domain.edu"
  foreign.mkdir(parents=True)
  (foreign / "1").write_text("stale\n", encoding="utf-8")
  planted = reset_screening_state(corpus_dir=corpus, archive_dir=archive)
  assert planted == ["benchhost0000.cluster_name.domain.edu"]
  assert not foreign.exists()
  assert (archive / planted[0] / "1700000000").is_file()


def test_screening_reset_ensures_daily_archive_parent(tmp_path):
  """Append path requires daily_archive parent; wipe must recreate it."""
  daily = tmp_path / "daily_archive"
  # Simulate a missing parent (volume path never created).
  assert not daily.exists()
  daily.mkdir(parents=True, exist_ok=True)
  assert daily.is_dir()


def test_write_screening_artifact_round_trip(tmp_path):
  payload = build_screening_manifest(
      points=[{"threads": 1}],
      winner={"threads": 1},
      corpus_manifest_path=Path("manifest.json"),
      widths=(1,),
      replicates=2,
      python_abi="3.14t",
      run_id="abc123",
  )
  out = write_screening_artifact(payload, repo_root=tmp_path)
  assert out.name == "screening_abc123.json"
  assert out.is_file()


def test_knobs_mode_defaults_and_winner(monkeypatch):
  from tests.sync_timedb_benchmark.screening_runner import (
      DEFAULT_KNOBS_REPLICATES,
      DEFAULT_KNOBS_WIDTH,
      KNOB_SWEEPS,
      build_knobs_manifest,
      knobs_fixed_width,
      knobs_mode_enabled,
      knob_getter_name,
      parse_replicates_env,
      select_knob_winner,
      summarize_knob_replicates,
      write_screening_artifact,
  )

  monkeypatch.setenv("HPCPERFSTATS_SYNC_TIMEDB_KNOBS", "1")
  monkeypatch.delenv("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_SYNC_TIMEDB_KNOBS_WIDTH", raising=False)
  assert knobs_mode_enabled()
  assert knobs_fixed_width() == DEFAULT_KNOBS_WIDTH
  assert parse_replicates_env() == DEFAULT_KNOBS_REPLICATES
  assert knob_getter_name("sync_day_close_max_inflight") == (
      "get_sync_day_close_max_inflight"
  )
  assert KNOB_SWEEPS[0][0] == "sync_day_close_max_inflight"
  winner = select_knob_winner(
      [
          summarize_knob_replicates(2, [1.0, 1.0]),
          summarize_knob_replicates(8, [1.02, 1.03]),
      ],
  )
  assert winner["value"] == 2


def test_write_knobs_artifact_prefix(tmp_path):
  from tests.sync_timedb_benchmark.screening_runner import (
      build_knobs_manifest,
      write_screening_artifact,
  )

  payload = build_knobs_manifest(
      factors=[{"factor": "sync_day_close_max_inflight", "winner": {"value": 8}}],
      ingest_width=48,
      replicates=3,
      python_abi="3.14t",
      run_id="knobsid",
  )
  assert payload["kind"] == "supporting_knobs"
  out = write_screening_artifact(payload, repo_root=tmp_path, prefix="knobs")
  assert out.name == "knobs_knobsid.json"


def test_workflow_knobs_flag_and_ambient_clear():
  text = (
      Path(__file__).resolve().parents[1]
      / "run_sync_timedb_benchmark_workflow.sh"
  ).read_text(encoding="utf-8")
  assert "--knobs" in text
  assert "HPCPERFSTATS_SYNC_TIMEDB_KNOBS=1" in text
  inner = (
      Path(__file__).resolve().parents[1]
      / "run_sync_timedb_benchmark_inner.sh"
  ).read_text(encoding="utf-8")
  assert "test_supporting_knobs.py" in inner
  assert "HPCPERFSTATS_SYNC_TIMEDB_KNOBS" in inner


def test_write_knobs_artifact_under_repo_test_runs():
  """Host harness proof for knobs_*.json gate (compose matrix deferred)."""
  from tests.sync_timedb_benchmark.screening_runner import (
      build_knobs_manifest,
      write_screening_artifact,
  )

  repo_root = Path(__file__).resolve().parents[2]
  payload = build_knobs_manifest(
      factors=[
          {
              "factor": "sync_day_close_max_inflight",
              "points": [{"value": 8, "mean_files_per_s": 0.05}],
              "winner": {"value": 8, "mean_files_per_s": 0.05},
              "note": "host unit harness proof; not a live matrix winner",
          },
      ],
      ingest_width=48,
      replicates=3,
      python_abi="host-unit",
      run_id="host_harness_proof",
  )
  out = write_screening_artifact(payload, repo_root=repo_root, prefix="knobs")
  assert out.is_file()
  assert out.name.startswith("knobs_")
  assert "test_runs/sync_timedb_bench" in str(out)

def test_e6_mode_defaults_retain_and_manifest(monkeypatch, tmp_path):
  from tests.sync_timedb_benchmark.screening_runner import (
      DEFAULT_E6_REPLICATES,
      DEFAULT_E6_WIDTH,
      build_e6_ab_manifest,
      e6_arm,
      e6_fixed_width,
      e6_mode_enabled,
      e6_retain_candidate,
      latest_e6_baseline_artifact,
      parse_replicates_env,
      write_screening_artifact,
  )

  monkeypatch.setenv("HPCPERFSTATS_SYNC_TIMEDB_E6", "1")
  monkeypatch.delenv("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_SYNC_TIMEDB_E6_WIDTH", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_E6_ARM", raising=False)
  assert e6_mode_enabled()
  assert e6_fixed_width() == DEFAULT_E6_WIDTH
  assert parse_replicates_env() == DEFAULT_E6_REPLICATES
  assert e6_arm() == "baseline"
  assert e6_arm("candidate") == "candidate"
  baseline = {"mean_files_per_s": 1.0, "lower_ci_files_per_s": 0.9}
  assert e6_retain_candidate(
      baseline=baseline,
      candidate={"mean_files_per_s": 1.2, "lower_ci_files_per_s": 1.05},
  )
  assert not e6_retain_candidate(
      baseline=baseline,
      candidate={"mean_files_per_s": 1.01, "lower_ci_files_per_s": 0.95},
  )
  payload = build_e6_ab_manifest(
      baseline=baseline,
      candidate={"mean_files_per_s": 1.2, "lower_ci_files_per_s": 1.05},
      ingest_width=48,
      replicates=5,
      python_abi="3.14t",
      retain=True,
      run_id="e6id",
  )
  assert payload["kind"] == "e6_parse_feed_ab"
  assert payload["retain"] is True
  out = write_screening_artifact(
      {"kind": "e6_arm_baseline", "baseline": baseline},
      repo_root=tmp_path,
      prefix="e6_arm_baseline",
  )
  assert latest_e6_baseline_artifact(tmp_path) == out


def test_e7_mode_defaults_retain_and_manifest(monkeypatch, tmp_path):
  from tests.sync_timedb_benchmark.screening_runner import (
      DEFAULT_E7_REPLICATES,
      DEFAULT_E7_WIDTH,
      build_e7_ab_manifest,
      e6_retain_candidate,
      e7_arm,
      e7_fixed_width,
      e7_mode_enabled,
      latest_e7_baseline_artifact,
      parse_replicates_env,
      write_screening_artifact,
  )

  monkeypatch.setenv("HPCPERFSTATS_SYNC_TIMEDB_E7", "1")
  monkeypatch.delenv("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_SYNC_TIMEDB_E7_WIDTH", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_E7_ARM", raising=False)
  assert e7_mode_enabled()
  assert e7_fixed_width() == DEFAULT_E7_WIDTH
  assert parse_replicates_env() == DEFAULT_E7_REPLICATES
  assert e7_arm() == "baseline"
  assert e7_arm("candidate") == "candidate"
  baseline = {"mean_files_per_s": 1.0, "lower_ci_files_per_s": 0.9}
  assert e6_retain_candidate(
      baseline=baseline,
      candidate={"mean_files_per_s": 1.2, "lower_ci_files_per_s": 1.05},
  )
  payload = build_e7_ab_manifest(
      baseline=baseline,
      candidate={"mean_files_per_s": 1.2, "lower_ci_files_per_s": 1.05},
      ingest_width=48,
      replicates=5,
      python_abi="3.14t",
      retain=True,
      run_id="e7id",
  )
  assert payload["kind"] == "e7_proc_build_ab"
  assert payload["retain"] is True
  out = write_screening_artifact(
      {"kind": "e7_arm_baseline", "baseline": baseline},
      repo_root=tmp_path,
      prefix="e7_arm_baseline",
  )
  assert latest_e7_baseline_artifact(tmp_path) == out


def test_workflow_e6_flag_and_inner_target():
  text = (
      Path(__file__).resolve().parents[1]
      / "run_sync_timedb_benchmark_workflow.sh"
  ).read_text(encoding="utf-8")
  assert "--e6" in text
  assert "HPCPERFSTATS_SYNC_TIMEDB_E6=1" in text
  inner = (
      Path(__file__).resolve().parents[1]
      / "run_sync_timedb_benchmark_inner.sh"
  ).read_text(encoding="utf-8")
  assert "test_e6_parse_feed_ab.py" in inner
  assert "HPCPERFSTATS_SYNC_TIMEDB_E6" in inner


def test_workflow_e7_flag_and_inner_target():
  text = (
      Path(__file__).resolve().parents[1]
      / "run_sync_timedb_benchmark_workflow.sh"
  ).read_text(encoding="utf-8")
  assert "--e7" in text
  assert "HPCPERFSTATS_SYNC_TIMEDB_E7=1" in text
  inner = (
      Path(__file__).resolve().parents[1]
      / "run_sync_timedb_benchmark_inner.sh"
  ).read_text(encoding="utf-8")
  assert "test_e7_proc_build_ab.py" in inner
  assert "HPCPERFSTATS_SYNC_TIMEDB_E7" in inner


def test_contention_mode_defaults_retain_and_manifest(monkeypatch, tmp_path):
  from tests.sync_timedb_benchmark.screening_runner import (
      CONTENTION_NO_REGRESSION_WAVES,
      CONTENTION_WAVES,
      DEFAULT_CONTENTION_REPLICATES,
      DEFAULT_CONTENTION_WIDTH,
      build_contention_ab_manifest,
      contention_arm,
      contention_fixed_width,
      contention_mode_enabled,
      contention_retain_candidate,
      contention_wave,
      latest_contention_baseline_artifact,
      parse_replicates_env,
      write_screening_artifact,
  )

  monkeypatch.setenv("HPCPERFSTATS_SYNC_TIMEDB_CONTENTION", "1")
  monkeypatch.setenv("HPCPERFSTATS_CONTENTION_WAVE", "caches")
  monkeypatch.delenv("HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_SYNC_TIMEDB_CONTENTION_WIDTH", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_CONTENTION_ARM", raising=False)
  assert contention_mode_enabled()
  assert contention_fixed_width() == DEFAULT_CONTENTION_WIDTH
  assert parse_replicates_env() == DEFAULT_CONTENTION_REPLICATES
  assert contention_arm() == "baseline"
  assert contention_wave() == "caches"
  assert "caches" in CONTENTION_WAVES
  assert "caches" in CONTENTION_NO_REGRESSION_WAVES
  baseline = {"mean_files_per_s": 1.0, "lower_ci_files_per_s": 1.0}
  assert contention_retain_candidate(
      baseline=baseline,
      candidate={"mean_files_per_s": 0.99, "lower_ci_files_per_s": 0.96},
      wave="caches",
  )
  assert not contention_retain_candidate(
      baseline=baseline,
      candidate={"mean_files_per_s": 0.9, "lower_ci_files_per_s": 0.94},
      wave="caches",
  )
  assert contention_retain_candidate(
      baseline=baseline,
      candidate={"mean_files_per_s": 1.2, "lower_ci_files_per_s": 1.05},
      wave="park_resume",
  )
  assert not contention_retain_candidate(
      baseline=baseline,
      candidate={"mean_files_per_s": 1.01, "lower_ci_files_per_s": 0.95},
      wave="park_resume",
  )
  payload = build_contention_ab_manifest(
      wave="caches",
      baseline=baseline,
      candidate={"mean_files_per_s": 0.99, "lower_ci_files_per_s": 0.96},
      ingest_width=48,
      replicates=5,
      python_abi="3.14t",
      retain=True,
      run_id="cid",
  )
  assert payload["kind"] == "contention_caches_ab"
  assert payload["gate"] == "no_regression"
  assert payload["retain"] is True
  out = write_screening_artifact(
      {"kind": "contention_caches_arm_baseline", "baseline": baseline},
      repo_root=tmp_path,
      prefix="contention_caches_arm_baseline",
  )
  assert latest_contention_baseline_artifact(tmp_path, "caches") == out


def test_workflow_contention_flag_and_inner_target():
  text = (
      Path(__file__).resolve().parents[1]
      / "run_sync_timedb_benchmark_workflow.sh"
  ).read_text(encoding="utf-8")
  assert "--contention" in text
  assert "HPCPERFSTATS_SYNC_TIMEDB_CONTENTION=1" in text
  assert "HPCPERFSTATS_CONTENTION_WAVE" in text
  inner = (
      Path(__file__).resolve().parents[1]
      / "run_sync_timedb_benchmark_inner.sh"
  ).read_text(encoding="utf-8")
  assert "test_contention_ab.py" in inner
  assert "HPCPERFSTATS_SYNC_TIMEDB_CONTENTION" in inner
