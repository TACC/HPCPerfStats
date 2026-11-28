"""scripts/pg18_write_primary_marker.sh against a fake podman and temp binds.

The fake ``podman`` answers ``info``, ``inspect``, ``exec`` and ``unshare``
from a JSON state file; ``podman-compose config`` prints a fixture config
whose bind volumes point at temp PG15 / PG18 / hpcperfstatsdata dirs. Every
refusal must name its reason, exit non-zero and leave every file as it was.
"""

from __future__ import annotations

import fcntl
import json
import os
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pg18_write_primary_marker.sh"
P = "hpcperfstats"
T0 = 1_790_000_000  # pg15_readonly_ok time; everything else is relative
PG18_ID = "7412345678901234567"
PG15_ID = "-7212345678901234567"
OID = 16384
WRITES = 5000

_FAKE_PODMAN = r"""
import json, os, re, subprocess, sys
args = sys.argv[1:]
state = json.load(open(os.environ["FAKE_PODMAN_STATE"]))
with open(os.environ["FAKE_PODMAN_LOG"], "a") as fh:
  fh.write(" ".join(args) + "\n")
cmd = args[0]
ZERO = -62135596800

def render(fmt, c):
  def one(m):
    field, attr = m.group(1), m.group(2)
    if field == "Running":
      return "true" if c["running"] else "false"
    t = c[{"StartedAt": "started", "FinishedAt": "finished"}[field]]
    if attr == "IsZero":
      return "true" if t is None else "false"
    if attr == "Unix":
      return str(ZERO if t is None else t[0])
    return str(0 if t is None else t[1])
  return re.sub(r"\{\{\s*\.State\.(\w+)(?:\.(\w+))?\s*\}\}", one, fmt)

if cmd == "info":
  print(state["rootless"])
elif cmd == "inspect":
  fmt = args[args.index("--format") + 1]
  name = args[-1]
  c = state["containers"].get(name)
  if c is None:
    print(f"Error: no such object: {name}", file=sys.stderr)
    sys.exit(125)
  print(render(fmt, c))
elif cmd == "exec":
  rest = args[1:]
  while rest[0].startswith("-"):
    rest = rest[1:]
  name, rest = rest[0], rest[1:]
  c = state["containers"].get(name)
  if c is None or not c["running"]:
    print(f"Error: {name} is not running", file=sys.stderr)
    sys.exit(125)
  if rest[0] == "psql":
    for key, out in state["sql"].get(name, []):
      if key in rest[-1]:
        print(out)
        sys.exit(0)
    sys.exit(2)
  mount = state["mounts"][name]
  rest = [a.replace("/var/lib/postgresql", mount, 1) for a in rest]
  sys.exit(subprocess.run(rest).returncode)
else:
  print(f"fake podman: unsupported {args}", file=sys.stderr)
  sys.exit(64)
"""

_FAKE_COMPOSE = r"""
import os, sys
assert "config" in sys.argv[1:], sys.argv
sys.stdout.write(open(os.environ["FAKE_COMPOSE_CONFIG"]).read())
"""


def _pg_control(path: Path, sysid: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_bytes(struct.pack("=q", int(sysid)) + b"\0" * 200)


def _ctr(
  running: bool, started: int | None, finished: int | None
) -> dict[str, object]:
  return {
    "running": running,
    "started": None if started is None else [started, 250_000_000],
    "finished": None if finished is None else [finished, 500_000_000],
  }


class Site:
  """Temp binds, stamps, fake podman state and a runner for the script."""

  def __init__(self, tmp_path: Path) -> None:
    self.tmp = tmp_path
    self.pg15 = tmp_path / "pg15"
    self.pg18 = tmp_path / "pg18"
    self.data = tmp_path / "data"
    self.bin = tmp_path / "bin"
    self.log = tmp_path / "podman.log"
    self.state_path = tmp_path / "podman_state.json"
    self.held: list = []
    for d in (self.pg15, self.pg18, self.data / "pg18_migrate", self.bin):
      d.mkdir(parents=True)
    # The script needs a python3 that imports PyYAML; the web image's PATH
    # python3 does not, so pin the interpreter running this test.
    (self.bin / "python3").symlink_to(sys.executable)
    (self.pg15 / "PG_VERSION").write_text("15\n")
    _pg_control(self.pg15 / "global" / "pg_control", PG15_ID)
    (self.pg15 / ".hpc-server.lock").write_text("")
    pgdata = self.pg18 / "18" / "docker"
    pgdata.mkdir(parents=True)
    (pgdata / "PG_VERSION").write_text("18\n")
    _pg_control(pgdata / "global" / "pg_control", PG18_ID)
    (pgdata / "base" / str(OID)).mkdir(parents=True)
    (self.pg18 / ".hpc-server.lock").write_text("")
    (self.pg18 / "hpcperfstats-migrate-target").write_text("migrate-target\n")
    (self.data / "pg18_migrate" / "state.json").write_text("{}")
    self.stamp("pg15_readonly_ok", T0, pg15_writes=WRITES)
    self.stamp("verify_ok.json", T0 + 100, verified_rows=10)
    self.stamp("relational_copy_ok", T0 + 200)
    self.stamp("pg15_writes_ok", T0 + 300, pg15_writes=WRITES)
    self.state: dict = {
      "rootless": "true",
      "containers": {
        f"{P}_web_1": _ctr(False, T0 - 5000, T0 + 150),
        f"{P}_proxy_1": _ctr(False, T0 - 5000, T0 + 150),
        f"{P}_pipeline_1": _ctr(False, T0 - 5000, T0 + 150),
        f"{P}_db_1": _ctr(False, T0 - 1000, T0 + 400),
        f"{P}_db_pg18_1": _ctr(False, T0 - 2000, T0 + 400),
      },
      "mounts": {f"{P}_db_pg18_1": str(self.pg18)},
      "sql": {
        f"{P}_db_1": [
          [
            "system_identifier",
            f"on|/var/lib/postgresql/data/postgresql.auto.conf|{PG15_ID}|{WRITES}",
          ]
        ],
        f"{P}_db_pg18_1": [["system_identifier", f"{PG18_ID}|{OID}"]],
      },
    }
    for name, body in (
      ("fake_podman.py", _FAKE_PODMAN),
      ("podman-compose", _FAKE_COMPOSE),
    ):
      exe = self.bin / name
      exe.write_text(f"#!{sys.executable} -IS\n{body}", encoding="utf-8")
      exe.chmod(0o755)
    # unshare is the hot path (every host file op); skip a Python start for it.
    podman = self.bin / "podman"
    podman.write_text(
      "#!/bin/bash\n"
      'if [[ "$1" == unshare ]]; then\n'
      '  printf "%s\\n" "$*" >> "$FAKE_PODMAN_LOG"\n'
      "  shift\n"
      '  exec "$@"\n'
      "fi\n"
      f'exec "{self.bin}/fake_podman.py" "$@"\n',
      encoding="utf-8",
    )
    podman.chmod(0o755)
    config = tmp_path / "compose_config.yaml"
    lines = ["services: {}", "volumes:"]
    for key, dev in (
      ("postgres_data", self.pg15),
      ("postgres_data_pg18", self.pg18),
      ("hpcperfstatsdata", self.data),
    ):
      lines += [
        f"  {key}:",
        "    driver: local",
        "    driver_opts:",
        "      type: none",
        "      o: bind",
        f"      device: {dev}",
      ]
    config.write_text("\n".join(lines) + "\n")
    self.config = config

  def stamp_path(self, name: str) -> Path:
    if name == "verify_ok.json":
      return self.data / "pg18_migrate" / name
    return self.pg18 / name

  def stamp(self, name: str, epoch: int, **extra: object) -> None:
    doc = {
      "stamp": name,
      "written_at": "2026-09-21T13:33:20Z",
      "written_at_epoch": epoch,
      "pg18_system_identifier": PG18_ID,
      "pg18_database_oid": OID,
      "pg15_system_identifier": PG15_ID,
      **extra,
    }
    self.stamp_path(name).write_text(json.dumps(doc) + "\n")

  def edit_stamp(self, name: str, **changes: object) -> None:
    path = self.stamp_path(name)
    doc = json.loads(path.read_text())
    doc.update(changes)
    path.write_text(json.dumps(doc) + "\n")

  def ctr(self, short: str, **changes: object) -> None:
    c = self.state["containers"][f"{P}_{short}_1"]
    for key in ("started", "finished"):
      if key in changes and changes[key] is not None:
        changes[key] = [changes[key], 0]
    c.update(changes)

  def hold_lock(self, path: Path) -> None:
    fh = open(path, "a")  # closed by the site fixture teardown
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    self.held.append(fh)

  def snapshot(self) -> dict[str, tuple[bytes, int]]:
    out: dict[str, tuple[bytes, int]] = {}
    for base in (self.pg15, self.pg18, self.data):
      for p in sorted(base.rglob("*")):
        key = str(p.relative_to(self.tmp))
        out[key] = (b"<dir>" if p.is_dir() else p.read_bytes(), 0)
        if p.is_file():
          out[key] = (p.read_bytes(), p.stat().st_mtime_ns)
    return out

  def run(self, *args: str) -> subprocess.CompletedProcess[str]:
    self.state_path.write_text(json.dumps(self.state))
    env = {
      "PATH": f"{self.bin}:/usr/bin:/bin",
      "HOME": str(self.tmp),
      "USER": os.environ.get("USER", "tester"),
      "HPCPERFSTATS_COMPOSE_PROJECT": P,
      "FAKE_PODMAN_STATE": str(self.state_path),
      "FAKE_PODMAN_LOG": str(self.log),
      "FAKE_COMPOSE_CONFIG": str(self.config),
      "LC_ALL": "C",
    }
    return subprocess.run(
      ["bash", str(SCRIPT), *args],
      env=env,
      capture_output=True,
      text=True,
      timeout=120,
      check=False,
    )

  def podman_calls(self) -> list[str]:
    return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def site(tmp_path: Path) -> Site:
  s = Site(tmp_path)
  yield s
  for fh in s.held:
    fh.close()


def _assert_refused(
  site: Site, proc: subprocess.CompletedProcess[str], reason: str, before: dict
) -> None:
  assert proc.returncode != 0, proc.stdout + proc.stderr
  assert "refusing" in proc.stderr
  assert reason in proc.stderr, proc.stderr
  assert site.snapshot() == before


Mutation = Callable[[Site], None]

_MARK_REFUSALS: list[tuple[str, Mutation, str]] = [
  (
    "web-finished-after-relational",
    lambda s: s.ctr("web", finished=T0 + 250),
    f"{P}_web_1 FinishedAt",
  ),
  (
    "proxy-finished-after-relational",
    lambda s: s.ctr("proxy", finished=T0 + 201),
    f"{P}_proxy_1 FinishedAt",
  ),
  (
    "pipeline-finished-after-relational",
    lambda s: s.ctr("pipeline", finished=T0 + 9999),
    f"{P}_pipeline_1 FinishedAt",
  ),
  (
    "web-finished-zero",
    lambda s: s.ctr("web", finished=None),
    f"{P}_web_1 FinishedAt is zero",
  ),
  (
    "proxy-missing",
    lambda s: s.state["containers"].pop(f"{P}_proxy_1"),
    f"{P}_proxy_1 does not exist",
  ),
  (
    "pipeline-running",
    lambda s: s.ctr("pipeline", running=True),
    f"{P}_pipeline_1 is running",
  ),
  (
    "pg15-started-after-readonly",
    lambda s: s.ctr("db", started=T0 + 5),
    f"{P}_db_1 StartedAt",
  ),
  ("pg15-running", lambda s: s.ctr("db", running=True), f"{P}_db_1 is running"),
  (
    "pg15-missing",
    lambda s: s.state["containers"].pop(f"{P}_db_1"),
    f"{P}_db_1 does not exist",
  ),
  (
    "db-pg18-running",
    lambda s: s.ctr("db_pg18", running=True),
    f"{P}_db_pg18_1 is running",
  ),
  (
    "pg18-lock-held",
    lambda s: s.hold_lock(s.pg18 / ".hpc-server.lock"),
    "lock is held",
  ),
  (
    "pg15-lock-held",
    lambda s: s.hold_lock(s.pg15 / ".hpc-server.lock"),
    "lock is held",
  ),
  *[
    (
      f"missing-{name}",
      (lambda n: lambda s: s.stamp_path(n).unlink())(name),
      f"missing stamp {name}",
    )
    for name in (
      "pg15_readonly_ok",
      "verify_ok.json",
      "relational_copy_ok",
      "pg15_writes_ok",
    )
  ],
  (
    "foreign-pg18-id",
    lambda s: s.edit_stamp("verify_ok.json", pg18_system_identifier="1"),
    "verify_ok.json: pg18_system_identifier 1",
  ),
  (
    "foreign-pg15-id",
    lambda s: s.edit_stamp("relational_copy_ok", pg15_system_identifier="2"),
    "relational_copy_ok: pg15_system_identifier 2",
  ),
  (
    "pg18-cluster-replaced",
    lambda s: _pg_control(
      s.pg18 / "18" / "docker" / "global" / "pg_control", "99"
    ),
    "pg18_system_identifier",
  ),
  (
    "verify-before-readonly",
    lambda s: s.edit_stamp("verify_ok.json", written_at_epoch=T0 - 1),
    "pg15_readonly_ok must be older than verify_ok.json",
  ),
  (
    "relational-before-readonly",
    lambda s: s.edit_stamp("relational_copy_ok", written_at_epoch=T0),
    "pg15_readonly_ok must be older than relational_copy_ok",
  ),
  (
    "writes-check-before-readonly",
    lambda s: s.edit_stamp("pg15_writes_ok", written_at_epoch=T0 - 50),
    "pg15_readonly_ok must be older than pg15_writes_ok",
  ),
  (
    "write-counters-moved",
    lambda s: s.edit_stamp("pg15_writes_ok", pg15_writes=WRITES + 1),
    "PG15 write counters moved",
  ),
  (
    "database-oid-differs",
    lambda s: s.edit_stamp("relational_copy_ok", pg18_database_oid=OID + 1),
    "pg18_database_oid",
  ),
  (
    "database-dir-missing",
    lambda s: (s.pg18 / "18" / "docker" / "base" / str(OID)).rmdir(),
    f"base/{OID} missing",
  ),
  (
    "wrong-stamp-name",
    lambda s: s.edit_stamp("pg15_writes_ok", stamp="pg15_readonly_ok"),
    "pg15_writes_ok: stamp name",
  ),
  (
    "pg15-bind-not-pg15",
    lambda s: (s.pg15 / "PG_VERSION").write_text("18\n"),
    "PG_VERSION",
  ),
]


@pytest.mark.parametrize(
  ("mutate", "reason"),
  [pytest.param(m, r, id=i) for i, m, r in _MARK_REFUSALS],
)
@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
def test_marker_refuses_and_changes_nothing(
  site: Site, mutate: Mutation, reason: str, dry_run: bool
) -> None:
  mutate(site)
  before = site.snapshot()
  proc = site.run(*(["--dry-run"] if dry_run else []))
  _assert_refused(site, proc, reason, before)


def test_marker_success_writes_marker_and_retired_and_removes_sentinel(
  site: Site,
) -> None:
  proc = site.run()
  assert proc.returncode == 0, proc.stderr
  marker = json.loads((site.pg18 / "hpcperfstats-primary").read_text())
  retired = json.loads((site.pg15 / "hpcperfstats-retired").read_text())
  for doc, name in (
    (marker, "hpcperfstats-primary"),
    (retired, "hpcperfstats-retired"),
  ):
    assert doc["stamp"] == name
    assert doc["pg18_system_identifier"] == PG18_ID
    assert doc["pg15_system_identifier"] == PG15_ID
    assert isinstance(doc["written_at_epoch"], int) and doc["written_at"]
  assert not (site.pg18 / "hpcperfstats-migrate-target").exists()
  for name in ("pg15_readonly_ok", "relational_copy_ok", "pg15_writes_ok"):
    assert site.stamp_path(name).exists()
  leftovers = [p.name for p in site.tmp.rglob("*.tmp*")]
  assert leftovers == []


def test_marker_dry_run_reports_and_writes_nothing(site: Site) -> None:
  before = site.snapshot()
  proc = site.run("--dry-run")
  assert proc.returncode == 0, proc.stderr
  assert "would write" in proc.stdout and "would remove" in proc.stdout
  assert site.snapshot() == before


def test_rootless_host_file_ops_go_through_podman_unshare(site: Site) -> None:
  assert site.run().returncode == 0
  calls = site.podman_calls()
  for tool in ("od", "flock", "cat", "sh"):
    assert any(c.startswith(f"unshare {tool} ") for c in calls), (tool, calls)


def test_rootful_host_file_ops_run_directly(site: Site) -> None:
  site.state["rootless"] = "false"
  proc = site.run()
  assert proc.returncode == 0, proc.stderr
  assert not any(c.startswith("unshare") for c in site.podman_calls())
  assert (site.pg18 / "hpcperfstats-primary").exists()


def _cut_over(site: Site) -> None:
  """Run the marker, then move to the E1 failure point before ingest."""
  assert site.run().returncode == 0
  marker_t = T0 + 1000
  os.utime(site.pg18 / "hpcperfstats-primary", (marker_t, marker_t))
  site.ctr("db", running=False, started=marker_t + 10)
  site.ctr("web", running=False, started=marker_t + 20)


def test_rollback_restores_pre_cutover_file_state(site: Site) -> None:
  pre = site.snapshot()
  _cut_over(site)
  proc = site.run("--rollback")
  assert proc.returncode == 0, proc.stderr
  after = site.snapshot()
  stamps = {
    str(site.stamp_path(n).relative_to(site.tmp))
    for n in (
      "pg15_readonly_ok",
      "verify_ok.json",
      "relational_copy_ok",
      "pg15_writes_ok",
    )
  }
  sentinel = str(
    (site.pg18 / "hpcperfstats-migrate-target").relative_to(site.tmp)
  )
  expect = {k: v for k, v in pre.items() if k not in stamps | {sentinel}}
  assert {k: v for k, v in after.items() if k != sentinel} == expect
  assert after[sentinel][0] == b"migrate-target\n"


_ROLLBACK_REFUSALS: list[tuple[str, Mutation, str]] = [
  (
    "pipeline-started-on-pg18",
    lambda s: s.ctr("pipeline", started=T0 + 1001),
    f"{P}_pipeline_1 StartedAt",
  ),
  (
    "pipeline-missing",
    lambda s: s.state["containers"].pop(f"{P}_pipeline_1"),
    f"{P}_pipeline_1 does not exist",
  ),
  (
    "web-running",
    lambda s: s.ctr("web", running=True),
    f"{P}_web_1 is running",
  ),
  ("db-running", lambda s: s.ctr("db", running=True), f"{P}_db_1 is running"),
  (
    "no-marker",
    lambda s: (s.pg18 / "hpcperfstats-primary").unlink(),
    "no marker",
  ),
]


@pytest.mark.parametrize(
  ("mutate", "reason"),
  [pytest.param(m, r, id=i) for i, m, r in _ROLLBACK_REFUSALS],
)
def test_rollback_refuses_and_changes_nothing(
  site: Site, mutate: Mutation, reason: str
) -> None:
  _cut_over(site)
  mutate(site)
  before = site.snapshot()
  _assert_refused(site, site.run("--rollback"), reason, before)


def test_rollback_dry_run_changes_nothing(site: Site) -> None:
  _cut_over(site)
  before = site.snapshot()
  proc = site.run("--rollback", "--dry-run")
  assert proc.returncode == 0, proc.stderr
  assert "would remove" in proc.stdout
  assert site.snapshot() == before


def _freeze(site: Site) -> None:
  """D2 state: PG15 db and db_pg18 running, no stamps yet."""
  for name in ("pg15_readonly_ok", "verify_ok.json", "relational_copy_ok"):
    site.stamp_path(name).unlink()
  site.stamp_path("pg15_writes_ok").unlink()
  site.ctr("db", running=True)
  site.ctr("db_pg18", running=True)


def _pg15_answer(site: Site, answer: str) -> None:
  site.state["sql"][f"{P}_db_1"] = [["system_identifier", answer]]


def test_stamp_readonly_writes_stamp_with_counters(site: Site) -> None:
  _freeze(site)
  proc = site.run("--stamp-readonly")
  assert proc.returncode == 0, proc.stderr
  doc = json.loads(site.stamp_path("pg15_readonly_ok").read_text())
  assert doc["stamp"] == "pg15_readonly_ok"
  assert doc["pg15_writes"] == WRITES
  assert doc["pg18_system_identifier"] == PG18_ID
  assert doc["pg18_database_oid"] == OID
  assert doc["pg15_system_identifier"] == PG15_ID
  assert isinstance(doc["written_at_epoch"], int)
  assert [p.name for p in site.pg18.glob("*.tmp*")] == []


_READONLY_REFUSALS: list[tuple[str, Mutation, str]] = [
  (
    "read-only-off",
    lambda s: _pg15_answer(
      s, f"off|/x/postgresql.auto.conf|{PG15_ID}|{WRITES}"
    ),
    "default_transaction_read_only is off",
  ),
  (
    "read-only-not-alter-system",
    lambda s: _pg15_answer(s, f"on||{PG15_ID}|{WRITES}"),
    "postgresql.auto.conf",
  ),
  (
    "counters-unreadable",
    lambda s: _pg15_answer(s, f"on|/x/postgresql.auto.conf|{PG15_ID}|"),
    "pg15_writes",
  ),
  (
    "pg15-stopped",
    lambda s: s.ctr("db", running=False),
    f"{P}_db_1 is not running",
  ),
  (
    "pg18-stopped",
    lambda s: s.ctr("db_pg18", running=False),
    f"{P}_db_pg18_1 is not running",
  ),
]


@pytest.mark.parametrize(
  ("mutate", "reason"),
  [pytest.param(m, r, id=i) for i, m, r in _READONLY_REFUSALS],
)
def test_stamp_readonly_refuses_and_changes_nothing(
  site: Site, mutate: Mutation, reason: str
) -> None:
  _freeze(site)
  site.stamp("pg15_readonly_ok", T0 - 99, pg15_writes=1)  # an earlier attempt
  mutate(site)
  before = site.snapshot()
  _assert_refused(site, site.run("--stamp-readonly"), reason, before)


def _before_switch(site: Site) -> None:
  """E1 step 2: web/proxy/pipeline stopped, PG15 db and db_pg18 running."""
  site.stamp_path("pg15_writes_ok").unlink()
  site.ctr("db", running=True)
  site.ctr("db_pg18", running=True)


def test_check_writes_records_unchanged_counters(site: Site) -> None:
  _before_switch(site)
  proc = site.run("--check-writes")
  assert proc.returncode == 0, proc.stderr
  doc = json.loads(site.stamp_path("pg15_writes_ok").read_text())
  assert doc["stamp"] == "pg15_writes_ok"
  assert doc["pg15_writes"] == WRITES
  assert doc["pg15_system_identifier"] == PG15_ID
  assert doc["written_at_epoch"] > T0


_CHECK_REFUSALS: list[tuple[str, Mutation, str]] = [
  (
    "counters-moved",
    lambda s: _pg15_answer(
      s, f"on|/x/postgresql.auto.conf|{PG15_ID}|{WRITES + 3}"
    ),
    "PG15 write counters moved",
  ),
  (
    "read-only-off",
    lambda s: _pg15_answer(
      s, f"off|/x/postgresql.auto.conf|{PG15_ID}|{WRITES}"
    ),
    "default_transaction_read_only is off",
  ),
  (
    "web-running",
    lambda s: s.ctr("web", running=True),
    f"{P}_web_1 is running",
  ),
  (
    "pipeline-running",
    lambda s: s.ctr("pipeline", running=True),
    f"{P}_pipeline_1 is running",
  ),
  (
    "no-readonly-stamp",
    lambda s: s.stamp_path("pg15_readonly_ok").unlink(),
    "missing stamp pg15_readonly_ok",
  ),
  (
    "foreign-pg15",
    lambda s: s.edit_stamp("pg15_readonly_ok", pg15_system_identifier="5"),
    "pg15_readonly_ok: pg15_system_identifier 5",
  ),
  (
    "pg15-stopped",
    lambda s: s.ctr("db", running=False),
    f"{P}_db_1 is not running",
  ),
]


@pytest.mark.parametrize(
  ("mutate", "reason"),
  [pytest.param(m, r, id=i) for i, m, r in _CHECK_REFUSALS],
)
def test_check_writes_refuses_and_changes_nothing(
  site: Site, mutate: Mutation, reason: str
) -> None:
  _before_switch(site)
  mutate(site)
  before = site.snapshot()
  _assert_refused(site, site.run("--check-writes"), reason, before)


def test_usage_errors() -> None:
  bad = subprocess.run(
    ["bash", str(SCRIPT), "--rollback", "--check-writes"],
    capture_output=True,
    text=True,
    check=False,
  )
  assert bad.returncode == 2 and "Usage:" in bad.stderr
  ok = subprocess.run(
    ["bash", str(SCRIPT), "--help"], capture_output=True, text=True, check=False
  )
  assert ok.returncode == 0 and "--stamp-readonly" in ok.stdout


def test_script_is_tracked_shape() -> None:
  assert os.access(SCRIPT, os.X_OK)
  ignored = subprocess.run(
    ["git", "check-ignore", "-q", str(SCRIPT)], cwd=ROOT, check=False
  )
  assert ignored.returncode == 1
  text = SCRIPT.read_text()
  assert "set -euo pipefail" in text and "<<" not in text
  assert max(len(line) for line in text.splitlines()) <= 100


def test_check_writes_refusal_invalidates_earlier_success(site: Site) -> None:
  """An earlier pg15_writes_ok must not outlive a later counters-moved run.

  Otherwise the marker would accept the old stamp although PG15 took writes
  after it.
  """
  _before_switch(site)
  assert site.run("--check-writes").returncode == 0
  assert site.stamp_path("pg15_writes_ok").is_file()
  _pg15_answer(site, f"on|/x/postgresql.auto.conf|{PG15_ID}|{WRITES + 3}")
  proc = site.run("--check-writes")
  assert proc.returncode == 1
  assert "PG15 write counters moved" in proc.stderr
  assert "removed the earlier pg15_writes_ok" in proc.stderr
  assert not site.stamp_path("pg15_writes_ok").exists()
