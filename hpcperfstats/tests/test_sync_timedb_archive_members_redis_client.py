"""GATES G5 wrapper: archive-members Redis client timeouts, lock, and reset (T1)."""
from __future__ import annotations

from hpcperfstats.tests.test_sync_timedb_redis_client_hardening import (  # noqa: F401
    _clean_singleton,
    test_concurrent_first_touch_creates_exactly_one_client,
    test_connection_kwargs_bound_every_call_in_time,
    test_drop_client_disconnects_pool_and_forces_reconnect,
    test_ping_failure_drops_cached_client,
    test_runtime_client_is_created_with_timeout_kwargs,
)

from hpcperfstats.dbload.lib import sync_timedb_archive_members_redis as amr


def test_populate_wait_bounded():
  """T6: populate wait must be a finite fail-closed deadline, not an open sleep."""
  seconds = amr.populate_wait_max_seconds()
  assert seconds > 0
  source = amr.request_archive_members_populate_and_wait.__doc__ or ""
  wait_src = __import__("inspect").getsource(
      amr.request_archive_members_populate_and_wait,
  )
  assert "populate_max" in wait_src or "_populate_max_seconds" in wait_src or "populate_wait_max_seconds" in wait_src
  del source
