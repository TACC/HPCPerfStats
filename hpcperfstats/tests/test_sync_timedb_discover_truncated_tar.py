"""A truncated open daily tar must not abort the whole-archive discover walk.

hpcperfstats01 2026-11-11: ``2026-10-14.tar`` was cut short (GNU ``tar tvf``:
``Unexpected EOF in archive``, rc=2). Discover's per-file append probe
(``discover_raw_needs_tar_append``) let the ``RuntimeError`` from
``get_mutable_tar_authority_member_map`` escape, so every oldest-first
whole-archive walk died at the first 10-14 file (``queue_orchestrator discover
failed window=all``), re-ran ~10 min later, and died again: no file of 10-14 or
any later day was ever discovered by a whole walk.

Discover now treats an unreadable open tar as "needs append" (at-least-once):
the append worker runs ``ensure_open_daily_tar_readable`` (salvage, repair,
sealed restore) before it writes. The failure is remembered per tar identity
(size + mtime), so the other files of that day do not each re-list the tar;
once the tar changes (repaired, appended) discover reads it again.
"""

from __future__ import annotations

import os
import tarfile
from datetime import datetime

from hpcperfstats.dbload.lib import (
  sync_timedb_archive_helpers as helpers,
  sync_timedb_job_reconstruct as jr,
)

DAY = "2026-10-14"


def _raw(tmp_path, name: str, *, size: int = 64) -> str:
  """Write one closed raw stats file whose filename epoch falls on DAY."""
  host = tmp_path / "archive" / "c001.example"
  host.mkdir(parents=True, exist_ok=True)
  epoch = int(datetime.fromisoformat(f"{DAY}T{name}").timestamp())
  raw = host / str(epoch)
  raw.write_bytes(b"x" * size)
  return str(raw)


def _truncated_tar(tmp_path) -> str:
  """Create an open daily tar whose last member's data is cut short."""
  daily = tmp_path / "daily"
  daily.mkdir()
  src = tmp_path / "member"
  src.write_bytes(b"y" * 20000)
  tar_path = str(daily / f"{DAY}.tar")
  with tarfile.open(tar_path, "w", format=tarfile.GNU_FORMAT) as tf:
    tf.add(str(src), arcname="other.example/1")
  with open(tar_path, "r+b") as fh:
    fh.truncate(512 + 4096)
  return tar_path


def _count_reads(monkeypatch) -> list[str]:
  """Count open-tar member reads while still running the real GNU tvf."""
  reads: list[str] = []
  real = helpers.get_mutable_tar_authority_member_map

  def _wrapped(path):
    reads.append(path)
    return real(path)

  monkeypatch.setattr(helpers, "get_mutable_tar_authority_member_map", _wrapped)
  return reads


def test_truncated_open_tar_queues_append_instead_of_raising(
  tmp_path, monkeypatch
):
  raw = _raw(tmp_path, "12:00:00")
  tar_path = _truncated_tar(tmp_path)
  daily = os.path.dirname(tar_path)
  reads = _count_reads(monkeypatch)
  try:
    helpers.get_mutable_tar_authority_member_map(tar_path)
  except RuntimeError as exc:
    assert "gnu tar tvf failed" in str(exc)
  else:  # pragma: no cover - fixture must reproduce production
    raise AssertionError("fixture tar is not truncated")
  reads.clear()
  # Production signature: this raised and killed the whole walk.
  assert jr.discover_raw_needs_tar_append(raw, daily) is True
  assert jr.discover_append_is_complete(raw, daily) is False
  assert reads == [tar_path]


def test_truncated_tar_is_listed_once_per_identity(tmp_path, monkeypatch):
  raws = [_raw(tmp_path, f"1{i}:00:00") for i in range(5)]
  tar_path = _truncated_tar(tmp_path)
  daily = os.path.dirname(tar_path)
  reads = _count_reads(monkeypatch)
  assert all(jr.discover_raw_needs_tar_append(r, daily) for r in raws)
  assert reads == [tar_path]


def test_changed_tar_is_read_again_and_answers_from_members(
  tmp_path, monkeypatch
):
  raw = _raw(tmp_path, "12:00:00")
  tar_path = _truncated_tar(tmp_path)
  daily = os.path.dirname(tar_path)
  reads = _count_reads(monkeypatch)
  assert jr.discover_raw_needs_tar_append(raw, daily) is True
  # The append worker repairs the tar and appends the raw file.
  os.remove(tar_path)
  with tarfile.open(tar_path, "w", format=tarfile.GNU_FORMAT) as tf:
    tf.add(raw, arcname=helpers.get_tar_member_name(raw))
  assert jr.discover_raw_needs_tar_append(raw, daily) is False
  assert reads == [tar_path, tar_path]
