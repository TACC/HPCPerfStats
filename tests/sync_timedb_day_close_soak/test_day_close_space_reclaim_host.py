"""Host soak-style assertions for day-close open_tar / dual reclaim (no prod)."""

from __future__ import annotations

import os

import pytest


@pytest.mark.parametrize(
    "test_id",
    [
        "post_seal_false",
        "phase_done_dual",
        "reseal_after_delete",
        "vnd_reopen",
    ],
)
def test_day_close_space_reclaim_oracle_ids_present(test_id):
  """Registry gate: reclaim scenarios stay named for soak workflow wiring."""
  from hpcperfstats.tests import test_sync_timedb_queue_orchestrator as qo_tests
  from hpcperfstats.tests import test_sync_timedb_day_raw_removal as raw_tests

  mapping = {
      "post_seal_false": qo_tests.test_day_close_skips_tar_drop_when_post_seal_returns_false,
      "phase_done_dual": qo_tests.test_day_close_phase_done_dual_reclaims_open_tar,
      "reseal_after_delete": qo_tests.test_day_close_reseals_after_raw_delete_when_zst_missing,
      "vnd_reopen": raw_tests.test_apply_batch_delete_reopens_phase_done_verified_pending,
  }
  assert callable(mapping[test_id])
  assert os.path.isfile(mapping[test_id].__code__.co_filename)
