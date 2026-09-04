"""Dockerfile contracts: debian trixie dual CPython + jemalloc both ways."""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _stage_body(dockerfile: str, stage_name: str) -> str:
  match = re.search(
      rf"^FROM .* AS {re.escape(stage_name)}\s*\n(.*?)(?=^FROM |\Z)",
      dockerfile,
      flags=re.MULTILINE | re.DOTALL,
  )
  assert match, f"{stage_name} stage not found in Dockerfile"
  return match.group(1)


def test_dockerfile_uses_debian_trixie_builder_and_slim_runtime():
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  assert "FROM python:3.14.7-trixie" not in dockerfile
  assert re.search(
      r"^FROM debian:trixie AS python-build\s*$",
      dockerfile,
      flags=re.MULTILINE,
  )
  assert re.search(
      r"^FROM debian:trixie-slim AS hpcperfstats-base\s*$",
      dockerfile,
      flags=re.MULTILINE,
  )
  assert "python-freethreaded" not in dockerfile


def test_compiled_library_pins_are_latest_known():
  """Lock Dockerfile compile-from-source pins (bump when intentionally upgrading)."""
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  assert "jemalloc-5.3.1.tar.bz2" in build
  assert "3826bc80232f22ed5c4662f3034f799ca316e819103bdc7bb99018a421706f92" in build
  assert "mpdecimal-4.0.1.tar.gz" in build
  assert "96d33abb4bb0070c7be0fed4246cd38416188325f820468214471938545b1ac8" in build
  assert "libffi-3.8.0.tar.gz" in build
  assert "7da3e2d9a171eb0a038f592ecad3ff2bb2550f3496d87b3b29ad0cf4430c0db4" in build
  assert "zlib-ng/archive/refs/tags/2.2.5.tar.gz" in build
  assert "5b3b022489f3ced82384f06db1e13ba148cbce38c7941e424d6cb414416acd18" in build
  assert "zstd-1.5.7.tar.gz" in build
  assert "eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3" in build
  # Stale pins must not linger.
  assert "jemalloc-5.3.0.tar.bz2" not in build
  assert "mpdecimal-4.0.0.tar.gz" not in build
  assert "libffi-3.4.8.tar.gz" not in build


def test_zlib_ng_compat_opt_direct_link_no_explicit_apt_zlib():
  """zlib-ng ZLIB_COMPAT under /opt; CPython/extensions link it; no apt zlib* pin."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  build = _stage_body(dockerfile, "python-build")
  base = _stage_body(dockerfile, "hpcperfstats-base")
  assert "CMAKE_INSTALL_PREFIX=/opt/zlib-ng" in build
  assert "ZLIB_COMPAT=ON" in build
  assert "WITH_NATIVE_INSTRUCTIONS=ON" in build
  assert "-L/opt/zlib-ng/lib" in build
  assert "-Wl,-rpath,/opt/zlib-ng/lib" in build
  assert "-I/opt/zlib-ng/include" in build
  assert "ldd" in build and "/opt/zlib-ng" in build
  # CPython does not DT_NEEDED libz on bin/python* — only on zlib*.so (and
  # similarly libmpdec/_decimal, libffi/_ctypes). Grepping the interpreter
  # binary for zlib-ng fails the GIL/FT bake after make install.
  assert "name 'zlib*.so'" in build
  assert "name '_decimal*.so'" in build
  assert "name '_ctypes*.so'" in build
  assert not re.search(
      r"ldd /opt/python3\.14(?:t)?/bin/python3\.14t? \| grep '/opt/zlib-ng",
      build,
  )
  # Do not explicitly apt-install stock zlib (transitive Depends OK).
  assert "zlib1g-dev" not in build
  assert not re.search(
      r"apt-get install[^\n]*\bzlib1g\b",
      base,
  )
  assert "COPY --from=python-build /opt/zlib-ng" in base
  assert "/opt/zlib-ng/lib" in base
  # libz is link/rpath only — jemalloc keeps LD_PRELOAD; do not preload libz.
  for ln in base.splitlines():
    if "LD_PRELOAD" in ln:
      assert "zlib-ng" not in ln
      assert "libz.so" not in ln


def test_zstd_opt_direct_link_replaces_system_cli_enables_cpython_zstd():
  """zstd under /opt; CPython _zstd + system CLI from /opt (no apt zstd pin)."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  build = _stage_body(dockerfile, "python-build")
  base = _stage_body(dockerfile, "hpcperfstats-base")
  assert "PREFIX=/opt/zstd" in build
  assert "-L/opt/zstd/lib" in build
  assert "-Wl,-rpath,/opt/zstd/lib" in build
  assert "-I/opt/zstd/include" in build
  assert "/opt/zstd/lib/pkgconfig" in build
  assert "name '_zstd*.so'" in build
  assert "import _zstd" in build
  assert "compression.zstd" in build
  assert "COPY --from=python-build /opt/zstd" in base
  assert "/opt/zstd/lib" in base
  assert "/opt/zstd/bin/" in base
  assert "ln -sfn /opt/zstd/bin/$b" in base
  # zstd CLI gzip support must link zlib-ng (not stock apt zlib).
  assert re.search(
      r"PKG_CONFIG_PATH=.*?/opt/zlib-ng/lib/pkgconfig",
      build,
  )
  zstd_run = build[build.index("zstd-1.5.7.tar.gz") : build.index("mpdecimal-4.0.1")]
  assert "-I/opt/zlib-ng/include" in zstd_run
  assert "-L/opt/zlib-ng/lib" in zstd_run
  assert "-Wl,-rpath,/opt/zlib-ng/lib" in zstd_run
  assert "HAVE_ZLIB=1" in zstd_run
  assert "ldd /opt/zstd/bin/zstd" in zstd_run
  assert "/opt/zlib-ng/.*libz" in zstd_run
  # Runtime must not apt-install the stock CLI (our /opt binary replaces it).
  assert not re.search(r"apt-get install[^\n]*\bzstd\b", base)
  for ln in base.splitlines():
    if "LD_PRELOAD" in ln:
      assert "libzstd" not in ln
      assert "/opt/zstd" not in ln


def test_gil_and_ft_assign_zstd_so_before_test_n():
  """Both ABI post-install blocks must assign zstd_so before set -u expands it.

  Regression: GIL RUN had test -n \"$zstd_so\" without a find assignment
  (FT already correct), which fails builds under set -u.
  """
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  gil = build[
      build.index("--prefix=/opt/python3.14") : build.index("--prefix=/opt/python3.14t")
  ]
  ft = build[build.index("--prefix=/opt/python3.14t") :]
  gil_assign = (
      "zstd_so=\"$(find /opt/python3.14 -name '_zstd*.so' -type f | head -1)\""
  )
  ft_assign = (
      "zstd_so=\"$(find /opt/python3.14t -name '_zstd*.so' -type f | head -1)\""
  )
  assert gil_assign in gil
  assert ft_assign in ft
  assert gil.index(gil_assign) < gil.index('test -n "$zstd_so"')
  assert ft.index(ft_assign) < ft.index('test -n "$zstd_so"')


def test_jemalloc_configure_flags_and_no_initial_exec_tls():
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  assert "MAKEFLAGS=-j40" in build or "make -j40" in build
  assert "--prefix=/opt/jemalloc" in build
  assert "--disable-stats" in build
  assert "--disable-fill" in build
  assert "--disable-debug" in build
  assert "--with-lg-page=" in build
  # Flag must not appear on jemalloc ./configure argv (comments alone are fine).
  configure_lines = [
      ln for ln in build.splitlines() if "./configure" in ln or "--prefix=/opt/jemalloc" in ln
  ]
  assert configure_lines
  assert all("--disable-initial-exec-tls" not in ln for ln in configure_lines)
  assert "--disable-initial-exec-tls" not in "\n".join(
      ln for ln in build.splitlines() if not ln.lstrip().startswith("#")
  )
  assert "-march=native" in build
  assert "-flto" in build
  assert "-g0" in build
  assert "strip --strip-unneeded" in build
  assert "--with-pydebug" not in build
  assert "-ggdb" not in build
  assert "patchelf" not in build


def test_libmpdec_and_libffi_native_flags():
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  assert "/opt/mpdecimal" in build
  assert "-DCONFIG_64" in build
  assert "-DASM" in build
  assert "--with-system-libmpdec" in build
  assert "/opt/libffi" in build
  assert "--with-gcc-arch=native" in build
  assert "--disable-static" in build
  # --with-system-ffi was removed in CPython 3.12; libffi via pkg-config /
  # LIBFFI_CFLAGS + LIBFFI_LIBS only (passing the old flag warns).
  assert "--with-system-ffi" not in build
  assert "LIBFFI_CFLAGS=" in build
  assert "LIBFFI_LIBS=" in build
  assert "ldd" in build and "/opt/libffi/.*libffi" in build


def test_cpython_gil_without_mimalloc_ft_keeps_mimalloc_both_force_jemalloc():
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  assert "--with-lto" in build
  assert "--enable-optimizations" in build
  assert "-Wl,--no-as-needed" in build
  assert "-ljemalloc" in build
  # --without-mimalloc only on GIL configure (not on --disable-gil block).
  gil_cfg = build[
      build.index("--prefix=/opt/python3.14") : build.index("--prefix=/opt/python3.14t")
  ]
  ft_cfg = build[build.index("--prefix=/opt/python3.14t") :]
  assert "--without-mimalloc" in gil_cfg
  assert "--disable-gil" not in gil_cfg.split("--without-mimalloc")[0]
  assert "--without-mimalloc" not in ft_cfg.split("make -j40")[0]
  assert "--disable-gil" in ft_cfg


def test_runtime_jemalloc_both_ways_preload_and_ld_so_preload():
  base = _stage_body((_repo_root() / "Dockerfile").read_text(), "hpcperfstats-base")
  assert "ENV LD_PRELOAD=/opt/jemalloc/lib/libjemalloc.so.2" in base or (
      "LD_PRELOAD=/opt/jemalloc/lib/libjemalloc.so.2" in base
  )
  assert "/etc/ld.so.preload" in base
  assert "MALLOC_CONF=background_thread:false" in base
  assert "build-essential" not in base
  assert "gfortran" not in base
  assert "cmake" not in base
  assert "ninja-build" not in base
  assert "COPY --from=python-build /opt/jemalloc" in base
  assert "COPY --from=python-build /opt/python3.14 " in base
  assert "COPY --from=python-build /opt/python3.14t" in base
  # Prefer ${var##*/} over $(basename "$var"); this RUN must also avoid any "
  # because podman wraps RUN in sh -c "…" (Unterminated quoted string).
  assert "/usr/local/lib/${so##*/}" in base
  symlink_run = next(
      body
      for body in re.findall(
          r"^RUN /bin/bash -o pipefail -c '((?:\\'|[^'])*)'",
          base,
          flags=re.MULTILINE | re.DOTALL,
      )
      if "/etc/ld.so.preload" in body and "grep -F 1.5.7" in body
  )
  assert '"' not in symlink_run
  assert "grep -F 1.5.7" in symlink_run
  assert "_zstd_usr=$(readlink -f /usr/local/bin/zstd)" in symlink_run
  # trixie-slim has no `file` package; ELF checks stay in python-build only.
  assert "file -b" not in symlink_run


def test_dockerfile_avoids_nested_quotes_inside_command_substitution():
  """Podman/buildah: RUN is sh -c \"…\"; $(… \" …) and bare \" break quoting."""
  text = (_repo_root() / "Dockerfile").read_text()
  assert '$(basename "' not in text
  assert "$(basename \"" not in text
  assert "${so##*/}" in text
  assert "${_mkl_vers[0]##*/}" in text
  # Base ldconfig/symlink RUN (single-quoted -c) must contain no double quotes.
  base = _stage_body(text, "hpcperfstats-base")
  symlink_run = next(
      body
      for body in re.findall(
          r"^RUN /bin/bash -o pipefail -c '((?:\\'|[^'])*)'",
          base,
          flags=re.MULTILINE | re.DOTALL,
      )
      if "/etc/ld.so.preload" in body and "zstd --version" in body
  )
  assert '"' not in symlink_run


def test_hpcperfstats_base_copies_only_opt_prefixes_not_compile_trees():
  """Runtime stage must not COPY /usr/src or other compile trees from python-build."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  build = _stage_body(dockerfile, "python-build")
  base = _stage_body(dockerfile, "hpcperfstats-base")
  copies = [
      ln.strip()
      for ln in base.splitlines()
      if ln.strip().startswith("COPY --from=python-build")
  ]
  assert copies
  for ln in copies:
    assert "/opt/" in ln, ln
    assert "/usr/src" not in ln, ln
  assert "COPY --from=python-build /opt/zlib-ng" in base
  assert "COPY --from=python-build /opt/zstd" in base
  # Builder must wipe /usr/src trees before stage end (fail-closed).
  assert "test ! -d /usr/src/zlib-ng" in build
  assert "test ! -d /usr/src/zstd" in build
  assert "test ! -d /usr/src/jemalloc" in build
  assert "test ! -d /usr/src/python" in build
  assert "leftover /usr/src compile tree" in build
