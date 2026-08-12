"""Legacy cpuset budget bench removed — absolute INI pool sizes.

Use conf_parser absolute getters instead of derive_pipeline_cpuset_priority_budget.
"""
from __future__ import annotations

import hpcperfstats.dbload.lib.conf_parser as cfg


def main() -> None:
  """Print absolute pool sizes for operator comparison."""
  print({
      "sync_ingest_pool_processes": cfg.get_sync_ingest_pool_processes(),
      "metrics_pool_processes": cfg.get_metrics_pool_processes(),
      "gunicorn_workers": cfg.get_gunicorn_workers(),
      "listend_db_ingest_pool_processes": cfg.get_listend_db_ingest_pool_processes(),
      "sync_write_lock_shards": cfg.get_sync_write_lock_shards(),
      "summary_aggregate_prefetch_max_threads": cfg.get_summary_aggregate_prefetch_max_threads(),
  })


if __name__ == "__main__":
  main()
