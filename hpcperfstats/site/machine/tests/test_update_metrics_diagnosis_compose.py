"""Docker Compose integration: seed mixed-scale jobs and run ``update_metrics_for_dates``.

Requires PostgreSQL on host ``db`` (``HPCPERFSTATS_COMPOSE_NETWORK=1``). See
``tests/run_update_metrics_diagnosis_workflow.sh``.

If ``HPCPERFSTATS_UM_DIAG_JSON_OUT`` is set, a copy of the diagnosis report is
also written to that path (e.g. under the repo mount in Docker).
"""
import json
import os
import threading
from pathlib import Path

import pytest
import psycopg
from django.db import connections

from hpcperfstats.analysis.metrics import update_metrics as um
from hpcperfstats.site.machine.models import job_data


def _compose_network():
  return os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


def _default_db_connect_kwargs():
  settings = connections["default"].settings_dict
  kwargs = {
      "dbname": settings.get("NAME") or "",
      "user": settings.get("USER") or "",
      "password": settings.get("PASSWORD") or "",
      "host": settings.get("HOST") or "",
  }
  port = settings.get("PORT")
  if port:
    kwargs["port"] = port
  return kwargs


@pytest.mark.django_db(transaction=True)
def test_update_metrics_diagnosis_compose_records_phases(monkeypatch, tmp_path):
  """Small (100–300) and large (300–5000) in-window host_data rows; capture phase totals."""
  if not _compose_network():
    pytest.skip(
        "Requires Docker Compose network (PostgreSQL at host 'db'). "
        "Run: tests/run_update_metrics_diagnosis_workflow.sh"
    )
  monkeypatch.setenv("HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS", "1")
  # Prewarm is validated elsewhere; this test gates readiness+metrics throughput signals.
  monkeypatch.setenv("HPCPERFSTATS_METRICS_SCHEDULER_SKIP_PREWARM", "1")

  from hpcperfstats.site.machine.tests.update_metrics_diagnosis_seed import (
      seed_update_metrics_diagnosis_jobs,
  )

  um.LAST_UPDATE_METRICS_DIAGNOSTICS = None
  meta = seed_update_metrics_diagnosis_jobs()
  # Defaults seed 100-300 / 300-5000 rows; env overrides may intentionally exceed.
  assert meta["n_rows_small"] > 0, meta["n_rows_small"]
  assert meta["n_rows_large"] > 0, meta["n_rows_large"]
  assert meta["n_rows_large"] >= meta["n_rows_small"], (meta["n_rows_small"], meta["n_rows_large"])

  from hpcperfstats.analysis.metrics.update_metrics import update_metrics_for_dates

  update_metrics_for_dates([meta["metrics_date"]], rerun=False)

  diag = um.LAST_UPDATE_METRICS_DIAGNOSTICS
  assert diag is not None, "LAST_UPDATE_METRICS_DIAGNOSTICS not set (env gate?)"
  assert diag["stats"]["processed"] >= 2
  totals = diag["phase_totals"]
  assert "candidate_sql_s" in totals
  assert "readiness_s" in totals
  assert "public_ef_artifacts_s" in totals
  assert "metrics_compute_s" in totals
  assert "prewarm_s" in totals
  assert totals["metrics_compute_s"] >= 0.0
  assert diag["jobs_per_min"] >= 0.0

  report = {
      "scale_axis": "in_window_host_data_rows_per_job",
      "n_rows_small": meta["n_rows_small"],
      "n_rows_large": meta["n_rows_large"],
      "phase_totals": totals,
      "stats": diag["stats"],
      "elapsed_s": diag["elapsed_s"],
      "jobs_per_min": diag["jobs_per_min"],
  }
  text = json.dumps(report, indent=2) + "\n"
  path = tmp_path / "update_metrics_diagnosis.json"
  path.write_text(text, encoding="utf-8")
  assert path.is_file()
  extra = os.environ.get("HPCPERFSTATS_UM_DIAG_JSON_OUT", "").strip()
  if extra:
    p = Path(extra)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.mark.django_db(transaction=True)
def test_update_metrics_compose_parent_persist_lock_timeout_surfaces_in_diagnostics(
    monkeypatch,
):
  """A real PostgreSQL row lock on job_data should surface as parent-persist failure, not a silent hang."""
  if not _compose_network():
    pytest.skip(
        "Requires Docker Compose network (PostgreSQL at host 'db'). "
        "Run: tests/run_update_metrics_diagnosis_workflow.sh"
    )
  monkeypatch.setenv("HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS", "1")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_SCHEDULER_SKIP_PREWARM", "1")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PERSIST_LOCK_TIMEOUT_MS", "1000")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PERSIST_STATEMENT_TIMEOUT_MS", "5000")
  monkeypatch.setattr(
      um,
      "refresh_public_expansion_factor_artifacts_parallel",
      lambda pool, **kwargs: {},
  )
  monkeypatch.setattr(um, "refresh_public_expansion_factor_artifacts_safe", lambda: None)

  from hpcperfstats.site.machine.tests.update_metrics_diagnosis_seed import (
      seed_update_metrics_diagnosis_jobs,
  )

  meta = seed_update_metrics_diagnosis_jobs()
  locked_jid = meta["jid_small"]

  def _fake_compute_metrics(self, job):
    return {
        "rows": [{
            "jid": job.jid,
            "type": "cpu",
            "metric": "avg_cpuusage",
            "units": "#cores",
            "value": 1.0,
            "no_data_reason": None,
        }],
        "distinct_time_count": 1,
    }

  monkeypatch.setattr(um.metrics.Metrics, "compute_metrics", _fake_compute_metrics)

  release_lock = threading.Event()
  lock_ready = threading.Event()
  lock_error = []

  def _hold_job_lock():
    try:
      with psycopg.connect(**_default_db_connect_kwargs()) as conn:
        conn.autocommit = False
        with conn.cursor() as cursor:
          cursor.execute(
              'SELECT jid FROM "{0}" WHERE jid = %s FOR UPDATE'.format(
                  job_data._meta.db_table
              ),
              [locked_jid],
          )
          lock_ready.set()
          release_lock.wait(timeout=30.0)
          conn.rollback()
    except Exception as exc:
      lock_error.append(exc)
      lock_ready.set()

  lock_thread = threading.Thread(target=_hold_job_lock, daemon=True)
  lock_thread.start()
  assert lock_ready.wait(timeout=10.0), "lock holder never acquired row lock"
  assert not lock_error, lock_error

  try:
    um.LAST_UPDATE_METRICS_DIAGNOSTICS = None
    um.update_metrics_for_dates([meta["metrics_date"]], rerun=False)
  finally:
    release_lock.set()
    lock_thread.join(timeout=10.0)

  diag = um.LAST_UPDATE_METRICS_DIAGNOSTICS
  assert diag is not None
  assert diag["stats"]["attempted_total"] >= 1
  assert diag["stats"]["parent_persist_failures_total"] >= 1
