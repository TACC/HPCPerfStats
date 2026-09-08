"""Peak Django PostgreSQL slot estimator vs compose max_connections."""

from __future__ import annotations

from hpcperfstats.dbload.lib import pg_slot_budget as budget


def test_estimate_django_pg_slot_peak_formula(monkeypatch):
  monkeypatch.setattr(budget, "get_gunicorn_workers", lambda: 32)
  monkeypatch.setattr(budget, "get_api_small_executor_max_workers", lambda: 4)
  monkeypatch.setattr(budget, "get_parallel_db_prefetch_max", lambda: 4)
  monkeypatch.setattr(budget, "get_listend_db_ingest_pool_processes", lambda: 32)
  monkeypatch.setattr(budget, "get_metrics_pool_processes", lambda: 24)
  monkeypatch.setattr(budget, "get_sync_ingest_pool_processes", lambda: 32)
  monkeypatch.setattr(budget, "get_sync_day_close_max_inflight", lambda: 4)
  monkeypatch.setattr(budget, "get_sync_archive_pool_processes", lambda: 2)

  est = budget.estimate_django_pg_slot_peak()
  web = 32 * 4
  expected = (
      web
      + 32
      + 24
      + 32
      + 4
      + 2
      + budget.RESERVE_SLOTS
  )
  assert est["components"]["web"] == web
  assert est["components"]["listend"] == 32
  assert est["components"]["metrics_pool"] == 24
  assert est["peak"] == expected
  assert est["max_connections"] == budget.COMPOSE_PG_MAX_CONNECTIONS
  assert est["warn_threshold"] == int(
      budget.COMPOSE_PG_MAX_CONNECTIONS * budget.WARN_FRACTION
  )


def test_estimate_django_pg_slot_peak_warns_at_eighty_percent(monkeypatch):
  monkeypatch.setattr(budget, "get_gunicorn_workers", lambda: 80)
  monkeypatch.setattr(budget, "get_api_small_executor_max_workers", lambda: 8)
  monkeypatch.setattr(budget, "get_parallel_db_prefetch_max", lambda: 8)
  monkeypatch.setattr(budget, "get_listend_db_ingest_pool_processes", lambda: 32)
  monkeypatch.setattr(budget, "get_metrics_pool_processes", lambda: 24)
  monkeypatch.setattr(budget, "get_sync_ingest_pool_processes", lambda: 32)
  monkeypatch.setattr(budget, "get_sync_day_close_max_inflight", lambda: 4)
  monkeypatch.setattr(budget, "get_sync_archive_pool_processes", lambda: 2)

  est = budget.estimate_django_pg_slot_peak()
  assert est["peak"] >= est["warn_threshold"]
  assert est["should_warn"] is True
  assert est["max_connections"] == 500


def test_log_pg_slot_budget_is_log_only_when_over_threshold(monkeypatch):
  lines = []
  monkeypatch.setattr(
      budget,
      "estimate_django_pg_slot_peak",
      lambda: {
          "peak": 450,
          "max_connections": 500,
          "warn_threshold": 400,
          "should_warn": True,
          "components": {},
      },
  )
  result = budget.log_pg_slot_budget_if_needed(log_fn=lines.append)
  assert result["should_warn"] is True
  assert any("WARN" in line and "450" in line for line in lines)
