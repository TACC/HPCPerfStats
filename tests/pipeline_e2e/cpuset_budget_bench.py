"""Print absolute INI pool sizes from conf_parser for operator comparison."""
from __future__ import annotations

import hpcperfstats.dbload.lib.conf_parser as cfg


def main() -> None:
  """Print absolute pool sizes for operator comparison."""
  print({
      "sync_ingest_pool_processes": cfg.get_sync_ingest_pool_processes(),
      "metrics_pool_processes": cfg.get_metrics_pool_processes(),
      "gunicorn_workers": cfg.get_gunicorn_workers(),
      "listend_db_ingest_pool_processes": cfg.get_listend_db_ingest_pool_processes(),
  })


if __name__ == "__main__":
  main()
