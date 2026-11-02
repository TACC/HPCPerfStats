"""Daily-tar appends clone raw blocks into a pax archive, crash-safely.

``tar -r`` copied every appended byte at 18-30 MB/s per stream
(hpcperfstats01 2026-11-01: 25-50 GB batches took 5-10 minutes and doubled
the day's disk use on a 98% full filesystem). ``clone_append`` writes pax
headers that put member data on 4 KiB boundaries and shares the raw file's
extents into the tar with FICLONERANGE; a durable journal makes a stop at
any instruction lose nothing.

Reflink tests need a reflink filesystem; the host ``/tmp`` and ``/data`` are
XFS with ``reflink=1``.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import textwrap
from unittest import mock

import pytest

from hpcperfstats.dbload.lib import (
  sync_timedb_progress_io as pio,
  sync_timedb_tar_clone_append as tca,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
  get_tar_member_name,
)

GIB = 1 << 30


@pytest.fixture(autouse=True)
def _reflink(tmp_path):
  if not tca._probe_clone(str(tmp_path)):
    pytest.fail(f"test filesystem at {tmp_path} cannot reflink")


def _raw(tmp_path, sizes, sub="raw"):
  d = tmp_path / sub / "c101-001.example"
  d.mkdir(parents=True, exist_ok=True)
  out = []
  for i, n in enumerate(sizes):
    p = d / f"{1785500000 + i}"
    p.write_bytes(os.urandom(n))
    out.append(str(p))
  return out


def _gnu_tar(tar, paths, fmt="posix"):
  subprocess.run(
    ["tar", "-c", f"--format={fmt}", "-C", "/", "-f", str(tar)]
    + [p.lstrip("/") for p in paths],
    check=True,
  )


def _gnu_list(tar):
  out = subprocess.run(
    ["tar", "-tvf", str(tar)], capture_output=True, text=True, check=False
  )
  return out.returncode, out.stdout.splitlines()


def _tarfile_sizes(tar):
  with tarfile.open(tar, "r:") as tf:
    return {m.name: m.size for m in tf if m.isfile()}


def _sha(path):
  return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_clone_append_roundtrip_gnu_and_tarfile(tmp_path):
  paths = _raw(tmp_path, [0, 1, 511, 512, 4096, 5000, 3 << 20])
  tar = tmp_path / "2026-07-31.tar"
  res = tca.clone_append(str(tar), paths)
  assert (res.appended, res.copied, res.cloned) == (7, 0, 6)
  rc, lines = _gnu_list(tar)
  assert rc == 0 and len(lines) == 7
  assert _tarfile_sizes(tar) == {
    get_tar_member_name(p): os.path.getsize(p) for p in paths
  }
  out = tmp_path / "x"
  out.mkdir()
  subprocess.run(["tar", "-x", "-f", str(tar), "-C", str(out)], check=True)
  for p in paths:
    assert (out / p.lstrip("/")).read_bytes() == open(p, "rb").read()


def test_clone_append_data_offsets_4k_aligned(tmp_path):
  tar = tmp_path / "2026-07-31.tar"
  _gnu_tar(tar, _raw(tmp_path, [700], sub="base"))
  tca.clone_append(str(tar), _raw(tmp_path, [1, 513, 9000, 4096]))
  with tarfile.open(tar, "r:") as tf:
    appended = [m for m in tf if "/raw/" in m.name]
  assert len(appended) == 4
  assert all(m.offset_data % 4096 == 0 for m in appended)


def test_clone_append_onto_gnu_b20_tar_clones_every_member(tmp_path):
  # Regression: GNU tar pads to a 10 KiB record past the logical end. Without
  # truncating that padding first every clone landed mid-file, XFS refused
  # the partial tail block (EINVAL) and the writer silently copied.
  base = _raw(tmp_path, [700, 20000], sub="base")
  tar = tmp_path / "2026-07-31.tar"
  _gnu_tar(tar, base)
  size = os.path.getsize(tar)
  with open(tar, "rb") as fh:
    end = tca.logical_end(fh.fileno(), size)
  assert end < size and size % 10240 == 0
  before = open(tar, "rb").read()[:end]
  paths = _raw(tmp_path, [1, 777, 4096, 100000])
  res = tca.clone_append(str(tar), paths)
  assert (res.cloned, res.copied, res.old_end) == (4, 0, end)
  assert open(tar, "rb").read()[:end] == before
  assert _gnu_list(tar)[0] == 0
  assert len(_tarfile_sizes(tar)) == 6


def test_clone_append_creates_missing_tar(tmp_path):
  tar = tmp_path / "2026-07-31.tar"
  res = tca.clone_append(str(tar), _raw(tmp_path, [10, 20]))
  assert res.appended == 2 and tar.is_file()
  assert _gnu_list(tar)[0] == 0
  assert sorted(os.listdir(tmp_path)) == ["2026-07-31.tar", "raw"]


def test_clone_append_skips_vanished_and_non_regular_paths(tmp_path):
  paths = _raw(tmp_path, [10, 20])
  link = tmp_path / "raw" / "link"
  link.symlink_to(paths[0])
  tar = tmp_path / "2026-07-31.tar"
  res = tca.clone_append(str(tar), [paths[0], "/nonexistent/x", str(link)])
  assert (res.appended, res.skipped) == (1, 2)
  assert tca.clone_append(str(tar), ["/nonexistent/y"]).appended == 0


def test_clone_append_copy_fallback(tmp_path):
  paths = _raw(tmp_path, [1, 5000, 2 << 20])
  tar = tmp_path / "2026-07-31.tar"
  _gnu_tar(tar, _raw(tmp_path, [300], sub="base"))
  with mock.patch.object(
    tca, "_clone_range", side_effect=OSError(errno.EOPNOTSUPP, "no reflink")
  ):
    res = tca.clone_append(str(tar), paths)
  assert (res.cloned, res.copied) == (0, 3)
  out = tmp_path / "x"
  out.mkdir()
  subprocess.run(["tar", "-x", "-f", str(tar), "-C", str(out)], check=True)
  for p in paths:
    assert (out / p.lstrip("/")).read_bytes() == open(p, "rb").read()


def test_hung_copy_fallback_is_idle_killed_and_rolled_back(tmp_path):
  # A copy that stops growing the tar is killed by the progress watchdog
  # (like tar -r was) and the append is rolled back; it never hangs silently.
  tar = tmp_path / "2026-07-31.tar"
  _gnu_tar(tar, _raw(tmp_path, [300], sub="base"))
  before = _sha(tar)
  real = pio.run_subprocess_with_progress
  seen = {}

  def _hang(args, **kw):
    seen.update(kw)
    return real(
      ["sleep", "60"],
      progress_path=kw["progress_path"],
      stage=kw["stage"],
      metric=kw["metric"],
      idle_s=0.5,
      poll_s=0.05,
    )

  with (
    mock.patch.object(
      tca, "_clone_range", side_effect=OSError(errno.EXDEV, "x")
    ),
    mock.patch.object(pio, "run_subprocess_with_progress", _hang),
    pytest.raises(pio.ProgressIdleError),
  ):
    tca.clone_append(str(tar), _raw(tmp_path, [1 << 20]))
  assert seen["stage"] == "tar_append_copy" and seen["progress_path"] == str(
    tar
  )
  assert _sha(tar) == before
  assert not os.path.exists(tca.journal_path(str(tar)))


def test_copy_fallback_fails_on_short_copy(tmp_path):
  tar = tmp_path / "2026-07-31.tar"
  _gnu_tar(tar, _raw(tmp_path, [300], sub="base"))
  before = _sha(tar)
  done = subprocess.CompletedProcess(
    args=[], returncode=0, stdout="", stderr=""
  )
  with (
    mock.patch.object(
      tca, "_clone_range", side_effect=OSError(errno.EXDEV, "x")
    ),
    mock.patch.object(pio, "run_subprocess_with_progress", return_value=done),
    pytest.raises(OSError, match="short copy"),
  ):
    tca.clone_append(str(tar), _raw(tmp_path, [9000]))
  assert _sha(tar) == before


def test_clone_append_giant_mixed_format(tmp_path):
  # A > 8 GiB member appended onto a GNU-format (non-pax) archive: GNU tar
  # and tarfile must read the pax size (gates deleting the pax convert path).
  tar = tmp_path / "2026-07-31.tar"
  _gnu_tar(tar, _raw(tmp_path, [1234], sub="base"), fmt="gnu")
  giant = tmp_path / "raw" / "c101-002.example" / "1785500999"
  giant.parent.mkdir(parents=True)
  with open(giant, "wb") as fh:
    fh.truncate(9 * GIB + 12345)
    fh.seek(9 * GIB)
    fh.write(b"tail-bytes")
  res = tca.clone_append(str(tar), [str(giant)])
  assert res.cloned == 1
  rc, lines = _gnu_list(tar)
  assert rc == 0 and len(lines) == 2
  assert str(9 * GIB + 12345) in lines[1]
  sizes = _tarfile_sizes(tar)
  assert sizes[get_tar_member_name(str(giant))] == 9 * GIB + 12345
  with tarfile.open(tar, "r:") as tf:
    m = tf.getmember(get_tar_member_name(str(giant)))
    with tf.extractfile(m) as fh:
      fh.seek(9 * GIB)
      assert fh.read(10) == b"tail-bytes"


def test_clone_append_rollback_mid_batch(tmp_path):
  tar = tmp_path / "2026-07-31.tar"
  _gnu_tar(tar, _raw(tmp_path, [300], sub="base"))
  before = _sha(tar)
  real = tca._clone_range
  calls = []

  def _flaky(src, dst, off):
    calls.append(off)
    if len(calls) == 3:
      raise OSError(errno.ENOSPC, "No space left on device")
    real(src, dst, off)

  with (
    mock.patch.object(tca, "_clone_range", _flaky),
    pytest.raises(OSError, match="No space"),
  ):
    tca.clone_append(str(tar), _raw(tmp_path, [10, 20, 30, 40]))
  assert _sha(tar) == before
  assert not os.path.exists(tca.journal_path(str(tar)))


def test_clone_append_journal_recovery(tmp_path):
  tar = tmp_path / "2026-07-31.tar"
  _gnu_tar(tar, _raw(tmp_path, [300], sub="base"))
  before = _sha(tar)
  size = os.path.getsize(tar)
  fd = os.open(tar, os.O_RDWR)
  end = tca.logical_end(fd, size)
  journal = {
    "v": 1,
    "old_end": end,
    "old_size": size,
    "st_dev": os.fstat(fd).st_dev,
    "st_ino": os.fstat(fd).st_ino,
    "tail_sha256": tca._tail_sha256(fd, end),
  }
  os.pwrite(fd, b"garbage-half-member" * 999, end)
  os.close(fd)
  tca._write_durable(tca.journal_path(str(tar)), json.dumps(journal).encode())
  logs = []
  assert (
    tca.recover_interrupted_append(
      str(tar), log_fn=lambda m, **k: logs.append(m)
    )
    == "recovered"
  )
  assert _sha(tar) == before
  assert "archive append recovered" in logs[0]
  assert not os.path.exists(tca.journal_path(str(tar)))
  # The next append also applies a leftover journal first.
  fd = os.open(tar, os.O_RDWR)
  os.pwrite(fd, b"x" * 50000, end)
  os.close(fd)
  tca._write_durable(tca.journal_path(str(tar)), json.dumps(journal).encode())
  res = tca.clone_append(str(tar), _raw(tmp_path, [77]))
  assert res.old_end == end and _gnu_list(tar)[0] == 0
  assert len(_tarfile_sizes(tar)) == 2


@pytest.mark.parametrize("field", ["st_ino", "tail_sha256"])
def test_recover_stale_journal_never_truncates(tmp_path, field):
  tar = tmp_path / "2026-07-31.tar"
  _gnu_tar(tar, _raw(tmp_path, [300, 5000], sub="base"))
  before = _sha(tar)
  st = os.stat(tar)
  journal = {
    "old_end": 512,
    "old_size": 0,
    "st_dev": st.st_dev,
    "st_ino": st.st_ino,
    "tail_sha256": "0" * 64,
  }
  if field == "st_ino":
    with open(tar, "rb") as fh:
      journal["tail_sha256"] = tca._tail_sha256(fh.fileno(), 512)
    journal["st_ino"] = st.st_ino + 1
  tca._write_durable(tca.journal_path(str(tar)), json.dumps(journal).encode())
  assert tca.recover_interrupted_append(str(tar), log_fn=None) == "stale"
  assert _sha(tar) == before
  assert not os.path.exists(tca.journal_path(str(tar)))


def test_recover_corrupt_journal_moves_aside(tmp_path):
  tar = tmp_path / "2026-07-31.tar"
  _gnu_tar(tar, _raw(tmp_path, [300], sub="base"))
  before = _sha(tar)
  open(tca.journal_path(str(tar)), "w").write("{not json")
  assert tca.recover_interrupted_append(str(tar), log_fn=None) == "corrupt"
  assert _sha(tar) == before
  aside = [n for n in os.listdir(tmp_path) if ".append_journal.corrupt." in n]
  assert len(aside) == 1


def test_recover_orphan_journal_without_tar_is_removed(tmp_path):
  tar = tmp_path / "2026-07-31.tar"
  open(tca.journal_path(str(tar)), "w").write(
    json.dumps(
      {"old_end": 0, "old_size": 0, "st_dev": 1, "st_ino": 1, "tail_sha256": ""}
    )
  )
  assert tca.recover_interrupted_append(str(tar), log_fn=None) == "orphan"
  assert os.listdir(tmp_path) == []


def test_recover_all_interrupted_appends_in_daily_dir(tmp_path):
  for day in ("2026-07-30", "2026-07-31"):
    open(tca.journal_path(str(tmp_path / f"{day}.tar")), "w").write(
      json.dumps(
        {
          "old_end": 0,
          "old_size": 0,
          "st_dev": 1,
          "st_ino": 1,
          "tail_sha256": "",
        }
      )
    )
  (tmp_path / "unrelated.append_journal").write_text("{}")
  assert tca.recover_all_interrupted_appends(str(tmp_path), log_fn=None) == 2
  assert os.listdir(tmp_path) == ["unrelated.append_journal"]


def test_clone_append_shares_extents_with_the_raw_file(tmp_path):
  paths = _raw(tmp_path, [8 << 20])
  tar = tmp_path / "2026-07-31.tar"
  tca.clone_append(str(tar), paths)
  out = subprocess.run(
    ["filefrag", "-v", str(tar)], capture_output=True, text=True, check=True
  ).stdout
  assert "shared" in out


def test_clone_append_accepts_a_one_shot_iterable(tmp_path):
  paths = _raw(tmp_path, [10, 20])
  res = tca.clone_append(
    str(tmp_path / "2026-07-31.tar"), iter([*paths, "/nonexistent/x"])
  )
  assert (res.appended, res.skipped) == (2, 1)


def test_empty_only_batch_does_not_record_clone_capability(
  tmp_path, monkeypatch
):
  monkeypatch.setattr(tca, "_CLONE_CAPABLE", {})
  paths = _raw(tmp_path, [0, 0])
  tca.clone_append(str(tmp_path / "2026-07-31.tar"), paths)
  assert tca._CLONE_CAPABLE == {}
  with mock.patch.object(
    tca, "_clone_range", side_effect=OSError(errno.EXDEV, "x")
  ):
    tca.clone_append(
      str(tmp_path / "2026-07-31.tar"), _raw(tmp_path, [5], "r2")
    )
  assert list(tca._CLONE_CAPABLE.values()) == [False]


def test_clone_capable(tmp_path):
  tar = str(tmp_path / "2026-07-31.tar")
  assert tca.clone_capable(tar) is True
  assert tca.clone_capable(tar, sample_path="/proc/self/stat") is False
  assert tca.clone_capable("/nonexistent/dir/x.tar") is False


def test_header_alignment_for_long_names_and_odd_offsets():
  st = os.stat(__file__)
  for offset in range(0, 8192, 512):
    for name in ("a", "b" * 99, "c/" * 80 + "d"):
      hdr = tca.build_member_header(name, st, offset)
      assert (offset + len(hdr)) % tca.ALIGN == 0


_KILL_CHILD = textwrap.dedent(
  """
  import os, signal, sys
  sys.path.insert(0, {root!r})
  from hpcperfstats.dbload.lib import sync_timedb_tar_clone_append as tca
  step, tar, *paths = sys.argv[1:]
  die = lambda: os.kill(os.getpid(), signal.SIGKILL)
  n = {{"pwrite": 0, "ftruncate": 0, "fsync_dir": 0}}

  def after(name, fn, when=1):
    def wrapped(*a, **k):
      out = fn(*a, **k)
      n[name] = n.get(name, 0) + 1
      if n[name] == when:
        die()
      return out
    return wrapped

  if step == "journal_temp":
    os.replace = lambda *a, **k: die()
  elif step == "journal_replaced":
    tca._fsync_dir = after("fsync_dir", tca._fsync_dir)
  elif step == "mid_header":
    os.pwrite = after("pwrite", os.pwrite)
  elif step == "mid_clone":
    tca._clone_range = after("clone", tca._clone_range)
  elif step == "after_eof":
    os.ftruncate = after("ftruncate", os.ftruncate, when=2)
  elif step == "after_fsync":
    tca._remove_durable = lambda *a, **k: die()
  elif step == "after_commit":
    tca._remove_durable = after("remove", tca._remove_durable)
  tca.clone_append(tar, paths, log_fn=None)
  """
)

_STEPS = [
  ("journal_temp", False),
  ("journal_replaced", False),
  ("mid_header", False),
  ("mid_clone", False),
  ("after_eof", False),
  ("after_fsync", False),
  ("after_commit", True),
]


def test_clone_append_kill_at_every_step(tmp_path):
  root = os.path.dirname(os.path.dirname(os.path.dirname(tca.__file__)))
  script = tmp_path / "child.py"
  script.write_text(_KILL_CHILD.format(root=root))
  base = _raw(tmp_path, [300, 70000], sub="base")
  batch = _raw(tmp_path, [1, 4096, 9000, 3 << 20])
  pristine = tmp_path / "pristine.tar"
  _gnu_tar(pristine, base)
  old = open(pristine, "rb").read()
  for step, committed in _STEPS:
    daily = tmp_path / f"daily-{step}"
    daily.mkdir()
    tar = daily / "2026-07-31.tar"
    shutil.copyfile(pristine, tar)
    proc = subprocess.run(
      [sys.executable, str(script), step, str(tar), *batch],
      capture_output=True,
      text=True,
    )
    assert proc.returncode == -signal.SIGKILL, (step, proc.stderr)
    tca.recover_interrupted_append(str(tar), log_fn=None)
    data = open(tar, "rb").read()
    rc, lines = _gnu_list(tar)
    assert rc == 0, step
    assert not os.path.exists(tca.journal_path(str(tar))), step
    if committed:
      assert data[:2048] == old[:2048], step
      assert len(lines) == len(base) + len(batch), step
    else:
      assert data == old, step
      assert len(lines) == len(base), step


def test_salvage_cuts_truncated_clone_written_tar_cleanly(tmp_path):
  # Salvage cuts at the last listed member's start (tar -tvR reports the
  # member's own header, after its pax extended header) and must also drop
  # that member's pax header, here padded with an alignment comment.
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  paths = _raw(tmp_path, [700, 9000, 300000])
  tar = tmp_path / "2026-07-31.tar"
  tca.clone_append(str(tar), paths)
  with tarfile.open(tar, "r:") as tf:
    last = list(tf)[-1]
  with open(tar, "r+b") as fh:
    fh.truncate(last.offset_data + 4096)  # power cut mid-member, no journal
  assert _gnu_list(tar)[0] != 0
  res = helpers.salvage_truncated_tar_prefix(str(tar), log_fn=None)
  assert res.status == "salvaged", res
  with tarfile.open(tar, "r:") as tf:  # strict reader: no dangling pax header
    assert [m.name for m in tf] == [get_tar_member_name(p) for p in paths[:2]]
  assert helpers.verify_tar_archive_readable(str(tar))
  # The salvaged tar takes further clone appends.
  res2 = tca.clone_append(str(tar), [paths[2]])
  assert res2.cloned == 1 and len(_tarfile_sizes(tar)) == 3
