"""Golden-corpus script helpers that must work inside the dev container."""

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = (
  Path(__file__).resolve().parents[2] / "scripts" / "metrics_golden_corpus.py"
)


@pytest.fixture(scope="module")
def corpus_mod():
  spec = importlib.util.spec_from_file_location(
    "metrics_golden_corpus_t", _SCRIPT
  )
  mod = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = mod
  spec.loader.exec_module(mod)
  return mod


def test_pandas_path_commit_prefers_env(monkeypatch, corpus_mod):
  monkeypatch.setenv("HPCPERFSTATS_GOLDEN_COMMIT", "deadbeef")
  assert corpus_mod.pandas_path_commit() == "deadbeef"


def test_pandas_path_commit_without_git_binary_is_unknown(
  monkeypatch, corpus_mod
):
  # Regression: the dev container has no ``git``; capture used to crash with
  # FileNotFoundError before writing any fixture.
  monkeypatch.delenv("HPCPERFSTATS_GOLDEN_COMMIT", raising=False)

  def _raise(*_a, **_k):
    raise FileNotFoundError(2, "No such file or directory", "git")

  monkeypatch.setattr(corpus_mod.subprocess, "run", _raise)
  assert corpus_mod.pandas_path_commit() == "unknown"


def test_pandas_path_commit_uses_git_output(monkeypatch, corpus_mod):
  monkeypatch.delenv("HPCPERFSTATS_GOLDEN_COMMIT", raising=False)
  monkeypatch.setattr(
    corpus_mod.subprocess,
    "run",
    lambda *_a, **_k: subprocess.CompletedProcess(
      _a, 0, stdout="abc\n", stderr=""
    ),
  )
  assert corpus_mod.pandas_path_commit() == "abc"


def _float64_capture(tmp_path, corpus_mod, values):
  """Save a one-row frame with float64 value columns, as old captures do."""
  import numpy as np

  from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame

  f = JobFrame.from_records(
    "1", [(1_000_000, "h1", "cpu", "user", None, 1, 1.0, 1.0, 1.0)]
  )
  path = tmp_path / "f.npz"
  corpus_mod._save_frame_npz(path, f)
  data = dict(np.load(path, allow_pickle=True))
  for name in ("value", "arc", "delta"):
    data[name] = np.asarray(values, dtype=np.float64)
  np.savez_compressed(path, **data)
  return path


def test_load_frame_npz_loads_float64_and_float32_captures_as_float64(
  tmp_path, corpus_mod
):
  """Regression (float32 frames, 2026-11-21): ``check_contract`` rejected
  every capture whose value dtype differed from ``VALUE_DTYPE``. Frames are
  float64 again (host_data double precision): float64 captures load
  unchanged and float32 captures widen exactly."""
  import numpy as np

  from hpcperfstats.analysis.metrics.lib.columnar.jobframe import VALUE_DTYPE

  frame = corpus_mod.load_frame_npz(
    _float64_capture(tmp_path, corpus_mod, [0.1])
  )
  frame.check_contract()
  assert frame.value.dtype == VALUE_DTYPE == np.float64
  assert frame.value.tolist() == [0.1]
  path = tmp_path / "f.npz"
  data = dict(np.load(path, allow_pickle=True))
  for name in ("value", "arc", "delta"):
    data[name] = np.asarray([0.1], dtype=np.float32)
  np.savez_compressed(path, **data)
  frame = corpus_mod.load_frame_npz(path)
  frame.check_contract()
  assert frame.value.tolist() == [float(np.float32(0.1))]


def _seed_args(tmp_path, ignore_log):
  return argparse.Namespace(
    raw_root=str(tmp_path / "raw"),
    max_bytes=10,
    min_samples=3,
    limit=0,
    ignore_log=ignore_log,
    dir=str(tmp_path),
  )


def test_seed_exemplars_ignore_log_reingests_after_db_reset(
  tmp_path, monkeypatch, corpus_mod
):
  """Regression (A/B gate, 2026-11-21): the dev database was emptied but
  ``seeded_raw_files.txt`` still listed every exemplar, so
  ``seed-exemplars`` found zero candidates and the corpus could not be
  rebuilt. ``--ignore-log`` empties the log and ingests again."""
  raw = tmp_path / "raw" / "c1.host" / "1700000000"
  raw.parent.mkdir(parents=True)
  raw.write_text("x")
  (tmp_path / corpus_mod.SEEDED_FILES_LOG).write_text(f"{raw}\n")
  monkeypatch.setattr(
    corpus_mod, "iter_raw_stats_files", lambda _root, _max: [raw]
  )
  monkeypatch.setattr(corpus_mod, "raw_file_job_ids", lambda *_a: {"1": 3})

  class _Ingest(Exception):
    pass

  def _stop():
    raise _Ingest

  monkeypatch.setattr(corpus_mod, "_ensure_django", _stop)
  assert corpus_mod.cmd_seed_exemplars(_seed_args(tmp_path, False)) == 0
  with pytest.raises(_Ingest):
    corpus_mod.cmd_seed_exemplars(_seed_args(tmp_path, True))
  assert (tmp_path / corpus_mod.SEEDED_FILES_LOG).read_text() == ""


def _reseed_args(tmp_path):
  return argparse.Namespace(
    raw_root="/corpus/raw",
    max_bytes=10,
    min_samples=3,
    username="corpus",
    work_dir=str(tmp_path / "edge"),
    dir=str(tmp_path),
  )


def test_reseed_dev_db_runs_steps_in_order_and_checks_coverage(
  tmp_path, monkeypatch, corpus_mod
):
  calls = []
  for name in (
    "cmd_seed_exemplars",
    "cmd_rewrite_host_suffix",
    "cmd_seed_job_data",
    "cmd_seed_edge_jobs",
  ):
    monkeypatch.setattr(
      corpus_mod,
      name,
      lambda ns, name=name: calls.append((name, vars(ns))) or 0,
    )
  gaps = []
  monkeypatch.setattr(
    corpus_mod, "manifest_jobs_missing_from_db", lambda _d: gaps
  )
  assert corpus_mod.cmd_reseed_dev_db(_reseed_args(tmp_path)) == 0
  assert [c[0] for c in calls] == [
    "cmd_seed_exemplars",
    "cmd_rewrite_host_suffix",
    "cmd_seed_job_data",
    "cmd_seed_edge_jobs",
  ]
  assert calls[0][1]["ignore_log"] is True
  assert all(c[1]["username"] == "corpus" for c in calls)
  gaps.append("3339983:host_data")
  assert corpus_mod.cmd_reseed_dev_db(_reseed_args(tmp_path)) == 1


def test_reseed_dev_db_stops_at_first_failing_step(
  tmp_path, monkeypatch, corpus_mod
):
  calls = []
  monkeypatch.setattr(corpus_mod, "cmd_seed_exemplars", lambda _ns: 1)
  monkeypatch.setattr(
    corpus_mod, "cmd_rewrite_host_suffix", lambda _ns: calls.append(1) or 0
  )
  assert corpus_mod.cmd_reseed_dev_db(_reseed_args(tmp_path)) == 1
  assert calls == []


def test_reseed_dev_db_parser_wiring(corpus_mod):
  args = corpus_mod.build_parser().parse_args(
    ["reseed-dev-db", "--raw-root", "/corpus/raw"]
  )
  assert args.func is corpus_mod.cmd_reseed_dev_db
  assert args.username == "corpus"
  seed = corpus_mod.build_parser().parse_args(
    ["seed-exemplars", "--raw-root", "/r", "--ignore-log"]
  )
  assert seed.ignore_log is True
