"""Architecture contracts for the in-process update_metrics worker model."""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
METRICS_SOURCE = REPO_ROOT / "hpcperfstats/analysis/metrics/lib/metrics.py"
SCHEDULER_SOURCE = REPO_ROOT / "hpcperfstats/analysis/metrics/update_metrics.py"
PUBLIC_EF_SOURCE = (
  REPO_ROOT / "hpcperfstats/site/lib/machine/public_metrics_artifacts.py"
)
CONF_SOURCE = REPO_ROOT / "hpcperfstats/dbload/lib/conf_parser.py"
INI_EXAMPLE_SOURCE = REPO_ROOT / "hpcperfstats.ini.example"


@pytest.mark.machine_unit_mock
def test_update_metrics_production_path_has_no_process_pool_contract() -> None:
  """Lock the hard cutover to titled in-process workers."""
  sources = {
    path: path.read_text(encoding="utf-8")
    for path in (METRICS_SOURCE, SCHEDULER_SOURCE, PUBLIC_EF_SOURCE)
  }

  forbidden = (
    "multiprocessing.Pool(",
    "apply_pool_worker_process_title",
    "abort_if_metrics_pool_workers_dead",
    "reap_metrics_main_zombie_children",
    "SIGALRM",
    "setitimer",
    "metrics_pool_maxtasksperchild",
    "_persist_metrics_payload_bounded",
    "[worker:metrics-pool]",
    "[worker:public-ef-pool]",
  )
  failures = [
    f"{path.relative_to(REPO_ROOT)} still contains {token!r}"
    for path, source in sources.items()
    for token in forbidden
    if token in source
  ]
  assert failures == []

  scheduler = sources[SCHEDULER_SOURCE]
  prewarm_worker = scheduler.split(
      "def _prewarm_jid_on_metrics_pool", 1
  )[1].split("class _CompletionReporter", 1)[0]
  assert "connections.close_all()" not in prewarm_worker


@pytest.mark.machine_unit_mock
def test_removed_process_only_metrics_options_are_absent_from_config() -> None:
  """Keep process recycle and process-wide timer options deleted."""
  sources = {
    path: path.read_text(encoding="utf-8")
    for path in (CONF_SOURCE, INI_EXAMPLE_SOURCE)
  }
  for token in ("metrics_pool_maxtasksperchild", "metrics_run_per_job_timeout_s"):
    failures = [
      str(path.relative_to(REPO_ROOT))
      for path, source in sources.items()
      if token in source
    ]
    assert failures == [], f"{token} remains in {failures}"

