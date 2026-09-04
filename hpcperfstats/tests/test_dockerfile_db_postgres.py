"""Contract tests for services-conf/db.Dockerfile (homemade PG18 Alpine bake)."""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _dockerfile() -> str:
    return (_repo_root() / "services-conf" / "db.Dockerfile").read_text()


def test_db_dockerfile_pins_alpine_3_24_not_latest_or_trixie() -> None:
    text = _dockerfile()
    assert "ARG ALPINE_VERSION=3.24.1" in text
    assert "FROM alpine:${ALPINE_VERSION}" in text
    assert "alpine:latest" not in text
    assert "alpine:edge" not in text
    assert "debian:trixie" not in text


def test_db_dockerfile_pins_postgres_18_sha_and_timescale() -> None:
    text = _dockerfile()
    assert "ARG PG_VERSION=18.6" in text
    assert "555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f" in text
    assert "ARG TIMESCALEDB_VERSION=2.29.2" in text
    assert "3817f8acb8e167bf22b873a4c4e17d801089ed5a34c232eedd4f86dc222c8dc6" in text


def test_db_dockerfile_pins_jemalloc_icu_liburing_lz4_zlib_ng_zstd() -> None:
    text = _dockerfile()
    assert "ARG JEMALLOC_VERSION=5.3.1" in text
    assert "3826bc80232f22ed5c4662f3034f799ca316e819103bdc7bb99018a421706f92" in text
    assert "ARG ICU_VERSION=78.3" in text
    assert "3a2e7a47604ba702f345878308e6fefeca612ee895cf4a5f222e7955fabfe0c0" in text
    assert "ARG LIBURING_VERSION=2.15" in text
    assert "8d052f2622dcb3678cbaee5ff582a87572672a6c0a56533cdda5b65cb636120a" in text
    assert "ARG LZ4_VERSION=1.10.0" in text
    assert "537512904744b35e232912055ccf8ec66d768639ff3abe5788d90d792ec5f48b" in text
    assert "ARG ZLIB_NG_VERSION=2.2.5" in text
    assert "5b3b022489f3ced82384f06db1e13ba148cbce38c7941e424d6cb414416acd18" in text
    assert "ARG ZSTD_VERSION=1.5.7" in text
    assert "eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3" in text
    assert "jemalloc-${JEMALLOC_VERSION}.tar.bz2" in text
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
    assert "if apk info -e zlib" in text
    assert "/opt/zlib-ng/.+libz" in text
    assert "ldd /opt/zstd/bin/zstd" in text


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
    # Must not pass docker-library's --disable-rpath to ./configure.
    configure_block = text[text.index("./configure") : text.index("make -j")]
    assert "--disable-rpath" not in configure_block
    # PG18 removed --enable-thread-safety (always on); --enable-option-checking=fatal
    # rejects unrecognized options (bake failure on prod: 2026-09-04).
    assert "--enable-thread-safety" not in configure_block


def test_db_dockerfile_postgres_and_timescale_prefer_512_vector_width() -> None:
    text = _dockerfile()
    assert "-march=native -mprefer-vector-width=512" in text
    assert "OPT_CFLAGS_PG" in text
    assert "CMAKE_C_FLAGS=" in text
    assert "APACHE_ONLY" in text  # mentioned only to forbid ON
    assert "-DAPACHE_ONLY" not in text


def test_db_dockerfile_timescale_229_no_external_lz4_zstd_ldd_gate() -> None:
    """Timescale must not DT_NEEDED jemalloc/lz4/zstd; musl ldd on .so is misleading.

    Bake failure (hpcperfstats02 2026-09-04): grep /opt/lz4 on timescaledb.so ldd
    failed because 2.29.2 has no external lz4/zstd link; musl ldd also prints
    unresolved PG backend symbols that only exist when loaded by postgres.

    Second bake: timescaledb.so still DT_NEEDED libjemalloc because cmake uses
    `pg_config --ldflags` (-ljemalloc from postgres bake). unset LDFLAGS alone
    is insufficient; wrap pg_config. Also `! grep` under set -e does not fail
    the layer when the forbidden pattern matches (bash quirk) — use if/exit 1.

    Third bake (podman): shell heredoc (`cat <<EOF`) inside RUN is parsed as a
    bogus CHMOD instruction — use printf to write the wrap script inline.

    Fourth bake (Alpine BusyBox sed): `s|(^|…)|…|` is invalid — `|` used as both
    delimiter and regex OR. Use `#` delimiters / plain `s/-ljemalloc//g`.
    """
    text = _dockerfile()
    ts_run = text[text.index("# --- TimescaleDB") : text.index("# Prune docs/man")]
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
    assert "if grep -E 'liblz4|libzstd|libjemalloc' /tmp/timescaledb.needed" in ts_run
    assert 'echo "timescaledb.so must not DT_NEEDED jemalloc/lz4/zstd"' in ts_run
    assert "! grep -E 'liblz4|libzstd|libjemalloc' /tmp/timescaledb.needed" not in ts_run
    # Must not require DT_NEEDED lz4/zstd on the extension (false fail-closed).
    assert "grep -E '/opt/lz4/.+liblz4' /tmp/timescaledb" not in ts_run
    assert "grep -E '/opt/zstd/.+libzstd' /tmp/timescaledb" not in ts_run
    # Postgres binary still must link /opt codecs (separate stage).
    assert "grep -E '/opt/lz4/.+liblz4' /tmp/postgres.ldd" in text
    assert not (_repo_root() / "services-conf" / "db-pg-config-no-jemalloc.sh").exists()


def test_db_dockerfile_jemalloc_ld_preload_and_fail_closed_ldd() -> None:
    text = _dockerfile()
    assert "LD_PRELOAD=/opt/jemalloc/lib/libjemalloc.so.2" in text
    assert "ldd /usr/local/bin/postgres" in text
    assert "/opt/jemalloc/.+libjemalloc" in text
    assert "/opt/liburing/.+liburing" in text
    assert "/opt/zlib-ng/.+libz" in text
    # Runtime apk-zlib gate must use if/exit (not "! apk info") under set -e.
    assert "if apk info -e zlib" in text
    assert "! apk info -e zlib" not in text


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
