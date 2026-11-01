"""Non-PGO contracts formerly colocated with deleted test_pgo_namespaces.py."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def test_compose_pipeline_memory_high_uses_state_cgroup_path() -> None:
  lib = (
    _repo_root() / "scripts" / "lib" / "compose_pipeline_memory_high.sh"
  ).read_text()
  assert "{{.State.CgroupPath}}" in lib
  assert "pipeline_cgroup_dir_on_host" in lib


def test_docker_compose_example_keeps_stack_image_names() -> None:
  example = (_repo_root() / "docker-compose.yaml.example").read_text()
  assert (
    sum(1 for line in example.splitlines() if line.strip() == "services:") == 1
  )
  assert (
    "image: hpcperfstats\n" in example or "    image: hpcperfstats\n" in example
  )
  assert "image: hpcperfstats-proxy" in example
  assert "image: hpcperfstats-db" in example


def test_rebuild_full_site_compose_down_before_up() -> None:
  script = (_repo_root() / "scripts" / "rebuild_full_site.sh").read_text()
  assert "compose_down_project" in script
  assert "down --remove-orphans" in script
  assert 'down -t "${timeout}"' not in script
  assert "HPCPERFSTATS_COMPOSE_DOWN_TIMEOUT:-30" not in script
  assert "up -d --force-recreate" not in script
  assert "up -d --no-build" in script
  assert "--profile" not in script.replace("--profile-phase", "")
  assert "PG18_PROFILE" not in script
  down_pos = script.index("compose_down_project")
  up_pos = script.index("compose_up_project_stack()")
  assert down_pos < up_pos
  assert "wait_for_compose_service_running" in script
  run_stack = script[script.index("run_stack_up_and_verify()") :]
  run_stack_end = run_stack.index("\n\n", run_stack.index("UP_COMPLETED=1"))
  assert "up_db_pg18" not in run_stack[:run_stack_end]


def test_compose_ensure_local_stack_image_tags_splits_canonical_and_alias() -> (
  None
):
  helpers = (
    _repo_root() / "scripts" / "lib" / "compose_frontend_helpers.sh"
  ).read_text()
  start = helpers.index("compose_ensure_local_stack_image_tags()")
  block = helpers[start : start + 500]
  assert "while read -r canonical alias" in block
  assert "IFS= read -r canonical alias" not in block


def test_rebuild_full_site_refuses_legacy_pgo_flags() -> None:
  script = (_repo_root() / "scripts" / "rebuild_full_site.sh").read_text()
  assert "custom Clang host profiling was removed" in script
  assert "PGO" + "_PHASE" not in script
  assert "source" not in script or ("pgo_" + "lib.sh") not in script


def test_rebuild_full_site_builds_but_does_not_start_db_pg18() -> None:
  script = (_repo_root() / "scripts" / "rebuild_full_site.sh").read_text()
  assert "compose_build_db_pg18" in script
  required = script[script.index("local -a required=") :].splitlines()[0]
  assert "PG18_SERVICE" not in required
  assert "db_pg18" not in required


def _postgres_socket_check_fn() -> str:
  script = (_repo_root() / "scripts" / "rebuild_full_site.sh").read_text()
  start = script.index("verify_postgres_socket_shared() {")
  return script[start : script.index("\n}\n", start) + 3]


def _run_socket_check(tmp_path: Path, missing_in: str) -> tuple[int, str]:
  """Run the real function body with a fake podman; ``missing_in`` lacks the socket."""
  import subprocess

  fake = tmp_path / "podman"
  fake.write_text(
    "#!/bin/sh\n"
    'echo "$@" >> "$FAKE_LOG"\n'
    '[ "$1" = logs ] && exit 0\n'
    f'case "$2" in *_{missing_in}_1) exit 1 ;; esac\n'
    "exit 0\n"
  )
  fake.chmod(0o755)
  log = tmp_path / "calls.log"
  prog = (
    "set -euo pipefail\n"
    f"PODMAN=({fake})\n"
    "HPCPERFSTATS_COMPOSE_PROJECT=hpcperfstats\n"
    "HPCPERFSTATS_STACK_VERIFY_WAIT_TIMEOUT=0\n"
    "compose_service_container_name() { printf '%s_%s_1' "
    '"${HPCPERFSTATS_COMPOSE_PROJECT}" "$1"; }\n'
    "sleep() { :; }\n"
    + _postgres_socket_check_fn()
    + "verify_postgres_socket_shared\n"
  )
  proc = subprocess.run(
    ["bash", "-c", prog],
    capture_output=True,
    text=True,
    env={"FAKE_LOG": str(log), "PATH": "/usr/bin:/bin"},
    check=False,
  )
  calls = log.read_text() if log.exists() else ""
  return proc.returncode, proc.stderr + "\n" + calls


def test_rebuild_full_site_socket_check_passes_when_web_and_pipeline_see_it(
  tmp_path: Path,
) -> None:
  rc, out = _run_socket_check(tmp_path, missing_in="nobody")
  assert rc == 0, out
  assert "exec hpcperfstats_web_1 test -S /run/postgresql/.s.PGSQL.5432" in out
  assert (
    "exec hpcperfstats_pipeline_1 test -S /run/postgresql/.s.PGSQL.5432" in out
  )


def test_rebuild_full_site_socket_check_fails_when_pipeline_lacks_socket(
  tmp_path: Path,
) -> None:
  """Regression: a pipeline without the shared socket volume still 'runs'.

  It waits forever in supervisor_startup.sh, so the running-state check alone
  reported a good rebuild.
  """
  rc, out = _run_socket_check(tmp_path, missing_in="pipeline")
  assert rc == 1, out
  assert "pipeline cannot see the db socket" in out
  assert "postgres_runtime" in out
  assert "logs --tail 20 hpcperfstats_pipeline_1" in out


def test_rebuild_full_site_runs_socket_check_after_running_check() -> None:
  script = (_repo_root() / "scripts" / "rebuild_full_site.sh").read_text()
  run_stack = script[script.index("run_stack_up_and_verify() {") :]
  run_stack = run_stack[: run_stack.index("\n}\n")]
  assert run_stack.index("verify_default_stack_running") < run_stack.index(
    "verify_postgres_socket_shared"
  )
  assert "compose_bind_volumes_verify_mounted" in run_stack
  # Full down + up recreates db/web/pipeline together (no partial recreate).
  assert "down --remove-orphans" in script
  assert "up -d --no-build" in script
