"""Contract: populate-pool threads must not retitle the process with setproctitle."""
from __future__ import annotations

import inspect

from hpcperfstats.dbload.lib import sync_timedb_populate_pool as pop


def test_populate_pool_worker_entry_no_process_title():
  """Populate threads must not call process-title / setproctitle init."""
  src = inspect.getsource(pop._populate_pool_worker_entry)
  assert "apply_ingest_pool_worker_init" not in src
  assert "apply_pool_worker_process_title" not in src
  assert "setproctitle" not in src
  assert "set_worker_pool_kind" in src
  assert "set_worker_diagnostics_registry" in src
