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


def _pgo_ensure_raw_namespaces_from_shell() -> set[str]:
  script = (_repo_root() / "services-conf" / "pgo_clang_flags.sh").read_text()
  ensured = set(
    re.findall(
      r"hpcperfstats_pgo_ensure_raw_dir\s+([a-z0-9./_-]+)",
      script,
    )
  )
  for loop in re.finditer(
    r"for ns in\s+([^;]+);\s*do\s+hpcperfstats_pgo_ensure_raw_dir",
    script,
    flags=re.DOTALL,
  ):
    for token in re.findall(r"[a-z0-9]+/[a-z0-9./_-]+", loop.group(1)):
      ensured.add(token.strip())
  return ensured


def test_pgo_ensure_helpers_list_every_db_and_proxy_registry_namespace() -> (
  None
):
  """Link-time raw dirs: no registry row may rely on ad hoc mkdir later."""
  ensured = _pgo_ensure_raw_namespaces_from_shell()
  reg = set(_registry_namespaces())
  for ns in sorted(n for n in reg if n.startswith(("db/", "proxy/"))):
    assert ns in ensured, (
      f"missing ensure_raw_dir for {ns} in pgo_clang_flags.sh"
    )


def test_pgo_ensure_helpers_list_every_web_registry_namespace() -> None:
  ensured = _pgo_ensure_raw_namespaces_from_shell()
  reg = set(_registry_namespaces())
  for ns in sorted(n for n in reg if n.startswith("web/")):
    assert ns in ensured, (
      f"missing ensure_raw_dir for {ns} in pgo_clang_flags.sh"
    )


def test_pgo_registry_rows_use_pg_mount_with_bake_cflags() -> None:
  """Every PGO bake/cpython make call in image Dockerfiles must sit in a from=pgo RUN."""
  mount = "from=pgo,source=.,target=/root/.hpcperfstats_pgo"
  for path in (
    _repo_root() / "Dockerfile",
    _repo_root() / "services-conf" / "db.Dockerfile",
    _repo_root() / "services-conf" / "proxy.Dockerfile",
  ):
    text = path.read_text()
    for pattern in (
      r"hpcperfstats_bake_cflags[^\n\\]+",
      r"hpcperfstats_cpython_make_install[^\n\\]+",
    ):
      for match in re.finditer(pattern, text):
        start = text.rfind("RUN", 0, match.start())
        end = text.find("\n\n", match.start())
        if end == -1:
          end = len(text)
        run_block = text[start:end]
        assert mount in run_block, (
          f"{path.name}: PGO without PGOROOT mount near {match.group(0)!r}"
        )


def test_web_dockerfile_declares_pgo_namespace_for_each_web_row() -> None:
  web_text = (_repo_root() / "Dockerfile").read_text()
  build = web_text[web_text.index("FROM debian:trixie AS python-build") :]
  for ns in _registry_namespaces():
    if not ns.startswith("web/"):
      continue
    if ns in ("web/gil/cpython", "web/ft/cpython"):
      assert f"hpcperfstats_cpython_make_install {ns}" in build, ns
      assert f"hpcperfstats_configure_cflags {ns}" in build, ns
    else:
      assert f"hpcperfstats_bake_cflags {ns}" in build, ns


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


def test_pgo_alpine_libs_bake_omits_ld_path_from_cflags() -> None:
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  env = {**os.environ, "HPC_PGO_PHASE": "skip"}
  out = subprocess.check_output(
    [
      "bash",
      "-c",
      f'source "{script}"; hpcperfstats_bake_cflags proxy/jemalloc',
    ],
    env=env,
    text=True,
  ).strip()
  assert "-flto=thin" in out
  assert "--ld-path=" not in out


def test_pgo_alpine_thinlto_ldflags_for_jemalloc_link() -> None:
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  out = subprocess.check_output(
    [
      "bash",
      "-c",
      f'source "{script}"; hpcperfstats_alpine_thinlto_ldflags',
    ],
    text=True,
  ).strip()
  assert "-flto=thin" in out
  assert "--ld-path=/usr/lib/llvm22/bin/ld.lld" in out


def test_cpython_make_install_uses_upstream_profile_targets() -> None:
  script = (_repo_root() / "services-conf" / "pgo_clang_flags.sh").read_text()
  assert "profile-gen-stamp" in script
  assert "touch profile-run-stamp" in script
  assert "profile-opt" in script
  assert "hpcperfstats_cpython_stage_profiles_for_profile_opt" in script
  assert "hpcperfstats_cpython_run_makefile_prof_merger" in script
  assert "LLVM_PROF_MERGER" in script
  assert "llvm-profdata merge" not in script
  assert "code-*.profclangr" in script
  assert "python-%p.profraw" in script
  assert "build_all_generate_profile" not in script
  assert "hpcperfstats_cpython_enable_optimizations_for_configure" in script


def test_cpython_bake_omits_namespace_pgo_instr_flags() -> None:
  """CPython PGO uses --enable-optimizations + split make targets, not bake instr flags."""
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  import tempfile

  with tempfile.TemporaryDirectory() as tmp:
    for phase in ("skip", "generate", "use"):
      out = subprocess.check_output(
        [
          "bash",
          "-c",
          f'source "{script}"; hpcperfstats_bake_cflags web/gil/cpython debian-lib',
        ],
        env={
          **os.environ,
          "HPC_PGO_PHASE": phase,
          "HPC_PGO_ROOT": tmp,
        },
        text=True,
      ).strip()
      assert "-fprofile-instr-generate" not in out, phase
      assert "-fprofile-instr-use" not in out, phase


def test_cpython_llvm_profile_file_matches_upstream_profraw_pattern() -> None:
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  out = subprocess.check_output(
    [
      "bash",
      "-c",
      f'source "{script}"; hpcperfstats_cpython_llvm_profile_file web/gil/cpython',
    ],
    env={**os.environ, "HPC_PGO_ROOT": "/tmp/pgo"},
    text=True,
  ).strip()
  assert out.endswith("/web/gil/cpython/raw/python-%p.profraw")


def test_pgo_generate_bake_uses_profraw_filename_template() -> None:
  """Regression: -fprofile-instr-generate must not point at raw/ alone (EISDIR at runtime)."""
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  import tempfile

  with tempfile.TemporaryDirectory() as tmp:
    env = {**os.environ, "HPC_PGO_PHASE": "generate", "HPC_PGO_ROOT": tmp}
    out = subprocess.check_output(
      [
        "bash",
        "-c",
        f'source "{script}"; hpcperfstats_bake_cflags web/shared/zstd debian-lib',
      ],
      env=env,
      text=True,
    ).strip()
  assert "/raw/%m.profraw" in out
  assert out.endswith(".profraw") or "/raw/%m.profraw" in out
  assert "-fprofile-instr-generate=" in out
  bad = out.split("-fprofile-instr-generate=", 1)[1].split()[0]
  assert not bad.endswith("/raw"), f"EISDIR path: {bad!r}"


def test_pgo_clang_flags_shared_lib_configure_vs_bake() -> None:
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  cmd = (
    f'source "{script}"; '
    "b=$(hpcperfstats_bake_cflags db/jemalloc); "
    "c=$(hpcperfstats_configure_cflags db/jemalloc); "
    'printf "%s|%s" "$c" "$b"'
  )
  out = subprocess.check_output(["bash", "-c", cmd], text=True).strip()
  cfg, bake = out.split("|", 1)
  assert "-fPIC" in bake
  assert "-flto=thin" in bake
  assert "-flto=thin" not in cfg
  assert "-fPIC" not in cfg


def test_pgo_ensure_db_and_proxy_link_dirs_on_generate() -> None:
  import tempfile

  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  with tempfile.TemporaryDirectory() as tmp:
    env = {**os.environ, "HPC_PGO_PHASE": "generate", "HPC_PGO_ROOT": tmp}
    subprocess.check_call(
      [
        "bash",
        "-c",
        (
          f'source "{script}"; '
          "hpcperfstats_pgo_ensure_db_zstd_upstream; "
          "hpcperfstats_pgo_ensure_db_postgresql_link_dirs; "
          "hpcperfstats_pgo_ensure_proxy_nginx_link_dirs"
        ),
      ],
      env=env,
    )
    for ns in ("db/lz4", "db/postgresql", "proxy/openssl"):
      assert (Path(tmp) / ns / "raw").is_dir()


def test_pgo_ensure_web_abi_link_dirs_creates_raw_on_generate() -> None:
  import tempfile

  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  with tempfile.TemporaryDirectory() as tmp:
    env = {**os.environ, "HPC_PGO_PHASE": "generate", "HPC_PGO_ROOT": tmp}
    subprocess.check_call(
      [
        "bash",
        "-c",
        (
          f'source "{script}"; '
          "hpcperfstats_pgo_ensure_web_gil_optimization_stack_link_dirs"
        ),
      ],
      env=env,
    )
    for ns in ("web/gil/cpython", "web/gil/optimization-stack"):
      assert (Path(tmp) / ns / "raw").is_dir()


def test_pgo_ensure_web_shared_link_dirs_creates_raw_on_generate() -> None:
  """Regression: CPython links PGO-instrumented /opt/zlib-ng and /opt/zstd."""
  import tempfile

  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  with tempfile.TemporaryDirectory() as tmp:
    env = {
      **os.environ,
      "HPC_PGO_PHASE": "generate",
      "HPC_PGO_ROOT": tmp,
    }
    subprocess.check_call(
      [
        "bash",
        "-c",
        f'source "{script}"; hpcperfstats_pgo_ensure_web_shared_link_dirs',
      ],
      env=env,
    )
    for ns in (
      "web/shared/zlib-ng",
      "web/shared/zstd",
      "web/shared/jemalloc",
    ):
      assert (Path(tmp) / ns / "raw").is_dir()


def test_alpine_pgo_dockerfiles_use_bash_shell_for_pgo_clang_flags() -> None:
  """Regression: ash sourcing pgo_clang_flags.sh fails on bash array syntax."""
  for name in ("proxy.Dockerfile", "db.Dockerfile"):
    text = (_repo_root() / "services-conf" / name).read_text()
    assert "apk add" in text and "bash" in text, name
    assert 'SHELL ["/bin/bash"' in text, name
    assert "pgo_clang_flags.sh" in text, name


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
