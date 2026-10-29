"""A failed open-tar membership read must not look like an empty tar.

hpcperfstats04 2026-10-29: while a 42 GB seal held the 10-07 tar, the append
membership ``tvf`` timed out on its read lock. ``get_mutable_tar_authority_
member_map`` returned ``{}`` and cached it under the tar's identity, so the
append treated every file as new (``to_add=2``), waited 60 s on the seal's
write lock and failed with ``TimeoutError``; the same job after the seal had
``to_add=0``.
"""

import tarfile

import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as ah
import hpcperfstats.dbload.sync_timedb as st
from hpcperfstats.dbload.lib import (
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
)
from hpcperfstats.dbload.lib.archive_compress import (
  daily_tar_path_from_compressed,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


def _tar_with_member(tmp_path, day="2024-03-05", pad=0):
  raw = tmp_path / "1709123456"
  raw.write_text("1709123456 job1 cn001\n" + "x" * pad)
  archive_key = str(tmp_path / f"{day}.tar.zst")
  tar_path = daily_tar_path_from_compressed(archive_key)
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(raw), arcname=ah.get_tar_member_name(str(raw)))
  return raw, archive_key, tar_path


def test_lock_timeout_is_not_cached_as_an_empty_member_map(
  tmp_path, monkeypatch
):
  ah.clear_mutable_tar_authority_members_cache()
  raw, _key, tar_path = _tar_with_member(tmp_path)
  real = ah.file_read_lock_wait
  calls = {"n": 0}

  def _lock(path, *a, **k):
    calls["n"] += 1
    if calls["n"] == 1:
      raise TimeoutError("Timed out waiting 60.0s for read lock")
    return real(path, *a, **k)

  monkeypatch.setattr(ah, "file_read_lock_wait", _lock)
  assert ah.get_mutable_tar_authority_member_map(tar_path) == {}
  members = ah.get_mutable_tar_authority_member_map(tar_path)
  assert members.get(ah.get_tar_member_name(str(raw))) == raw.stat().st_size


def test_append_requeues_when_open_tar_scan_returns_no_members(
  tmp_path, monkeypatch
):
  """A tar past one tar record with no members is a failed read: requeue."""
  ah.clear_mutable_tar_authority_members_cache()
  _raw, archive_key, tar_path = _tar_with_member(tmp_path, pad=20000)
  new_raw = tmp_path / "1709123457"
  new_raw.write_text("1709123457 job2 cn002\n")
  logs = []
  appends = []
  monkeypatch.setattr(st, "log_print", lambda msg, **kw: logs.append(str(msg)))
  monkeypatch.setattr(
    st, "filter_paths_head_ingested", lambda paths, **_: (list(paths), [])
  )
  monkeypatch.setattr(
    ah, "get_mutable_tar_authority_member_map", lambda *_a, **_k: {}
  )
  monkeypatch.setattr(st, "_append_to_tar", lambda *a, **k: appends.append(a))
  outcome = st._archive_stats_files_body((archive_key, [str(new_raw)]))
  assert appends == []
  assert getattr(outcome, "soft_requeue", False)
  assert any(
    "archive_job soft_skip" in m and "reason=open_tar_members_unavailable" in m
    for m in logs
  )
  assert tar_path


def test_append_still_runs_for_a_genuinely_empty_tar(tmp_path, monkeypatch):
  """An empty open tar (one record) has no members and must still append."""
  ah.clear_mutable_tar_authority_members_cache()
  archive_key = str(tmp_path / "2024-03-06.tar.zst")
  tar_path = daily_tar_path_from_compressed(archive_key)
  with tarfile.open(tar_path, "w"):
    pass
  new_raw = tmp_path / "1709209857"
  new_raw.write_text("1709209857 job2 cn002\n")
  appends = []
  monkeypatch.setattr(st, "log_print", lambda *a, **k: None)
  monkeypatch.setattr(
    st, "filter_paths_head_ingested", lambda paths, **_: (list(paths), [])
  )
  monkeypatch.setattr(st, "verify_tar_archive_readable", lambda *_a, **_k: True)
  monkeypatch.setattr(st, "_append_to_tar", lambda *a, **k: appends.append(a))
  st._archive_stats_files_body((archive_key, [str(new_raw)]))
  assert len(appends) == 1


def test_append_soft_requeue_is_bounded(tmp_path, monkeypatch):
  """INV-NEG: repeated soft_requeue escalates past the no-progress cap."""
  from hpcperfstats.dbload.sync_timedb import ArchiveAppendOutcome

  monkeypatch.setattr(qo.cfg, "get_sync_noprogress_escalate_after", lambda: 2)
  dead = []
  monkeypatch.setattr(
    qo, "_dead_letter_claim", lambda *a, **k: dead.append(k["claim"].identity)
  )
  outcome = ArchiveAppendOutcome(
    ok=False, soft_requeue=True, skip_finalize_invalidate=True
  )

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      return outcome

  client = SyncTimedbJobStore("")
  ident = str(tmp_path / "h" / "1709123457")
  jq.enqueue_list_job(client, kind=jq.JOB_KIND_APPEND, identity=ident)
  for _ in range(2):
    claim = jq.claim_list_job(
      client,
      kind=jq.JOB_KIND_APPEND,
      owner_token=jq.make_lease_owner_token(),
    )
    qo._drain_append_ready(
      client,
      inflight={"/d/2024-03-05.tar": _Ready()},
      claims={"/d/2024-03-05.tar": claim},
      tgz_archive_dir="/d",
      archive_data_dir=str(tmp_path),
    )
  assert dead == [ident]
