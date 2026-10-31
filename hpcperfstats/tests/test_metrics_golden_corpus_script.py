"""Golden-corpus script helpers that must work inside the dev container."""

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
