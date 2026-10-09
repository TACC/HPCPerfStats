"""Regression: archive populate/tvf/remaining_raw RSS buffer collapses (OOM Sep 26)."""

from __future__ import annotations

import inspect
import tarfile

from hpcperfstats.dbload.lib import (
  sync_timedb_archive_helpers as helpers,
  sync_timedb_archive_members_coord as coord,
  sync_timedb_day_raw_removal as drm,
)


def test_stream_compressed_archive_members_source_has_no_occurrences():
  src = inspect.getsource(helpers._stream_compressed_archive_members)
  assert "occurrences" not in src
  assert "defaultdict" not in src


def test_read_tar_member_sizes_keeps_largest_duplicate(tmp_path):
  """Duplicate member names keep the largest size (tvf/max path)."""
  tar_path = tmp_path / "2026-01-01.tar"
  a = tmp_path / "a.bin"
  b = tmp_path / "b.bin"
  a.write_bytes(b"aa")
  b.write_bytes(b"bbbb")
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(a), arcname="dup")
    tf.add(str(b), arcname="dup")
  sizes = helpers._read_tar_file_member_sizes_unlocked(str(tar_path))
  assert sizes["dup"] == 4
  occ, rc, _stderr = helpers._run_gnu_tvf_file_members(str(tar_path))
  assert rc == 0
  assert len(occ) == 2


def test_run_gnu_tvf_streams_without_capture_output():
  src = inspect.getsource(helpers._run_gnu_tvf_file_members)
  assert "capture_output" not in src
  assert "Popen" in src


def test_populate_finish_does_not_triplicate_dict_running_max():
  src = inspect.getsource(coord.populate_archive_members)
  assert "members=dict(running_max)" not in src
  assert src.count("dict(running_max)") == 0


def test_day_raw_single_memo_form_no_flat_sidecar():
  """Pass memo keeps the map only; flat list is derived without a second store."""
  src_init = inspect.getsource(drm._DayRawRemovalState.__init__)
  src_paths = inspect.getsource(
    drm._DayRawRemovalState._closed_raw_paths_on_disk
  )
  src_clear = inspect.getsource(
    drm._DayRawRemovalState._clear_closed_raw_pass_memo
  )
  assert "_closed_raw_paths_pass_memo" not in src_init
  assert "_closed_raw_paths_pass_memo" not in src_paths
  assert "_closed_raw_paths_pass_memo" not in src_clear


def test_mutable_invalidate_helper_exists_for_append_merge():
  """Invalidate stays for non-append mutations; success merge updates in place."""
  import hpcperfstats.dbload.sync_timedb as st

  assert hasattr(helpers, "invalidate_mutable_tar_authority_members")
  assert hasattr(helpers, "update_mutable_tar_authority_after_append")
  body = inspect.getsource(st._archive_stats_files_body)
  assert "update_mutable_tar_authority_after_append(" in body
  assert "invalidate_mutable_tar_authority_members(" not in body
