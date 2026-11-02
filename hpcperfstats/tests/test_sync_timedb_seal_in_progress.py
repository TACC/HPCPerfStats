"""A daily tar being sealed is visible in-process, and discover skips it.

hpcperfstats01 2026-11-02: four multi-TB seals each held their tar's write
lock for 9+ hours of zstd. The single discover walk classified every raw file
of 2026-07-31 through ``get_mutable_tar_authority_member_map``, waited the full
60 s read-lock timeout per file (647 timeouts, about 11 hours), and then, with
an empty member map, queued an append for a file the seal had already proven
was in the tar. The seal only starts once every closed raw file of the day is
a same-size member of the tar or the sealed copy (``_closed_raw_not_archived``),
so a sealing day needs no append: discover skips it and day close hands any
raw it finds unarchived back to ingest afterwards.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import time
from datetime import datetime

import pytest

from hpcperfstats.dbload.lib import (
  sync_timedb_archive_helpers as helpers,
  sync_timedb_job_reconstruct as jr,
)

DAY = "2026-07-31"


def _raw_for_day(tmp_path, *, size: int = 64) -> str:
  """Write one closed raw stats file whose filename epoch falls on DAY."""
  host = tmp_path / "archive" / "c001.example"
  host.mkdir(parents=True)
  epoch = int(datetime.fromisoformat(f"{DAY}T12:00:00").timestamp())
  raw = host / str(epoch)
  raw.write_bytes(b"x" * size)
  return str(raw)


def _open_tar(tmp_path) -> str:
  """Create an open daily tar with one unrelated member."""
  daily = tmp_path / "daily"
  daily.mkdir()
  src = tmp_path / "member"
  src.write_bytes(b"y" * 10)
  tar_path = str(daily / f"{DAY}.tar")
  with tarfile.open(tar_path, "w", format=tarfile.GNU_FORMAT) as tf:
    tf.add(str(src), arcname="other.example/1")
  return tar_path


def test_sealing_tar_is_skipped_by_discover_without_a_member_read(
  tmp_path, monkeypatch
):
  raw = _raw_for_day(tmp_path)
  tar_path = _open_tar(tmp_path)
  daily = os.path.dirname(tar_path)
  reads = []

  def _member_read(path):
    reads.append(path)
    return {}

  monkeypatch.setattr(
    helpers, "get_mutable_tar_authority_member_map", _member_read
  )
  # Not sealing: the raw is not in the tar, so discover queues an append.
  assert jr.discover_raw_needs_tar_append(raw, daily) is True
  assert reads == [tar_path]
  reads.clear()
  with helpers.tar_sealing(tar_path):
    assert helpers.tar_seal_in_progress(tar_path)
    t0 = time.monotonic()
    assert jr.discover_raw_needs_tar_append(raw, daily) is False
    assert time.monotonic() - t0 < 1.0
  assert reads == []
  assert not helpers.tar_seal_in_progress(tar_path)
  assert jr.discover_append_is_complete(raw, daily) is False


def test_sealing_mark_clears_when_the_holder_raises(tmp_path):
  tar_path = _open_tar(tmp_path)
  with pytest.raises(RuntimeError), helpers.tar_sealing(tar_path):
    assert helpers.tar_seal_in_progress(tar_path)
    raise RuntimeError("zstd exit 27")
  assert not helpers.tar_seal_in_progress(tar_path)


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd not on PATH")
def test_seal_marks_the_tar_for_the_whole_compress(tmp_path, monkeypatch):
  tar_path = _open_tar(tmp_path)
  real_compress = helpers.zstd_compress_tar_to_file
  seen = []

  def _compress(tar, out, *args, **kwargs):
    seen.append(helpers.tar_seal_in_progress(tar))
    return real_compress(tar, out, *args, **kwargs)

  monkeypatch.setattr(helpers, "zstd_compress_tar_to_file", _compress)
  helpers.atomic_seal_tar_to_zst(
    tar_path,
    tar_path + ".zst",
    1,
    3,
    keep_uncompressed_tar=True,
    log_fn=None,
  )
  assert seen == [True]
  assert os.path.isfile(tar_path + ".zst")
  assert not helpers.tar_seal_in_progress(tar_path)
