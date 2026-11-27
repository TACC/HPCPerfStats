"""The kit's bin/ and lib/ must not be swallowed by the checkout-root bin/ and lib/ ignores."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]

KIT_SOURCES = [
  "bin/measure_query_latency.py",
  "bin/emit_paper_facts.py",
  "bin/summarize_ticket_time.py",
  "bin/capture_pipeline_scale.sh",
  "bin/capture_storage_ha.sh",
  "bin/common.sh",
  "bin/run_local_checks.sh",
  "lib/query_latency.py",
  "lib/paper_facts.py",
]


def _check_ignore(rel: str) -> int:
  return subprocess.run(
    ["git", "check-ignore", "-q", "--no-index", rel],
    cwd=KIT,
    check=False,
    capture_output=True,
  ).returncode


@pytest.fixture(autouse=True)
def _need_git_checkout() -> None:
  if shutil.which("git") is None:
    pytest.skip("git not installed")
  inside = subprocess.run(
    ["git", "rev-parse", "--is-inside-work-tree"],
    cwd=KIT,
    check=False,
    capture_output=True,
  )
  if inside.returncode != 0:
    pytest.skip("not a git checkout")


@pytest.mark.parametrize("rel", KIT_SOURCES)
def test_kit_source_not_gitignored(rel: str) -> None:
  assert (KIT / rel).is_file(), f"README/RUNBOOK reference missing file {rel}"
  assert _check_ignore(rel) == 1, (
    f"{rel} is gitignored; it would never be committed"
  )


@pytest.mark.parametrize(
  "rel", ["bin/__pycache__/x.pyc", "lib/__pycache__/x.pyc", "results/x.json"]
)
def test_kit_generated_output_stays_ignored(rel: str) -> None:
  assert _check_ignore(rel) == 0, f"{rel} should stay gitignored"
