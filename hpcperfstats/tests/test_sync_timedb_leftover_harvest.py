"""Absence tests for deleted leftovers (incl. the retired file-state machine)."""

from __future__ import annotations

import inspect

import hpcperfstats.dbload.sync_timedb as st
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo


def test_leftover_unused_defs_are_absent():
  """Deleted leftovers must not remain as product defs (cov-ex-6)."""
  st_src = inspect.getsource(st)
  qo_src = inspect.getsource(qo)
  for name in (
    "_handle_ingest_worker_memory_after_imap",
    "_archive_task_succeeded",
    "_ingest_remaining_count",
  ):
    assert f"def {name}" not in st_src
    assert not hasattr(st, name)
  assert "def _renew_active_claims" not in qo_src
  assert not hasattr(qo, "_renew_active_claims")
  # A10 (dead-end plan): the never-called file-state machine is gone.
  for name in (
    "SyncFileState",
    "_SYNC_STATE_TRANSITIONS",
    "_transition_file_state",
  ):
    assert not hasattr(st, name)
