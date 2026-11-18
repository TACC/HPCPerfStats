"""Run scripts/rebuild_proxy.sh against fake podman / podman-compose binaries."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rebuild_proxy.sh"

_FAKE_PODMAN = """#!/bin/sh
echo "podman $*" >> "$FAKE_LOG"
case "$*" in
  "inspect --format {{.State.Running}}"*) echo true ;;
  "image inspect"*) echo newimage ;;
  "inspect --format {{.Image}}"*) echo "$FAKE_RUNNING_IMAGE" ;;
esac
exit 0
"""

_FAKE_COMPOSE = """#!/bin/sh
echo "podman-compose $*" >> "$FAKE_LOG"
case "$*" in
  *"config --services"*) printf '%s\\n' $FAKE_SERVICES ;;
esac
exit 0
"""


def _run(
  tmp_path: Path,
  *args: str,
  running_image: str = "newimage",
  services: str = "web pipeline proxy",
) -> tuple[int, str, list[str]]:
  bin_dir = tmp_path / "bin"
  bin_dir.mkdir(exist_ok=True)
  for name, body in (
    ("podman", _FAKE_PODMAN),
    ("podman-compose", _FAKE_COMPOSE),
  ):
    fake = bin_dir / name
    fake.write_text(body)
    fake.chmod(0o755)
  log = tmp_path / "calls.log"
  env = {
    "PATH": f"{bin_dir}:/usr/bin:/bin",
    "USER": os.environ.get("USER", "tester"),
    "HOME": str(tmp_path),
    "TMPDIR": str(tmp_path),
    "FAKE_LOG": str(log),
    "FAKE_RUNNING_IMAGE": running_image,
    "FAKE_SERVICES": services,
    "HPCPERFSTATS_PROXY_START_TIMEOUT": "0",
  }
  proc = subprocess.run(
    ["bash", str(SCRIPT), *args],
    capture_output=True,
    text=True,
    env=env,
    check=False,
  )
  calls = log.read_text().splitlines() if log.exists() else []
  return proc.returncode, proc.stdout + proc.stderr, calls


def test_builds_then_recreates_only_proxy(tmp_path: Path) -> None:
  rc, out, calls = _run(tmp_path)
  assert rc == 0, out
  build = [i for i, c in enumerate(calls) if "proxy.Dockerfile" in c]
  ups = [i for i, c in enumerate(calls) if " up " in c]
  nginx_t = [i for i, c in enumerate(calls) if "proxy_1 nginx -t" in c]
  assert len(build) == 1 and len(ups) == 1 and len(nginx_t) == 1, calls
  assert calls[ups[0]].endswith(
    "up --detach --no-deps --no-build --force-recreate proxy"
  )
  assert build[0] < ups[0] < nginx_t[0]
  touched = (" stop", " rm ", " down", " restart")
  assert not any(t in c for c in calls for t in touched), calls


def test_fails_when_container_still_runs_old_image(tmp_path: Path) -> None:
  rc, out, calls = _run(tmp_path, running_image="oldimage")
  assert rc == 1, out
  assert "expected new hpcperfstats-proxy" in out
  assert not any("nginx -t" in c for c in calls)


def test_build_only_skips_recreate(tmp_path: Path) -> None:
  rc, out, calls = _run(tmp_path, "--build-only")
  assert rc == 0, out
  assert any("proxy.Dockerfile" in c for c in calls)
  assert not any(" up " in c for c in calls)


def test_dry_run_runs_nothing(tmp_path: Path) -> None:
  rc, out, calls = _run(tmp_path, "--dry-run")
  assert rc == 0, out
  assert "[dry-run]" in out
  assert not any(c.startswith("podman build") for c in calls)
  assert not any(" up " in c for c in calls)


def test_refuses_stack_without_proxy_service(tmp_path: Path) -> None:
  rc, out, calls = _run(tmp_path, services="web pipeline")
  assert rc == 1, out
  assert "no proxy service" in out
  assert not any(c.startswith("podman build") for c in calls)
