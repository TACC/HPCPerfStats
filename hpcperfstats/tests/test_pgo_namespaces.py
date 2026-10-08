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
  assert "pgo_reset_sketch_tree" in ensure
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
  assert "-fuse-ld=lld" in out
  assert "--ld-path=/usr/bin/ld.lld" in out


def test_pgo_alpine_thinlto_ldflags_adds_libc_on_generate() -> None:
  """Regression: PGO generate .so links need -lc for libclang_rt.profile.a on musl+lld."""
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  skip = subprocess.check_output(
    [
      "bash",
      "-c",
      f'source "{script}"; hpcperfstats_alpine_thinlto_ldflags',
    ],
    env={**os.environ, "PGO_PHASE": "skip"},
    text=True,
  ).strip()
  assert " -lc" not in skip and not skip.endswith("-lc")
  gen = subprocess.check_output(
    [
      "bash",
      "-c",
      f'source "{script}"; hpcperfstats_alpine_thinlto_ldflags',
    ],
    env={**os.environ, "PGO_PHASE": "generate"},
    text=True,
  ).strip()
  assert gen.endswith("-lc")


def test_pgo_alpine_jemalloc_export_uses_llvm_binutils() -> None:
  """ThinLTO .sym.o objects need llvm-nm/llvm-ar, not GNU nm from build-base."""
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  out = subprocess.check_output(
    [
      "bash",
      "-c",
      f'source "{script}"; hpcperfstats_alpine_jemalloc_export_link_toolchain; printf "AR=%s RANLIB=%s NM=%s\\n" "$AR" "$RANLIB" "$NM"',
    ],
    text=True,
  ).strip()
  assert "AR=/usr/lib/llvm22/bin/llvm-ar" in out
  assert "RANLIB=/usr/lib/llvm22/bin/llvm-ranlib" in out
  assert "NM=/usr/lib/llvm22/bin/llvm-nm" in out


def test_pgo_alpine_bootstrap_llvm_toolchain_when_env_flag() -> None:
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  out = subprocess.check_output(
    [
      "bash",
      "-c",
      f'source "{script}"; printf "CC=%s NM=%s LTO=%s\\n" "$CC" "$NM" "$HPC_THINLTO_LDFLAGS"',
    ],
    env={
      **os.environ,
      "HPC_ALPINE_LLVM_TOOLCHAIN": "1",
      "CC": "clang",
      "CXX": "clang++",
    },
    text=True,
  ).strip()
  assert "CC=clang" in out, (
    "bootstrap must not rewrite CC (clang_march_native_probe uses ${CC} as argv0)"
  )
  assert "NM=/usr/lib/llvm22/bin/llvm-nm" in out
  assert "-fuse-ld=lld" in out


def test_cpython_generate_makefile_relax_matches_tabbed_makefile() -> None:
  """Regression: CPython Makefile uses tabs after ':'; space-only sed never rewired install."""
  import tempfile

  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  sample = (
    "all:\t\tprofile-opt\n"
    "profile-opt: profile-run-stamp\n"
    "sharedinstall: all\n"
    "libinstall:\tall $(srcdir)/Modules/xxmodule.c\n"
    "libainstall: all scripts\n"
  )
  with tempfile.TemporaryDirectory() as tmp:
    mk = Path(tmp) / "Makefile"
    mk.write_text(sample, encoding="utf-8")
    subprocess.run(
      [
        "bash",
        "-c",
        f'source "{script}"; hpcperfstats_cpython_relax_install_deps_for_instrumented',
      ],
      cwd=tmp,
      check=True,
    )
    out = mk.read_text(encoding="utf-8")
  assert "all: build_all" in out.replace("\t", " ")
  assert "libinstall: build_all" in out.replace("\t", " ")
  assert "sharedinstall: build_all" in out.replace("\t", " ")
  assert "libainstall: build_all" in out.replace("\t", " ")
  assert "profile-opt" in out


def test_cpython_make_install_uses_upstream_profile_targets() -> None:
  script = (_repo_root() / "services-conf" / "pgo_clang_flags.sh").read_text()
  assert "all:[[:space:]]*profile-opt" in script
  assert "profile-gen-stamp" in script
  assert "profile-run-stamp" in script
  assert "touch profile-run-stamp" in script
  assert "profile-opt" in script
  assert "hpcperfstats_cpython_stage_profiles_for_profile_opt" in script
  assert "hpcperfstats_cpython_run_makefile_prof_merger" in script
  assert (
    "hpcperfstats_cpython_makefile_block_profile_opt_without_profclangd"
    in script
  )
  assert "hpcperfstats-profile-opt-guard" in script
  assert "hpcperfstats-block-profile-run-stamp" in script
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
    for phase in ("skip", "stdlib", "generate", "use"):
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


def test_cpython_stdlib_upstream_mini_pgo_no_clang_instr_on_libs() -> None:
  """Default rebuild (PGO_PHASE=stdlib): CPython mini PGO; no Clang -fprofile-instr-* on libs."""
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  import tempfile

  with tempfile.TemporaryDirectory() as tmp:
    env = {**os.environ, "HPC_PGO_PHASE": "stdlib", "HPC_PGO_ROOT": tmp}
    opt = subprocess.check_output(
      [
        "bash",
        "-c",
        f'source "{script}"; hpcperfstats_cpython_enable_optimizations_for_configure',
      ],
      env=env,
      text=True,
    ).strip()
    zstd = subprocess.check_output(
      [
        "bash",
        "-c",
        f'source "{script}"; hpcperfstats_bake_cflags web/shared/zstd debian-lib',
      ],
      env=env,
      text=True,
    ).strip()
    skip_zstd = subprocess.check_output(
      [
        "bash",
        "-c",
        f'source "{script}"; hpcperfstats_bake_cflags web/shared/zstd debian-lib',
      ],
      env={**os.environ, "HPC_PGO_PHASE": "skip", "HPC_PGO_ROOT": tmp},
      text=True,
    ).strip()
  assert opt == "--enable-optimizations"
  assert "-fprofile-instr-generate" not in zstd
  assert "-fprofile-instr-use" not in zstd
  assert zstd == skip_zstd
  body = script.read_text()
  normalized = body.replace("\t", " ")
  assert "stdlib | generate | use)" in normalized
  assert "stdlib)" in normalized
  assert "make -j" in body and "profile-gen-stamp" in body
  make_install_body = body.split("hpcperfstats_cpython_make_install", 1)[1]
  stdlib_branch = make_install_body.split("stdlib)", 1)[1].split(";;", 1)[0]
  assert "profile-gen-stamp" not in stdlib_branch
  assert "profile-opt" not in stdlib_branch


def test_pgo_skip_applies_merged_profiles_when_profdata_present() -> None:
  """Post-use rebuilds (PGO_PHASE=skip) still consume default.profdata when present."""
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  pgo_lib = _repo_root() / "scripts" / "pgo_lib.sh"
  import tempfile
  from pathlib import Path

  with (
    tempfile.TemporaryDirectory() as tmp_empty,
    tempfile.TemporaryDirectory() as tmp,
  ):
    opt_empty = subprocess.check_output(
      [
        "bash",
        "-c",
        f'source "{script}"; hpcperfstats_cpython_enable_optimizations_for_configure',
      ],
      env={**os.environ, "HPC_PGO_PHASE": "skip", "HPC_PGO_ROOT": tmp_empty},
      text=True,
    ).strip()
    assert opt_empty == ""

    ns = Path(tmp) / "web" / "shared" / "zstd"
    ns.mkdir(parents=True)
    prof = ns / "default.profdata"
    prof.write_bytes(b"profdata-placeholder")

    env = {**os.environ, "HPC_PGO_PHASE": "skip", "HPC_PGO_ROOT": tmp}
    zstd = subprocess.check_output(
      [
        "bash",
        "-c",
        f'source "{script}"; hpcperfstats_bake_cflags web/shared/zstd debian-lib',
      ],
      env=env,
      text=True,
    ).strip()
    assert "-fprofile-instr-use=" in zstd
    assert str(prof) in zstd

    opt = subprocess.check_output(
      [
        "bash",
        "-c",
        f'source "{script}"; hpcperfstats_cpython_enable_optimizations_for_configure',
      ],
      env=env,
      text=True,
    ).strip()
    assert opt == "--enable-optimizations"

  use_nc = subprocess.check_output(
    [
      "bash",
      "-c",
      f'source "{pgo_lib}"; PGO_PHASE=use; pgo_image_build_cache_args',
    ],
    text=True,
  ).strip()
  assert use_nc == "--no-cache"

  skip_nc = subprocess.check_output(
    [
      "bash",
      "-c",
      f'source "{pgo_lib}"; PGO_PHASE=skip; pgo_image_build_cache_args',
    ],
    text=True,
  ).strip()
  assert skip_nc == ""


def test_pgo_skip_partial_pgroot_refuses_mixed_bake(tmp_path: Path) -> None:
  """PGO_PHASE=skip with some profdata but a missing namespace must die, not plain-compile."""
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  zstd_dir = tmp_path / "web" / "shared" / "zstd"
  zstd_dir.mkdir(parents=True)
  (zstd_dir / "default.profdata").write_bytes(b"merged")
  proc = subprocess.run(
    [
      "bash",
      "-c",
      f'source "{script}"; hpcperfstats_bake_cflags web/shared/jemalloc debian-lib',
    ],
    env={
      **os.environ,
      "HPC_PGO_PHASE": "skip",
      "HPC_PGO_ROOT": str(tmp_path),
    },
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode != 0
  assert "partial PGOROOT" in proc.stderr
  assert "web/shared/jemalloc" in proc.stderr


def test_pgo_skip_empty_pgroot_omits_instr_use(tmp_path: Path) -> None:
  script = _repo_root() / "services-conf" / "pgo_clang_flags.sh"
  out = subprocess.check_output(
    [
      "bash",
      "-c",
      f'source "{script}"; hpcperfstats_bake_cflags web/shared/zstd debian-lib',
    ],
    env={
      **os.environ,
      "HPC_PGO_PHASE": "skip",
      "HPC_PGO_ROOT": str(tmp_path),
    },
    text=True,
  ).strip()
  assert "-fprofile-instr-use" not in out


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
    assert "BASH_ENV=/usr/local/lib/hpcperfstats/pgo_clang_flags.sh" in text, (
      name
    )
    assert "/bin/bash -o pipefail" in text, name
    assert ". /usr/local/lib/hpcperfstats/pgo_clang_flags.sh" not in text, name


def _source_pgo_lib_profiles_ready(
  tmp: Path,
) -> subprocess.CompletedProcess[str]:
  pgo_lib = _repo_root() / "scripts" / "pgo_lib.sh"
  repo = _repo_root()
  return subprocess.run(
    [
      "bash",
      "-c",
      (
        f'source "{pgo_lib}"; '
        f'profiles_ready "{repo}"; '
        "echo profiles_ready_exit=$?"
      ),
    ],
    env={**os.environ, "PGOROOT": str(tmp)},
    capture_output=True,
    text=True,
    check=False,
  )


def test_profiles_ready_false_when_only_generate_instr_raw(
  tmp_path: Path,
) -> None:
  """Generate-time profraw must not trigger PGO use (CPython still needs soak)."""
  ns = "web/gil/cpython"
  raw = tmp_path / ns / "raw"
  raw.mkdir(parents=True)
  (tmp_path / "web/shared/jemalloc/raw").mkdir(parents=True)
  (tmp_path / "web/shared/jemalloc/raw/jemalloc.profraw").write_bytes(b"x")
  proc = _source_pgo_lib_profiles_ready(tmp_path)
  assert proc.returncode != 0


def test_partial_cpython_generate_profraw_only_dies_not_stdlib(
  tmp_path: Path,
) -> None:
  """Incomplete CPython generate profraw (no soak) must fail closed, not stdlib."""
  pgo_lib = _repo_root() / "scripts" / "pgo_lib.sh"
  repo = _repo_root()
  (tmp_path / "web/gil/cpython/raw").mkdir(parents=True)
  (tmp_path / "web/gil/cpython/raw/module.profraw").write_bytes(b"gen")
  proc = subprocess.run(
    [
      "bash",
      "-c",
      (
        f'source "{pgo_lib}"; set +e; '
        f'pgo_die_if_partial_profile_collection "{repo}" 0; '
        "echo exit=$?"
      ),
    ],
    env={**os.environ, "PGOROOT": str(tmp_path)},
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode != 0
  assert "PGO profile collection incomplete" in proc.stderr


def test_partial_profile_collection_dies_with_use_breadcrumb(
  tmp_path: Path,
) -> None:
  """Use breadcrumb must not bypass partial PGOROOT fail-closed."""
  pgo_lib = _repo_root() / "scripts" / "pgo_lib.sh"
  repo = _repo_root()
  (tmp_path / "breadcrumbs").mkdir(parents=True, exist_ok=True)
  (tmp_path / "breadcrumbs/pgo_use_full_rebuild.done").write_text("done")
  (tmp_path / "web/shared/jemalloc/raw").mkdir(parents=True)
  (tmp_path / "web/shared/jemalloc/raw/jemalloc.profraw").write_bytes(b"x")
  proc = subprocess.run(
    [
      "bash",
      "-c",
      (
        f'source "{pgo_lib}"; set +e; '
        f'pgo_die_if_partial_profile_collection "{repo}" 0; '
        "echo exit=$?"
      ),
    ],
    env={**os.environ, "PGOROOT": str(tmp_path)},
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode != 0
  assert "PGO profile collection incomplete" in proc.stderr


def test_partial_profile_collection_dies_before_compile(tmp_path: Path) -> None:
  pgo_lib = _repo_root() / "scripts" / "pgo_lib.sh"
  repo = _repo_root()
  (tmp_path / "web/shared/jemalloc/raw").mkdir(parents=True)
  (tmp_path / "web/shared/jemalloc/raw/jemalloc.profraw").write_bytes(b"x")
  (tmp_path / "web/gil/cpython/raw").mkdir(parents=True)
  proc = subprocess.run(
    [
      "bash",
      "-c",
      (
        f'source "{pgo_lib}"; set +e; '
        f'pgo_die_if_partial_profile_collection "{repo}" 0; '
        "echo exit=$?"
      ),
    ],
    env={**os.environ, "PGOROOT": str(tmp_path)},
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode != 0
  assert "PGO profile collection incomplete" in proc.stderr
  assert "web/gil/cpython" in proc.stderr
  assert "web/ft/cpython" in proc.stderr


def test_pgo_wipe_pgroot_for_profile_phase_removes_all_contents(
  tmp_path: Path,
) -> None:
  pgo_lib = _repo_root() / "scripts" / "pgo_lib.sh"
  prof = tmp_path / "web/gil/cpython/default.profdata"
  prof.parent.mkdir(parents=True)
  prof.write_bytes(b"merged")
  (tmp_path / "breadcrumbs").mkdir(parents=True, exist_ok=True)
  (tmp_path / "breadcrumbs/pgo_use_full_rebuild.done").write_text("x")
  (tmp_path / "manifest.yaml").write_text("old", encoding="utf-8")
  subprocess.run(
    [
      "bash",
      "-c",
      f'source "{pgo_lib}"; pgo_wipe_pgroot_for_profile_phase',
    ],
    env={**os.environ, "PGOROOT": str(tmp_path)},
    check=True,
    capture_output=True,
    text=True,
  )
  assert not any(tmp_path.iterdir())


def test_rebuild_full_site_wipes_pgroot_on_profile_phase() -> None:
  script = (_repo_root() / "scripts" / "rebuild_full_site.sh").read_text()
  pgo_lib = (_repo_root() / "scripts" / "pgo_lib.sh").read_text()
  assert "pgo_confirm_wipe_pgroot_for_profile_phase" in script
  assert "pgo_confirm_wipe_pgroot_for_profile_phase" in pgo_lib
  assert "HPC_PGO_PROFILE_PHASE_FORCE_WIPE" in pgo_lib
  assert 'if [[ "${PROFILE_PHASE}" -eq 1 ]]; then' in script


def test_profile_phase_force_wipe_with_complete_profiles(
  tmp_path: Path,
) -> None:
  pgo_lib = _repo_root() / "scripts" / "pgo_lib.sh"
  repo = _repo_root()
  for ns in _registry_namespaces():
    (tmp_path / ns / "raw").mkdir(parents=True, exist_ok=True)
    if ns in ("web/gil/cpython", "web/ft/cpython"):
      (tmp_path / ns / "raw/python-1.profraw").write_bytes(b"soak")
    else:
      (tmp_path / ns / "raw/mod.profraw").write_bytes(b"gen")
  (tmp_path / "manifest.yaml").write_text("keep", encoding="utf-8")
  subprocess.run(
    [
      "bash",
      "-c",
      (
        f'source "{pgo_lib}"; '
        f'pgo_confirm_wipe_pgroot_for_profile_phase "{repo}"'
      ),
    ],
    env={
      **os.environ,
      "PGOROOT": str(tmp_path),
      "HPC_PGO_PROFILE_PHASE_FORCE_WIPE": "1",
    },
    check=True,
    capture_output=True,
    text=True,
  )
  assert not any(tmp_path.iterdir())


def test_partial_profile_collection_allowed_during_profile_phase_flag(
  tmp_path: Path,
) -> None:
  pgo_lib = _repo_root() / "scripts" / "pgo_lib.sh"
  repo = _repo_root()
  (tmp_path / "web/shared/jemalloc/raw").mkdir(parents=True)
  (tmp_path / "web/shared/jemalloc/raw/jemalloc.profraw").write_bytes(b"x")
  proc = subprocess.run(
    [
      "bash",
      "-c",
      (
        f'source "{pgo_lib}"; '
        f'pgo_die_if_partial_profile_collection "{repo}" 1; '
        "echo ok"
      ),
    ],
    env={**os.environ, "PGOROOT": str(tmp_path)},
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode == 0 and "ok" in proc.stdout


def test_profiles_ready_true_when_cpython_soak_raw_present(
  tmp_path: Path,
) -> None:
  for ns in _registry_namespaces():
    (tmp_path / ns / "raw").mkdir(parents=True, exist_ok=True)
    if ns in ("web/gil/cpython", "web/ft/cpython"):
      (tmp_path / ns / "raw/python-12345.profraw").write_bytes(b"soak")
    else:
      (tmp_path / ns / "raw/module.profraw").write_bytes(b"gen")
  proc = _source_pgo_lib_profiles_ready(tmp_path)
  assert proc.returncode == 0 and "profiles_ready_exit=0" in proc.stdout


def test_pgo_reset_sketch_tree_clears_layout_only_pgroot(
  tmp_path: Path,
) -> None:
  pgo_lib = _repo_root() / "scripts" / "pgo_lib.sh"
  repo = _repo_root()
  (tmp_path / "breadcrumbs").mkdir(parents=True)
  (tmp_path / "manifest.yaml").write_text("old", encoding="utf-8")
  (tmp_path / "web/shared/jemalloc/raw").mkdir(parents=True)
  env = {**os.environ, "PGOROOT": str(tmp_path)}
  proc = subprocess.run(
    [
      "bash",
      "-c",
      (
        f'source "{pgo_lib}"; set +e; '
        f'pgo_reset_sketch_tree "{repo}"; r=$?; set -e; echo exit=$r'
      ),
    ],
    env=env,
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode == 0 and "exit=0" in proc.stdout
  assert not (tmp_path / "manifest.yaml").exists()
  assert not (tmp_path / "breadcrumbs").exists()


def test_pgo_reset_sketch_tree_keeps_tree_when_profile_data_present(
  tmp_path: Path,
) -> None:
  pgo_lib = _repo_root() / "scripts" / "pgo_lib.sh"
  repo = _repo_root()
  raw = tmp_path / "web/shared/jemalloc/raw"
  raw.mkdir(parents=True)
  (raw / "jemalloc.profraw").write_bytes(b"x")
  (tmp_path / "manifest.yaml").write_text("keep", encoding="utf-8")
  env = {**os.environ, "PGOROOT": str(tmp_path)}
  proc = subprocess.run(
    [
      "bash",
      "-c",
      (
        f'source "{pgo_lib}"; set +e; '
        f'pgo_reset_sketch_tree "{repo}"; r=$?; set -e; echo exit=$r'
      ),
    ],
    env=env,
    capture_output=True,
    text=True,
    check=False,
  )
  assert proc.returncode == 0 and "exit=1" in proc.stdout
  assert (tmp_path / "manifest.yaml").read_text(encoding="utf-8") == "keep"


def test_pgo_ensure_layout_refreshes_manifest_after_sketch_clear(
  tmp_path: Path,
) -> None:
  env = os.environ.copy()
  env["PGOROOT"] = str(tmp_path)
  (tmp_path / "manifest.yaml").write_text("stale", encoding="utf-8")
  (tmp_path / "web/shared/zstd/raw").mkdir(parents=True)
  subprocess.run(
    [_repo_root() / "scripts" / "pgo_ensure_layout.sh"],
    check=True,
    env=env,
    cwd=_repo_root(),
    capture_output=True,
    text=True,
  )
  manifest = (tmp_path / "manifest.yaml").read_text(encoding="utf-8")
  assert "stale" not in manifest
  assert "namespaces_source:" in manifest
  assert (tmp_path / "web/shared/zstd/raw").is_dir()


def test_profiles_ready_true_when_all_namespaces_have_profdata(
  tmp_path: Path,
) -> None:
  for ns in _registry_namespaces():
    out = tmp_path / ns / "default.profdata"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"merged")
  proc = _source_pgo_lib_profiles_ready(tmp_path)
  assert proc.returncode == 0 and "profiles_ready_exit=0" in proc.stdout


def test_alpine_dockerfiles_invoke_bash_for_pgo_not_shell() -> None:
  """Regression: Podman OCI ignores Dockerfile SHELL; ash cannot source pgo_clang_flags.sh."""
  root = _repo_root()
  for name in ("services-conf/proxy.Dockerfile", "services-conf/db.Dockerfile"):
    text = (root / name).read_text()
    assert "BASH_ENV=/usr/local/lib/hpcperfstats/pgo_clang_flags.sh" in text
    assert "/bin/bash -o pipefail" in text
    assert ". /usr/local/lib/hpcperfstats/pgo_clang_flags.sh" not in text


def test_compose_pipeline_memory_high_uses_state_cgroup_path() -> None:
  lib = (
    _repo_root() / "scripts" / "lib" / "compose_pipeline_memory_high.sh"
  ).read_text()
  assert "{{.State.CgroupPath}}" in lib
  assert "pipeline_cgroup_dir_on_host" in lib


def test_rebuild_full_site_compose_down_before_up() -> None:
  script = (_repo_root() / "scripts" / "rebuild_full_site.sh").read_text()
  assert "compose_down_project" in script
  assert 'down -t "${timeout}" --remove-orphans' in script
  assert "up -d --force-recreate" not in script
  assert '"${PODMAN_COMPOSE[@]}" up -d' in script
  down_pos = script.index("compose_down_project")
  up_pos = script.index("up_default_stack()")
  assert down_pos < up_pos


def test_rebuild_full_site_pgo_fail_loud_helpers() -> None:
  script = (_repo_root() / "scripts" / "rebuild_full_site.sh").read_text()
  pgo_lib = (_repo_root() / "scripts" / "pgo_lib.sh").read_text()
  assert "PGO FAILED" in pgo_lib
  assert "pgo_die_if_partial_profile_collection" in script
  assert "pgo_die_if_partial_profile_collection" in pgo_lib
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
