"""Day-close dedupe rewrites only tars with duplicates, and marks the tar.

hpcperfstats02 2026-11-05: six day_close jobs sat in the ``dedupe`` stage
(``<day>.tar.dedupe.tmp`` open on 10-03, 10-04, 10-06, 10-20, 10-21, 10-22).
``dedupe_tar_keep_largest_file_per_member`` copied every member of each
multi-hundred-GB open tar into a new tar under the tar's write lock, whether
or not any member name appeared twice. Meanwhile the single discover walk
read 10-03's member map per raw file, waited the 60 s read-lock timeout each
time (239 timeouts on ``2026-10-03.tar.fnctl.lock`` in 4 h, one append job
queued per minute), held ``discover=1/1`` so no new raw was queued
(``ingest=0/0`` for 3.5 h), and the four append writers cycled on 60 s
write-lock waits for the same tars.

Fix: (A) dedupe returns without writing ``.dedupe.tmp`` when its first pass
finds no duplicate file name; (B) dedupe holds ``tar_sealing`` for its write
lock, so discover answers "no append" without reading the tar and append
dispatch parks the day, as for a seal. The seal stage's
``_closed_raw_not_archived`` check hands any raw left unarchived back to
ingest right after dedupe.
"""

from __future__ import annotations

import os
import tarfile
import time
from datetime import datetime

import pytest

from hpcperfstats.dbload.lib import (
  sync_timedb_archive_helpers as helpers,
  sync_timedb_job_reconstruct as jr,
)
from hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation import (
  DayCloseYieldError,
)

DAY = "2026-10-03"


def _tar(tmp_path, members: list[tuple[str, int]]) -> str:
  """Write a daily tar with ``members`` (name, size) in order."""
  daily = tmp_path / "daily"
  daily.mkdir(exist_ok=True)
  tar_path = str(daily / f"{DAY}.tar")
  with tarfile.open(tar_path, "w", format=tarfile.GNU_FORMAT) as tf:
    for i, (name, size) in enumerate(members):
      src = tmp_path / f"src{i}"
      src.write_bytes(b"z" * size)
      tf.add(str(src), arcname=name)
  return tar_path


def _sizes(tar_path: str) -> list[tuple[str, int]]:
  with tarfile.open(tar_path, "r") as tf:
    return [(m.name, m.size) for m in tf.getmembers() if m.isfile()]


def _raw_for_day(tmp_path) -> str:
  host = tmp_path / "archive" / "c001.example"
  host.mkdir(parents=True)
  epoch = int(datetime.fromisoformat(f"{DAY}T12:00:00").timestamp())
  raw = host / str(epoch)
  raw.write_bytes(b"x" * 64)
  return str(raw)


def test_dedupe_without_duplicates_leaves_the_tar_untouched(
  tmp_path, monkeypatch
):
  tar_path = _tar(tmp_path, [("h/1", 10), ("h/2", 20)])
  before = os.stat(tar_path)
  invalidated = []
  monkeypatch.setattr(
    helpers,
    "invalidate_after_daily_tar_mutation",
    lambda *a, **k: invalidated.append(a),
  )
  real_open = tarfile.open
  written = []

  def _open(name=None, mode="r", *a, **k):
    if "w" in mode:
      written.append(name)
    return real_open(name, mode, *a, **k)

  monkeypatch.setattr(helpers.tarfile, "open", _open)
  assert (
    helpers.dedupe_tar_keep_largest_file_per_member(tar_path, log_fn=None)
    is True
  )
  after = os.stat(tar_path)
  assert written == [], "no .dedupe.tmp may be written without duplicates"
  assert not os.path.exists(f"{tar_path}.dedupe.tmp")
  assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
    before.st_ino,
    before.st_size,
    before.st_mtime_ns,
  )
  assert invalidated == []


def test_dedupe_with_duplicates_still_keeps_the_largest(tmp_path):
  tar_path = _tar(tmp_path, [("h/1", 10), ("h/2", 20), ("h/1", 30)])
  ino = os.stat(tar_path).st_ino
  assert (
    helpers.dedupe_tar_keep_largest_file_per_member(tar_path, log_fn=None)
    is True
  )
  assert sorted(_sizes(tar_path)) == [("h/1", 30), ("h/2", 20)]
  assert os.stat(tar_path).st_ino != ino
  assert not os.path.exists(f"{tar_path}.dedupe.tmp")


def test_repair_rewrite_still_copies_a_tar_without_duplicates(tmp_path):
  """The truncated-tar repair reuses the rewrite and must still write it."""
  tar_path = _tar(tmp_path, [("h/1", 10), ("h/2", 20)])
  tmp_out = f"{tar_path}.repair.tmp"
  assert (
    helpers._rewrite_daily_tar_largest_member_wins(
      tar_path, tmp_out, tgz_archive_dir="", yield_phase="repair"
    )
    is True
  )
  assert sorted(_sizes(tmp_out)) == [("h/1", 10), ("h/2", 20)]


def test_discover_and_append_skip_a_deduping_tar(tmp_path, monkeypatch):
  tar_path = _tar(tmp_path, [("h/1", 10), ("h/1", 30)])
  raw = _raw_for_day(tmp_path)
  daily = os.path.dirname(tar_path)
  real_rewrite = helpers._rewrite_daily_tar_largest_member_wins
  seen = []

  def _rewrite(src, dst, **kwargs):
    reads = []
    monkeypatch.setattr(
      helpers,
      "get_mutable_tar_authority_member_map",
      lambda path: reads.append(path) or {},
    )
    t0 = time.monotonic()
    needs = jr.discover_raw_needs_tar_append(raw, daily)
    seen.append(
      (helpers.tar_seal_in_progress(src), needs, reads, time.monotonic() - t0)
    )
    return real_rewrite(src, dst, **kwargs)

  monkeypatch.setattr(
    helpers, "_rewrite_daily_tar_largest_member_wins", _rewrite
  )
  assert helpers.dedupe_tar_keep_largest_file_per_member(tar_path, log_fn=None)
  [(marked, needs, reads, elapsed)] = seen
  assert marked is True
  assert needs is False
  assert reads == []
  assert elapsed < 1.0
  assert not helpers.tar_seal_in_progress(tar_path)


def test_dedupe_mark_clears_when_the_rewrite_yields(tmp_path, monkeypatch):
  tar_path = _tar(tmp_path, [("h/1", 10), ("h/1", 30)])

  def _yield(src, dst, **_k):
    assert helpers.tar_seal_in_progress(src)
    raise DayCloseYieldError(src, phase="dedupe", reason="ingest_hot")

  monkeypatch.setattr(helpers, "_rewrite_daily_tar_largest_member_wins", _yield)
  with pytest.raises(DayCloseYieldError):
    helpers.dedupe_tar_keep_largest_file_per_member(tar_path, log_fn=None)
  assert not helpers.tar_seal_in_progress(tar_path)
  assert not os.path.exists(f"{tar_path}.dedupe.tmp")
