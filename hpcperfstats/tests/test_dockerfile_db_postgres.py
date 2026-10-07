"""Contract tests for services-conf/db.Dockerfile (homemade PG18 Alpine bake)."""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _dockerfile() -> str:
  return (_repo_root() / "services-conf" / "db.Dockerfile").read_text()


def test_db_dockerfile_uses_clang22_apk_toolchain() -> None:
  text = _dockerfile()
  assert "clang22" in text
  assert "compiler-rt" in text
  assert "alpine_pgo_profile_runtime_link.sh" in text
  assert "lld22" in text
  assert "ENV CC=clang CXX=clang++" in text
  assert "HPC_ALPINE_LLVM_TOOLCHAIN=1" in text
  assert "NM=/usr/lib/llvm22/bin/llvm-nm" in text
  assert "gcc-toolchain" not in text
  assert "assert_gcc_min_version" not in text
  assert "pgo_clang_flags.sh" in text
  assert "from=pgo,source=.,target=/root/.hpcperfstats_pgo" in text
  assert "clang_march_native_probe.sh" in text
  assert "-Q --help=target" not in text
  assert "-flto=thin" in text
  assert 'ENV OPT_CFLAGS_LIBS="-O2' in text


def test_db_dockerfile_pins_alpine_3_24_not_latest_or_trixie() -> None:
  text = _dockerfile()
  assert "ARG ALPINE_VERSION=3.24.2" in text
  assert "FROM alpine:${ALPINE_VERSION}" in text
  first_from = text.index("FROM ")
  alpine_arg = text.index("ARG ALPINE_VERSION=3.24.2")
  assert alpine_arg < first_from, (
    "ALPINE_VERSION must be declared before first FROM"
  )


def test_db_dockerfile_apk_includes_bzip2_for_source_tarballs() -> None:
  text = _dockerfile()
  assert (
    "bzip2"
    in text.split("apk add", 1)[1].split("clang_march_native_probe", 1)[0]
  )
  assert "alpine:latest" not in text
  assert "alpine:edge" not in text
  assert "debian:trixie" not in text


def test_db_dockerfile_pins_postgres_18_sha_and_timescale() -> None:
  text = _dockerfile()
  assert "ARG PG_VERSION=18.6" in text
  assert (
    "555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f" in text
  )
  assert "ARG TIMESCALEDB_VERSION=2.30.2" in text
  assert (
    "a7003a70836477dc8d575d95a4c515d8a22ed219d0cb03b3640bb813f04e1b42" in text
  )


def test_db_dockerfile_pins_jemalloc_icu_liburing_lz4_zlib_ng_zstd() -> None:
  text = _dockerfile()
  assert "ARG JEMALLOC_VERSION=5.4.0" in text
  assert (
    "200776fac271093e7c2f21edd6d62657ecd2be578d9328633f2a86bfa6ef4f1d" in text
  )
  assert "ARG ICU_VERSION=78.3" in text
  assert (
    "3a2e7a47604ba702f345878308e6fefeca612ee895cf4a5f222e7955fabfe0c0" in text
  )
  assert "ARG LIBURING_VERSION=2.15" in text
  assert (
    "8d052f2622dcb3678cbaee5ff582a87572672a6c0a56533cdda5b65cb636120a" in text
  )
  assert "ARG LZ4_VERSION=1.10.0" in text
  assert (
    "537512904744b35e232912055ccf8ec66d768639ff3abe5788d90d792ec5f48b" in text
  )
  assert "ARG ZLIB_NG_VERSION=2.3.3" in text
  assert (
    "f9c65aa9c852eb8255b636fd9f07ce1c406f061ec19a2e7d508b318ca0c907d1" in text
  )
  assert "ARG ZSTD_VERSION=1.5.7" in text
  assert (
    "eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3" in text
  )
  assert "jemalloc-${JEMALLOC_VERSION}.tar.bz2" in text
  assert "hpcperfstats_configure_cflags db/jemalloc" in text
  assert "hpcperfstats_bake_cflags db/jemalloc" in text
  assert "hpcperfstats_alpine_thinlto_ldflags" in text
  jem_run = text[text.index("# --- jemalloc") : text.index("# --- lz4")]
  assert "hpcperfstats_alpine_jemalloc_export_link_toolchain" in jem_run
  assert "EXTRA_LDFLAGS=" in jem_run
  assert "LDFLAGS=" in jem_run and 'AR="${AR}"' in jem_run
  assert 'NM="${NM}"' in jem_run, (
    "jemalloc ThinLTO .sym.o needs llvm-nm, not GNU nm"
  )
  assert jem_run.index(
    "hpcperfstats_alpine_jemalloc_export_link_toolchain"
  ) < jem_run.index("./configure"), (
    "export llvm nm/ar before jemalloc configure"
  )
  pre_cfg = jem_run[: jem_run.index("./configure")]
  assert 'NM="${NM}"' in pre_cfg and 'AR="${AR}"' in pre_cfg, (
    "jemalloc ./configure must receive llvm nm/ar (Makefile bakes NM=)"
  )
  assert "zstd-${ZSTD_VERSION}.tar.gz" in text
  assert "zlib-ng/archive/refs/tags/${ZLIB_NG_VERSION}.tar.gz" in text


def test_db_dockerfile_uses_zlib_ng_not_apk_zlib() -> None:
  text = _dockerfile()
  assert "CMAKE_INSTALL_PREFIX=/opt/zlib-ng" in text
  assert "ZLIB_COMPAT=ON" in text
  assert "WITH_NATIVE_INSTRUCTIONS=ON" in text
  assert "HAVE_ZLIB=1" in text
  assert "HAVE_LZ4=1" in text
  assert "-I/opt/zlib-ng/include" in text
  assert "-L/opt/zlib-ng/lib" in text
  assert "-Wl,-rpath,/opt/zlib-ng/lib" in text
  assert "-I/opt/lz4/include" in text
  assert "-L/opt/lz4/lib" in text
  # zstd bake must rpath both codecs (zlib-ng + lz4), not only the global PG LDFLAGS.
  zstd_run = text[text.index("# --- zstd") : text.index("ENV PKG_CONFIG_PATH=")]
  assert "hpcperfstats_pgo_ensure_db_zstd_upstream" in zstd_run
  assert "HAVE_LZ4=1" in zstd_run
  assert "/opt/lz4" in zstd_run
  assert "/opt/lz4/.+liblz4" in zstd_run
  assert "COPY --from=db-build /opt/zlib-ng" in text
  assert not re.search(r"(?m)^\s+zlib-dev\b", text)
  # Runtime must not apk-add stock zlib (comments mentioning zlib-ng / forbid are OK).
  runtime = text.split("FROM alpine:${ALPINE_VERSION}", 2)[-1]
  assert not re.search(r"(?m)^\s+zlib\s*\\?\s*$", runtime)
  assert not re.search(r"apk add[^\n]*\bzlib\b", runtime)
  assert "apk add --no-cache $runDeps" in text
  # Do not fail-closed on `apk info -e zlib`: openssl/libxml2 pull it transitively.
  assert "if apk info -e zlib" not in text
  assert "/opt/zlib-ng/.+libz" in text
  assert "ldd /opt/zstd/bin/zstd" in text
  # Linked-ABI gate: postgres/zstd must not resolve apk /lib|/usr/lib libz.
  assert "/lib/libz\\.|/usr/lib/libz\\." in text or "/lib/libz\\." in text


def test_db_dockerfile_postgres_and_timescale_heredocs_avoid_inline_hash_comments() -> (
  None
):
  """Regression: '; # comment' on one line comments out ./configure and bootstrap (bake no-op)."""
  text = _dockerfile()
  pg_run = text[
    text.index("# --- PostgreSQL") : text.index("# --- TimescaleDB")
  ]
  ts_run = text[
    text.index("# --- TimescaleDB") : text.index("# Prune docs/man")
  ]
  assert "; # Intentionally omit docker-library" not in pg_run
  assert "./configure --enable-option-checking=fatal" in pg_run
  assert "make -j" in pg_run and "world-bin" in pg_run
  assert "postgres --version" in pg_run
  assert "; # BusyBox sed" not in ts_run
  assert "pg-config-wrap/pg_config" in ts_run
  assert "./bootstrap -DCMAKE_BUILD_TYPE=Release" in ts_run


def test_db_dockerfile_links_opt_icu_liburing_lz4_zstd_into_postgres() -> None:
  text = _dockerfile()
  assert "--with-icu" in text
  assert "--with-liburing" in text
  assert "--with-lz4" in text
  assert "--with-zstd" in text
  assert "--with-llvm" in text
  assert "/opt/icu" in text
  assert "/opt/liburing" in text
  assert "/opt/lz4" in text
  assert "/opt/zstd" in text
  assert "/opt/jemalloc" in text
  assert "/opt/zlib-ng" in text
  assert "-Wl,-rpath,/opt/icu/lib" in text
  assert "-Wl,-rpath,/opt/liburing/lib" in text
  assert "-Wl,-rpath,/opt/lz4/lib" in text
  assert "-Wl,-rpath,/opt/zstd/lib" in text
  assert "-Wl,-rpath,/opt/zlib-ng/lib" in text
  assert "-ljemalloc -lstdc++" in text
  # Must not pass docker-library's --disable-rpath to postgres ./configure.
  # Slice stops before the fail-closed config.status grep (which names the flag).
  pg_run = text[
    text.index("# --- PostgreSQL") : text.index("# --- TimescaleDB")
  ]
  configure_block = pg_run[
    pg_run.index("./configure") : pg_run.index(
      "if grep -q -- '--disable-rpath'"
    )
  ]
  assert "--disable-rpath" not in configure_block
  # PG18 removed --enable-thread-safety (always on); --enable-option-checking=fatal
  # rejects unrecognized options (bake failure on prod: 2026-09-04).
  assert "--enable-thread-safety" not in configure_block
  assert 'gnuArch="$(clang -dumpmachine)"' in pg_run
  assert "hpcperfstats_pgo_ensure_db_postgresql_link_dirs" in pg_run
  assert "hpcperfstats_bake_cflags db/postgresql pg" in pg_run
  assert 'export LLVM_CONFIG="${LLVM_CONFIG}"' in pg_run
  assert "LLVM_CONFIG=/usr/lib/llvm22/bin/llvm-config" in text
  assert "pg_bake_ldflags" not in pg_run


def test_db_dockerfile_postgres_and_timescale_prefer_512_vector_width() -> None:
  text = _dockerfile()
  assert "-march=native -mprefer-vector-width=512" in text
  assert "OPT_CFLAGS_PG" in text
  assert "CMAKE_C_FLAGS=" in text
  assert "APACHE_ONLY" in text  # mentioned only to forbid ON
  assert "-DAPACHE_ONLY" not in text


def test_db_dockerfile_libs_mtune_and_lz4_heapmode() -> None:
  """OPT_CFLAGS_LIBS include -mtune=native; lz4 bake sets -DLZ4_HEAPMODE=0."""
  text = _dockerfile()
  assert (
    'ENV OPT_CFLAGS_LIBS="-O2 -march=native -mtune=native -flto=thin --ld-path=/usr/bin/ld.lld -g0"'
    in text
  )
  assert "-mtune=native" in text
  lz4_run = text[text.index("# --- lz4 ---") : text.index("# --- ICU")]
  assert "-DLZ4_HEAPMODE=0" in lz4_run
  assert "hpcperfstats_bake_cflags db/lz4" in lz4_run
  assert 'AR="${AR}"' in lz4_run and 'NM="${NM}"' in lz4_run
  assert "HPC_THINLTO_LDFLAGS" in lz4_run


def test_db_dockerfile_opt_libs_use_llvm_binutils_not_gnu() -> None:
  """Regression: GNU nm on ThinLTO .sym.o → file format not recognized (jemalloc)."""
  text = _dockerfile()
  zlib_run = text[text.index("# --- zlib-ng") : text.index("# --- zstd")]
  assert "CMAKE_AR=" in zlib_run and "CMAKE_NM=" in zlib_run
  assert "CMAKE_EXE_LINKER_FLAGS=" in zlib_run
  icu_run = text[text.index("# --- ICU") : text.index("# --- liburing")]
  assert 'NM="${NM}"' in icu_run
  zstd_run = text[text.index("# --- zstd") : text.index("ENV PKG_CONFIG_PATH=")]
  assert "HPC_THINLTO_LDFLAGS" in zstd_run
  assert 'AR="${AR}"' in zstd_run
  pg_run = text[
    text.index("# --- PostgreSQL") : text.index("# --- TimescaleDB")
  ]
  assert "HPC_THINLTO_LDFLAGS" in pg_run
  assert 'AR="${AR}"' in pg_run and 'NM="${NM}"' in pg_run
  ts_run = text[
    text.index("# --- TimescaleDB") : text.index("# Prune docs/man")
  ]
  assert "CMAKE_AR=" in ts_run and "CMAKE_NM=" in ts_run


def test_db_dockerfile_opt_lib_bake_order_cache_and_deps() -> None:
  """Slowest-changing independent /opt pins first; zstd after lz4 + zlib-ng.

  Docker layer cache is linear: bumping a fast pin (zlib-ng) must not
  rebuild jemalloc/lz4/icu/liburing. zstd DT_NEEDED those two codecs, so
  its RUN stays after both even though zstd itself ships ~yearly.
  """
  text = _dockerfile()
  markers = [
    "ARG ALPINE_VERSION=",
    "ARG JEMALLOC_VERSION=",
    "ARG LZ4_VERSION=",
    "ARG ICU_VERSION=",
    "ARG LIBURING_VERSION=",
    "ARG ZLIB_NG_VERSION=",
    "ARG ZSTD_VERSION=",
    "ARG PG_VERSION=",
    "ARG TIMESCALEDB_VERSION=",
  ]
  idxs = [text.index(m) for m in markers]
  assert idxs == sorted(idxs), (
    "db.Dockerfile /opt bake order must be Alpine, jemalloc, lz4, "
    "ICU, liburing, zlib-ng, zstd, Postgres, Timescale"
  )
  assert text.index("# --- lz4 ---") < text.index("# --- zstd")
  assert text.index("# --- zlib-ng") < text.index("# --- zstd")
  runtime_copies = [
    "COPY --from=db-build /opt/jemalloc",
    "COPY --from=db-build /opt/lz4",
    "COPY --from=db-build /opt/icu",
    "COPY --from=db-build /opt/liburing",
    "COPY --from=db-build /opt/zlib-ng",
    "COPY --from=db-build /opt/zstd",
    "COPY --from=db-build /usr/local",
  ]
  copy_idxs = [text.index(m) for m in runtime_copies]
  assert copy_idxs == sorted(copy_idxs)


def test_db_dockerfile_timescale_229_no_external_lz4_zstd_ldd_gate() -> None:
  """Timescale must not DT_NEEDED jemalloc/lz4/zstd; musl ldd on .so is misleading.

  Bake failure (hpcperfstats02 2026-09-04): grep /opt/lz4 on timescaledb.so ldd
  failed because 2.29.2 has no external lz4/zstd link; musl ldd also prints
  unresolved PG backend symbols that only exist when loaded by postgres.

  Second bake: timescaledb.so still DT_NEEDED libjemalloc because cmake uses
  `pg_config --ldflags` (-ljemalloc from postgres bake). unset LDFLAGS alone
  is insufficient; wrap pg_config. Also `! grep` under set -e does not fail
  the layer when the forbidden pattern matches (bash quirk) - use if/exit 1.

  Third bake (podman): shell heredoc (`cat <<EOF`) inside RUN is parsed as a
  bogus CHMOD instruction - use printf to write the wrap script inline.

  Fourth bake (Alpine BusyBox sed): `s|(^|...)|...|` is invalid - `|` used as both
  delimiter and regex OR. Use `#` delimiters / plain `s/-ljemalloc//g`.
  """
  text = _dockerfile()
  ts_run = text[
    text.index("# --- TimescaleDB") : text.index("# Prune docs/man")
  ]
  assert "hpcperfstats_pgo_ensure_db_timescaledb_link_dirs" in ts_run
  assert "unset LDFLAGS" in ts_run
  assert "pg-config-wrap" in ts_run
  assert "printf '%s\\n'" in ts_run
  assert "--ldflags|--libs)" in ts_run
  assert "s/-ljemalloc//g" in ts_run
  assert "s#-L/opt/jemalloc" in ts_run
  assert "s#-Wl,-rpath,/opt/jemalloc" in ts_run
  # Forbidden: pipe delimiter + alternation (BusyBox "bad option in substitution").
  assert "s|(^|" not in ts_run
  assert "sed -i -E" not in ts_run
  assert "COPY db-pg-config-no-jemalloc.sh" not in ts_run
  assert "<<'EOF'" not in ts_run
  assert "scanelf -n" in ts_run
  assert "if grep -qi 'APACHE_ONLY:BOOL=ON'" in ts_run
  # Fail-closed must use if/exit (not "! grep") so set -e actually stops the bake.
  assert (
    "if grep -E 'liblz4|libzstd|libjemalloc' /tmp/timescaledb.needed" in ts_run
  )
  assert 'echo "timescaledb.so must not DT_NEEDED jemalloc/lz4/zstd"' in ts_run
  assert (
    "! grep -E 'liblz4|libzstd|libjemalloc' /tmp/timescaledb.needed"
    not in ts_run
  )
  # Must not require DT_NEEDED lz4/zstd on the extension (false fail-closed).
  assert "grep -E '/opt/lz4/.+liblz4' /tmp/timescaledb" not in ts_run
  assert "grep -E '/opt/zstd/.+libzstd' /tmp/timescaledb" not in ts_run
  # Postgres binary still must link /opt codecs (separate stage).
  assert "grep -E '/opt/lz4/.+liblz4' /tmp/postgres.ldd" in text
  assert not (
    _repo_root() / "services-conf" / "db-pg-config-no-jemalloc.sh"
  ).exists()


def test_db_dockerfile_jemalloc_ld_preload_and_fail_closed_ldd() -> None:
  text = _dockerfile()
  assert "LD_PRELOAD=/opt/jemalloc/lib/libjemalloc.so.2" in text
  assert "ldd /usr/local/bin/postgres" in text
  assert "/opt/jemalloc/.+libjemalloc" in text
  assert "/opt/liburing/.+liburing" in text
  assert "/opt/zlib-ng/.+libz" in text
  # Linked ABI is gated by ldd (apk zlib may exist as openssl transitive dep).
  assert "runtime postgres linked apk zlib" in text
  assert "if apk info -e zlib" not in text
  assert "! apk info -e zlib" not in text


def test_db_entrypoint_pg18_bind_chown_hint() -> None:
  """PG18 bind at /var/lib/postgresql needs host uid 70; entrypoint must explain mkdir fails."""
  text = (
    _repo_root() / "services-conf" / "db-docker-entrypoint.sh"
  ).read_text()
  assert "chown postgres:postgres /var/lib/postgresql" in text
  assert "chown -R 70:70" in text
  assert "cannot create" in text
  assert "NFS root_squash" in text


def test_db_entrypoint_scripts_shipped() -> None:
  """db.Dockerfile COPY needs these scripts; root *.sh must not hide them from git."""
  import subprocess

  root = _repo_root() / "services-conf"
  for name in ("db-docker-entrypoint.sh", "db-docker-ensure-initdb.sh"):
    path = root / name
    assert path.is_file(), path
    rel = f"services-conf/{name}"
    # Must be trackable (gitignore negation). Prod bake failed when ignored.
    # Use repo-relative paths: absolute paths can make check-ignore lie.
    check = subprocess.run(
      ["git", "check-ignore", "-q", rel],
      cwd=_repo_root(),
      capture_output=True,
      text=True,
      check=False,
    )
    assert check.returncode == 1, (
      f"{rel} is gitignored; add !{rel} under .gitignore"
    )
  text = _dockerfile()
  assert "COPY db-docker-entrypoint.sh" in text
  assert "COPY db-docker-ensure-initdb.sh" in text
  ignore = (_repo_root() / ".gitignore").read_text()
  assert "!services-conf/db-docker-entrypoint.sh" in ignore
  assert "!services-conf/db-docker-ensure-initdb.sh" in ignore
