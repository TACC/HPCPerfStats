"""Day-close reconcile merges but leaves sealing to the gated seal stage.

hpcperfstats02 2026-10-30: 09-11 was restored from its .tar.zst, got appends
(tar now a superset of the zst), and day-close ``reconcile_merge`` sealed all
322 GB at once while more appends for the day were queued; they soft-requeued
(``open_tar_members_unavailable``) for the whole compress and the day needed a
second full seal after they landed. The seal stage defers that seal while raw
is not in the tar; reconcile bypassed it.
"""

from __future__ import annotations

import inspect
import shutil
import tarfile

import pytest

import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
  atomic_seal_tar_to_zst,
  reconcile_open_tar_with_sealed_zst,
)

pytestmark = pytest.mark.skipif(
  not shutil.which("zstd"), reason="zstd not on PATH"
)


def _tar(path, tmp_path, members):
  with tarfile.open(path, "w") as tf:
    for name, text in members:
      src = tmp_path / f"src-{name.replace('/', '_')}"
      src.write_text(text)
      tf.add(str(src), arcname=name)


def _sealed_day(tmp_path, zst_members):
  zst = tmp_path / "2026-09-11.tar.zst"
  _tar(tmp_path / "zst_src.tar", tmp_path, zst_members)
  atomic_seal_tar_to_zst(
    str(tmp_path / "zst_src.tar"),
    str(zst),
    num_threads=1,
    compress_level=3,
    keep_uncompressed_tar=False,
    log_fn=None,
  )
  return zst


def _reconcile(tar_path, tmp_path, **kwargs):
  return reconcile_open_tar_with_sealed_zst(
    str(tar_path),
    zstd_threads=1,
    compress_level=3,
    force_remove_uncompressed_tar=False,
    log_fn=None,
    tgz_archive_dir=str(tmp_path),
    **kwargs,
  )


def test_restored_day_with_appends_is_not_sealed_by_reconcile(tmp_path):
  zst = _sealed_day(tmp_path, [("h/1", "aaa")])
  zst_before = zst.read_bytes()
  tar_path = tmp_path / "2026-09-11.tar"
  _tar(tar_path, tmp_path, [("h/1", "aaa"), ("h/2", "appended")])
  tar_before = tar_path.read_bytes()
  result = _reconcile(tar_path, tmp_path, defer_seal=True)
  assert result.success, result.reason
  assert result.action == "seal_from_tar"
  assert result.sealed is False
  assert zst.read_bytes() == zst_before
  assert tar_path.read_bytes() == tar_before


def test_divergent_day_is_merged_but_not_sealed(tmp_path):
  zst = _sealed_day(tmp_path, [("h/1", "aaa"), ("h/3", "zst-only")])
  zst_before = zst.read_bytes()
  tar_path = tmp_path / "2026-09-11.tar"
  _tar(tar_path, tmp_path, [("h/1", "aaa"), ("h/2", "appended")])
  result = _reconcile(tar_path, tmp_path, defer_seal=True)
  assert result.success, result.reason
  assert result.action == "merge_and_seal"
  assert result.sealed is False
  assert zst.read_bytes() == zst_before
  merged = helpers.get_existing_archive_members(str(tar_path))
  assert set(merged) == {"h/1", "h/2", "h/3"}


def test_unreadable_zst_still_resealed_by_reconcile(tmp_path):
  """DE-9 repair keeps sealing in reconcile."""
  zst = tmp_path / "2026-09-11.tar.zst"
  zst.write_bytes(b"not a zstd frame")
  tar_path = tmp_path / "2026-09-11.tar"
  _tar(tar_path, tmp_path, [("h/1", "aaa")])
  result = _reconcile(tar_path, tmp_path, defer_seal=True)
  assert result.success, result.reason
  assert result.reason == "zst_unreadable_reseal"
  assert result.sealed is True


def test_default_reconcile_still_seals_superset(tmp_path):
  zst = _sealed_day(tmp_path, [("h/1", "aaa")])
  zst_before = zst.read_bytes()
  tar_path = tmp_path / "2026-09-11.tar"
  _tar(tar_path, tmp_path, [("h/1", "aaa"), ("h/2", "appended")])
  result = _reconcile(tar_path, tmp_path)
  assert result.sealed is True
  assert zst.read_bytes() != zst_before


def test_day_close_reconcile_stage_defers_the_seal():
  src = inspect.getsource(qo._run_day_close_job)
  call = src[src.index("reconcile_open_tar_with_sealed_zst(") :]
  call = call[: call.index(")\n")]
  assert "defer_seal=True" in call
