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
