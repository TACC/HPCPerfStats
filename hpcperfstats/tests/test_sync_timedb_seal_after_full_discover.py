"""Day-close does not seal until this boot's whole-archive discover finishes.

The seal can only see raw the manifest and the queues know about; it never
walks the disk (H19/H22/H24). Until the boot whole-archive walk has visited
every host, a day can still have never-ingested raw on disk that nothing
knows about, and sealing it means a 20-40 GB reseal when the walk finds it
(hpcperfstats04 2026-10-29: 09-22/09-28/09-29/10-06 resealed after the walk
reached c102-004/c102-005).
"""

import contextlib
import tarfile

import pytest

from hpcperfstats.dbload.lib import (
  sync_timedb_archive_helpers as ah,
  sync_timedb_job_reconstruct as jr,
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


@pytest.fixture(autouse=True)
def _clear_boot_flag():
  qo._BOOT_FULL_DISCOVER_PENDING.clear()
  yield
  qo._BOOT_FULL_DISCOVER_PENDING.clear()


def _seal_harness(tmp_path, monkeypatch):
  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / "2020-01-10.tar"
  raw_in = tmp_path / "h" / "1578657600"
  raw_in.parent.mkdir()
  raw_in.write_text("in tar")
  with tarfile.open(tar, "w") as tf:
    tf.add(str(raw_in), arcname=str(raw_in).lstrip("/"))
  ah.invalidate_mutable_tar_authority_members(str(tar))
  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
    qo, "_day_close_append_or_hot_active", lambda *a, **k: False
  )
  calls = []
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
    lambda *a, **k: calls.append(k["only_when_no_remaining_raw"]),
  )

  class _Coord:
    def __init__(self, **_kw):
      pass

    def phase(self, _t):
      return "verification_complete"

    def run_pre_seal_verify_sync(self, _t):
      return True

    def run_post_seal_verify_sync(self, _t):
      return False

    def apply_batch_delete(self, _t):
      return 0

    def has_closed_raw_on_disk(self, _t):
      return True

    def closed_raw_paths_on_disk(self, _t):
      return [str(raw_in)]

    def remaining_raw_paths_blocking_tar_drop(self, _t):
      return {str(tar) + ".zst": [str(raw_in)]}

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
    _Coord,
  )
  monkeypatch.setattr(
    qo,
    "reconcile_open_tar_with_sealed_zst",
    lambda *a, **k: None,
    raising=False,
  )
  logs = []

  def _claim():
    return qo._run_day_close_job(
      "2020-01-10",
      tgz_archive_dir=str(daily),
      archive_data_dir=str(tmp_path),
      log_fn=lambda msg, **_k: logs.append(msg),
    )

  return _claim, calls, logs


def test_seal_yields_while_boot_full_discover_is_pending(tmp_path, monkeypatch):
  """Flag set: no seal attempt, cooperative ``yielded``; cleared: seals."""
  claim, calls, logs = _seal_harness(tmp_path, monkeypatch)
  qo._BOOT_FULL_DISCOVER_PENDING.set()
  assert claim() == "yielded"
  assert calls == []
  assert any(
    "stage=seal result=yielded reason=full_discover_pending" in m for m in logs
  )
  qo._BOOT_FULL_DISCOVER_PENDING.clear()
  claim()
  assert calls == [False]


def _unarchived_harness(tmp_path, monkeypatch, found):
  claim, calls, logs = _seal_harness(tmp_path, monkeypatch)
  handed = []
  monkeypatch.setattr(qo, "_closed_raw_not_archived", lambda *a: found)
  monkeypatch.setattr(
    qo,
    "_handoff_retryable_paths_to_ingest",
    lambda _c, _t, paths, **k: handed.extend(paths) or len(paths),
  )
  return claim, calls, logs, handed


def test_seal_yields_and_requeues_raw_that_is_in_no_archive(
  tmp_path, monkeypatch
):
  """04 2026-10-29: 08-25 sealed while 189 of its files were never queued."""
  late = str(tmp_path / "h" / "1578657601")
  claim, calls, logs, handed = _unarchived_harness(
    tmp_path, monkeypatch, [late]
  )
  assert claim() == "yielded"
  assert calls == []
  assert handed == [late]
  assert any(
    "stage=seal result=yielded reason=unarchived_raw n=1" in m for m in logs
  )


def test_seal_yields_when_the_unarchived_check_fails(tmp_path, monkeypatch):
  """Fail closed: an unreadable tar or a failed day scan never seals."""
  claim, calls, logs, handed = _unarchived_harness(tmp_path, monkeypatch, None)
  assert claim() == "yielded"
  assert calls == []
  assert handed == []
  assert any("reason=unarchived_raw_check_failed" in m for m in logs)


def test_seal_runs_when_every_raw_file_is_archived(tmp_path, monkeypatch):
  claim, calls, _logs, handed = _unarchived_harness(tmp_path, monkeypatch, [])
  claim()
  assert calls == [False]
  assert handed == []


def _day_raw(tmp_path, monkeypatch, sealed=None):
  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / "2020-01-10.tar"
  host = tmp_path / "h"
  host.mkdir()
  in_tar, in_zst, nowhere = (host / n for n in ("1", "2", "3"))
  for raw in (in_tar, in_zst, nowhere):
    raw.write_text("raw")
  with tarfile.open(tar, "w") as tf:
    tf.add(str(in_tar), arcname=str(in_tar).lstrip("/"))
  ah.invalidate_mutable_tar_authority_members(str(tar))
  monkeypatch.setattr(
    ah,
    "build_day_scoped_closed_raw_by_gz",
    lambda *a, **k: {
      str(tar) + ".zst": [str(in_tar), str(in_zst), str(nowhere)]
    },
  )
  members = {str(in_zst).lstrip("/"): 3} if sealed is None else sealed
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_sealed_member_map.sealed_members_lookup",
    lambda *_a, **_k: members,
  )
  return str(daily), str(tar), str(nowhere)


def test_unarchived_raw_is_in_neither_the_open_tar_nor_the_sealed_copy(
  tmp_path, monkeypatch
):
  daily, tar, nowhere = _day_raw(tmp_path, monkeypatch)
  assert qo._closed_raw_not_archived(str(tmp_path), daily, tar) == [nowhere]


def test_unarchived_check_fails_closed_on_an_unreadable_open_tar(
  tmp_path, monkeypatch
):
  daily, tar, _nowhere = _day_raw(tmp_path, monkeypatch)
  with open(tar, "ab") as fh:
    fh.write(b"\0" * (qo._EMPTY_TAR_MAX_BYTES + 1))
  monkeypatch.setattr(
    ah, "get_mutable_tar_authority_member_map", lambda *_a, **_k: {}
  )
  assert qo._closed_raw_not_archived(str(tmp_path), daily, tar) is None


def _run_discover(tmp_path, monkeypatch, mtime_days, *, raise_exc=False):
  client = SyncTimedbJobStore("")
  jq.enqueue_list_job(
    client,
    kind=jq.JOB_KIND_DISCOVER,
    identity=qo.discover_job_identity(str(tmp_path), mtime_days),
  )

  def _walk(*_a, **_k):
    if raise_exc:
      raise RuntimeError("walk failed")
    return qo.jd.StreamingDiscoverStats(
      seen=0,
      enqueued_ingest=0,
      enqueued_append=0,
      skipped_complete=0,
      enqueued_day_close=0,
      stopped_at_capacity=False,
    )

  monkeypatch.setattr(qo, "_boot_stream_discover", _walk)
  qo._BOOT_FULL_DISCOVER_PENDING.set()
  with contextlib.suppress(RuntimeError):
    qo._run_background_discover(
      client,
      str(tmp_path),
      tgz_archive_dir=str(tmp_path / "daily"),
      log_fn=lambda *a, **k: None,
      mtime_days=1,
      startdate=None,
      enddate=None,
    )


def test_whole_archive_walk_clears_the_boot_flag(tmp_path, monkeypatch):
  _run_discover(tmp_path, monkeypatch, None)
  assert not qo._BOOT_FULL_DISCOVER_PENDING.is_set()


def test_failed_whole_archive_walk_still_clears_the_boot_flag(
  tmp_path, monkeypatch
):
  """INV-NEG terminal: a walk that raises must not hold every seal forever."""
  _run_discover(tmp_path, monkeypatch, None, raise_exc=True)
  assert not qo._BOOT_FULL_DISCOVER_PENDING.is_set()


def test_windowed_walk_keeps_the_boot_flag(tmp_path, monkeypatch):
  _run_discover(tmp_path, monkeypatch, 1)
  assert qo._BOOT_FULL_DISCOVER_PENDING.is_set()


def test_boot_sets_the_flag_with_the_whole_archive_identity():
  """Source contract: boot sets the flag where it queues the full walk."""
  import inspect

  src = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  loop = src[src.index("for boot_mtime_days in") :][:600]
  assert "_BOOT_FULL_DISCOVER_PENDING.set()" in loop


def test_forced_whole_archive_pass_clears_the_boot_flag(tmp_path, monkeypatch):
  """run_once force pass walks on its own thread; a full walk clears too."""
  client = SyncTimedbJobStore("")
  jq.enqueue_list_job(
    client,
    kind=jq.JOB_KIND_DISCOVER,
    identity=qo.discover_job_identity(str(tmp_path), None),
  )
  monkeypatch.setattr(
    qo,
    "_boot_stream_discover",
    lambda *a, **k: qo.jd.StreamingDiscoverStats(0, 0, 0, 0),
  )
  qo._BOOT_FULL_DISCOVER_PENDING.set()
  qo._idle_reconstruct_pass(
    client,
    str(tmp_path),
    tgz_archive_dir=str(tmp_path / "daily"),
    force=True,
    mtime_days=None,
  )
  assert not qo._BOOT_FULL_DISCOVER_PENDING.is_set()
