"""scripts/migrate_daily_archive_gz_to_zst.sh never loses a daily archive.

Each case runs the real script against a temp daily dir. A legacy .tar.gz is
removed only after a byte-verified .tar.zst replacement exists; every failure
path leaves the .tar.gz (and any .tar / .tar.zst) exactly as it was.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "migrate_daily_archive_gz_to_zst.sh"

pytestmark = pytest.mark.skipif(not shutil.which("zstd"), reason="needs zstd")

DAY = "2024-03-05"
MEMBERS = [
  ("c1.example.com/1709618400", b"x" * 5000),
  ("c2.example.com/1709622000", os.urandom(7000)),
]


def _tar_bytes(members: list[tuple[str, bytes]]) -> bytes:
  buf = io.BytesIO()
  with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tf:
    for name, data in members:
      info = tarfile.TarInfo(name)
      info.size = len(data)
      info.mtime = 1700000000
      tf.addfile(info, io.BytesIO(data))
  return buf.getvalue()


def _write_gz(daily: Path, members, day: str = DAY) -> Path:
  gz = daily / f"{day}.tar.gz"
  gz.write_bytes(gzip.compress(_tar_bytes(members)))
  return gz


def _write_zst(daily: Path, members, day: str = DAY) -> Path:
  zst = daily / f"{day}.tar.zst"
  subprocess.run(
    ["zstd", "-q", "-f", "-o", str(zst)],
    input=_tar_bytes(members),
    check=True,
  )
  return zst


def _zst_bytes(zst: Path) -> bytes:
  return subprocess.run(
    ["zstd", "-dc", str(zst)], capture_output=True, check=True
  ).stdout


def _run(daily: Path, *args: str, env: dict | None = None):
  return subprocess.run(
    [
      "bash",
      str(_SCRIPT),
      "--daily",
      str(daily),
      "--margin-gb",
      "0",
      "--allow-running",
      "--log",
      str(daily.parent / "run.log"),
      *args,
    ],
    capture_output=True,
    text=True,
    env=env,
    timeout=120,
  )


def _snapshot(daily: Path) -> dict[str, str]:
  return {
    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(daily.iterdir())
    if not p.name.startswith(".")
  }


def _names(daily: Path) -> list[str]:
  return sorted(p.name for p in daily.iterdir() if not p.name.startswith("."))


@pytest.fixture
def daily(tmp_path):
  d = tmp_path / "daily_archive"
  d.mkdir()
  return d


def test_dry_run_changes_nothing(daily):
  _write_gz(daily, MEMBERS)
  before = _snapshot(daily)
  proc = _run(daily)
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "WOULD CONVERT " + DAY in proc.stdout
  assert _snapshot(daily) == before


def test_gz_only_day_converts_byte_identical_and_drops_gz(daily):
  gz = _write_gz(daily, MEMBERS)
  raw = gzip.decompress(gz.read_bytes())
  proc = _run(daily, "--apply")
  assert proc.returncode == 0, proc.stdout + proc.stderr
  zst = daily / f"{DAY}.tar.zst"
  assert not gz.exists()
  assert _zst_bytes(zst) == raw
  assert _names(daily) == [zst.name]


def test_truncated_gz_is_kept_and_no_zst_is_left(daily):
  gz = _write_gz(daily, MEMBERS)
  gz.write_bytes(gz.read_bytes()[:-40])
  before = _snapshot(daily)
  proc = _run(daily, "--apply")
  assert proc.returncode != 0
  assert "FAIL " + DAY in proc.stdout
  assert _snapshot(daily) == before


def test_bad_compressor_output_fails_verify_and_keeps_gz(daily, tmp_path):
  """A zstd that silently drops bytes must be caught by the sha256 compare."""
  _write_gz(daily, MEMBERS)
  before = _snapshot(daily)
  shim = tmp_path / "bin"
  shim.mkdir()
  real = shutil.which("zstd")
  (shim / "zstd").write_text(
    "#!/bin/bash\n"
    'for a in "$@"; do [ "$a" = -o ] && { head -c -100 | '
    f'"{real}" "$@"; exit; }}; done\n'
    f'exec "{real}" "$@"\n'
  )
  (shim / "zstd").chmod(0o755)
  env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")
  proc = _run(daily, "--apply", env=env)
  assert proc.returncode != 0
  assert "verify" in proc.stdout
  assert _snapshot(daily) == before


def test_gz_covered_by_existing_zst_is_dropped_zst_untouched(daily):
  gz = _write_gz(daily, MEMBERS)
  zst = _write_zst(daily, [*MEMBERS, ("c3.example.com/1709625600", b"z" * 10)])
  zst_before = zst.read_bytes()
  proc = _run(daily, "--apply")
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "DROPPED " + DAY in proc.stdout
  assert not gz.exists()
  assert zst.read_bytes() == zst_before


def test_gz_with_member_missing_from_zst_keeps_both(daily):
  _write_gz(daily, MEMBERS)
  _write_zst(daily, MEMBERS[:1])
  before = _snapshot(daily)
  proc = _run(daily, "--apply")
  assert "KEEP BOTH " + DAY in proc.stdout
  assert "c2.example.com/1709622000" in proc.stdout
  assert _snapshot(daily) == before


def test_gz_member_with_different_bytes_same_size_keeps_both(daily):
  """Same name and size but different content is not a copy."""
  name, data = MEMBERS[0]
  _write_gz(daily, MEMBERS)
  _write_zst(daily, [(name, b"y" * len(data)), MEMBERS[1]])
  before = _snapshot(daily)
  proc = _run(daily, "--apply")
  assert "KEEP BOTH " + DAY in proc.stdout
  assert _snapshot(daily) == before


def test_corrupt_existing_zst_keeps_both(daily):
  _write_gz(daily, MEMBERS)
  zst = _write_zst(daily, MEMBERS)
  zst.write_bytes(zst.read_bytes()[:-20])
  before = _snapshot(daily)
  proc = _run(daily, "--apply")
  assert proc.returncode != 0
  assert _snapshot(daily) == before


def test_day_with_open_tar_is_left_alone(daily):
  _write_gz(daily, MEMBERS)
  (daily / f"{DAY}.tar").write_bytes(_tar_bytes(MEMBERS))
  before = _snapshot(daily)
  proc = _run(daily, "--apply")
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert "open .tar" in proc.stdout
  assert _snapshot(daily) == before


def test_stale_tmp_from_killed_run_is_replaced(daily):
  gz = _write_gz(daily, MEMBERS)
  raw = gzip.decompress(gz.read_bytes())
  (daily / f"{DAY}.tar.zst.gz2zst.tmp").write_bytes(b"partial")
  proc = _run(daily, "--apply")
  assert proc.returncode == 0, proc.stdout + proc.stderr
  assert _zst_bytes(daily / f"{DAY}.tar.zst") == raw
  assert _names(daily) == [f"{DAY}.tar.zst"]


def test_no_room_keeps_gz(daily):
  _write_gz(daily, MEMBERS)
  before = _snapshot(daily)
  proc = _run(daily, "--apply", "--margin-gb", "100000000")
  assert "not enough free space" in proc.stdout
  assert _snapshot(daily) == before


def test_parallel_jobs_convert_every_day(daily):
  days = [f"2024-03-0{i}" for i in range(1, 6)]
  raws = {}
  for day in days:
    raws[day] = gzip.decompress(_write_gz(daily, MEMBERS, day).read_bytes())
  proc = _run(daily, "--apply", "--jobs", "3")
  assert proc.returncode == 0, proc.stdout + proc.stderr
  for day in days:
    assert not (daily / f"{day}.tar.gz").exists()
    assert _zst_bytes(daily / f"{day}.tar.zst") == raws[day]
  assert "converted=5" in proc.stdout


def test_second_concurrent_run_is_refused(daily):
  _write_gz(daily, MEMBERS)
  lock = daily / ".migrate_daily_archive_gz_to_zst.lock"
  lock.touch()
  holder = subprocess.Popen(["flock", str(lock), "sleep", "30"])
  try:
    time.sleep(0.5)
    proc = _run(daily, "--apply")
  finally:
    holder.kill()
    holder.wait()
  assert proc.returncode == 2
  assert "another run" in proc.stderr
  assert (daily / f"{DAY}.tar.gz").exists()
