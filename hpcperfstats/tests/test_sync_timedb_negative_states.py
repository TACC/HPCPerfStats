"""Unit tests for sync_timedb_negative_states (environment class, G2 table)."""

from __future__ import annotations

import errno

from hpcperfstats.dbload.lib import sync_timedb_negative_states as ns


def test_environment_error_follows_chained_cause():
  try:
    try:
      raise BlockingIOError(errno.EAGAIN, "again")
    except BlockingIOError as inner:
      raise RuntimeError("wrapper") from inner
  except RuntimeError as exc:
    assert ns.is_environment_error(exc)
  assert not ns.is_environment_error(None)
  assert not ns.is_environment_error(RuntimeError("plain"))


def test_stderr_markers_and_tags():
  assert ns.stderr_reports_environment_error("No space left on device")
  assert not ns.stderr_reports_environment_error("Unexpected EOF in archive")
  assert ns.environment_error_tag(PermissionError(13, "x")) == "env:EACCES"
  assert ns.environment_error_tag(RuntimeError("x")) == "env:RuntimeError"


def test_negative_state_exit_default_and_known():
  assert ns.negative_state_exit("day_close_outcome", "error") == (
    ns.DEFAULT_NEGATIVE_EXIT
  )
  assert ns.negative_state_exit("day_removal_status", "skipped_quarantine") == (
    "terminal_quarantine"
  )


def test_write_failed_outcome_is_environment_requeue():
  """DE-22: a failed DB chunk write retries without burning file attempts."""
  import inspect

  from hpcperfstats.dbload import sync_timedb as st

  assert ns.negative_state_exit("ingest_outcome", "write_failed") == (
    "env_requeue"
  )
  src = inspect.getsource(st)
  assert src.count('outcome="ingested" if ingest_ok else "write_failed"') == 2
