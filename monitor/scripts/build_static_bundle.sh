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
#
# RPM: prepare_rpmbuild_dirs.sh builds deps into ./embedded-static-prefix/ and make dist
# bundles that tree into the source tarball. In %%build, set PREFIX to .../embedded-static-prefix
# and SKIP_DEPS=1 so this script only configures and compiles hpcperfstatsd.
# Environment:
#   PREFIX          Install tree (default: <repo>/.build/prefix-static)
#   SRCDIR          Download and build under this directory (default: <repo>/.build/src-static)
#   JOBS            Parallel jobs (default: nproc)
#   SKIP_DEPS       If set to 1, skip building deps (use existing PREFIX)
#   SKIP_CLEAN      If set to 1, do not remove .build-static before configuring
#                   (default: remove it so a prior failed/partial monitor build cannot
#                   poison the next run)
#   HPC_BUNDLE_RELEASE_BUILD  When 1 (or yes/true), apply optimized CFLAGS/CXXFLAGS,
#                   link-time --gc-sections, configure --disable-debug, and strip(1)
#                   the daemon after link. Intended for RPM packaging (see hpcperfstats.spec
#                   %%global hpc_release_build). CLI: pass --release.
#   HPC_BUNDLE_ENABLE_DEBUG   When 1 (or yes/true) and release build is off, pass
#                   configure --enable-debug (DEBUG macro, /dev/shm payload mirror).
#                   Set by hpcperfstats.spec when rpmbuild uses hpc_debug_build 1.
#
# Optional stacks (InfiniBand MAD, NVIDIA DCGM GPU, AMD GPUPerfAPI): this script probes the
# build host and passes --disable-* when development libs/headers are missing so configure
# does not fail (configure defaults IB on; GPU/AMD use auto + lspci and then hard-require
# libdcgm / GPUPerfAPI headers when hardware matches). Extra CONFIGURE_ARGS still override
# or extend (e.g. --disable-lustre).
#
# CPU counters: configure uses --with-cpu-counter-backend=auto (x86 -> LIKWID, else DCGM);
# non-x86 builds still need system libdcgm for the DCGM CPU path.
#
# Pinned versions: edit STATIC_PIN_* below. Runtime overrides: LIBEV_VER,
# RABBITMQ_VER, LIKWID_TAG, and *_URL_FMT for mirrors.
#
# Architecture (deps + configure):
#   x86_64 / i686: builds LIKWID static libs; configure auto-selects LIKWID CPU backend.
#   Other (e.g. aarch64): builds libev + rabbitmq-c only; configure auto-selects DCGM.
#   DCGM GPU/CPU headers are vendored under monitor/third_party/nvidia-dcgm; linking still
#   needs libdcgm from the NVIDIA DCGM package when those paths are enabled.
#
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/monitor_tree_clean.sh
source "${SCRIPT_DIR}/lib/monitor_tree_clean.sh"
readonly REPO_ROOT="$(cd "${MONITOR_DIR}/../.." && pwd)"

# =============================================================================
# Pinned dependency versions / refs (single place to edit)
# =============================================================================
# Release tarballs: upstream version strings exactly as used in URLs and paths.
STATIC_PIN_LIBEV_VERSION="4.33"
STATIC_PIN_RABBITMQ_C_VERSION="0.15.0"
# LIKWID: Git tag name without a leading "v" (archive is .../tags/v${VER}.tar.gz).
STATIC_PIN_LIKWID_VERSION="5.5.1"
STATIC_PIN_LIBBPF_VERSION="1.7.0"
# Optional: override tarball base URLs (must contain a single %s for version where used).
STATIC_PIN_LIBEV_URL_FMT="http://dist.schmorp.de/libev/libev-%s.tar.gz"
# rabbitmq-c: source-of-truth repo is alanxz/rabbitmq-c; github.com/rabbitmq/rabbitmq-c archive URLs return 404.
STATIC_PIN_RABBITMQ_C_URL_FMT="https://github.com/alanxz/rabbitmq-c/archive/refs/tags/v%s.tar.gz"
STATIC_PIN_LIKWID_URL_FMT="https://github.com/RRZE-HPC/likwid/archive/refs/tags/v%s.tar.gz"
STATIC_PIN_LIBBPF_URL_FMT="https://github.com/libbpf/libbpf/archive/refs/tags/v%s.tar.gz"
# =============================================================================

PREFIX="${PREFIX:-${REPO_ROOT}/.build/prefix-static}"
SRCDIR="${SRCDIR:-${REPO_ROOT}/.build/src-static}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"
SKIP_DEPS="${SKIP_DEPS:-0}"
SKIP_CLEAN="${SKIP_CLEAN:-0}"
HPCS_BUNDLE_REQUIRE_DCGM_GPU="${HPCS_BUNDLE_REQUIRE_DCGM_GPU:-0}"

# Populated by static_bundle_print_detection_summary(); used by build_monitor (no second probe pass).
STATIC_BUNDLE_FEAT_FLAGS=()

static_bundle_release_build_enabled() {
  case "${HPC_BUNDLE_RELEASE_BUILD:-0}" in
  1 | yes | YES | true | TRUE | on | ON) return 0 ;;
  *) return 1 ;;
  esac
}

static_bundle_enable_debug_enabled() {
  case "${HPC_BUNDLE_ENABLE_DEBUG:-0}" in
  1 | yes | YES | true | TRUE | on | ON) return 0 ;;
  *) return 1 ;;
  esac
}

# Remove debug and conflicting -O* tokens so release base flags win; remaining tokens
# (e.g. hardening from rpmbuild) are preserved.
static_bundle_sanitize_compiler_flags() {
  local out="" tok
  for tok in "$@"; do
    case "$tok" in
    -g | -g[0-9] | -ggdb | -ggdb[0-9] | -gdb | -gdwarf-* | -grecord-gcc-switches) continue ;;
    -O0 | -O1 | -O2 | -O3 | -Os | -Ofast | -Og) continue ;;
    esac
    out+=" ${tok}"
  done
  printf '%s' "${out# }"
}

static_bundle_apply_release_build_flags() {
  static_bundle_release_build_enabled || return 0
  local base="-O3 -pipe -DNDEBUG -ffunction-sections -fdata-sections"
  local c_rest cxx_rest ld_extra
  c_rest="$(static_bundle_sanitize_compiler_flags ${CFLAGS-})"
  cxx_rest="$(static_bundle_sanitize_compiler_flags ${CXXFLAGS-})"
  export CFLAGS="${base}${c_rest:+ }${c_rest}"
  export CXXFLAGS="${base}${cxx_rest:+ }${cxx_rest}"
  ld_extra="-Wl,-O1 -Wl,--as-needed -Wl,--gc-sections"
  case " ${LDFLAGS-} " in
  *" --gc-sections "* | *" -Wl,--gc-sections "*) ;;
  *) export LDFLAGS="${ld_extra} ${LDFLAGS-}" ;;
  esac
  echo "Static bundle: release build (HPC_BUNDLE_RELEASE_BUILD): ${CFLAGS}" >&2
}

# Effective pins (env overrides keep legacy names working).
LIBEV_VER="${LIBEV_VER:-${STATIC_PIN_LIBEV_VERSION}}"
RABBITMQ_VER="${RABBITMQ_VER:-${STATIC_PIN_RABBITMQ_C_VERSION}}"
LIKWID_TAG="${LIKWID_TAG:-${STATIC_PIN_LIKWID_VERSION}}"
LIBBPF_VER="${LIBBPF_VER:-${STATIC_PIN_LIBBPF_VERSION}}"
LIBEV_URL_FMT="${LIBEV_URL_FMT:-${STATIC_PIN_LIBEV_URL_FMT}}"
RABBITMQ_C_URL_FMT="${RABBITMQ_C_URL_FMT:-${STATIC_PIN_RABBITMQ_C_URL_FMT}}"
LIKWID_URL_FMT="${LIKWID_URL_FMT:-${STATIC_PIN_LIKWID_URL_FMT}}"
LIBBPF_URL_FMT="${LIBBPF_URL_FMT:-${STATIC_PIN_LIBBPF_URL_FMT}}"

WANT_METRIC_PROFILER_EBPF=0

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

# Compile+link a one-line C program; mirrors configure's optional stack checks.
monitor_link_probe() {
  local snippet="$1"
  shift
  local tbase out rc
  tbase="$(mktemp "${TMPDIR:-/tmp}/hpsmonprobe.XXXXXX")"
  out="${tbase}.out"
  printf '%s\n' "$snippet" >"${tbase}.c"
  rc=1
  if "${CC:-cc}" -o "${out}" "${tbase}.c" "$@" 2>/dev/null; then
    rc=0
  fi
  rm -f "${tbase}.c" "${out}"
  return "${rc}"
}

monitor_probe_infiniband_stack() {
  monitor_link_probe '#include <infiniband/mad.h>
#include <infiniband/umad.h>
int main(void){return 0;}' -libmad -libumad
}

monitor_probe_dcgm_vendor_link() {
  local inc="-I${MONITOR_DIR}/third_party/nvidia-dcgm"
  monitor_link_probe '#include <dcgm_agent.h>
int main(void){(void)dcgmInit();return 0;}' ${inc} -ldcgm -ldl
}

monitor_probe_amd_gpup_perfapi_sdk() {
  test -f /usr/include/gpu_performance_api/gpu_perf_api.h \
    || test -f /usr/local/include/gpu_performance_api/gpu_perf_api.h
}

# Match configure.ac GPU auto-detect (lspci + awk); used for summary only.
monitor_lspci_sees_nvidia() {
  command -v lspci >/dev/null 2>&1 || return 1
  lspci -nn 2>/dev/null | awk '
    { l = tolower($0)
      if ((match(l, /vga compatible controller/) || match(l, /3d controller/) || \
           match(l, /display controller/) || match(l, /processing accelerators/)) && \
          match(l, /nvidia/))
        found = 1
    }
    END { exit(found ? 0 : 1) }'
}

monitor_lspci_sees_amd() {
  command -v lspci >/dev/null 2>&1 || return 1
  lspci -nn 2>/dev/null | awk '
    { l = tolower($0)
      if ((match(l, /vga compatible controller/) || match(l, /3d controller/) || \
           match(l, /display controller/) || match(l, /processing accelerators/)) && \
          (match(l, /advanced micro devices/) || match(l, / amd\/ati /)))
        found = 1
    }
    END { exit(found ? 0 : 1) }'
}

# Prints detection summary and sets STATIC_BUNDLE_FEAT_FLAGS (configure --disable-* list).
static_bundle_print_detection_summary() {
  STATIC_BUNDLE_FEAT_FLAGS=()
  local mach cpu_backend likwid_build lspci_path pci_nvidia pci_amd ib_ok dcgm_ok amd_ok

  mach="$(uname -m 2>/dev/null || echo unknown)"
  if is_x86_build_host; then
    cpu_backend="LIKWID"
    likwid_build="yes (this run compiles LIKWID into PREFIX unless SKIP_DEPS=1)"
  else
    cpu_backend="DCGM"
    likwid_build="no (non-x86; needs system libdcgm for CPU counters)"
  fi

  if command -v lspci >/dev/null 2>&1; then
    lspci_path="$(command -v lspci)"
    if monitor_lspci_sees_nvidia; then pci_nvidia=detected; else pci_nvidia="not detected"; fi
    if monitor_lspci_sees_amd; then pci_amd=detected; else pci_amd="not detected"; fi
  else
    lspci_path="(not in PATH; configure GPU auto-detect may be limited)"
    pci_nvidia=n/a
    pci_amd=n/a
  fi

  if monitor_probe_infiniband_stack; then
    ib_ok=detected
  else
    ib_ok="not detected"
    STATIC_BUNDLE_FEAT_FLAGS+=(--disable-infiniband)
  fi

  if monitor_probe_dcgm_vendor_link; then
    dcgm_ok=detected
  else
    dcgm_ok="not detected"
    if test "${HPCS_BUNDLE_REQUIRE_DCGM_GPU}" = "1"; then
      cat <<EOF >&2
ERROR: DCGM link probe failed but HPCS_BUNDLE_REQUIRE_DCGM_GPU=1.
Refusing to auto-disable nvidia_gpu in this build.
Install libdcgm development package for this build root (and ensure linker visibility),
or unset HPCS_BUNDLE_REQUIRE_DCGM_GPU if this build intentionally omits NVIDIA GPU support.
EOF
      exit 1
    fi
    STATIC_BUNDLE_FEAT_FLAGS+=(--disable-gpu)
  fi

  if monitor_probe_amd_gpup_perfapi_sdk; then
    amd_ok=detected
  else
    amd_ok="not detected"
    STATIC_BUNDLE_FEAT_FLAGS+=(--disable-amd-gpu)
  fi

  printf '\n'
  printf '%s\n' "=== Static bundle: build host detection (before any compile) ==="
  printf '%-36s %s\n' "Machine (uname -m):" "${mach}"
  printf '%-36s %s\n' "CPU counter backend (configure auto):" "${cpu_backend}"
  printf '%-36s %s\n' "LIKWID static dependency build:" "${likwid_build}"
  printf '%-36s %s\n' "lspci:" "${lspci_path}"
  printf '%-36s %s\n' "PCI class hint (NVIDIA GPU):" "${pci_nvidia}"
  printf '%-36s %s\n' "PCI class hint (AMD GPU):" "${pci_amd}"
  printf '%-36s %s\n' "InfiniBand devel (libibmad + headers):" "${ib_ok}"
  printf '%-36s %s\n' "NVIDIA DCGM link (libdcgm + vendored hdr):" "${dcgm_ok}"
  printf '%-36s %s\n' "AMD GPUPerfAPI header (gpu_perf_api.h):" "${amd_ok}"
  if test "${#STATIC_BUNDLE_FEAT_FLAGS[@]}" -gt 0; then
    printf '%-36s %s\n' "Extra configure flags from probes:" "${STATIC_BUNDLE_FEAT_FLAGS[*]}"
  else
    printf '%-36s %s\n' "Extra configure flags from probes:" "(none)"
  fi
  if static_bundle_release_build_enabled; then
    printf '%-36s %s\n' "Release build (HPC_BUNDLE_RELEASE_BUILD):" \
      "yes (-O3, -DNDEBUG, -ffunction/data-sections, link GC, --disable-debug, strip)"
  else
    printf '%-36s %s\n' "Release build (HPC_BUNDLE_RELEASE_BUILD):" "no"
  fi
  printf '%s\n\n' "=== end detection summary ==="
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
  local -a cmake_extra=()
  if test -n "${CFLAGS:-}"; then
    cmake_extra+=(-DCMAKE_C_FLAGS="${CFLAGS}")
  fi
  if test -n "${CXXFLAGS:-}"; then
    cmake_extra+=(-DCMAKE_CXX_FLAGS="${CXXFLAGS}")
  fi
  cmake .. \
    -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    "${cmake_extra[@]}" \
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
  local -a likwid_mk=()
  if test -n "${CFLAGS:-}"; then
    likwid_mk+=(CFLAGS="${CFLAGS}")
  fi
  make -j"${JOBS}" PREFIX="${PREFIX}" INSTALLED_PREFIX="${PREFIX}" \
    BUILDDAEMON=false BUILDFREQ=false BUILD_SYSFEATURES=false ACCESSMODE=direct \
    "${likwid_mk[@]}"
  make install PREFIX="${PREFIX}" INSTALLED_PREFIX="${PREFIX}" \
    BUILDDAEMON=false BUILDFREQ=false BUILD_SYSFEATURES=false ACCESSMODE=direct \
    "${likwid_mk[@]}"
}

build_libbpf() {
  local d="${SRCDIR}/libbpf-${LIBBPF_VER}"
  local t="${SRCDIR}/libbpf-${LIBBPF_VER}.tar.gz"
  if test ! -d "$d"; then
    fetch_url "$(printf "${LIBBPF_URL_FMT}" "${LIBBPF_VER}")" "$t"
    tar -C "${SRCDIR}" -xzf "$t"
  fi
  cd "$d/src"
  make -j"${JOBS}" BUILD_STATIC_ONLY=y OBJDIR=build DESTDIR= CC="${CC_FOR_BUILD:-cc}" HOSTCC="${HOSTCC_FOR_BUILD:-cc}"
  make install PREFIX="${PREFIX}" BUILD_STATIC_ONLY=y OBJDIR=build DESTDIR= CC="${CC_FOR_BUILD:-cc}" HOSTCC="${HOSTCC_FOR_BUILD:-cc}"
}

configure_arg_requests_ebpf() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --with-metric-profiler-backend=ebpf) return 0 ;;
      --with-metric-profiler-backend=none) return 1 ;;
    esac
  done
  case "${MONITOR_METRIC_PROFILER_BACKEND:-none}" in
    ebpf) return 0 ;;
  esac
  return 1
}

build_monitor() {
  monitor_tree_clean_build_static "${MONITOR_DIR}"
  mkdir -p "${MONITOR_DIR}/.build-static"
  cd "${MONITOR_DIR}/.build-static"
  if test -f "${MONITOR_DIR}/configure.ac" || test -f "${MONITOR_DIR}/configure.in"; then
    echo "Regenerating monitor Autotools files: autoreconf -fi (${MONITOR_DIR})" >&2
    (cd "${MONITOR_DIR}" && autoreconf -fi)
  elif test ! -x "${MONITOR_DIR}/configure"; then
    echo "error: ${MONITOR_DIR}/configure.ac missing and no executable configure script" >&2
    return 1
  else
    echo "Using existing ${MONITOR_DIR}/configure (no configure.ac; skipped autoreconf)" >&2
  fi
  export CPPFLAGS="-I${PREFIX}/include ${CPPFLAGS:-}"
  export LDFLAGS="-L${PREFIX}/lib -L${PREFIX}/lib64 ${LDFLAGS:-}"
  # Prefer static archives from our tree (no RPATH to PREFIX for runtime).
  local -a feat=("${STATIC_BUNDLE_FEAT_FLAGS[@]}")
  local -a cfg=(
    --enable-all-static
    --with-systemduserunitdir=no
    --with-cpu-counter-backend=auto
    "${feat[@]}"
  )
  if static_bundle_release_build_enabled; then
    cfg+=(--disable-debug)
  elif static_bundle_enable_debug_enabled; then
    cfg+=(--enable-debug)
  fi
  "${MONITOR_DIR}/configure" "${cfg[@]}" "$@"
  make -j"${JOBS}"
  local daemon="${MONITOR_DIR}/.build-static/src/hpcperfstatsd"
  if static_bundle_release_build_enabled && test -f "${daemon}"; then
    if command -v strip >/dev/null 2>&1; then
      strip --strip-unneeded "${daemon}" 2>/dev/null || strip "${daemon}" 2>/dev/null || true
      echo "Release build: stripped ${daemon}" >&2
    else
      echo "Release build: strip(1) not found; leaving symbols on ${daemon}" >&2
    fi
  fi
  echo ""
  echo "Built: ${daemon}"
  if command -v ldd >/dev/null 2>&1; then
    if is_x86_build_host; then
      echo "Dynamic dependencies (libc/libpthread; plus libdcgm/ibmad if those features stayed enabled):"
    else
      echo "Dynamic dependencies (expect system libc and libdcgm when DCGM CPU backend is used):"
    fi
    ldd "${daemon}" || true
  fi
  if test "${SKIP_CAPABILITIES:-0}" != "1"; then
    echo "Emitting monitor-build-capabilities.json"
    (cd "${MONITOR_DIR}/.build-static" && make capabilities) || true
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
- This script probes the build host for InfiniBand (libibmad + headers), NVIDIA
  DCGM (libdcgm + vendored dcgm_agent.h), and AMD GPUPerfAPI headers; missing
  pieces become --disable-infiniband / --disable-gpu / --disable-amd-gpu so
  configure can succeed. Configure routes shared-only stacks after -Wl,-Bdynamic
  when using --enable-all-static: DCGM, Infiniband (libibmad), Omni-Path / OPA,
  and GPUPerfAPI (AMD) is dlopen'd at runtime.
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
    if test "${WANT_METRIC_PROFILER_EBPF}" = "1"; then
      echo "Pinned static deps (metric profiler ebpf): libbpf=${LIBBPF_VER}"
      build_libbpf
    fi
  fi
}

usage_exit() {
  cat <<EOF
Usage: $(basename "$0") [--deps-only] [--print-configure-flags] [--release] [CONFIGURE_ARGS...]

  --deps-only   Build and install static archives (libev, rabbitmq-c, and LIKWID on x86)
                into PREFIX only. Use this when monitor configure
                --enable-all-static fails at link time with missing static .a
                archives.

  --print-configure-flags
                Probe the build host and print probe-derived configure flags (e.g.
                --disable-amd-gpu) one per line on stdout; detection summary on stderr.
                No dependency builds. Used by prepare_rpmbuild_dirs.sh before make dist.

  --release     Same as HPC_BUNDLE_RELEASE_BUILD=1: -O3, -DNDEBUG, section GC,
                --disable-debug, strip hpcperfstatsd (see script header).

  [CONFIGURE_ARGS...] are appended to configure inside build_monitor (e.g.
  --disable-lustre); ignored with --deps-only and --print-configure-flags.

Environment: PREFIX, SRCDIR, SKIP_DEPS, SKIP_CLEAN, JOBS, HPC_BUNDLE_RELEASE_BUILD,
  MONITOR_METRIC_PROFILER_BACKEND (ebpf|none), and pin overrides (see script header).
EOF
  exit "${1:-0}"
}

main() {
  local deps_only=0
  local print_configure_flags=0
  local -a monitor_args=()
  while test $# -gt 0; do
    case "$1" in
      --deps-only)
        deps_only=1
        shift
        ;;
      --print-configure-flags)
        print_configure_flags=1
        shift
        ;;
      --release)
        HPC_BUNDLE_RELEASE_BUILD=1
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

  if test "${print_configure_flags}" = "1"; then
    static_bundle_print_detection_summary >&2
    local flag
    for flag in "${STATIC_BUNDLE_FEAT_FLAGS[@]}"; do
      printf '%s\n' "${flag}"
    done
    exit 0
  fi

  if configure_arg_requests_ebpf "${monitor_args[@]}"; then
    WANT_METRIC_PROFILER_EBPF=1
  fi

  static_bundle_print_detection_summary
  static_bundle_apply_release_build_flags
  build_static_dependencies
  if test "${deps_only}" = "1"; then
    echo ""
    echo "Static dependency install complete: PREFIX=${PREFIX}"
    if is_x86_build_host; then
      echo "Expected archives include: libev.a librabbitmq.a liblikwid.a liblikwid-hwloc.a liblikwid-lua.a"
    else
      echo "Expected archives include: libev.a librabbitmq.a (LIKWID not built on this architecture)."
    fi
    if test "${WANT_METRIC_PROFILER_EBPF}" = "1"; then
      echo "Expected archives include: libbpf.a (metric profiler eBPF backend selected)."
    fi
    echo "Configure the monitor with the same PREFIX in CPPFLAGS/LDFLAGS, then make (default --enable-all-static)."
    print_notes
    exit 0
  fi
  build_monitor "${monitor_args[@]}"
  print_notes
}

main "$@"
