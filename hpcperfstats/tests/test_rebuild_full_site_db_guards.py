"""rebuild_full_site.sh db guards, run end to end against fake podman tools.

The real script and its libs are copied into a temp tree next to a fixture
docker-compose.yaml; ``podman``, ``podman-compose``, ``flock`` and ``sleep``
are shell stubs on PATH that log every call and answer from a state dir.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
PROJECT = "hpcperfstats"
HUB_PG15 = "docker.io/timescale/timescaledb:2.28.3-pg15"
PG18_IMAGE = "hpcperfstats-db:guard1"
LEGACY = "docker-compose.pg15-primary.yaml"

FAKE_PODMAN = r"""#!/bin/bash
echo "podman $*" >> "$FAKE_LOG"
S=$FAKE_STATE
cmd=$1
shift
case $cmd in
  info) echo "${FAKE_ROOTLESS:-false}" ;;
  unshare) exec "$@" ;;
  inspect)
    fmt=
    name=
    while [ $# -gt 0 ]; do
      case $1 in
        --format) fmt=$2; shift 2 ;;
        *) name=$1; shift ;;
      esac
    done
    case $fmt in
      *State.Running*)
        [ -f "$S/running/$name" ] || exit 1
        cat "$S/running/$name" ;;
      *'if eq .Type'*) : ;;
      *Mounts*) cat "$S/mounts/$name" 2>/dev/null || true ;;
      *) echo "$name: summary" ;;
    esac ;;
  stop)
    [ "$1" = -t ] && shift 2
    for n in "$@"; do
      case " ${FAKE_STOP_IGNORE:-} " in
        *" $n "*) ;;
        *) [ -f "$S/running/$n" ] && echo false > "$S/running/$n" ;;
      esac
    done ;;
  ps)
    case "$*" in
      *ImageID*) cat "$S/imageids" 2>/dev/null || true ;;
      *label=*) : ;;
      *'{{.Names}}'*) cat "$S/all" 2>/dev/null || true ;;
    esac ;;
  container) [ -f "$S/running/$2" ]; exit ;;
  image)
    case $1 in
      exists)
        [ "$2" != localhost/hpcperfstats-db:latest ] || [ -f "$S/old_latest" ]
        exit ;;
      inspect) echo sha256:0ldimage ;;
    esac ;;
  volume)
    case $1 in
      inspect)
        name=$2
        [ "$name" = --format ] && name=$4
        dev=$(cat "$S/voldev/$name" 2>/dev/null)
        case "$*" in
          *'"o"'*) echo "$dev|bind" ;;
          *Mountpoint*) echo /var/lib/containers/x ;;
          *) echo "$dev" ;;
        esac ;;
    esac ;;
  exec)
    case "$*" in
      *'SHOW io_method'*) echo worker ;;
    esac ;;
esac
exit 0
"""

FAKE_COMPOSE = r"""#!/bin/bash
echo "compose $*" >> "$FAKE_LOG"
case " $* " in
  *" config --services "*) printf '%s\n' $FAKE_SERVICES ;;
  *" config "*) cat "$FAKE_CONFIG" ;;
esac
exit 0
"""

FAKE_FLOCK = r"""#!/bin/bash
echo "flock $*" >> "$FAKE_LOG"
case " ${FAKE_LOCKED:-} " in *" $2 "*) exit 1 ;; esac
exit 0
"""

ALL_SERVICES = "web pipeline proxy db rabbitmq valkey db_pg18"


@dataclass
class Site:
  """One temp checkout with fake tools, data binds and podman state."""

  root: Path
  bin: Path
  state: Path
  log: Path
  pg15: Path
  pg18: Path
  env: dict[str, str]

  def write_site(self, *, legacy: bool, extra: str = "") -> None:
    include = ["docker-compose.defaults.yaml"] + ([LEGACY] if legacy else [])
    lines = ["name: hpcperfstats", "include:"]
    lines += [f"  - {path}" for path in include]
    lines += ["services:", "  db: {}", extra]
    (self.root / "docker-compose.yaml").write_text("\n".join(lines) + "\n")

  def write_merged(self, services: dict, volumes: dict | None = None) -> None:
    if volumes is None:
      volumes = {
        "postgres_data": _bind(self.pg15),
        "postgres_data_pg18": _bind(self.pg18),
      }
    cfg = {"name": PROJECT, "services": services, "volumes": volumes}
    (self.root / "merged.yaml").write_text(yaml.safe_dump(cfg))
    for key, vol in volumes.items():
      dev = (vol.get("driver_opts") or {}).get("device", "")
      (self.state / "voldev" / f"{PROJECT}_{key}").write_text(dev)

  def set_running(self, name: str, running: bool = True) -> None:
    (self.state / "running" / name).write_text("true" if running else "false")

  def add_container(self, name: str, mounts: list[str], running: bool) -> None:
    with (self.state / "all").open("a") as fh:
      fh.write(name + "\n")
    (self.state / "mounts" / name).write_text("".join(m + "\n" for m in mounts))
    self.set_running(name, running)

  def run(self, *args: str, **env: str) -> tuple[int, str, str]:
    proc = subprocess.run(
      ["bash", str(self.root / "scripts" / "rebuild_full_site.sh"), *args],
      capture_output=True,
      text=True,
      env={**self.env, **env},
      cwd=self.root,
      timeout=120,
      check=False,
    )
    log = self.log.read_text() if self.log.exists() else ""
    return proc.returncode, proc.stdout + proc.stderr, log


def _bind(device: Path) -> dict:
  return {
    "driver": "local",
    "driver_opts": {"type": "none", "o": "bind", "device": str(device)},
  }


def _pg15_db(volumes: list[str] | None = None) -> dict:
  return {
    "image": HUB_PG15,
    "volumes": volumes
    or [
      "postgres_data:/var/lib/postgresql/data/",
      "postgres_runtime:/run/postgresql",
      "./services-conf/postgres_pg_hba.conf:/etc/postgresql/pg_hba.conf:ro",
      "./services-conf/db-docker-pg15-guard.sh:"
      "/usr/local/bin/hpc-pg15-guard.sh:ro",
    ],
  }


def _pg18_db(volumes: list[str] | None = None) -> dict:
  return {
    "image": PG18_IMAGE,
    "volumes": volumes
    or [
      "postgres_data_pg18:/var/lib/postgresql",
      "./services-conf/postgres_pg_hba.conf:/etc/postgresql/pg_hba.conf:ro",
    ],
  }


def _app_services() -> dict:
  return {
    svc: {"image": "hpcperfstats"}
    for svc in ("web", "pipeline", "proxy", "rabbitmq", "valkey")
  }


def _legacy_services() -> dict:
  return {**_app_services(), "db": _pg15_db(), "db_pg18": _pg18_db()}


def _new_install_services() -> dict:
  db = _pg18_db()
  db["volumes"] = db["volumes"] + ["postgres_runtime:/run/postgresql"]
  return {**_app_services(), "db": db}


@pytest.fixture
def site(tmp_path: Path) -> Site:
  root = tmp_path / "checkout"
  (root / "scripts").mkdir(parents=True)
  shutil.copy(REPO / "scripts" / "rebuild_full_site.sh", root / "scripts")
  shutil.copytree(REPO / "scripts" / "lib", root / "scripts" / "lib")
  (root / "services-conf").mkdir()
  (root / "docker-compose.defaults.yaml").write_text("services: {}\n")
  bindir = tmp_path / "bin"
  bindir.mkdir()
  # The script needs a python3 that imports PyYAML; the web image's PATH
  # python3 does not, so pin the interpreter running this test.
  (bindir / "python3").symlink_to(sys.executable)
  for name, body in (
    ("podman", FAKE_PODMAN),
    ("podman-compose", FAKE_COMPOSE),
    ("flock", FAKE_FLOCK),
    ("sleep", "#!/bin/sh\nexit 0\n"),
  ):
    (bindir / name).write_text(body)
    (bindir / name).chmod(0o755)
  state = tmp_path / "state"
  for sub in ("running", "mounts", "voldev"):
    (state / sub).mkdir(parents=True)
  pg15 = tmp_path / "db" / "pg15"
  pg18 = tmp_path / "db" / "pg18"
  pg15.mkdir(parents=True)
  pg18.mkdir(parents=True)
  log = tmp_path / "calls.log"
  env = {
    "PATH": f"{bindir}:/usr/bin:/bin",
    "USER": os.environ.get("USER", "tester"),
    "HOME": str(tmp_path),
    "TMPDIR": str(tmp_path),
    "HPCPERFSTATS_COMPOSE_PROJECT": PROJECT,
    "HPCPERFSTATS_GIT_COMMIT": "test",
    "HPCPERFSTATS_STACK_VERIFY_WAIT_TIMEOUT": "4",
    "FAKE_LOG": str(log),
    "FAKE_STATE": str(state),
    "FAKE_CONFIG": str(root / "merged.yaml"),
    "FAKE_SERVICES": ALL_SERVICES,
  }
  s = Site(root, bindir, state, log, pg15, pg18, env)
  for svc in ("web", "pipeline", "proxy", "db", "rabbitmq", "valkey"):
    s.set_running(f"{PROJECT}_{svc}_1")
  return s


def _legacy_site(site: Site) -> Site:
  site.write_site(legacy=True)
  site.write_merged(_legacy_services())
  (site.pg15 / "PG_VERSION").write_text("15\n")
  (site.pg18 / "18" / "docker").mkdir(parents=True)
  (site.pg18 / "18" / "docker" / "PG_VERSION").write_text("18\n")
  return site


def _assert_untouched(log: str) -> None:
  """Preflight refusals run before any build, supervisor shutdown or stop."""
  for word in (" build", " down", " stop", " exec", " up ", " rmi", " tag "):
    assert word not in log, (word, log)


def _refuses(site: Site, dry_run: bool, needle: str) -> str:
  rc, out, log = site.run(*(["--dry-run"] if dry_run else []))
  assert rc == 1, out
  assert "refusing: " in out and needle in out, out
  assert "nothing was built or stopped" in out
  _assert_untouched(log)
  return log


DRY = pytest.mark.parametrize("dry_run", [False, True], ids=["run", "dry"])


@DRY
def test_refuses_site_naming_deleted_pg18_primary_overlay(
  site: Site, dry_run: bool
) -> None:
  _legacy_site(site)
  site.write_site(legacy=True)
  text = (site.root / "docker-compose.yaml").read_text()
  text = text.replace(LEGACY, "docker-compose.pg18-primary.yaml")
  (site.root / "docker-compose.yaml").write_text(text)
  log = _refuses(site, dry_run, "deleted docker-compose.pg18-primary.yaml")
  assert "config" not in log


@DRY
def test_comment_naming_deleted_overlay_is_not_refused(
  site: Site, dry_run: bool
) -> None:
  _legacy_site(site)
  site.write_site(legacy=True, extra="# was docker-compose.pg18-primary.yaml")
  rc, out, _ = site.run(*(["--dry-run"] if dry_run else []))
  assert rc == 0, out


@DRY
def test_refuses_site_file_defining_db_pg18(site: Site, dry_run: bool) -> None:
  _legacy_site(site)
  site.write_site(legacy=True, extra="  db_pg18: !reset {}")
  _refuses(site, dry_run, "defines a db_pg18: service")


@DRY
def test_refuses_unpromoted_pg18_copy_without_legacy_include(
  site: Site, dry_run: bool
) -> None:
  _legacy_site(site)
  (site.pg15 / "hpcperfstats-retired").write_text("")
  site.write_site(legacy=False)
  site.write_merged(_new_install_services())
  _refuses(site, dry_run, "without the hpcperfstats-primary marker")


@DRY
def test_refuses_live_pg15_bind_without_legacy_include(
  site: Site, dry_run: bool
) -> None:
  _legacy_site(site)
  (site.pg18 / "hpcperfstats-primary").write_text("{}")
  site.write_site(legacy=False)
  site.write_merged(_new_install_services())
  _refuses(site, dry_run, "holds the live PG15 cluster")


@DRY
def test_refuses_legacy_include_without_pg15_cluster(
  site: Site, dry_run: bool
) -> None:
  _legacy_site(site)
  (site.pg15 / "PG_VERSION").write_text("18\n")
  _refuses(site, dry_run, "PG_VERSION '18'")


@DRY
def test_refuses_legacy_include_without_postgres_data_bind(
  site: Site, dry_run: bool
) -> None:
  _legacy_site(site)
  site.write_merged(
    _legacy_services(), {"postgres_data_pg18": _bind(site.pg18)}
  )
  _refuses(
    site, dry_run, "not an o=bind volume whose device holds PG_VERSION 15"
  )


@DRY
def test_refuses_missing_pg18_bind(site: Site, dry_run: bool) -> None:
  site.write_site(legacy=False)
  site.write_merged(
    _new_install_services(),
    {"postgres_data_pg18": {"driver": "local"}},
  )
  _refuses(site, dry_run, "postgres_data_pg18 is not an o=bind volume")


@pytest.mark.parametrize(
  ("services", "needle"),
  [
    (
      {"db": _pg18_db(["postgres_data:/var/lib/postgresql/data/"])},
      "db (hpcperfstats-db:guard1) mounts postgres_data",
    ),
    (
      {"db": _pg18_db(["other:/var/lib/postgresql/data"])},
      "PG15 data in a PG18 image",
    ),
    (
      {"db_pg18": _pg15_db(["postgres_data_pg18:/var/lib/postgresql"])},
      "db_pg18 (docker.io/timescale/timescaledb:2.28.3-pg15) mounts",
    ),
    (
      {"db": _pg15_db(["postgres_data:/var/lib/postgresql/data/"])},
      "PG15 db lacks the db-docker-pg15-guard.sh mount",
    ),
  ],
  ids=[
    "pg18-on-pg15-volume",
    "pg18-on-pg15-path",
    "pg15-on-pg18",
    "pg15-mounts",
  ],
)
@DRY
def test_refuses_merged_wiring_violations(
  site: Site, dry_run: bool, services: dict, needle: str
) -> None:
  _legacy_site(site)
  site.write_merged({**_legacy_services(), **services})
  rc, out, log = site.run(*(["--dry-run"] if dry_run else []))
  assert rc == 1, out
  assert needle in out, out
  assert "wires a db service to the wrong data" in out
  _assert_untouched(log)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 dirs")
@DRY
def test_unreadable_bind_counts_as_refuse(site: Site, dry_run: bool) -> None:
  _legacy_site(site)
  site.write_site(legacy=False)
  site.write_merged(_new_install_services())
  locked = site.pg18 / "18"
  locked.chmod(0)
  try:
    _refuses(site, dry_run, f"cannot read {locked}/docker/PG_VERSION")
  finally:
    locked.chmod(0o755)


def test_rootless_reads_bind_files_through_podman_unshare(site: Site) -> None:
  _legacy_site(site)
  rc, out, log = site.run("--dry-run", FAKE_ROOTLESS="true")
  assert rc == 0, out
  assert "podman info --format {{.Host.Security.Rootless}}" in log
  assert f"podman unshare cat {site.pg15}/PG_VERSION" in log
  assert "podman unshare sh -c" in log


def test_rootful_reads_bind_files_directly(site: Site) -> None:
  _legacy_site(site)
  rc, out, log = site.run("--dry-run")
  assert rc == 0, out
  assert "unshare" not in log


def test_new_install_with_primary_marker_passes(site: Site) -> None:
  site.write_site(legacy=False)
  site.write_merged(_new_install_services())
  (site.pg18 / "18" / "docker").mkdir(parents=True)
  (site.pg18 / "18" / "docker" / "PG_VERSION").write_text("18\n")
  (site.pg18 / "hpcperfstats-primary").write_text("{}")
  rc, out, _ = site.run("--dry-run")
  assert rc == 0, out


def _calls(log: str, needle: str) -> list[int]:
  return [i for i, line in enumerate(log.splitlines()) if needle in line]


def test_full_rebuild_stops_every_db_bind_container_before_up(
  site: Site,
) -> None:
  """The scan covers other projects' containers and both mount kinds."""
  _legacy_site(site)
  site.add_container(
    "hpcperfstats-dev_db_1", [f"bind||{site.pg15}"], running=True
  )
  (site.state / "voldev" / "dev_pg18").write_text(str(site.pg18))
  site.add_container(
    "stray_pg18_copy",
    ["volume|dev_pg18|/var/lib/containers/storage/volumes/dev_pg18/_data"],
    running=True,
  )
  site.add_container("old_stopped", [f"bind||{site.pg18}/18"], running=False)
  site.add_container("unrelated", ["bind||/srv/other"], running=True)
  rc, out, log = site.run()
  assert rc == 0, out
  down = _calls(log, "down --remove-orphans")
  stops = _calls(log, "podman stop -t 1800")
  up = _calls(log, "compose --project-name hpcperfstats up -d --no-build")
  assert down and up and len(stops) == 2, log
  assert down[0] < min(stops) and max(stops) < up[0]
  assert "podman stop -t 1800 hpcperfstats-dev_db_1" in log
  assert "podman stop -t 1800 stray_pg18_copy" in log
  assert "stop -t 1800 old_stopped" not in log
  assert "unrelated" not in log.replace("podman inspect unrelated", "")
  assert f"flock -n {site.pg15}/.hpc-server.lock true" not in log
  assert "podman ps -a --format {{.Names}}" in log


def test_scan_refuses_up_when_a_db_container_keeps_running(site: Site) -> None:
  _legacy_site(site)
  site.add_container("other_db", [f"bind||{site.pg15}"], running=True)
  rc, out, log = site.run(FAKE_STOP_IGNORE="other_db")
  assert rc == 1, out
  assert "other_db mounts a db data bind and is still running" in out
  assert "stack left down" in out
  assert "down --remove-orphans" in log
  assert " up -d" not in log


def test_scan_refuses_up_when_a_lock_file_is_held(site: Site) -> None:
  _legacy_site(site)
  lock = site.pg18 / ".hpc-server.lock"
  lock.write_text("")
  rc, out, log = site.run(FAKE_LOCKED=str(lock))
  assert rc == 1, out
  assert f"{lock} is held" in out
  assert " up -d" not in log


def test_scan_checks_locks_through_unshare_when_rootless(site: Site) -> None:
  _legacy_site(site)
  (site.pg15 / ".hpc-server.lock").write_text("")
  rc, out, log = site.run(FAKE_ROOTLESS="true")
  assert rc == 0, out
  assert f"podman unshare flock -n {site.pg15}/.hpc-server.lock true" in log


def test_db_pg18_restarted_when_it_was_running(site: Site) -> None:
  _legacy_site(site)
  site.set_running(f"{PROJECT}_db_pg18_1")
  rc, out, log = site.run()
  assert rc == 0, out
  restart = _calls(log, "--profile pg18-migrate up -d --no-build db_pg18")
  assert restart and restart[0] > _calls(log, " up -d --no-build")[0]
  assert "pg_isready -U hpcperfstats" in log
  assert "io_method=worker" in out
  assert "copy" not in "".join(
    line for line in log.splitlines() if " up " in line
  )


def test_db_pg18_not_started_when_it_was_not_running(site: Site) -> None:
  _legacy_site(site)
  rc, out, log = site.run()
  assert rc == 0, out
  assert "--profile pg18-migrate up" not in log


def test_db_pg18_not_started_when_no_longer_defined(site: Site) -> None:
  _legacy_site(site)
  site.set_running(f"{PROJECT}_db_pg18_1")
  rc, out, log = site.run(FAKE_SERVICES="web pipeline proxy db rabbitmq valkey")
  assert rc == 0, out
  assert "--profile pg18-migrate up" not in log
  assert "no longer defined" in out


def test_builds_guard1_and_force_retags_aliases(site: Site) -> None:
  _legacy_site(site)
  rc, out, log = site.run("--build-only")
  assert rc == 0, out
  assert "-t hpcperfstats-db:guard1" in log
  assert "podman tag hpcperfstats-db:guard1 hpcperfstats_db_pg18:latest" in log
  assert "podman tag hpcperfstats:latest hpcperfstats_web:latest" in log
  assert "rmi" not in log


def test_unused_unguarded_latest_image_is_removed(site: Site) -> None:
  _legacy_site(site)
  (site.state / "old_latest").write_text("")
  (site.state / "imageids").write_text("sha256:otherimage\n")
  rc, out, log = site.run("--build-only")
  assert rc == 0, out
  assert "podman rmi localhost/hpcperfstats-db:latest" in log


def test_used_unguarded_latest_image_is_pointed_at_guard1(site: Site) -> None:
  _legacy_site(site)
  (site.state / "old_latest").write_text("")
  (site.state / "imageids").write_text("sha256:0ldimage\n")
  rc, out, log = site.run("--build-only")
  assert rc == 0, out
  assert (
    "podman tag hpcperfstats-db:guard1 localhost/hpcperfstats-db:latest" in log
  )
  assert "rmi" not in log


def test_dry_run_builds_and_stops_nothing(site: Site) -> None:
  _legacy_site(site)
  site.add_container("other_db", [f"bind||{site.pg15}"], running=True)
  site.set_running(f"{PROJECT}_db_pg18_1")
  rc, out, log = site.run("--dry-run")
  assert rc == 0, out
  _assert_untouched(log)


def test_bind_guard_stops_db_offenders_with_long_timeout(
  tmp_path: Path,
) -> None:
  """A wrong-mount db was stopped at podman's default timeout (SIGKILL)."""
  log = tmp_path / "calls.log"
  dev = tmp_path / "pg15"
  dev.mkdir()
  prog = (
    "set -euo pipefail\n"
    f"source {REPO}/scripts/lib/compose_bind_volume_guard.sh\n"
    "HPCPERFSTATS_COMPOSE_PROJECT=hpcperfstats\n"
    "PODMAN=(fake_podman)\n"
    "compose_bind_volume_specs() {\n"
    f"  printf 'hpcperfstats_postgres_data\\t{dev}\\n'\n"
    "}\n"
    "fake_podman() {\n"
    '  echo "$*" >> "$FAKE_LOG"\n'
    '  case "$1" in\n'
    "    ps) printf '%s\\n' hpcperfstats_db_1 hpcperfstats_web_1 ;;\n"
    "    inspect) echo 'hpcperfstats_postgres_data|/var/lib/postgresql/data' ;;\n"
    "    exec) echo 1:2 ;;\n"
    "  esac\n"
    "}\n"
    "compose_bind_volumes_verify_mounted\n"
  )
  proc = subprocess.run(
    ["bash", "-c", prog],
    capture_output=True,
    text=True,
    env={"FAKE_LOG": str(log), "PATH": "/usr/bin:/bin"},
    check=False,
  )
  assert proc.returncode == 1, proc.stderr
  calls = log.read_text().splitlines()
  assert "stop -t 1800 hpcperfstats_db_1" in calls
  assert "stop hpcperfstats_pipeline_1 hpcperfstats_web_1" in calls
