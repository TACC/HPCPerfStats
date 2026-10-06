"""Contract tests for PGO namespace registry vs Docker bake markers."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _registry_namespaces() -> list[str]:
  yaml_path = _repo_root() / "services-conf" / "pgo_namespaces.yaml"
  text = yaml_path.read_text()
  return re.findall(r"^\s+- namespace:\s+(\S+)\s*$", text, flags=re.MULTILINE)


def test_pgo_namespaces_yaml_non_empty_and_unique() -> None:
  names = _registry_namespaces()
  assert names
  assert len(names) == len(set(names))


def test_db_dockerfile_declares_pgo_namespace_for_each_db_row() -> None:
  db_text = (_repo_root() / "services-conf" / "db.Dockerfile").read_text()
  for ns in _registry_namespaces():
    if not ns.startswith("db/"):
      continue
    assert f"hpcperfstats_bake_cflags {ns}" in db_text, ns


def test_proxy_dockerfile_declares_pgo_namespace_for_each_proxy_row() -> None:
  proxy_text = (_repo_root() / "services-conf" / "proxy.Dockerfile").read_text()
  for ns in _registry_namespaces():
    if not ns.startswith("proxy/"):
      continue
    assert f"hpcperfstats_bake_cflags {ns}" in proxy_text, ns


def test_pgo_lib_bootstraps_pg_root_before_layout() -> None:
  pgo_lib = (_repo_root() / "scripts" / "pgo_lib.sh").read_text()
  ensure = (_repo_root() / "scripts" / "pgo_ensure_layout.sh").read_text()
  assert "pgo_ensure_pg_root" in pgo_lib
  assert "pgo_ensure_pg_root" in ensure
  assert "pgo_chmod_shared_tree" in pgo_lib
  assert "pgo_chmod_shared_tree" in ensure


def _dir_world_rwx(mode: int) -> bool:
  perms = mode & 0o777
  return perms in (0o777, 0o1777)


def test_pgo_ensure_layout_applies_shared_directory_mode(
  tmp_path: Path,
) -> None:
  pgoroot = tmp_path / "pgo-shared-perms"
  env = os.environ.copy()
  env["PGOROOT"] = str(pgoroot)
  subprocess.run(
    [_repo_root() / "scripts" / "pgo_ensure_layout.sh"],
    check=True,
    env=env,
    cwd=_repo_root(),
    capture_output=True,
    text=True,
  )
  assert pgoroot.is_dir()
  assert _dir_world_rwx(pgoroot.stat().st_mode)
  raw_dirs = list(pgoroot.glob("**/raw"))
  assert raw_dirs, "expected namespace raw/ dirs from pgo_namespaces.yaml"
  for raw in raw_dirs:
    assert _dir_world_rwx(raw.stat().st_mode)
  manifest = pgoroot / "manifest.yaml"
  assert manifest.is_file()
  assert manifest.stat().st_mode & 0o666 == 0o666


def test_rebuild_full_site_pgo_fail_loud_helpers() -> None:
  script = (_repo_root() / "scripts" / "rebuild_full_site.sh").read_text()
  pgo_lib = (_repo_root() / "scripts" / "pgo_lib.sh").read_text()
  assert "PGO FAILED" in pgo_lib
  assert "pgo_die" in script
  assert "--profile-phase" in script
  assert (
    "pgo_use_full_rebuild.done" in script or "pgo_use_breadcrumb_path" in script
  )
  assert (
    "pgo_use_failed_path" in script or "pgo_use_full_rebuild.failed" in script
  )
  assert "build_musl_gcc_toolchain_image" not in script
  assert 'run_cmd "${SCRIPT_DIR}/pgo_ensure_layout.sh"' not in script
