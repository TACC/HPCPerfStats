#!/usr/bin/env bash
# Build third-party dependencies as static archives under PREFIX, then configure
# and compile hpcperfstatsd with --enable-all-static so the binary does not depend
# on those shared libraries at runtime.
#
# You still need a normal C/C++ toolchain (gcc, g++, make, cmake, pkg-config).
# The glibc NSS stack normally remains dynamic unless you link with -static (not
# recommended here); see the notes at the bottom of this script.
#
# Usage:
#   ./scripts/build_static_bundle.sh              # deps + monitor (PREFIX default: <repo>/.build/prefix-static)
#   ./scripts/build_static_bundle.sh --deps-only  # static .a deps only (no hpcperfstatsd)
#   ./scripts/ensure_dotbuild_prefix_static.sh    # --deps-only with PREFIX=<repo>/.build/prefix
# Environment:
#   PREFIX          Install tree (default: <repo>/.build/prefix-static)
#   SRCDIR          Download and build under this directory (default: <repo>/.build/src-static)
#   JOBS            Parallel jobs (default: nproc)
#   SKIP_DEPS       If set to 1, skip building deps (use existing PREFIX)
#   SKIP_CLEAN      If set to 1, do not remove .build-static before configuring
#                   (default: remove it so a prior failed/partial monitor build cannot
#                   poison the next run)
#
# GPU: configure uses --enable-gpu=auto (default). If lspci on the build host shows an
# NVIDIA GPU and libdcgm is available, nvidia_gpu is compiled in (links libdcgm after
# -Wl,-Bdynamic). Build on a GPU-less login node without libdcgm: pass --disable-gpu
# as an extra CONFIGURE_ARGS, or build on a node that has both. Cross-deploy: use
# --enable-gpu so the GPU path is built whenever libdcgm is present, independent of lspci.
#
# Pinned versions: edit STATIC_PIN_* below. Runtime overrides: LIBEV_VER,
# RABBITMQ_VER, LIKWID_TAG, and *_URL_FMT for mirrors.
#
# Architecture:
#   x86_64 / i686: builds LIKWID static libs and configures --with-cpu-counter-backend=likwid.
#   Other (e.g. aarch64, arm64): builds libev + rabbitmq-c only; configures with
#   --with-cpu-counter-backend=dcgm. DCGM C headers are vendored under
#   monitor/third_party/nvidia-dcgm (from NVIDIA gpu-monitoring-tools bindings); you still
#   need libdcgm from NVIDIA DCGM at link/runtime.
#
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT="$(cd "${MONITOR_DIR}/../.." && pwd)"

# =============================================================================
# Pinned dependency versions / refs (single place to edit)
# =============================================================================
# Release tarballs: upstream version strings exactly as used in URLs and paths.
STATIC_PIN_LIBEV_VERSION="4.33"
STATIC_PIN_RABBITMQ_C_VERSION="0.14.0"
# LIKWID: Git tag name without a leading "v" (archive is .../tags/v${VER}.tar.gz).
STATIC_PIN_LIKWID_VERSION="5.3.0"
# Optional: override tarball base URLs (must contain a single %s for version where used).
STATIC_PIN_LIBEV_URL_FMT="http://dist.schmorp.de/libev/libev-%s.tar.gz"
# rabbitmq-c: source-of-truth repo is alanxz/rabbitmq-c; github.com/rabbitmq/rabbitmq-c archive URLs return 404.
STATIC_PIN_RABBITMQ_C_URL_FMT="https://github.com/alanxz/rabbitmq-c/archive/refs/tags/v%s.tar.gz"
STATIC_PIN_LIKWID_URL_FMT="https://github.com/RRZE-HPC/likwid/archive/refs/tags/v%s.tar.gz"
# =============================================================================

PREFIX="${PREFIX:-${REPO_ROOT}/.build/prefix-static}"
SRCDIR="${SRCDIR:-${REPO_ROOT}/.build/src-static}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"
SKIP_DEPS="${SKIP_DEPS:-0}"
SKIP_CLEAN="${SKIP_CLEAN:-0}"

# Effective pins (env overrides keep legacy names working).
LIBEV_VER="${LIBEV_VER:-${STATIC_PIN_LIBEV_VERSION}}"
RABBITMQ_VER="${RABBITMQ_VER:-${STATIC_PIN_RABBITMQ_C_VERSION}}"
LIKWID_TAG="${LIKWID_TAG:-${STATIC_PIN_LIKWID_VERSION}}"
LIBEV_URL_FMT="${LIBEV_URL_FMT:-${STATIC_PIN_LIBEV_URL_FMT}}"
RABBITMQ_C_URL_FMT="${RABBITMQ_C_URL_FMT:-${STATIC_PIN_RABBITMQ_C_URL_FMT}}"
LIKWID_URL_FMT="${LIKWID_URL_FMT:-${STATIC_PIN_LIKWID_URL_FMT}}"

fetch_url_validate_gzip() {
  local dest="$1"
  local url="$2"
  case "${dest}" in
  *.tar.gz | *.tgz) ;;
  *) return 0 ;;
  esac
  if ! gzip -t "${dest}" 2>/dev/null; then
    echo "fetch_url: not a valid gzip archive (wrong URL, error page, or corrupt download): ${url}" >&2
    rm -f "${dest}"
    exit 1
  fi
}

fetch_url() {
  local url="$1"
  local dest="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "${dest}" "${url}" || {
      rm -f "${dest}"
      exit 1
    }
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "${dest}" "${url}" || {
      rm -f "${dest}"
      exit 1
    }
  else
    echo "Need curl or wget to download ${url}" >&2
    exit 1
  fi
  fetch_url_validate_gzip "${dest}" "${url}"
}

# True when this host matches monitor configure's x86 LIKWID backend (see configure.ac is_x86).
is_x86_build_host() {
  case "$(uname -m)" in
  x86_64 | i?86) return 0 ;;
  *) return 1 ;;
  esac
}

mkdir -p "${SRCDIR}" "${PREFIX}/include" "${PREFIX}/lib" "${PREFIX}/lib/pkgconfig"

export PATH="${PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${PREFIX}/lib/pkgconfig:${PREFIX}/lib64/pkgconfig:${PKG_CONFIG_PATH:-}"

build_libev() {
  local d="${SRCDIR}/libev-${LIBEV_VER}"
  local t="${SRCDIR}/libev-${LIBEV_VER}.tar.gz"
  if test ! -d "$d"; then
    fetch_url "$(printf "${LIBEV_URL_FMT}" "${LIBEV_VER}")" "$t"
    tar -C "${SRCDIR}" -xzf "$t"
  fi
  cd "$d"
  ./configure --prefix="${PREFIX}" --enable-static --disable-shared
  make -j"${JOBS}"
  make install
}

build_rabbitmq_c() {
  local d="${SRCDIR}/rabbitmq-c-${RABBITMQ_VER}"
  local t="${SRCDIR}/rabbitmq-c-${RABBITMQ_VER}.tar.gz"
  if test ! -d "$d"; then
    fetch_url "$(printf "${RABBITMQ_C_URL_FMT}" "${RABBITMQ_VER}")" "$t"
    tar -C "${SRCDIR}" -xzf "$t"
  fi
  mkdir -p "${d}/build"
  cd "${d}/build"
  cmake .. \
    -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DBUILD_SHARED_LIBS=OFF \
    -DBUILD_STATIC_LIBS=ON \
    -DENABLE_SSL_SUPPORT=OFF \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_TOOLS=OFF \
    -DBUILD_TESTS=OFF
  cmake --build . -j"${JOBS}"
  cmake --install .
}

build_likwid() {
  local d="${SRCDIR}/likwid-${LIKWID_TAG}"
  local t="${SRCDIR}/likwid-${LIKWID_TAG}.tar.gz"
  if test ! -d "$d"; then
    fetch_url "$(printf "${LIKWID_URL_FMT}" "${LIKWID_TAG}")" "$t"
    tar -C "${SRCDIR}" -xzf "$t"
    if test ! -d "$d"; then
      if test -d "${SRCDIR}/likwid-${LIKWID_TAG#v}"; then
        mv "${SRCDIR}/likwid-${LIKWID_TAG#v}" "$d"
      else
        echo "Could not locate extracted LIKWID directory under ${SRCDIR}" >&2
        exit 1
      fi
    fi
  fi
  cd "$d"
  if grep -q '^SHARED_LIBRARY = true' config.mk 2>/dev/null; then
    sed -i 's/^SHARED_LIBRARY = true/SHARED_LIBRARY = false/' config.mk
  fi
  make -j"${JOBS}" PREFIX="${PREFIX}" INSTALLED_PREFIX="${PREFIX}" \
    BUILDDAEMON=false BUILDFREQ=false BUILD_SYSFEATURES=false
  make install PREFIX="${PREFIX}" INSTALLED_PREFIX="${PREFIX}" \
    BUILDDAEMON=false BUILDFREQ=false BUILD_SYSFEATURES=false
}

build_monitor() {
  if test "${SKIP_CLEAN}" != "1"; then
    if test -d "${MONITOR_DIR}/.build-static"; then
      echo "Removing prior monitor build tree (failed or stale): ${MONITOR_DIR}/.build-static"
      rm -rf "${MONITOR_DIR}/.build-static"
    fi
  fi
  mkdir -p "${MONITOR_DIR}/.build-static"
  cd "${MONITOR_DIR}/.build-static"
  if test ! -f "${MONITOR_DIR}/configure"; then
    (cd "${MONITOR_DIR}" && autoreconf -fi)
  fi
  export CPPFLAGS="-I${PREFIX}/include ${CPPFLAGS:-}"
  export LDFLAGS="-L${PREFIX}/lib -L${PREFIX}/lib64 ${LDFLAGS:-}"
  # Prefer static archives from our tree (no RPATH to PREFIX for runtime).
  # --disable-infiniband omits libibmad-linked collectors (ib_ext, ib_sw); sysfs-only ib remains.
  local -a cfg=(
    --enable-all-static
    --with-systemduserunitdir=no
    --disable-lustre
    --disable-infiniband
    --disable-mic
    --disable-amd-gpu
    --disable-opa
  )
  if is_x86_build_host; then
    cfg+=(--with-cpu-counter-backend=likwid)
  else
    cfg+=(--with-cpu-counter-backend=dcgm)
    echo "Non-x86 host ($(uname -m)): using DCGM CPU backend; ensure libdcgm is available (dcgm_agent.h is vendored in third_party/nvidia-dcgm)." >&2
  fi
  "${MONITOR_DIR}/configure" "${cfg[@]}" "$@"
  make -j"${JOBS}"
  echo ""
  echo "Built: ${MONITOR_DIR}/.build-static/src/hpcperfstatsd"
  if command -v ldd >/dev/null 2>&1; then
    if is_x86_build_host; then
      echo "Dynamic dependencies (expect mostly libc/libpthread only):"
    else
      echo "Dynamic dependencies (expect system libc and libdcgm, among others):"
    fi
    ldd "${MONITOR_DIR}/.build-static/src/hpcperfstatsd" || true
  fi
}

print_notes() {
  cat <<'EOF'

Notes
-----
EOF
  if is_x86_build_host; then
    cat <<'EOF'
- This path links rabbitmq-c, libev, and LIKWID statically into hpcperfstatsd.
  LIKWID's static lib embeds bundled Lua and internal hwloc objects; configure also
  pulls -llikwid-hwloc and -llikwid-lua when using --enable-all-static.
- The monitor uses LIKWID ACCESSMODE_DIRECT for MSR access (PMU + RAPL); run with
  privileges appropriate for MSR access on your site.
EOF
  else
    cat <<'EOF'
- On non-x86, this path links rabbitmq-c and libev statically; the CPU counter backend is
  DCGM (system libdcgm), not LIKWID. C headers ship under third_party/nvidia-dcgm; you still
  need libdcgm from NVIDIA DCGM. Match header and library generations when possible.
EOF
  fi
  cat <<'EOF'
- Configure routes shared-only stacks after -Wl,-Bdynamic when using
  --enable-all-static: DCGM, Infiniband (libibmad), Omni-Path / OPA (verbs,
  umad, mad, oib_utils, public), and Intel MIC (libmicmgmt). GPUPerfAPI (AMD)
  is dlopen'd at runtime. Optional -lmemusage for OPA stays on LDFLAGS from
  Makefile.am (typically resolves as shared).
- A fully static executable (including glibc) needs musl or careful NSS
  handling; partial static linking (this script) avoids installing the
  third-party deps system-wide while still using the system C library.
- To bundle without rebuilding deps: set SKIP_DEPS=1 and point PREFIX at an
  existing tree that contains the static .a files and headers.
- Unit tests: from the build tree root (e.g. .build-static), run make check, or
  run tests/run_tests.sh; see tests/README.md.

EOF
}

build_static_dependencies() {
  if test "${SKIP_DEPS}" != "1"; then
    echo "Pinned static deps: libev=${LIBEV_VER} rabbitmq-c=${RABBITMQ_VER}"
    build_libev
    build_rabbitmq_c
    if is_x86_build_host; then
      echo "Pinned static deps (x86): likwid=${LIKWID_TAG}"
      build_likwid
    else
      echo "Skipping LIKWID build on $(uname -m) (monitor uses DCGM CPU backend here, not LIKWID)." >&2
    fi
  fi
}

usage_exit() {
  cat <<EOF
Usage: $(basename "$0") [--deps-only] [CONFIGURE_ARGS...]

  --deps-only   Build and install static archives (libev, rabbitmq-c, and LIKWID on x86)
                into PREFIX only. Use this when monitor configure
                --enable-all-static fails at link time with missing static .a
                archives.

  [CONFIGURE_ARGS...] are passed to ../configure inside build_monitor (ignored
  with --deps-only).

Environment: PREFIX, SRCDIR, SKIP_DEPS, SKIP_CLEAN, JOBS, and pin overrides (see script header).
EOF
  exit "${1:-0}"
}

main() {
  local deps_only=0
  local -a monitor_args=()
  while test $# -gt 0; do
    case "$1" in
      --deps-only)
        deps_only=1
        shift
        ;;
      -h|--help)
        usage_exit 0
        ;;
      *)
        monitor_args+=("$1")
        shift
        ;;
    esac
  done

  build_static_dependencies
  if test "${deps_only}" = "1"; then
    echo ""
    echo "Static dependency install complete: PREFIX=${PREFIX}"
    if is_x86_build_host; then
      echo "Expected archives include: libev.a librabbitmq.a liblikwid.a liblikwid-hwloc.a liblikwid-lua.a"
    else
      echo "Expected archives include: libev.a librabbitmq.a (LIKWID not built on this architecture)."
    fi
    echo "Configure the monitor with the same PREFIX in CPPFLAGS/LDFLAGS, then make (default --enable-all-static)."
    print_notes
    exit 0
  fi
  build_monitor "${monitor_args[@]}"
  print_notes
}

main "$@"
