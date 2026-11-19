#!/usr/bin/env bash
# lib/gpu_toolchain.sh against a stub Lmod and stub CUDA / NVHPC trees laid out like the TACC
# nodes: Horizon (GB200, aarch64) needs 'gcc/15.3.0 cuda/13.3' and keeps cuBLAS in
# <nvhpc>/math_libs/<cuda ver>/lib64 next to an older math_libs/12.9; Stampede3 (RTX PRO 6000,
# x86_64) loads cuda/13.1 directly and has only shared cuBLAS. Also the override variable and
# every NOT_RUN reason (no Lmod, no cuda >= 12.8, no prerequisite, no nvcc, no cuBLAS, no
# cublas_v2.h, CUDA too old).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="${HERE}/../lib/gpu_toolchain.sh"
T="$(mktemp -d "${TMPDIR:-/tmp}/hps-gpu-toolchain-test.XXXXXX")"
trap 'rm -rf "${T}"' EXIT

fail() {
  echo "test_gpu_toolchain: $*" >&2
  exit 1
}

test -r "${LIB}" || fail "missing ${LIB}"

# nvhpc_tree ROOT CUDA_VER: an NVHPC root with cuda/<ver>/bin/nvcc and cudart in cuda/<ver>/lib64.
nvhpc_tree() {
  mkdir -p "$1/cuda/$2/bin" "$1/cuda/$2/lib64" "$1/cuda/$2/include"
  printf '#!/bin/sh\necho "Cuda compilation tools, release %s, V%s.0"\n' "$2" "$2" > "$1/cuda/$2/bin/nvcc"
  chmod +x "$1/cuda/$2/bin/nvcc"
  : > "$1/cuda/$2/lib64/libcudart_static.a"
  : > "$1/cuda/$2/lib64/libculibos.a"
}
# math_libs ROOT VER static|shared: cuBLAS in ROOT/math_libs/VER/lib64 with its include dir.
math_libs() {
  mkdir -p "$1/math_libs/$2/lib64" "$1/math_libs/$2/include"
  : > "$1/math_libs/$2/include/cublas_v2.h"
  : > "$1/math_libs/$2/lib64/libcublas.so"
  : > "$1/math_libs/$2/lib64/libcublasLt.so"
  if [ "$3" = static ]; then
    : > "$1/math_libs/$2/lib64/libcublas_static.a"
    : > "$1/math_libs/$2/lib64/libcublasLt_static.a"
  fi
}

HZ="${T}/hz/Linux_aarch64/26.9"
nvhpc_tree "${HZ}" 13.3
math_libs "${HZ}" 12.9 static
math_libs "${HZ}" 13.3 static
S3="${T}/s3/Linux_x86_64/26.1"
nvhpc_tree "${S3}" 13.1
math_libs "${S3}" 13.1 shared

# Stub Lmod: 'module' as a shell function (like Lmod's init/bash) that sets the TACC_CUDA_* /
# CUDA_HOME / NVHPC_CUDA_HOME variables the real cuda modulefiles set. Every call is logged.
mkdir -p "${T}/lmod/init" "${T}/nolmod"
cat > "${T}/lmod/init/bash" <<'EOF'
module() {
  echo "module $*" >> "${FAKE_LOG}"
  local root ver
  case "${FAKE_SITE}" in
    hz) root="${FAKE_HZ}" ;;
    *) root="${FAKE_S3}" ;;
  esac
  case "$*" in
    "-t spider cuda") printf '%b' "${FAKE_SPIDER}" >&2; return 0 ;;
    "-t spider cuda/"*) printf '%b' "${FAKE_DETAIL}" >&2; return 0 ;;
    load*)
      case " $* " in
        *" gcc/"*) ;;
        *) if [ "${FAKE_NEEDS_GCC:-0}" = 1 ]; then
             echo "Lmod has detected the following error: cuda is not loadable" >&2; return 1
           fi ;;
      esac
      ver="$*"
      ver="${ver##*cuda/}"
      export TACC_CUDA_DIR="${root}/cuda/${ver}" TACC_CUDA_VER="${FAKE_VER:-${ver}}"
      export TACC_CUDA_LIB="${root}/cuda/${ver}/lib64" CUDA_HOME="${root}/cuda/${ver}"
      export NVHPC_CUDA_HOME="${root}/cuda/${ver}"
      return 0 ;;
  esac
  return 1
}
EOF

HZ_DETAIL='\n  cuda: cuda/13.3\n\n    You will need to load all module(s) on any one of the lines below before the "cuda/13.3" module is available to load.\n\n      gcc/15.3.0\n \n    Help:\n'
S3_DETAIL='\n  cuda: cuda/13.1\n\n    This module can be loaded directly: module load cuda/13.1\n'

# run SITE [VAR=VALUE ...]: resolve in a clean shell; prints rc, the GPU_TC_* results and the log.
run() {
  local site=$1
  shift
  : > "${T}/module.log"
  # shellcheck disable=SC2016  # the -c script expands in the child
  env -i PATH=/usr/bin:/bin HOME="${T}" MODULESHOME="${T}/lmod" FAKE_LOG="${T}/module.log" \
    FAKE_SITE="${site}" FAKE_HZ="${HZ}" FAKE_S3="${S3}" "$@" \
    bash --noprofile --norc -c '
      . "$1"
      gpu_toolchain_resolve
      echo "rc=$?"
      echo "nvcc=${GPU_TC_NVCC:-}"
      echo "ver=${GPU_TC_CUDA_VER:-}"
      echo "modules=${GPU_TC_MODULES:-}"
      echo "cublas_lib=${GPU_TC_CUBLAS_LIB:-}"
      echo "cublas_inc=${GPU_TC_CUBLAS_INC:-}"
      echo "mode=${GPU_TC_LINK_MODE:-}"
      echo "link=${GPU_TC_LINK[*]:-}"
      echo "note=${GPU_TC_NOTE:-}"
      echo "not_run=${GPU_TC_NOT_RUN:-}"
    ' _ "${LIB}"
}
want() { # output pattern description
  printf '%s\n' "$1" | grep -qF -- "$2" || fail "$3: want '$2' in:
$1"
}

# Horizon: newest cuda >= 12.8 is 13.3 (listed out of order, older and too-old versions mixed
# in); the bare load fails, the prerequisite comes from spider; cuBLAS is math_libs/13.3, not the
# 12.9 directory that sorts first; every static archive exists, so the link is static.
out="$(run hz FAKE_NEEDS_GCC=1 FAKE_DETAIL="${HZ_DETAIL}" \
  FAKE_SPIDER='cuda/12.9\ncuda/13.3\ncuda/12.4\ncuda/13.1\ncudnn/9.1\n')"
want "${out}" "rc=0" "horizon resolves"
want "${out}" "nvcc=${HZ}/cuda/13.3/bin/nvcc" "horizon nvcc"
want "${out}" "ver=13.3" "horizon version"
want "${out}" "modules=gcc/15.3.0 cuda/13.3" "horizon modules"
want "${out}" "cublas_lib=${HZ}/math_libs/13.3/lib64" "horizon cuBLAS dir matches TACC_CUDA_VER"
want "${out}" "cublas_inc=${HZ}/math_libs/13.3/include" "horizon cuBLAS include"
want "${out}" "mode=static" "horizon static link"
want "${out}" "-lcublas_static -lcublasLt_static -lculibos" "horizon static archives"
want "${out}" "-cudart static" "horizon static cudart"
want "$(cat "${T}/module.log")" "module load gcc/15.3.0 cuda/13.3" "horizon prerequisite load"

# Stampede3: cuda/13.1 loads directly (no spider detail, no gcc); only shared cuBLAS, so the
# link falls back to the shared library with an rpath and says so.
out="$(run s3 FAKE_DETAIL="${S3_DETAIL}" FAKE_SPIDER='cuda/12.6\ncuda/13.1\n')"
want "${out}" "rc=0" "stampede3 resolves"
want "${out}" "modules=cuda/13.1" "stampede3 modules"
want "${out}" "cublas_lib=${S3}/math_libs/13.1/lib64" "stampede3 cuBLAS dir"
want "${out}" "mode=shared" "stampede3 shared fallback"
want "${out}" "-lcublas -lcublasLt" "stampede3 shared libs"
want "${out}" "-rpath -Xlinker ${S3}/math_libs/13.1/lib64" "stampede3 rpath"
want "${out}" "libcublas_static.a" "stampede3 note names the missing archive"
grep -q 'spider cuda/' "${T}/module.log" && fail "stampede3 must not ask spider for a prerequisite"

# Override: exactly the named modules, no spider.
out="$(run hz FAKE_NEEDS_GCC=1 HPCPERFSTATS_CUDA_MODULES="gcc/15.3.0 cuda/13.3")"
want "${out}" "rc=0" "override resolves"
want "${out}" "modules=gcc/15.3.0 cuda/13.3" "override modules"
grep -q spider "${T}/module.log" && fail "override must not call spider"

# NOT_RUN reasons.
out="$(run hz MODULESHOME="${T}/nolmod")"
want "${out}" "rc=1" "no lmod fails"
want "${out}" "${T}/nolmod/init/bash" "no lmod lists the searched init script"
out="$(run hz FAKE_SPIDER='cuda/12.4\ncuda/11.8\n')"
want "${out}" "rc=1" "old cuda modules fail"
want "${out}" "12.8" "old cuda modules name the minimum"
want "${out}" "cuda/12.4" "old cuda modules list what was found"
out="$(run hz FAKE_NEEDS_GCC=1 FAKE_DETAIL="${S3_DETAIL}" FAKE_SPIDER='cuda/13.3\n')"
want "${out}" "rc=1" "no prerequisite fails"
want "${out}" "names no prerequisite" "no prerequisite reason"
out="$(run hz HPCPERFSTATS_CUDA_MODULES="cuda/13.3" FAKE_VER=12.4)"
want "${out}" "rc=1" "cuda 12.4 fails"
want "${out}" "CUDA 12.4 < 12.8" "cuda too old reason"
mv "${HZ}/cuda/13.3/bin/nvcc" "${T}/nvcc.saved"
out="$(run hz HPCPERFSTATS_CUDA_MODULES="cuda/13.3")"
mv "${T}/nvcc.saved" "${HZ}/cuda/13.3/bin/nvcc"
want "${out}" "rc=1" "no nvcc fails"
want "${out}" "no nvcc" "no nvcc reason"
want "${out}" "${HZ}/cuda/13.3/bin/nvcc" "no nvcc lists the searched path"
mv "${HZ}/math_libs/13.3/include/cublas_v2.h" "${T}/h.saved"
out="$(run hz HPCPERFSTATS_CUDA_MODULES="cuda/13.3")"
mv "${T}/h.saved" "${HZ}/math_libs/13.3/include/cublas_v2.h"
want "${out}" "rc=1" "no cublas_v2.h fails"
want "${out}" "no cublas_v2.h" "no header reason"
want "${out}" "${HZ}/math_libs/13.3/include" "no header lists the searched include dir"
mv "${HZ}/math_libs/13.3" "${T}/ml.saved"
out="$(run hz HPCPERFSTATS_CUDA_MODULES="cuda/13.3")"
mv "${T}/ml.saved" "${HZ}/math_libs/13.3"
want "${out}" "rc=1" "no cuBLAS fails"
want "${out}" "no cuBLAS" "no cuBLAS reason"
want "${out}" "${HZ}/math_libs/13.3/lib64" "no cuBLAS lists math_libs/<ver>"
want "${out}" "${HZ}/cuda/13.3/lib64" "no cuBLAS lists the CUDA tree"
case "${out}" in *math_libs/12.9*) fail "no cuBLAS must not fall back to math_libs/12.9" ;; esac

echo "test_gpu_toolchain: PASS"
