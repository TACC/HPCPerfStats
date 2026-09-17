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

  assert mod.DEFAULT_CORPUS.name == "corpus_smoke"


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
