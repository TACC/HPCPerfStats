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
  # Stale pins must not linger.
  assert "jemalloc-5.3.0.tar.bz2" not in build
  assert "mpdecimal-4.0.0.tar.gz" not in build
  assert "libffi-3.4.8.tar.gz" not in build


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
  assert "/opt/libffi" in build
  assert "--with-gcc-arch=native" in build
  assert "--disable-static" in build


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
