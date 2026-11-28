"""Data-dir guards: PG18 entrypoint (version, role, lock) and the PG15 wrapper.

The real scripts run under bash/sh against temp directories. Nothing here
starts PostgreSQL: chown/chmod/mkdir/gosu are shell-function stubs that log,
and the Hub entrypoint is a stub on PATH.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ENTRY = REPO / "services-conf" / "db-docker-entrypoint.sh"
ENSURE = REPO / "services-conf" / "db-docker-ensure-initdb.sh"
PG15_GUARD = REPO / "services-conf" / "db-docker-pg15-guard.sh"
MARKER = "hpcperfstats-primary"
SENTINEL = "hpcperfstats-migrate-target"
RETIRED = "hpcperfstats-retired"
LOCK = ".hpc-server.lock"

pytestmark = pytest.mark.skipif(
  shutil.which("flock") is None, reason="flock(1) not installed"
)

_STUBS = (
  'chown() { echo "chown $*" >> "$CALLS"; }\n'
  'chmod() { echo "chmod $*" >> "$CALLS"; }\n'
  'mkdir() { echo "mkdir $*" >> "$CALLS"; }\n'
  'gosu() { echo "gosu $*" >> "$CALLS"; exit 0; }\n'
  'docker_create_db_directories() { echo create_dirs >> "$CALLS"; }\n'
)


def _tree(root: Path) -> list[str]:
  return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _write(path: Path, text: str = "") -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text)


def _calls(tmp_path: Path) -> str:
  path = tmp_path / "calls"
  return path.read_text() if path.exists() else ""


def _pg18(
  tmp_path: Path, body: str, role: str | None = None
) -> subprocess.CompletedProcess[str]:
  """Source the real PG18 entrypoint with its root at ``tmp_path/root``.

  Args:
    tmp_path (Path): Scratch dir; ``root/`` stands for /var/lib/postgresql.
    body (str): Bash run after sourcing (stubs and root already set).
    role (str | None): ``HPCPERFSTATS_DB_ROLE`` or ``None`` to leave unset.

  Returns:
    subprocess.CompletedProcess[str]: The bash run.
  """
  root = tmp_path / "root"
  root.mkdir(exist_ok=True)
  env = {k: v for k, v in os.environ.items() if k != "HPCPERFSTATS_DB_ROLE"}
  env.update(
    CALLS=str(tmp_path / "calls"),
    PG_MAJOR="18",
    PGDATA=f"{root}/18/docker",
    POSTGRES_PASSWORD="pw",
  )
  if role is not None:
    env["HPCPERFSTATS_DB_ROLE"] = role
  script = f"source '{ENTRY}'\nhpc_pg_root='{root}'\n{_STUBS}{body}\n"
  return subprocess.run(
    ["bash", "-c", script],
    env=env,
    capture_output=True,
    text=True,
    check=False,
    timeout=30,
  )


def _guard_ok(
  tmp_path: Path, role: str | None
) -> subprocess.CompletedProcess[str]:
  return _pg18(tmp_path, "hpc_guard_data_dir && echo GUARD_OK", role)


def _hold_lock(path: Path) -> subprocess.Popen[bytes]:
  """Start a process holding ``flock`` on ``path`` until killed.

  Args:
    path (Path): Lock file (created when missing).

  Returns:
    subprocess.Popen[bytes]: The holder; release it with ``_release``.
  """
  holder = subprocess.Popen(
    ["flock", "-n", str(path), "sleep", "30"], start_new_session=True
  )
  for _ in range(100):
    probe = subprocess.run(["flock", "-n", str(path), "true"], check=False)
    if path.exists() and probe.returncode != 0:
      return holder
    time.sleep(0.05)
  _release(holder)
  raise AssertionError("lock holder did not start")


def _release(holder: subprocess.Popen[bytes]) -> None:
  """Kill the holder and its ``sleep`` child (both keep the lock fd)."""
  os.killpg(holder.pid, signal.SIGKILL)
  holder.wait()


@pytest.mark.parametrize(
  "rel",
  ["PG_VERSION", "18/PG_VERSION", "18/docker/PG_VERSION", "data/PG_VERSION"],
)
@pytest.mark.parametrize("role", [None, "primary", "migrate-target"])
def test_entrypoint_refuses_pg15_data_before_any_write(
  tmp_path: Path, rel: str, role: str | None
) -> None:
  """Regression (owner): a PG18 server must never touch a PG15 data dir."""
  root = tmp_path / "root"
  _write(root / rel, "15\n")
  # Production bind leftovers that must not confuse the check.
  _write(root / "amcheck_1.log", "x")
  (root / "quarantine_1").mkdir()
  before = _tree(root)
  res = _pg18(tmp_path, "_main postgres", role)
  assert res.returncode == 1, res.stdout + res.stderr
  assert "PG_VERSION" in res.stderr and "15" in res.stderr
  assert "migrate-to-postgresql-18.md" in res.stderr
  assert _tree(root) == before  # not even the lock file
  assert _calls(tmp_path) == ""  # no chown/chmod/mkdir/create_dirs/gosu


def test_entrypoint_ignores_root_owned_18_without_pg_version(
  tmp_path: Path,
) -> None:
  root = tmp_path / "root"
  (root / "18").mkdir(parents=True)
  _write(root / "amcheck_2.log", "x")
  res = _guard_ok(tmp_path, None)
  assert res.returncode == 0, res.stderr
  assert "GUARD_OK" in res.stdout


def test_guard_runs_before_create_directories() -> None:
  text = ENTRY.read_text()
  main = text[text.index("_main() {") :]
  assert main.index("docker_setup_env") < main.index("hpc_guard_data_dir")
  assert main.index("hpc_guard_data_dir") < main.index(
    "docker_create_db_directories"
  )
  ensure = ENSURE.read_text()
  assert ensure.index("hpc_guard_data_dir") < ensure.index(
    "docker_create_db_directories"
  )


def test_primary_writes_marker_before_initdb_on_empty_bind(
  tmp_path: Path,
) -> None:
  res = _guard_ok(tmp_path, "primary")
  assert res.returncode == 0, res.stderr
  assert (tmp_path / "root" / MARKER).is_file()
  assert not (tmp_path / "root" / SENTINEL).exists()


def test_primary_refuses_existing_pgdata_without_marker(tmp_path: Path) -> None:
  """A partial PG18 copy has a real PG_VERSION 18; it must not go live."""
  root = tmp_path / "root"
  _write(root / "18" / "docker" / "PG_VERSION", "18\n")
  res = _pg18(tmp_path, "_main postgres", "primary")
  assert res.returncode == 1
  assert MARKER in res.stderr
  assert not (root / MARKER).exists()
  assert _calls(tmp_path) == ""


def test_primary_starts_existing_pgdata_with_marker(tmp_path: Path) -> None:
  root = tmp_path / "root"
  _write(root / "18" / "docker" / "PG_VERSION", "18\n")
  _write(root / MARKER, "x")
  res = _guard_ok(tmp_path, "primary")
  assert res.returncode == 0, res.stderr
  assert "GUARD_OK" in res.stdout


def test_primary_refuses_empty_bind_with_migrate_target_sentinel(
  tmp_path: Path,
) -> None:
  """A reset legacy PG18 bind must never become an empty live primary."""
  root = tmp_path / "root"
  _write(root / SENTINEL, "x")
  res = _pg18(tmp_path, "_main postgres", "primary")
  assert res.returncode == 1
  assert SENTINEL in res.stderr
  assert not (root / MARKER).exists()


def test_migrate_target_refuses_marker(tmp_path: Path) -> None:
  root = tmp_path / "root"
  _write(root / "18" / "docker" / "PG_VERSION", "18\n")
  _write(root / MARKER, "x")
  res = _pg18(tmp_path, "_main postgres", "migrate-target")
  assert res.returncode == 1
  assert MARKER in res.stderr
  assert _calls(tmp_path) == ""


def test_migrate_target_writes_sentinel(tmp_path: Path) -> None:
  res = _guard_ok(tmp_path, "migrate-target")
  assert res.returncode == 0, res.stderr
  assert (tmp_path / "root" / SENTINEL).is_file()
  assert not (tmp_path / "root" / MARKER).exists()


def test_pg15_legacy_role_always_refuses_in_pg18_image(tmp_path: Path) -> None:
  res = _pg18(tmp_path, "_main postgres", "pg15-legacy")
  assert res.returncode == 1
  assert "pg15-legacy" in res.stderr
  assert _tree(tmp_path / "root") == []


def test_unknown_role_refuses(tmp_path: Path) -> None:
  res = _pg18(tmp_path, "_main postgres", "primray")
  assert res.returncode == 1
  assert "HPCPERFSTATS_DB_ROLE" in res.stderr


def test_unset_role_keeps_upstream_behaviour(tmp_path: Path) -> None:
  res = _guard_ok(tmp_path, None)
  assert res.returncode == 0, res.stderr
  root = tmp_path / "root"
  assert not (root / MARKER).exists() and not (root / SENTINEL).exists()
  assert (root / LOCK).exists()


def test_guard_runs_as_non_root_through_main(tmp_path: Path) -> None:
  """Started with --user (no gosu hop): the guard still runs in _main."""
  if os.geteuid() == 0:
    pytest.skip("needs a non-root uid")
  root = tmp_path / "root"
  _write(root / "18" / "docker" / "PG_VERSION", "18\n")
  res = _pg18(tmp_path, "_main postgres", "primary")
  assert res.returncode == 1 and MARKER in res.stderr


def test_lock_second_instance_refused_then_released(tmp_path: Path) -> None:
  root = tmp_path / "root"
  root.mkdir()
  holder = _hold_lock(root / LOCK)
  try:
    res = _guard_ok(tmp_path, None)
  finally:
    _release(holder)
  assert res.returncode == 1
  assert "lock" in res.stderr
  # Holder died: the kernel released the lock, so a new server may start.
  res = _guard_ok(tmp_path, None)
  assert res.returncode == 0, res.stderr


def test_lock_kept_across_postgres_reentry(tmp_path: Path) -> None:
  """Re-running ``exec 9>>`` would close the inherited fd and drop the lock."""
  root = tmp_path / "root"
  body = (
    "hpc_guard_data_dir || exit 1\n"
    "hpc_guard_data_dir || exit 1\n"  # the gosu re-entry runs it again
    f"if flock -n '{root / LOCK}' true; then echo LOCK_FREE; "
    "else echo LOCK_HELD; fi\n"
  )
  res = _pg18(tmp_path, body, None)
  assert res.returncode == 0, res.stderr
  assert "LOCK_HELD" in res.stdout


def test_lock_file_uncreatable_refuses(tmp_path: Path) -> None:
  if os.geteuid() == 0:
    pytest.skip("root ignores directory permissions")
  root = tmp_path / "root"
  root.mkdir()
  root.chmod(0o555)
  try:
    res = _guard_ok(tmp_path, None)
  finally:
    root.chmod(0o755)
  assert res.returncode == 1
  assert "lock file" in res.stderr and "NFS" in res.stderr


def _pg15(tmp_path: Path) -> subprocess.CompletedProcess[str]:
  """Run the PG15 wrapper with PGDATA at ``tmp_path/data``.

  Args:
    tmp_path (Path): Scratch dir; ``bin/docker-entrypoint.sh`` is a stub
      that reports whether the lock is still held after the exec.

  Returns:
    subprocess.CompletedProcess[str]: The ``/bin/sh`` run.
  """
  data = tmp_path / "data"
  bindir = tmp_path / "bin"
  bindir.mkdir(exist_ok=True)
  stub = bindir / "docker-entrypoint.sh"
  stub.write_text(
    "#!/bin/sh\n"
    'echo "HUB_ENTRY $*"\n'
    f"if flock -n '{data / LOCK}' true; then echo LOCK_FREE; "
    "else echo LOCK_HELD; fi\n"
  )
  stub.chmod(0o755)
  env = {
    **os.environ,
    "PATH": f"{bindir}:{os.environ['PATH']}",
    "PGDATA": str(data),
  }
  return subprocess.run(
    ["/bin/sh", str(PG15_GUARD), "postgres", "-c", "x=1"],
    env=env,
    capture_output=True,
    text=True,
    check=False,
    timeout=30,
  )


def test_pg15_guard_execs_hub_entrypoint_holding_lock(tmp_path: Path) -> None:
  _write(tmp_path / "data" / "PG_VERSION", "15\n")
  res = _pg15(tmp_path)
  assert res.returncode == 0, res.stderr
  assert "HUB_ENTRY postgres -c x=1" in res.stdout
  assert "LOCK_HELD" in res.stdout


def test_pg15_guard_ignores_postmaster_pid(tmp_path: Path) -> None:
  """A stale postmaster.pid after a crash is PostgreSQL's to handle."""
  _write(tmp_path / "data" / "PG_VERSION", "15\n")
  _write(tmp_path / "data" / "postmaster.pid", "1\n")
  assert _pg15(tmp_path).returncode == 0


@pytest.mark.parametrize("content", [None, "18\n", "14\n", ""])
def test_pg15_guard_refuses_missing_or_wrong_pg_version(
  tmp_path: Path, content: str | None
) -> None:
  data = tmp_path / "data"
  data.mkdir()
  if content is not None:
    _write(data / "PG_VERSION", content)
  before = _tree(data)
  res = _pg15(tmp_path)
  assert res.returncode == 1
  assert "HUB_ENTRY" not in res.stdout
  assert "PG_VERSION" in res.stderr
  assert _tree(data) == before


@pytest.mark.parametrize("name", [MARKER, SENTINEL, "18", RETIRED])
def test_pg15_guard_refuses_pg18_or_retired_bind(
  tmp_path: Path, name: str
) -> None:
  data = tmp_path / "data"
  _write(data / "PG_VERSION", "15\n")
  if name == "18":
    (data / "18").mkdir()
  else:
    _write(data / name, "x")
  res = _pg15(tmp_path)
  assert res.returncode == 1
  assert name in res.stderr
  assert "HUB_ENTRY" not in res.stdout


def test_pg15_guard_refuses_held_lock(tmp_path: Path) -> None:
  data = tmp_path / "data"
  _write(data / "PG_VERSION", "15\n")
  holder = _hold_lock(data / LOCK)
  try:
    res = _pg15(tmp_path)
  finally:
    _release(holder)
  assert res.returncode == 1
  assert "lock" in res.stderr
  assert "HUB_ENTRY" not in res.stdout
