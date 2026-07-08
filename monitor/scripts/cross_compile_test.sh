#!/usr/bin/env bash
# Cross-compile smoke runner for monitor (rootless-only).
# - Native targets use the canonical static bundle flow.
# - Foreign targets use qemu-user wrappers + target sysroot tools.
# - No podman/docker/conmon/runc/binfmt setup is used.
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly MONITOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT="$(cd "${MONITOR_DIR}/../.." && pwd)"

# Keep pins aligned with scripts/build_static_bundle.sh where applicable.
LIBEV_VER="${LIBEV_VER:-4.33}"
RABBITMQ_VER="${RABBITMQ_VER:-0.17.0}"
LIKWID_TAG="${LIKWID_TAG:-5.5.1}"
LIBBPF_VER="${LIBBPF_VER:-1.7.0}"
QEMU_VER="${QEMU_VER:-11.0.1}"

LIBEV_URL_FMT="${LIBEV_URL_FMT:-http://dist.schmorp.de/libev/libev-%s.tar.gz}"
RABBITMQ_C_URL_FMT="${RABBITMQ_C_URL_FMT:-https://github.com/alanxz/rabbitmq-c/archive/refs/tags/v%s.tar.gz}"
LIKWID_URL_FMT="${LIKWID_URL_FMT:-https://github.com/RRZE-HPC/likwid/archive/refs/tags/v%s.tar.gz}"
LIBBPF_URL_FMT="${LIBBPF_URL_FMT:-https://github.com/libbpf/libbpf/archive/refs/tags/v%s.tar.gz}"
QEMU_URL_FMT="${QEMU_URL_FMT:-https://download.qemu.org/qemu-%s.tar.xz}"

TARGETS="${TARGETS:-aarch64-linux-gnu powerpc64le-linux-gnu riscv64-linux-gnu}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"
PREFIX_ROOT="${PREFIX_ROOT:-${REPO_ROOT}/.build}"
SKIP_DEPS="${SKIP_DEPS:-0}"
FAIL_FAST="${FAIL_FAST:-0}"
FORCE_FOREIGN="${FORCE_FOREIGN:-0}"
FORCE_NATIVE="${FORCE_NATIVE:-0}"
RUN_AUTORECONF="${RUN_AUTORECONF:-1}"
LOG_DIR_ROOT="${LOG_DIR_ROOT:-${MONITOR_DIR}/.build-cross-logs}"
LOCAL_QEMU_ROOT="${LOCAL_QEMU_ROOT:-${REPO_ROOT}/.build/qemu-local}"
AUTO_BOOTSTRAP_QEMU="${AUTO_BOOTSTRAP_QEMU:-1}"

AUTO_CREATE_ROCKY9_SYSROOT="${AUTO_CREATE_ROCKY9_SYSROOT:-1}"
ROCKY9_SYSROOT_ROOT="${ROCKY9_SYSROOT_ROOT:-${REPO_ROOT}/.build/sysroots}"
ROCKY9_RELEASEVER="${ROCKY9_RELEASEVER:-9}"
ROCKY9_ROOTFS_TARBALL_ROOT="${ROCKY9_ROOTFS_TARBALL_ROOT:-${REPO_ROOT}/.build/rootfs-tarballs}"
ROCKY9_ROOTFS_URL_FMT="${ROCKY9_ROOTFS_URL_FMT:-https://download.rockylinux.org/pub/rocky/%s/images/%s/Rocky-%s-Container-Base.latest.%s.tar.xz}"
ROCKY9_TOOLCHAIN_LAYER_URL_FMT="${ROCKY9_TOOLCHAIN_LAYER_URL_FMT:-https://download.rockylinux.org/pub/rocky/%s/images/%s/Rocky-%s-Container-Toolbox.latest.%s.tar.xz}"
ROCKY9_RPM_MIRROR_BASE="${ROCKY9_RPM_MIRROR_BASE:-https://download.rockylinux.org/pub/rocky/%s}"
ROCKY9_RPM_TOOLCHAIN_PACKAGES="${ROCKY9_RPM_TOOLCHAIN_PACKAGES:-make binutils cpp gcc gcc-c++ libgcc libstdc++ libstdc++-devel glibc-devel glibc-headers kernel-headers libxcrypt-devel libmpc mpfr gmp libzstd zlib}"

# Optional: turn on eBPF metric-profiler dependency in foreign mode.
WANT_METRIC_PROFILER_EBPF="${WANT_METRIC_PROFILER_EBPF:-0}"

usage_exit() {
  cat <<'EOF'
Usage: ./scripts/cross_compile_test.sh [options]

Options:
  --targets "t1 t2 ..."   Space/comma separated GNU triplets.
  --skip-deps             Reuse existing per-target PREFIX trees.
  --fail-fast             Stop on first target failure.
  --force-foreign         Treat all targets as foreign.
  --force-native          Treat all targets as native.
  --no-autoreconf         Skip host-side autoreconf -fi.
  -h, --help              Show this help.

Environment:
  TARGETS                 Default: "aarch64-linux-gnu powerpc64le-linux-gnu riscv64-linux-gnu"
  SYSROOT                 Optional fallback sysroot for all foreign targets.
  SYSROOT_<TRIPLET_SLUG>  Per-target sysroot; slug is upper-case with non-alnum -> "_".
                          Example: SYSROOT_AARCH64_LINUX_GNU=/opt/sysroots/aarch64
  ROOTFS_TARBALL          Optional fallback rootfs tarball for all foreign targets.
  ROOTFS_TARBALL_<TRIPLET_SLUG>  Per-target rootfs tarball extracted into SYSROOT path.
  QEMU_LD_PREFIX          Optional fallback; default is sysroot path.
  AUTO_CREATE_ROCKY9_SYSROOT  Create Rocky 9 sysroot when SYSROOT is unset (default: 1).
  ROCKY9_SYSROOT_ROOT     Root directory for auto-created Rocky 9 sysroots.
  ROCKY9_RELEASEVER       Rocky major release label for auto sysroot path naming.
  ROCKY9_ROOTFS_TARBALL_ROOT  Local cache root for generated Rocky rootfs tarballs.
  ROCKY9_ROOTFS_URL_FMT   Rocky rootfs URL format (expects release, arch_dir, release, arch_file).
  ROCKY9_TOOLCHAIN_LAYER_URL_FMT  Rocky toolbox rootfs URL for toolchain layering.
  ROCKY9_RPM_MIRROR_BASE  Rocky RPM mirror base URL format (expects release).
  ROCKY9_RPM_TOOLCHAIN_PACKAGES  Space-separated RPM names for toolchain fallback layering.
  LOCAL_QEMU_ROOT         Local install prefix for built qemu-user binaries.
  AUTO_BOOTSTRAP_QEMU     Build local qemu-user when emulator missing (default: 1).
  JOBS                    Parallel jobs for make/cmake.
  PREFIX_ROOT             Root for per-target install/build dirs (default: <repo>/.build)
  LOG_DIR_ROOT            Root for per-target logs (default: <monitor>/.build-cross-logs)
  SKIP_DEPS, FAIL_FAST, FORCE_FOREIGN, FORCE_NATIVE, RUN_AUTORECONF

Notes:
  - Foreign mode is rootless: no sudo, no binfmt registration, no podman/docker.
  - Foreign mode uses host make/cmake/pkg-config with qemu-wrapped target tools.
  - Foreign monitor builds use --disable-all-static (dynamic libc/libm from sysroot); native path still uses the static bundle.
  - Foreign x86 installs static LIKWID (.a only); configure probes perfmon_init with -llikwid-hwloc -llikwid-lua -lm first (see configure.ac).
  - For deterministic foreign smoke, monitor configure is passed:
      --disable-gpu --disable-amd-gpu --disable-infiniband --disable-opa
      --disable-lustre
EOF
  exit "${1:-0}"
}

fail() {
  echo "error: $*" >&2
  exit 1
}

slugify_triplet() {
  printf '%s' "$1" | tr '[:lower:]-.' '[:upper:]__'
}

triplet_cpu() {
  local t="$1"
  printf '%s' "${t%%-*}"
}

triplet_arch_for_pkgmgr() {
  case "$(triplet_cpu "$1")" in
    x86_64) echo "x86_64" ;;
    aarch64|arm64) echo "aarch64" ;;
    ppc64le) echo "ppc64le" ;;
    riscv64) echo "riscv64" ;;
    i?86) echo "i686" ;;
    *) echo "" ;;
  esac
}

normalize_cpu_family() {
  case "$1" in
    x86_64|amd64) echo "x86_64" ;;
    i386|i486|i586|i686) echo "x86_32" ;;
    aarch64|arm64) echo "aarch64" ;;
    armv7l|armv7*|armv6l|armv6*|armhf|armel|arm) echo "arm" ;;
    ppc64le) echo "ppc64le" ;;
    ppc64) echo "ppc64" ;;
    riscv64) echo "riscv64" ;;
    *) echo "$1" ;;
  esac
}

is_x86_triplet() {
  case "$(triplet_cpu "$1")" in
    x86_64|i?86) return 0 ;;
    *) return 1 ;;
  esac
}

is_native_target() {
  local target="$1"
  local host_cpu target_cpu
  host_cpu="$(normalize_cpu_family "$(uname -m 2>/dev/null || echo unknown)")"
  target_cpu="$(normalize_cpu_family "$(triplet_cpu "${target}")")"

  if test "${FORCE_FOREIGN}" = "1"; then
    return 1
  fi
  if test "${FORCE_NATIVE}" = "1"; then
    return 0
  fi
  test "${host_cpu}" = "${target_cpu}"
}

fetch_url_validate_gzip() {
  local dest="$1"
  local url="$2"
  case "${dest}" in
    *.tar.gz|*.tgz) ;;
    *) return 0 ;;
  esac
  if ! gzip -t "${dest}" 2>/dev/null; then
    echo "fetch_url: invalid gzip archive for URL: ${url}" >&2
    rm -f "${dest}"
    exit 1
  fi
}

fetch_url() {
  local url="$1"
  local dest="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "${dest}" "${url}" || { rm -f "${dest}"; return 1; }
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "${dest}" "${url}" || { rm -f "${dest}"; return 1; }
  else
    fail "Need curl or wget to download ${url}"
  fi
  fetch_url_validate_gzip "${dest}" "${url}"
}

ensure_python_tomli() {
  if python3 - <<'PY' >/dev/null 2>&1
import tomli
PY
  then
    return 0
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 is required for qemu bootstrap helper dependencies."
  fi
  if ! python3 -m pip --version >/dev/null 2>&1; then
    python3 -m ensurepip --user >/dev/null 2>&1 || true
  fi
  python3 -m pip install --user --upgrade tomli >/dev/null 2>&1 \
    || fail "Unable to install python package 'tomli' in user space; required for qemu bootstrap."
}

ensure_ninja() {
  if command -v ninja >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 is required to bootstrap ninja."
  fi
  if ! python3 -m pip --version >/dev/null 2>&1; then
    python3 -m ensurepip --user >/dev/null 2>&1 || true
  fi
  python3 -m pip install --user --upgrade ninja >/dev/null 2>&1 \
    || fail "Unable to install ninja in user space for qemu bootstrap."
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v ninja >/dev/null 2>&1 || fail "ninja bootstrap succeeded but ninja not found on PATH."
}

sysroot_for_target() {
  local target="$1"
  local slug env_name out="" auto_path
  slug="$(slugify_triplet "${target}")"
  env_name="SYSROOT_${slug}"
  out="${!env_name:-${SYSROOT:-}}"
  if test -z "${out}" && test "${AUTO_CREATE_ROCKY9_SYSROOT}" = "1"; then
    auto_path="${ROCKY9_SYSROOT_ROOT}/rocky${ROCKY9_RELEASEVER}-${target}"
    out="${auto_path}"
  fi
  if test -z "${out}"; then
    fail "No sysroot configured for ${target}. Set ${env_name} (preferred) or SYSROOT, or enable AUTO_CREATE_ROCKY9_SYSROOT=1."
  fi
  printf '%s' "${out}"
}

rootfs_tarball_for_target() {
  local target="$1"
  local slug env_name out="" arch
  slug="$(slugify_triplet "${target}")"
  env_name="ROOTFS_TARBALL_${slug}"
  out="${!env_name:-${ROOTFS_TARBALL:-}}"
  if test -n "${out}"; then
    printf '%s' "${out}"
    return 0
  fi
  if test "${AUTO_CREATE_ROCKY9_SYSROOT}" != "1"; then
    return 1
  fi
  arch="$(triplet_arch_for_pkgmgr "${target}")"
  test -n "${arch}" || fail "Unsupported target for Rocky 9 rootfs tarball generation: ${target}"
  mkdir -p "${ROCKY9_ROOTFS_TARBALL_ROOT}"
  printf '%s/rocky%s-container-base-%s.tar.xz' "${ROCKY9_ROOTFS_TARBALL_ROOT}" "${ROCKY9_RELEASEVER}" "${arch}"
}

ensure_rocky9_rootfs_tarball() {
  local target="$1"
  local tarball="$2"
  local arch url
  if test -f "${tarball}"; then
    return 0
  fi
  arch="$(triplet_arch_for_pkgmgr "${target}")"
  test -n "${arch}" || fail "Unsupported target arch for Rocky rootfs generation: ${target}"
  mkdir -p "$(dirname "${tarball}")"
  url="$(printf "${ROCKY9_ROOTFS_URL_FMT}" "${ROCKY9_RELEASEVER}" "${arch}" "${ROCKY9_RELEASEVER}" "${arch}")"
  echo "Downloading Rocky ${ROCKY9_RELEASEVER} rootfs tarball for ${target}: ${url}"
  fetch_url "${url}" "${tarball}"
}

has_required_target_toolchain() {
  local sysroot="$1"
  test -x "${sysroot}/usr/bin/gcc" \
    && test -x "${sysroot}/usr/bin/g++" \
    && test -x "${sysroot}/usr/bin/make" \
    && test -x "${sysroot}/usr/bin/ar" \
    && test -x "${sysroot}/usr/bin/ranlib" \
    && test -x "${sysroot}/usr/bin/strip"
}

has_required_target_gcc_runtime() {
  local sysroot="$1"
  local req
  for req in lib64/libmpc.so.3 lib64/libmpfr.so.6 lib64/libgmp.so.10 lib64/libzstd.so.1 lib64/libz.so.1; do
    test -e "${sysroot}/${req}" || return 1
  done
  return 0
}

rpm_toolchain_packages_fp() {
  printf '%s\n' ${ROCKY9_RPM_TOOLCHAIN_PACKAGES} | LC_ALL=C sort | sha256sum | awk '{print $1}'
}

ensure_rocky9_toolchain_layer() {
  local target="$1"
  local sysroot="$2"
  local arch layer_tarball layer_url marker tmpdir
  has_required_target_toolchain "${sysroot}" && has_required_target_gcc_runtime "${sysroot}" && return 0

  arch="$(triplet_arch_for_pkgmgr "${target}")"
  test -n "${arch}" || fail "Unsupported target arch for Rocky toolchain layer: ${target}"
  mkdir -p "${ROCKY9_ROOTFS_TARBALL_ROOT}" "${sysroot}"
  layer_tarball="${ROCKY9_ROOTFS_TARBALL_ROOT}/rocky${ROCKY9_RELEASEVER}-container-toolbox-${arch}.tar.xz"
  layer_url="$(printf "${ROCKY9_TOOLCHAIN_LAYER_URL_FMT}" "${ROCKY9_RELEASEVER}" "${arch}" "${ROCKY9_RELEASEVER}" "${arch}")"
  marker="${sysroot}/.hpc_toolchain_layer_from"
  if test -f "${marker}" && test "$(cat "${marker}")" = "${layer_tarball}" && has_required_target_toolchain "${sysroot}" && has_required_target_gcc_runtime "${sysroot}"; then
    return 0
  fi

  if test ! -f "${layer_tarball}"; then
    echo "Downloading Rocky ${ROCKY9_RELEASEVER} toolchain layer for ${target}: ${layer_url}"
    fetch_url "${layer_url}" "${layer_tarball}"
  fi
  echo "Layering toolchain rootfs for ${target}: ${layer_tarball} -> ${sysroot}"
  tmpdir="$(mktemp -d "${REPO_ROOT}/.build/rocky-oci-layer.XXXXXX")"
  tar -xJf "${layer_tarball}" -C "${tmpdir}"
  if test -f "${tmpdir}/oci-layout" && test -f "${tmpdir}/index.json"; then
    python3 - "${tmpdir}" "${sysroot}" <<'PY'
import json, os, tarfile, shutil, sys
tmpdir, sysroot = sys.argv[1], sys.argv[2]
def safe_join(base, name):
    joined = os.path.normpath(os.path.join(base, name.lstrip("/")))
    if not joined.startswith(os.path.abspath(base) + os.sep) and joined != os.path.abspath(base):
        raise RuntimeError(f"unsafe path: {name}")
    return joined
index = json.load(open(os.path.join(tmpdir, "index.json"), "r", encoding="utf-8"))
manifest_digest = index["manifests"][0]["digest"].split(":", 1)[1]
manifest_path = os.path.join(tmpdir, "blobs", "sha256", manifest_digest)
manifest = json.load(open(manifest_path, "r", encoding="utf-8"))
layers = manifest.get("layers", [])
for layer in layers:
    digest = layer["digest"].split(":", 1)[1]
    layer_path = os.path.join(tmpdir, "blobs", "sha256", digest)
    with tarfile.open(layer_path, "r:*") as tf:
        for member in tf.getmembers():
            name = member.name
            base = os.path.basename(name)
            dirname = os.path.dirname(name)
            if base.startswith(".wh."):
                parent = safe_join(sysroot, dirname)
                if base == ".wh..wh..opq":
                    if os.path.isdir(parent):
                        for entry in os.listdir(parent):
                            p = os.path.join(parent, entry)
                            if os.path.isdir(p) and not os.path.islink(p):
                                shutil.rmtree(p)
                            else:
                                try: os.remove(p)
                                except FileNotFoundError: pass
                else:
                    target = safe_join(parent, base[4:])
                    if os.path.isdir(target) and not os.path.islink(target):
                        shutil.rmtree(target)
                    else:
                        try: os.remove(target)
                        except FileNotFoundError: pass
                continue
            out_path = safe_join(sysroot, name)
            if member.isdir():
                os.makedirs(out_path, exist_ok=True); continue
            if member.issym():
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                try: os.remove(out_path)
                except FileNotFoundError: pass
                os.symlink(member.linkname, out_path); continue
            if member.islnk():
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                link_target = safe_join(sysroot, member.linkname)
                try: os.remove(out_path)
                except FileNotFoundError: pass
                os.link(link_target, out_path); continue
            if member.isfile():
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                try:
                    os.remove(out_path)
                except FileNotFoundError:
                    pass
                f = tf.extractfile(member)
                if f is None: continue
                with open(out_path, "wb") as out:
                    shutil.copyfileobj(f, out)
                os.chmod(out_path, member.mode & 0o7777)
PY
  else
    cp -a "${tmpdir}/." "${sysroot}/"
  fi
  rm -rf "${tmpdir}"
  printf '%s\n' "${layer_tarball}" > "${marker}"
  if has_required_target_toolchain "${sysroot}" && has_required_target_gcc_runtime "${sysroot}"; then
    return 0
  fi
  echo "Toolbox layer did not provide full toolchain; falling back to RPM payload layering."
  ensure_rocky9_rpm_toolchain_layer "${target}" "${sysroot}"
  has_required_target_toolchain "${sysroot}" || fail "Toolchain layering still missing required target compiler/binutils in ${sysroot}/usr/bin"
  has_required_target_gcc_runtime "${sysroot}" || fail "Toolchain layering still missing gcc runtime libraries in ${sysroot}/lib64"
}

ensure_rocky9_rpm_toolchain_layer() {
  local target="$1"
  local sysroot="$2"
  local arch mirror cache marker rpm_fp
  arch="$(triplet_arch_for_pkgmgr "${target}")"
  test -n "${arch}" || fail "Unsupported target arch for Rocky RPM toolchain layer: ${target}"
  mirror="$(printf "${ROCKY9_RPM_MIRROR_BASE}" "${ROCKY9_RELEASEVER}")"
  cache="${ROCKY9_ROOTFS_TARBALL_ROOT}/rpms-${arch}"
  marker="${sysroot}/.hpc_rpm_toolchain_layer_done"
  rpm_fp="$(rpm_toolchain_packages_fp)"
  if test -f "${marker}" \
    && test "$(head -n 1 "${marker}")" = "${rpm_fp}" \
    && has_required_target_toolchain "${sysroot}" \
    && has_required_target_gcc_runtime "${sysroot}"; then
    return 0
  fi
  command -v rpm2cpio >/dev/null 2>&1 || fail "rpm2cpio is required for rootless RPM toolchain layering."
  command -v cpio >/dev/null 2>&1 || fail "cpio is required for rootless RPM toolchain layering."
  mkdir -p "${cache}" "${sysroot}"

  python3 - "${mirror}" "${arch}" "${cache}" "${ROCKY9_RPM_TOOLCHAIN_PACKAGES}" <<'PY'
import gzip, os, sys, urllib.request, xml.etree.ElementTree as ET
mirror, arch, cache, pkg_list = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
pkgs = [p for p in pkg_list.split() if p]
repos = [
    f"{mirror}/BaseOS/{arch}/os",
    f"{mirror}/AppStream/{arch}/os",
    f"{mirror}/CRB/{arch}/os",
]
os.makedirs(cache, exist_ok=True)
NS_REPO = {"r":"http://linux.duke.edu/metadata/repo"}
NS_COMMON = {"c":"http://linux.duke.edu/metadata/common"}

def load_primary(repo_base):
    repomd = ET.fromstring(urllib.request.urlopen(f"{repo_base}/repodata/repomd.xml").read())
    href = None
    for d in repomd.findall("r:data", NS_REPO):
        if d.attrib.get("type") == "primary":
            loc = d.find("r:location", NS_REPO)
            if loc is not None:
                href = loc.attrib.get("href")
                break
    if not href:
        return None
    data = urllib.request.urlopen(f"{repo_base}/{href}").read()
    if href.endswith(".gz"):
        data = gzip.decompress(data)
    return ET.fromstring(data)

catalog = {}
for repo in repos:
    try:
        root = load_primary(repo)
    except Exception:
        continue
    if root is None:
        continue
    for pkg in root.findall("c:package", NS_COMMON):
        n = pkg.find("c:name", NS_COMMON)
        a = pkg.find("c:arch", NS_COMMON)
        v = pkg.find("c:version", NS_COMMON)
        loc = pkg.find("c:location", NS_COMMON)
        if None in (n, a, v, loc):
            continue
        name = (n.text or "").strip()
        parch = (a.text or "").strip()
        if parch not in (arch, "noarch"):
            continue
        rel = (v.attrib.get("epoch","0"), v.attrib.get("ver",""), v.attrib.get("rel",""), parch)
        href = loc.attrib.get("href","")
        if not href:
            continue
        url = f"{repo}/{href}"
        prev = catalog.get(name)
        if prev is None or rel > prev[0]:
            catalog[name] = (rel, os.path.basename(href), url)

for name in pkgs:
    rec = catalog.get(name)
    if rec is None:
        print(f"WARN missing rpm for package {name}", file=sys.stderr)
        continue
    _, fn, url = rec
    dest = os.path.join(cache, fn)
    if not os.path.exists(dest):
        print(f"Downloading RPM: {url}", file=sys.stderr)
        urllib.request.urlretrieve(url, dest)
print("RPM fetch complete", file=sys.stderr)
PY

  for rpm in "${cache}"/*.rpm; do
    if test -f "${rpm}"; then
      (cd "${sysroot}" && rpm2cpio "${rpm}" | cpio -idmu --quiet)
    fi
  done
  {
    printf '%s\n' "${rpm_fp}"
    printf '%s\n' "$(date -u +%FT%TZ)"
  } > "${marker}"
}

extract_rootfs_tarball_to_sysroot() {
  local target="$1"
  local sysroot="$2"
  local tarball marker tmpdir
  tarball="$(rootfs_tarball_for_target "${target}")" || return 1
  ensure_rocky9_rootfs_tarball "${target}" "${tarball}"

  mkdir -p "${sysroot}"
  marker="${sysroot}/.hpc_rootfs_extracted_from"
  if test -f "${marker}" && test "$(cat "${marker}")" = "${tarball}"; then
    return 0
  fi
  echo "Extracting rootfs tarball for ${target}: ${tarball} -> ${sysroot}"
  rm -rf "${sysroot:?}"/*
  tmpdir="$(mktemp -d "${REPO_ROOT}/.build/rocky-oci.XXXXXX")"
  case "${tarball}" in
    *.tar.gz|*.tgz) tar -xzf "${tarball}" -C "${tmpdir}" ;;
    *.tar.xz|*.txz) tar -xJf "${tarball}" -C "${tmpdir}" ;;
    *.tar.bz2|*.tbz2) tar -xjf "${tarball}" -C "${tmpdir}" ;;
    *.tar) tar -xf "${tarball}" -C "${tmpdir}" ;;
    *) rm -rf "${tmpdir}"; fail "Unsupported rootfs tarball extension: ${tarball}" ;;
  esac
  if test -f "${tmpdir}/oci-layout" && test -f "${tmpdir}/index.json"; then
    python3 - "${tmpdir}" "${sysroot}" <<'PY'
import json, os, tarfile, shutil, sys
tmpdir, sysroot = sys.argv[1], sys.argv[2]
def safe_join(base, name):
    joined = os.path.normpath(os.path.join(base, name.lstrip("/")))
    if not joined.startswith(os.path.abspath(base) + os.sep) and joined != os.path.abspath(base):
        raise RuntimeError(f"unsafe path: {name}")
    return joined
index = json.load(open(os.path.join(tmpdir, "index.json"), "r", encoding="utf-8"))
manifest_digest = index["manifests"][0]["digest"].split(":", 1)[1]
manifest_path = os.path.join(tmpdir, "blobs", "sha256", manifest_digest)
manifest = json.load(open(manifest_path, "r", encoding="utf-8"))
layers = manifest.get("layers", [])
for layer in layers:
    digest = layer["digest"].split(":", 1)[1]
    layer_path = os.path.join(tmpdir, "blobs", "sha256", digest)
    with tarfile.open(layer_path, "r:*") as tf:
        for member in tf.getmembers():
            name = member.name
            base = os.path.basename(name)
            dirname = os.path.dirname(name)
            if base.startswith(".wh."):
                parent = safe_join(sysroot, dirname)
                if base == ".wh..wh..opq":
                    if os.path.isdir(parent):
                        for entry in os.listdir(parent):
                            p = os.path.join(parent, entry)
                            if os.path.isdir(p) and not os.path.islink(p):
                                shutil.rmtree(p)
                            else:
                                try: os.remove(p)
                                except FileNotFoundError: pass
                else:
                    target = safe_join(parent, base[4:])
                    if os.path.isdir(target) and not os.path.islink(target):
                        shutil.rmtree(target)
                    else:
                        try: os.remove(target)
                        except FileNotFoundError: pass
                continue
            out_path = safe_join(sysroot, name)
            if member.isdir():
                os.makedirs(out_path, exist_ok=True); continue
            if member.issym():
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                try: os.remove(out_path)
                except FileNotFoundError: pass
                os.symlink(member.linkname, out_path); continue
            if member.islnk():
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                link_target = safe_join(sysroot, member.linkname)
                try: os.remove(out_path)
                except FileNotFoundError: pass
                os.link(link_target, out_path); continue
            if member.isfile():
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                try:
                    os.remove(out_path)
                except FileNotFoundError:
                    pass
                f = tf.extractfile(member)
                if f is None: continue
                with open(out_path, "wb") as out:
                    shutil.copyfileobj(f, out)
                os.chmod(out_path, member.mode & 0o7777)
PY
  else
    cp -a "${tmpdir}/." "${sysroot}/"
  fi
  rm -rf "${tmpdir}"
  printf '%s\n' "${tarball}" > "${marker}"
  return 0
}

ensure_rocky9_sysroot() {
  local target="$1"
  local sysroot="$2"
  local root_marker="${sysroot}/.hpc_rootfs_extracted_from"
  # Skip creation when path already looks provisioned.
  if has_required_target_toolchain "${sysroot}" && has_required_target_gcc_runtime "${sysroot}"; then
    return 0
  fi

  if test -f "${root_marker}"; then
    ensure_rocky9_toolchain_layer "${target}" "${sysroot}"
    return 0
  fi

  if extract_rootfs_tarball_to_sysroot "${target}" "${sysroot}"; then
    ensure_rocky9_toolchain_layer "${target}" "${sysroot}"
    return 0
  fi

  fail "No rootfs tarball configured and AUTO_CREATE_ROCKY9_SYSROOT=0."
}

detect_qemu_bin_for_target() {
  case "$(triplet_cpu "$1")" in
    aarch64) echo "qemu-aarch64-static" ;;
    arm*|armhf|armel) echo "qemu-arm-static" ;;
    x86_64) echo "qemu-x86_64-static" ;;
    i?86) echo "qemu-i386-static" ;;
    ppc64le) echo "qemu-ppc64le-static" ;;
    ppc64) echo "qemu-ppc64-static" ;;
    riscv64) echo "qemu-riscv64-static" ;;
    *) echo "" ;;
  esac
}

foreign_qemu_user_executable() {
  local target="$1"
  local qemu_bin
  qemu_bin="$(detect_qemu_bin_for_target "${target}")"
  test -n "${qemu_bin}" || fail "No qemu-user mapping for target ${target}"
  if command -v "${qemu_bin}" >/dev/null 2>&1; then
    command -v "${qemu_bin}"
    return 0
  fi
  if test -x "${LOCAL_QEMU_ROOT}/bin/${qemu_bin}"; then
    printf '%s/bin/%s\n' "${LOCAL_QEMU_ROOT}" "${qemu_bin}"
    return 0
  fi
  fail "Missing ${qemu_bin} for foreign test execution (install system package or bootstrap via LOCAL_QEMU_ROOT)"
}

local_qemu_build_root() {
  printf '%s/.build/qemu-build-host' "${REPO_ROOT}"
}

local_qemu_src_root() {
  printf '%s/.build/src-qemu-host' "${REPO_ROOT}"
}

toolwrap_root_for_target() {
  printf '%s/.build/qemu-toolwrap-%s' "${REPO_ROOT}" "$(slugify_triplet "$1")"
}

create_qemu_wrapper() {
  local wrapper="$1"
  local qemu_bin="$2"
  local sysroot="$3"
  local target_bin="$4"
  cat > "${wrapper}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "${qemu_bin}" -L "${sysroot}" "${target_bin}" "\$@"
EOF
  chmod +x "${wrapper}"
}

create_qemu_cc_wrapper() {
  local wrapper="$1"
  local qemu_path="$2"
  local sysroot="$3"
  local target_bin="$4"
  local tool_prefix="$5"
  local gcc_lib_dir="${6:-}"
  local extra_b=""
  if test -n "${gcc_lib_dir}"; then
    extra_b=" -B\"${gcc_lib_dir}/\""
  fi
  cat > "${wrapper}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "${qemu_path}" -L "${sysroot}" "${target_bin}" -B"${tool_prefix}/"${extra_b} --sysroot="${sysroot}" -fuse-ld=bfd -fno-use-linker-plugin "\$@"
EOF
  chmod +x "${wrapper}"
}

create_qemu_cpp_wrapper() {
  local wrapper="$1"
  local qemu_path="$2"
  local sysroot="$3"
  local target_bin="$4"
  cat > "${wrapper}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "${qemu_path}" -L "${sysroot}" "${target_bin}" --sysroot="${sysroot}" "\$@"
EOF
  chmod +x "${wrapper}"
}

create_pkg_config_wrapper() {
  local wrapper="$1"
  local sysroot="$2"
  local host_pc
  # Bake absolute host pkg-config path: run_foreign prepends wrapdir to PATH, which would otherwise recurse into this wrapper.
  host_pc="$(command -v pkg-config)"
  test -x "${host_pc}" || fail "Cannot locate host pkg-config while creating ${wrapper}"
  cat > "${wrapper}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec env PKG_CONFIG_SYSROOT_DIR="${sysroot}" PKG_CONFIG_LIBDIR="${sysroot}/usr/lib/pkgconfig:${sysroot}/usr/lib64/pkgconfig:${sysroot}/lib/pkgconfig:${sysroot}/lib64/pkgconfig" "${host_pc}" "\$@"
EOF
  chmod +x "${wrapper}"
}

ensure_local_qemu_user() {
  local target="$1"
  local qemu_bin srcroot bldroot srcdir tarball emu_name target_list host_cc host_cxx
  qemu_bin="$(detect_qemu_bin_for_target "${target}")"
  test -n "${qemu_bin}" || return 0

  if command -v "${qemu_bin}" >/dev/null 2>&1; then
    return 0
  fi
  if test -x "${LOCAL_QEMU_ROOT}/bin/${qemu_bin}"; then
    return 0
  fi
  if test "${AUTO_BOOTSTRAP_QEMU}" != "1"; then
    fail "Missing ${qemu_bin}; set AUTO_BOOTSTRAP_QEMU=1 or install qemu-user."
  fi

  emu_name="${qemu_bin#qemu-}"
  emu_name="${emu_name%-static}"
  target_list="${emu_name}-linux-user"
  ensure_python_tomli
  ensure_ninja

  srcroot="$(local_qemu_src_root)"
  bldroot="$(local_qemu_build_root)"
  srcdir="${srcroot}/qemu-${QEMU_VER}"
  tarball="${srcroot}/qemu-${QEMU_VER}.tar.xz"
  host_cc="$(command -v gcc 2>/dev/null || command -v cc 2>/dev/null || true)"
  host_cxx="$(command -v g++ 2>/dev/null || command -v c++ 2>/dev/null || true)"
  test -n "${host_cc}" || fail "Need gcc/cc on host to bootstrap qemu-user."
  test -n "${host_cxx}" || fail "Need g++/c++ on host to bootstrap qemu-user."
  mkdir -p "${srcroot}" "${bldroot}" "${LOCAL_QEMU_ROOT}"

  if test ! -d "${srcdir}"; then
    fetch_url "$(printf "${QEMU_URL_FMT}" "${QEMU_VER}")" "${tarball}"
    tar -C "${srcroot}" -xf "${tarball}"
  fi
  # Newer host headers may define struct sched_attr; older qemu assumes otherwise.
  python3 - "${srcdir}" <<'PY'
import pathlib, sys
srcdir = pathlib.Path(sys.argv[1])
f = srcdir / "linux-user" / "syscall.c"
txt = f.read_text(encoding="utf-8")
needle = "/* sched_attr is not defined in glibc */\nstruct sched_attr {"
if needle in txt and "#ifndef SCHED_ATTR_SIZE_VER0" not in txt:
    txt = txt.replace(needle, "/* sched_attr is not defined in glibc */\n#ifndef SCHED_ATTR_SIZE_VER0\nstruct sched_attr {", 1)
    txt = txt.replace("};\n#define __NR_sys_sched_getattr __NR_sched_getattr", "};\n#endif\n#define __NR_sys_sched_getattr __NR_sched_getattr", 1)
    f.write_text(txt, encoding="utf-8")
PY

  rm -rf "${bldroot}"
  mkdir -p "${bldroot}"
  (
    cd "${bldroot}"
    if test ! -x "${LOCAL_QEMU_ROOT}/bin/${qemu_bin}"; then
      CC="${host_cc}" CXX="${host_cxx}" "${srcdir}/configure" \
        --prefix="${LOCAL_QEMU_ROOT}" \
        --target-list="${target_list}" \
        --disable-system \
        --disable-tools \
        --disable-docs \
        --disable-werror
      make -j"${JOBS}"
      make install
      if test -x "${LOCAL_QEMU_ROOT}/bin/qemu-${emu_name}"; then
        ln -sf "qemu-${emu_name}" "${LOCAL_QEMU_ROOT}/bin/${qemu_bin}"
      fi
    fi
  )
}

create_gcc_exec_wrappers() {
  local wrapdir="$1"
  local qemu_path="$2"
  local sysroot="$3"
  local gcc_exec_dir target_triplet gcc_ver wrap_exec_dir exe
  for gcc_exec_dir in "${sysroot}/usr/libexec/gcc"/*/*; do
    test -d "${gcc_exec_dir}" || continue
    target_triplet="$(basename "$(dirname "${gcc_exec_dir}")")"
    gcc_ver="$(basename "${gcc_exec_dir}")"
    wrap_exec_dir="${wrapdir}/libexec/gcc/${target_triplet}/${gcc_ver}"
    mkdir -p "${wrap_exec_dir}"
    for exe in cc1 cc1plus collect2 lto-wrapper lto1; do
      if test -x "${gcc_exec_dir}/${exe}"; then
        create_qemu_wrapper "${wrap_exec_dir}/${exe}" "${qemu_path}" "${sysroot}" "${gcc_exec_dir}/${exe}"
      fi
    done
  done
}

ensure_foreign_toolwraps() {
  local target="$1"
  local sysroot="$2"
  local qemu_bin wrapdir qemu_path gcc_lib_dir d
  qemu_bin="$(detect_qemu_bin_for_target "${target}")"
  test -n "${qemu_bin}" || return 0
  wrapdir="$(toolwrap_root_for_target "${target}")"
  mkdir -p "${wrapdir}"

  if command -v "${qemu_bin}" >/dev/null 2>&1; then
    qemu_path="$(command -v "${qemu_bin}")"
  else
    qemu_path="${LOCAL_QEMU_ROOT}/bin/${qemu_bin}"
    test -x "${qemu_path}" || fail "Missing emulator ${qemu_bin} at ${qemu_path}"
  fi

  gcc_lib_dir=""
  for d in "${sysroot}/usr/lib/gcc"/*/*; do
    if test -d "${d}"; then
      gcc_lib_dir="${d}"
      break
    fi
  done
  create_qemu_cc_wrapper "${wrapdir}/gcc" "${qemu_path}" "${sysroot}" "${sysroot}/usr/bin/gcc" "${wrapdir}" "${gcc_lib_dir}"
  create_qemu_cc_wrapper "${wrapdir}/g++" "${qemu_path}" "${sysroot}" "${sysroot}/usr/bin/g++" "${wrapdir}" "${gcc_lib_dir}"
  if test -x "${sysroot}/usr/bin/cpp"; then
    create_qemu_cpp_wrapper "${wrapdir}/cpp" "${qemu_path}" "${sysroot}" "${sysroot}/usr/bin/cpp"
  fi
  if test -x "${sysroot}/usr/bin/as"; then
    create_qemu_wrapper "${wrapdir}/as" "${qemu_path}" "${sysroot}" "${sysroot}/usr/bin/as"
  fi
  if test -x "${sysroot}/usr/bin/ld"; then
    create_qemu_wrapper "${wrapdir}/ld" "${qemu_path}" "${sysroot}" "${sysroot}/usr/bin/ld"
  fi
  if test -x "${sysroot}/usr/bin/ld.bfd"; then
    create_qemu_wrapper "${wrapdir}/ld.bfd" "${qemu_path}" "${sysroot}" "${sysroot}/usr/bin/ld.bfd"
  fi
  create_gcc_exec_wrappers "${wrapdir}" "${qemu_path}" "${sysroot}"
  create_qemu_wrapper "${wrapdir}/ar" "${qemu_path}" "${sysroot}" "${sysroot}/usr/bin/ar"
  create_qemu_wrapper "${wrapdir}/ranlib" "${qemu_path}" "${sysroot}" "${sysroot}/usr/bin/ranlib"
  create_qemu_wrapper "${wrapdir}/strip" "${qemu_path}" "${sysroot}" "${sysroot}/usr/bin/strip"
  create_pkg_config_wrapper "${wrapdir}/pkg-config" "${sysroot}"
}

ensure_foreign_tooling() {
  local target="$1"
  local sysroot="$2"
  local qemu_bin

  ensure_rocky9_sysroot "${target}" "${sysroot}"
  test -d "${sysroot}" || fail "SYSROOT does not exist: ${sysroot}"
  test -x "${sysroot}/usr/bin/gcc" || fail "Expected target gcc at ${sysroot}/usr/bin/gcc"
  test -x "${sysroot}/usr/bin/g++" || fail "Expected target g++ at ${sysroot}/usr/bin/g++"
  test -x "${sysroot}/usr/bin/ar" || fail "Expected target ar at ${sysroot}/usr/bin/ar"
  test -x "${sysroot}/usr/bin/ranlib" || fail "Expected target ranlib at ${sysroot}/usr/bin/ranlib"
  test -x "${sysroot}/usr/bin/strip" || fail "Expected target strip at ${sysroot}/usr/bin/strip"
  command -v make >/dev/null 2>&1 || fail "Host make is required in rootless mode"
  command -v cmake >/dev/null 2>&1 || fail "Host cmake is required in rootless mode"
  command -v pkg-config >/dev/null 2>&1 || fail "Host pkg-config is required in rootless mode"

  ensure_local_qemu_user "${target}"
  qemu_bin="$(detect_qemu_bin_for_target "${target}")"
  if test -n "${qemu_bin}" && ! command -v "${qemu_bin}" >/dev/null 2>&1; then
    test -x "${LOCAL_QEMU_ROOT}/bin/${qemu_bin}" \
      || fail "Missing ${qemu_bin} after local bootstrap at ${LOCAL_QEMU_ROOT}/bin/${qemu_bin}"
    export PATH="${LOCAL_QEMU_ROOT}/bin:${PATH}"
  fi
  ensure_foreign_toolwraps "${target}" "${sysroot}"
}

run_foreign() {
  local target="$1"
  local sysroot="$2"
  shift 2
  local slug qemu_prefix wrapdir
  slug="$(slugify_triplet "${target}")"
  qemu_prefix="${QEMU_LD_PREFIX:-${sysroot}}"
  wrapdir="$(toolwrap_root_for_target "${target}")"
  env \
    PATH="${wrapdir}:${PATH}" \
    PKG_CONFIG_SYSROOT_DIR="${sysroot}" \
    PKG_CONFIG_LIBDIR="${sysroot}/usr/lib/pkgconfig:${sysroot}/usr/lib64/pkgconfig:${sysroot}/lib/pkgconfig:${sysroot}/lib64/pkgconfig" \
    QEMU_LD_PREFIX="${qemu_prefix}" \
    SHELL="/bin/bash" \
    CONFIG_SHELL="/bin/bash" \
    GCC_EXEC_PREFIX="${wrapdir}/libexec/gcc/" \
    COMPILER_PATH="${wrapdir}${COMPILER_PATH:+:${COMPILER_PATH}}" \
    CFLAGS="${CFLAGS:+${CFLAGS} }-fno-use-linker-plugin" \
    CXXFLAGS="${CXXFLAGS:+${CXXFLAGS} }-fno-use-linker-plugin" \
    CC="${wrapdir}/gcc" \
    CXX="${wrapdir}/g++" \
    CPP="${wrapdir}/gcc -E" \
    AR="${wrapdir}/ar" \
    RANLIB="${wrapdir}/ranlib" \
    STRIP="${wrapdir}/strip" \
    "HPC_CROSS_TARGET=${target}" \
    "HPC_CROSS_SLUG=${slug}" \
    "$@"
}

foreign_src_root() {
  printf '%s/.build/src-qemu-%s' "${MONITOR_DIR}" "$(slugify_triplet "$1")"
}

foreign_prefix() {
  printf '%s/prefix-qemu-%s' "${PREFIX_ROOT}" "$(slugify_triplet "$1")"
}

foreign_build_root() {
  printf '%s/.build-qemu-%s' "${MONITOR_DIR}" "$(slugify_triplet "$1")"
}

foreign_log_file() {
  printf '%s/%s.log' "${LOG_DIR_ROOT}" "$(slugify_triplet "$1")"
}

extract_tar_if_missing_dir() {
  local tar_file="$1"
  local out_dir="$2"
  local parent_dir="$3"
  if test ! -d "${out_dir}"; then
    mkdir -p "${parent_dir}"
    tar -C "${parent_dir}" -xzf "${tar_file}"
  fi
}

build_foreign_libev() {
  local target="$1"
  local sysroot="$2"
  local srcroot prefix d t
  srcroot="$(foreign_src_root "${target}")"
  prefix="$(foreign_prefix "${target}")"
  d="${srcroot}/libev-${LIBEV_VER}"
  t="${srcroot}/libev-${LIBEV_VER}.tar.gz"

  mkdir -p "${srcroot}"
  if test ! -d "${d}"; then
    fetch_url "$(printf "${LIBEV_URL_FMT}" "${LIBEV_VER}")" "${t}"
    extract_tar_if_missing_dir "${t}" "${d}" "${srcroot}"
  fi

  run_foreign "${target}" "${sysroot}" \
    bash -c "set -euo pipefail; cd '${d}' && ./configure --host='${target}' --prefix='${prefix}' --enable-static --disable-shared && make -j'${JOBS}' && make install"
}

build_foreign_rabbitmq_c() {
  local target="$1"
  local sysroot="$2"
  local srcroot prefix d t b
  srcroot="$(foreign_src_root "${target}")"
  prefix="$(foreign_prefix "${target}")"
  d="${srcroot}/rabbitmq-c-${RABBITMQ_VER}"
  t="${srcroot}/rabbitmq-c-${RABBITMQ_VER}.tar.gz"
  b="${d}/build"

  mkdir -p "${srcroot}"
  if test ! -d "${d}"; then
    fetch_url "$(printf "${RABBITMQ_C_URL_FMT}" "${RABBITMQ_VER}")" "${t}"
    extract_tar_if_missing_dir "${t}" "${d}" "${srcroot}"
  fi
  mkdir -p "${b}"

  run_foreign "${target}" "${sysroot}" \
    bash -c "set -euo pipefail; cd '${b}' && cmake .. -DCMAKE_INSTALL_PREFIX='${prefix}' -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DBUILD_SHARED_LIBS=OFF -DBUILD_STATIC_LIBS=ON -DENABLE_SSL_SUPPORT=OFF -DBUILD_EXAMPLES=OFF -DBUILD_TOOLS=OFF -DBUILD_TESTS=OFF && cmake --build . -j'${JOBS}' && cmake --install ."
}

build_foreign_likwid() {
  local target="$1"
  local sysroot="$2"
  local srcroot prefix d t
  srcroot="$(foreign_src_root "${target}")"
  prefix="$(foreign_prefix "${target}")"
  d="${srcroot}/likwid-${LIKWID_TAG}"
  t="${srcroot}/likwid-${LIKWID_TAG}.tar.gz"

  mkdir -p "${srcroot}"
  if test ! -d "${d}"; then
    fetch_url "$(printf "${LIKWID_URL_FMT}" "${LIKWID_TAG}")" "${t}"
    tar -C "${srcroot}" -xzf "${t}"
    if test ! -d "${d}" && test -d "${srcroot}/likwid-${LIKWID_TAG#v}"; then
      mv "${srcroot}/likwid-${LIKWID_TAG#v}" "${d}"
    fi
  fi
  test -d "${d}" || fail "Could not locate LIKWID source directory at ${d}"

  run_foreign "${target}" "${sysroot}" bash -c "
    set -euo pipefail
    cd '${d}'
    if grep -q '^SHARED_LIBRARY = true' config.mk 2>/dev/null; then
      sed -i 's/^SHARED_LIBRARY = true/SHARED_LIBRARY = false/' config.mk
    fi
    make -j'${JOBS}' PREFIX='${prefix}' INSTALLED_PREFIX='${prefix}' BUILDDAEMON=false BUILDFREQ=false BUILD_SYSFEATURES=false ACCESSMODE=direct
    make install PREFIX='${prefix}' INSTALLED_PREFIX='${prefix}' BUILDDAEMON=false BUILDFREQ=false BUILD_SYSFEATURES=false ACCESSMODE=direct
  "
}

build_foreign_libbpf() {
  local target="$1"
  local sysroot="$2"
  local srcroot prefix d t
  srcroot="$(foreign_src_root "${target}")"
  prefix="$(foreign_prefix "${target}")"
  d="${srcroot}/libbpf-${LIBBPF_VER}"
  t="${srcroot}/libbpf-${LIBBPF_VER}.tar.gz"

  mkdir -p "${srcroot}"
  if test ! -d "${d}"; then
    fetch_url "$(printf "${LIBBPF_URL_FMT}" "${LIBBPF_VER}")" "${t}"
    extract_tar_if_missing_dir "${t}" "${d}" "${srcroot}"
  fi

  run_foreign "${target}" "${sysroot}" \
    bash -c "set -euo pipefail; cd '${d}/src' && make -j'${JOBS}' BUILD_STATIC_ONLY=y OBJDIR=build DESTDIR= && make install PREFIX='${prefix}' BUILD_STATIC_ONLY=y OBJDIR=build DESTDIR="
}

build_foreign_deps() {
  local target="$1"
  local sysroot="$2"
  local prefix
  prefix="$(foreign_prefix "${target}")"
  mkdir -p "${prefix}/include" "${prefix}/lib" "${prefix}/lib/pkgconfig"

  if test "${SKIP_DEPS}" = "1"; then
    echo "Foreign ${target}: SKIP_DEPS=1, reusing ${prefix}"
    return 0
  fi

  echo "Foreign ${target}: building static deps into ${prefix}"
  build_foreign_libev "${target}" "${sysroot}" || return $?
  build_foreign_rabbitmq_c "${target}" "${sysroot}" || return $?
  if is_x86_triplet "${target}"; then
    build_foreign_likwid "${target}" "${sysroot}" || return $?
  else
    echo "Foreign ${target}: skipping LIKWID for non-x86 triplet"
  fi
  if test "${WANT_METRIC_PROFILER_EBPF}" = "1"; then
    build_foreign_libbpf "${target}" "${sysroot}" || return $?
  fi
}

foreign_monitor_cfg_args() {
  local target="$1"
  # Foreign builds link libc/libm dynamically; full --enable-all-static pulls glibc static archives
  # whose IFUNC resolvers need symbols not satisfied for qemu-user smoke links.
  local args=(
    --disable-all-static
    --with-systemduserunitdir=no
    --disable-gpu
    --disable-amd-gpu
    --disable-infiniband
    --disable-opa
    --disable-lustre
  )

  # Under emulation, /proc/cpuinfo may reflect host details. Keep x86 deterministic.
  if is_x86_triplet "${target}"; then
    args+=(--with-monitor-arch=intel --with-cpu-counter-backend=auto)
  else
    # Non-x86 foreign smoke has no LIKWID and usually no libdcgm in the sysroot.
    args+=(--disable-hardware)
  fi
  printf '%s\n' "${args[@]}"
}

build_foreign_monitor() {
  local target="$1"
  local sysroot="$2"
  local prefix build_dir cfg_file qemu_exe
  prefix="$(foreign_prefix "${target}")"
  build_dir="$(foreign_build_root "${target}")"
  qemu_exe="$(foreign_qemu_user_executable "${target}")"
  if test -d "${build_dir}"; then
    echo "Removing prior foreign monitor build tree: ${build_dir}"
    rm -rf "${build_dir}"
  fi
  mkdir -p "${build_dir}"
  cfg_file="${build_dir}/configure.args"
  : > "${cfg_file}"
  foreign_monitor_cfg_args "${target}" >> "${cfg_file}"

  run_foreign "${target}" "${sysroot}" bash -c "
    set -euo pipefail
    cd '${build_dir}'
    export CPPFLAGS='-I${prefix}/include '
    export LDFLAGS='-L${prefix}/lib -L${prefix}/lib64 '
    export PKG_CONFIG_PATH='${prefix}/lib/pkgconfig:${prefix}/lib64/pkgconfig:\${PKG_CONFIG_PATH:-}'
    mapfile -t cfg_args < '${cfg_file}'
    '${MONITOR_DIR}/configure' --host='${target}' \"\${cfg_args[@]}\"
    make -j'${JOBS}'
    make check LOG_COMPILER='${qemu_exe}'
  "
}

build_native_target() {
  local target="$1"
  echo "Native ${target}: running canonical static bundle build + tests"
  (cd "${MONITOR_DIR}" && "${MONITOR_DIR}/scripts/build_static_bundle.sh")
  (cd "${MONITOR_DIR}/.build-static" && make check)
}

build_foreign_target() {
  local target="$1"
  local sysroot log_file
  sysroot="$(sysroot_for_target "${target}")"
  log_file="$(foreign_log_file "${target}")"
  mkdir -p "${LOG_DIR_ROOT}"

  ensure_foreign_tooling "${target}" "${sysroot}"
  {
    echo "=== Foreign target: ${target} ==="
    echo "SYSROOT=${sysroot}"
    echo "PREFIX=$(foreign_prefix "${target}")"
    echo "BUILD=$(foreign_build_root "${target}")"
    run_foreign "${target}" "${sysroot}" gcc --version || return $?
    build_foreign_deps "${target}" "${sysroot}" || return $?
    build_foreign_monitor "${target}" "${sysroot}" || return $?
    echo "=== Foreign target ${target}: success ==="
  } > >(tee "${log_file}") 2>&1
}

run_target() {
  local target="$1"
  if is_native_target "${target}"; then
    build_native_target "${target}"
  else
    build_foreign_target "${target}"
  fi
}

parse_args() {
  while test $# -gt 0; do
    case "$1" in
      --targets)
        shift
        test $# -gt 0 || fail "--targets requires an argument"
        TARGETS="$1"
        shift
        ;;
      --skip-deps)
        SKIP_DEPS=1
        shift
        ;;
      --fail-fast)
        FAIL_FAST=1
        shift
        ;;
      --force-foreign)
        FORCE_FOREIGN=1
        shift
        ;;
      --force-native)
        FORCE_NATIVE=1
        shift
        ;;
      --no-autoreconf)
        RUN_AUTORECONF=0
        shift
        ;;
      -h|--help)
        usage_exit 0
        ;;
      *)
        fail "Unknown option: $1"
        ;;
    esac
  done
}

main() {
  parse_args "$@"
  test "${FORCE_FOREIGN}" = "0" || test "${FORCE_NATIVE}" = "0" \
    || fail "Cannot set both FORCE_FOREIGN=1 and FORCE_NATIVE=1"

  if test "${RUN_AUTORECONF}" = "1"; then
    echo "Host: regenerating monitor Autotools files (autoreconf -fi)"
    (cd "${MONITOR_DIR}" && autoreconf -fi)
  fi

  local raw targets=() t failures=0
  raw="${TARGETS//,/ }"
  for t in ${raw}; do
    targets+=("${t}")
  done
  test "${#targets[@]}" -gt 0 || fail "No targets provided"

  echo "Targets: ${targets[*]}"
  for t in "${targets[@]}"; do
    echo ""
    echo "--- Running target: ${t} ---"
    if run_target "${t}"; then
      echo "--- Target ${t}: success ---"
    else
      echo "--- Target ${t}: FAILED ---" >&2
      failures=$((failures + 1))
      if test "${FAIL_FAST}" = "1"; then
        exit 1
      fi
    fi
  done

  if test "${failures}" -gt 0; then
    echo "Completed with ${failures} failing target(s)." >&2
    exit 1
  fi
  echo "All targets completed successfully."
}

main "$@"
