"""Stamp contract of scripts/pg18_relational_copy.sh (run with fake clients).

The script runs inside db_pg18; here ``psql`` / ``pg_dump`` / ``pg_restore``
are fakes on PATH and ``PG18_RELATIONAL_ROOT`` stands in for
``/var/lib/postgresql``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = (
  Path(__file__).resolve().parents[2] / "scripts" / "pg18_relational_copy.sh"
)
PG15_ID = "-7212345678901234567"
PG18_ID = "7412345678901234567"

_FAKE_PSQL = r"""
import os, pathlib, sys
args = sys.argv[1:]
sql = args[-1]
src = args[args.index("-h") + 1] == "db"
log = pathlib.Path(os.environ["FAKE_LOG"])
stamp = pathlib.Path(os.environ["PG18_RELATIONAL_ROOT"]) / "relational_copy_ok"
side = "pg15" if src else "pg18"
with log.open("a") as fh:
  fh.write(f"psql {side} stamp={stamp.exists()} {sql[:40]}\n")
if "default_transaction_read_only" in sql:
  print(os.environ.get("FAKE_RO", "on"))
elif "quote_ident" in sql:
  print("a, b")
elif "query_to_xml" in sql:
  print("a=1 b=2" if src else os.environ.get("FAKE_DST_COUNTS", "a=1 b=2"))
elif "pg_control_system" in sql:
  print(os.environ["FAKE_PG15_ID"] if src else os.environ["FAKE_PG18_ID"])
elif "pg_database" in sql:
  print("16384")
"""

_FAKE_PG_DUMP = r"""
import pathlib, sys
out = pathlib.Path(sys.argv[sys.argv.index("-f") + 1])
out.mkdir(parents=True)
(out / "toc.dat").write_text("x")
"""

_FAKE_PG_RESTORE = r"""
import os, pathlib, time
started = os.environ.get("FAKE_RESTORE_STARTED")
if started:
  pathlib.Path(started).write_text("x")
  time.sleep(120)
"""


def _fake_bin(tmp_path: Path) -> Path:
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir()
  for name, body in (
    ("psql", _FAKE_PSQL),
    ("pg_dump", _FAKE_PG_DUMP),
    ("pg_restore", _FAKE_PG_RESTORE),
  ):
    path = bin_dir / name
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)
  return bin_dir


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
  root = tmp_path / "pg18"
  root.mkdir(exist_ok=True)
  env = dict(os.environ)
  env.update(
    PATH=f"{_fake_bin(tmp_path)}:{os.environ['PATH']}",
    PG18_RELATIONAL_ROOT=str(root),
    FAKE_LOG=str(tmp_path / "calls.log"),
    FAKE_PG15_ID=PG15_ID,
    FAKE_PG18_ID=PG18_ID,
  )
  env.update(extra)
  return env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
    ["sh", str(SCRIPT)],
    env=env,
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
  )


def _stamp(tmp_path: Path) -> Path:
  (tmp_path / "pg18").mkdir(exist_ok=True)
  return tmp_path / "pg18" / "relational_copy_ok"


def _calls(tmp_path: Path) -> list[str]:
  return (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines()


def test_success_writes_stamp_atomically_after_counts_match(
  tmp_path: Path,
) -> None:
  env = _env(tmp_path)
  _stamp(tmp_path).write_text("stale", encoding="utf-8")
  before = int(time.time())
  proc = _run(env)
  assert proc.returncode == 0, proc.stderr
  assert proc.stdout.rstrip().endswith("RELATIONAL_COPY_OK")
  doc = json.loads(_stamp(tmp_path).read_text(encoding="utf-8"))
  assert doc["stamp"] == "relational_copy_ok"
  assert doc["pg18_system_identifier"] == PG18_ID
  assert doc["pg15_system_identifier"] == PG15_ID
  assert doc["pg18_database_oid"] == 16384
  assert before <= doc["written_at_epoch"] <= int(time.time())
  assert doc["written_at"] == time.strftime(
    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(doc["written_at_epoch"])
  )
  assert sorted(p.name for p in (tmp_path / "pg18").iterdir()) == [
    "relational_copy_ok"
  ]


def test_read_only_check_runs_first_and_stamp_is_gone_before_pg18_write(
  tmp_path: Path,
) -> None:
  _stamp(tmp_path).write_text("stale", encoding="utf-8")
  assert _run(_env(tmp_path)).returncode == 0
  calls = _calls(tmp_path)
  assert calls[0].startswith("psql pg15 stamp=True SHOW default_transaction")
  truncate = next(c for c in calls if "TRUNCATE" in c)
  assert truncate.startswith("psql pg18 stamp=False")


def test_refuses_unless_pg15_read_only_and_keeps_everything(
  tmp_path: Path,
) -> None:
  _stamp(tmp_path).write_text("previous", encoding="utf-8")
  proc = _run(_env(tmp_path, FAKE_RO="off"))
  assert proc.returncode == 1
  assert "RELATIONAL_COPY_FAILED" in proc.stderr
  assert "default_transaction_read_only" in proc.stderr
  assert _calls(tmp_path) == [
    "psql pg15 stamp=True SHOW default_transaction_read_only"
  ]
  assert _stamp(tmp_path).read_text(encoding="utf-8") == "previous"


def test_count_mismatch_leaves_no_stamp(tmp_path: Path) -> None:
  _stamp(tmp_path).write_text("stale", encoding="utf-8")
  proc = _run(_env(tmp_path, FAKE_DST_COUNTS="a=1 b=1"))
  assert proc.returncode == 1
  assert "row counts differ" in proc.stderr
  assert not _stamp(tmp_path).exists()
  assert not any("pg_control_system" in c for c in _calls(tmp_path))


@pytest.mark.parametrize("bad_side", ["FAKE_PG15_ID", "FAKE_PG18_ID"])
def test_unreadable_identity_leaves_no_stamp(
  tmp_path: Path, bad_side: str
) -> None:
  proc = _run(_env(tmp_path, **{bad_side: ""}))
  assert proc.returncode == 1
  assert "system_identifier" in proc.stderr
  assert not _stamp(tmp_path).exists()


def test_sigkill_mid_restore_leaves_no_stamp(tmp_path: Path) -> None:
  started = tmp_path / "restore_started"
  _stamp(tmp_path).write_text("from an earlier run", encoding="utf-8")
  env = _env(tmp_path, FAKE_RESTORE_STARTED=str(started))
  proc = subprocess.Popen(["sh", str(SCRIPT)], env=env, start_new_session=True)
  try:
    deadline = time.monotonic() + 60
    while not started.exists():
      assert proc.poll() is None, "script exited before pg_restore"
      assert time.monotonic() < deadline, "pg_restore never started"
      time.sleep(0.05)
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=30)
  finally:
    if proc.poll() is None:
      os.killpg(proc.pid, signal.SIGKILL)
  assert not _stamp(tmp_path).exists()
  assert not (tmp_path / "pg18" / "relational_copy_ok.tmp").exists()
