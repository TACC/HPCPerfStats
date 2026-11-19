# CUDA + cuBLAS toolchain lookup for bin/gpu_tensor_calibrate.sh build (run as the invoking user).
# shellcheck shell=bash disable=SC2034  # GPU_TC_* are outputs for the caller
#
# Measurement-kit only: the daemon never links a vendor library (it dlopens NVML / DCGM because
# they are not on every node), so this lookup is not part of any production build.
#
# gpu_toolchain_resolve loads a CUDA module through Lmod and finds nvcc and cuBLAS. TACC layout
# (Horizon GB200, Stampede3 RTX PRO 6000): nvcc under <nvhpc>/cuda/<ver>, cuBLAS not in that CUDA
# tree but in <nvhpc>/math_libs/<ver>/lib64 for the same <ver> (several versions side by side, so
# the directory is chosen by version, never by globbing). On success (return 0) it sets:
#   GPU_TC_MODULES     modules loaded
#   GPU_TC_NVCC        nvcc path
#   GPU_TC_CUDA_VER    CUDA version (TACC_CUDA_VER, else nvcc --version)
#   GPU_TC_CUBLAS_LIB  directory with libcublas
#   GPU_TC_CUBLAS_INC  directory with cublas_v2.h
#   GPU_TC_LINK_MODE   static (cuBLAS, cuBLASLt, culibos, cudart archives) or shared
#   GPU_TC_LINK        nvcc link arguments (array)
#   GPU_TC_NOTE        why the link is shared, empty when static
# On failure (return 1) GPU_TC_NOT_RUN holds the reason with every path searched; nothing is
# guessed. HPCPERFSTATS_CUDA_MODULES (e.g. "gcc/15.3.0 cuda/13.3") replaces the module choice.

GPU_TC_MIN_CUDA=12.8

# version_ge A B: A >= B in version order.
gpu_tc_version_ge() {
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n 1)" = "$2" ]
}

# Lmod's module function, run with nounset off (Lmod's generated shell code reads unset vars).
gpu_tc_module() {
  local rc restore=0
  case $- in *u*) restore=1; set +u ;; esac
  module "$@"
  rc=$?
  [ "${restore}" = 1 ] && set -u
  return "${rc}"
}

gpu_tc_lmod_init() {
  local init searched=""
  if ! declare -F module >/dev/null 2>&1; then
    for init in ${MODULESHOME:+"${MODULESHOME}/init/bash"} /opt/apps/lmod/lmod/init/bash; do
      searched="${searched} ${init}"
      if [ -r "${init}" ]; then
        # shellcheck disable=SC1090
        . "${init}"
        break
      fi
    done
  fi
  if ! declare -F module >/dev/null 2>&1; then
    GPU_TC_NOT_RUN="no Lmod module function (searched:${searched})"
    return 1
  fi
  # TACC prepends its tree only when MODULEPATH was unset; a polluted path hides every module.
  if [ -d /opt/apps/modulefiles ]; then
    case ":${MODULEPATH:-}:" in
      *:/opt/apps/modulefiles:*) ;;
      *) export MODULEPATH="/opt/apps/modulefiles${MODULEPATH:+:${MODULEPATH}}" ;;
    esac
  fi
}

# gpu_tc_load MODULE...: module load in this shell; on failure GPU_TC_LOAD_ERR is Lmod's last lines.
gpu_tc_load() {
  local err rc
  err="$(mktemp "${TMPDIR:-/tmp}/gpu_toolchain.XXXXXX")" || return 1
  gpu_tc_module load "$@" >/dev/null 2>"${err}"
  rc=$?
  GPU_TC_LOAD_ERR="$(tail -n 3 "${err}" | tr '\n' ' ')"
  rm -f "${err}"
  return "${rc}"
}

# Load the newest cuda module >= GPU_TC_MIN_CUDA, with the prerequisite spider names when the
# bare load fails (Horizon: cuda sits under gcc/15.3.0 in the hierarchy).
gpu_tc_load_modules() {
  local avail ok="" v ver prereq
  if [ -n "${HPCPERFSTATS_CUDA_MODULES:-}" ]; then
    # shellcheck disable=SC2086  # a module list
    if ! gpu_tc_load ${HPCPERFSTATS_CUDA_MODULES}; then
      GPU_TC_NOT_RUN="module load ${HPCPERFSTATS_CUDA_MODULES} failed: ${GPU_TC_LOAD_ERR}"
      return 1
    fi
    GPU_TC_MODULES="${HPCPERFSTATS_CUDA_MODULES}"
    return 0
  fi
  avail="$(gpu_tc_module -t spider cuda 2>&1 | sed -n 's|^\(cuda/[0-9][0-9.]*\).*|\1|p' | sort -u)"
  for v in ${avail}; do
    gpu_tc_version_ge "${v#cuda/}" "${GPU_TC_MIN_CUDA}" && ok="${ok} ${v#cuda/}"
  done
  if [ -z "${ok}" ]; then
    GPU_TC_NOT_RUN="no cuda module >= ${GPU_TC_MIN_CUDA} in 'module -t spider cuda' (found: $(printf '%s' "${avail:-none}" | tr '\n' ' '))"
    return 1
  fi
  # shellcheck disable=SC2086  # a version list
  ver="$(printf '%s\n' ${ok} | sort -V | tail -n 1)"
  if gpu_tc_load "cuda/${ver}"; then
    GPU_TC_MODULES="cuda/${ver}"
    return 0
  fi
  prereq="$(gpu_tc_module -t spider "cuda/${ver}" 2>&1 \
    | awk '/You will need to load all module\(s\) on any one of the lines below/ { f = 1; next }
           f && NF { $1 = $1; print; exit }')"
  if [ -z "${prereq}" ]; then
    GPU_TC_NOT_RUN="module load cuda/${ver} failed (${GPU_TC_LOAD_ERR}) and 'module -t spider cuda/${ver}' names no prerequisite"
    return 1
  fi
  # shellcheck disable=SC2086  # prereq is a module list
  if ! gpu_tc_load ${prereq} "cuda/${ver}"; then
    GPU_TC_NOT_RUN="module load ${prereq} cuda/${ver} failed: ${GPU_TC_LOAD_ERR}"
    return 1
  fi
  GPU_TC_MODULES="${prereq} cuda/${ver}"
}

# first_with FILE DIR...: GPU_TC_FOUND = first DIR containing FILE; every DIR tried is appended to
# GPU_TC_SEARCHED for the NOT_RUN reason.
gpu_tc_first_with() {
  local f=$1 d
  shift
  GPU_TC_FOUND=""
  for d in "$@"; do
    GPU_TC_SEARCHED="${GPU_TC_SEARCHED} ${d}"
    if [ -e "${d}/${f}" ]; then
      GPU_TC_FOUND="${d}"
      return 0
    fi
  done
  return 1
}

gpu_toolchain_resolve() {
  local c cuda_dir target libdirs=() cudalibs=() missing="" d ldirs="" a culibos="" cudart=""
  GPU_TC_NOT_RUN="" GPU_TC_NOTE="" GPU_TC_MODULES="" GPU_TC_LINK=()
  gpu_tc_lmod_init || return 1
  gpu_tc_load_modules || return 1

  GPU_TC_NVCC="" GPU_TC_SEARCHED=""
  for c in ${TACC_CUDA_DIR:+"${TACC_CUDA_DIR}/bin/nvcc"} ${CUDA_HOME:+"${CUDA_HOME}/bin/nvcc"} \
    ${NVHPC_CUDA_HOME:+"${NVHPC_CUDA_HOME}/bin/nvcc"}; do
    GPU_TC_SEARCHED="${GPU_TC_SEARCHED} ${c}"
    if [ -x "${c}" ]; then GPU_TC_NVCC="${c}"; break; fi
  done
  if [ -z "${GPU_TC_NVCC}" ]; then
    GPU_TC_SEARCHED="${GPU_TC_SEARCHED} PATH"
    GPU_TC_NVCC="$(command -v nvcc 2>/dev/null)"
  fi
  if [ -z "${GPU_TC_NVCC}" ]; then
    GPU_TC_NOT_RUN="no nvcc after loading ${GPU_TC_MODULES} (searched:${GPU_TC_SEARCHED})"
    return 1
  fi
  cuda_dir="$(dirname "$(dirname "${GPU_TC_NVCC}")")"
  GPU_TC_CUDA_VER="${TACC_CUDA_VER:-$("${GPU_TC_NVCC}" --version 2>/dev/null \
    | sed -n 's/.*release \([0-9][0-9.]*\),.*/\1/p' | head -n 1)}"
  if [ -z "${GPU_TC_CUDA_VER}" ]; then
    GPU_TC_NOT_RUN="no CUDA version: TACC_CUDA_VER unset and '${GPU_TC_NVCC} --version' has no release"
    return 1
  fi
  if ! gpu_tc_version_ge "${GPU_TC_CUDA_VER}" "${GPU_TC_MIN_CUDA}"; then
    GPU_TC_NOT_RUN="CUDA ${GPU_TC_CUDA_VER} < ${GPU_TC_MIN_CUDA} (needed for sm_100 / sm_120)"
    return 1
  fi
  case "$(uname -m)" in
    aarch64) target=sbsa-linux ;;
    *) target="$(uname -m)-linux" ;;
  esac

  # cuBLAS: NVHPC math_libs/<ver> beside the CUDA tree (root = two levels above <root>/cuda/<ver>),
  # then the CUDA tree itself.
  for c in ${TACC_CUDA_DIR:+"${TACC_CUDA_DIR}"} ${NVHPC_CUDA_HOME:+"${NVHPC_CUDA_HOME}"} "${cuda_dir}"; do
    d="$(dirname "$(dirname "${c}")")/math_libs/${GPU_TC_CUDA_VER}/lib64"
    case " ${libdirs[*]} " in *" ${d} "*) continue ;; esac
    libdirs+=("${d}")
  done
  for d in ${TACC_CUDA_LIB:+"${TACC_CUDA_LIB}"} "${cuda_dir}/lib64" "${cuda_dir}/targets/${target}/lib"; do
    case " ${cudalibs[*]} " in *" ${d} "*) continue ;; esac
    cudalibs+=("${d}")
  done
  if ! gpu_tc_first_with libcublas.so "${libdirs[@]}" "${cudalibs[@]}" \
    && ! gpu_tc_first_with libcublas_static.a "${libdirs[@]}" "${cudalibs[@]}"; then
    GPU_TC_NOT_RUN="no cuBLAS (libcublas.so / libcublas_static.a) for CUDA ${GPU_TC_CUDA_VER} (searched: ${libdirs[*]} ${cudalibs[*]})"
    return 1
  fi
  GPU_TC_CUBLAS_LIB="${GPU_TC_FOUND}"
  d="$(dirname "${GPU_TC_CUBLAS_LIB}")"
  GPU_TC_SEARCHED=""
  if ! gpu_tc_first_with cublas_v2.h "${d}/include" "${d}/targets/${target}/include" \
    "${cuda_dir}/include" "${cuda_dir}/targets/${target}/include"; then
    GPU_TC_NOT_RUN="no cublas_v2.h for ${GPU_TC_CUBLAS_LIB} (searched:${GPU_TC_SEARCHED})"
    return 1
  fi
  GPU_TC_CUBLAS_INC="${GPU_TC_FOUND}"

  # Static link when every archive exists, so root runs the binary without modules.
  for a in libcublas_static.a libcublasLt_static.a; do
    [ -e "${GPU_TC_CUBLAS_LIB}/${a}" ] || missing="${missing} ${GPU_TC_CUBLAS_LIB}/${a}"
  done
  if gpu_tc_first_with libculibos.a "${GPU_TC_CUBLAS_LIB}" "${cudalibs[@]}"; then
    culibos="${GPU_TC_FOUND}"
  else
    missing="${missing} libculibos.a (in ${GPU_TC_CUBLAS_LIB} ${cudalibs[*]})"
  fi
  if gpu_tc_first_with libcudart_static.a "${cudalibs[@]}"; then
    cudart="${GPU_TC_FOUND}"
  else
    missing="${missing} libcudart_static.a (in ${cudalibs[*]})"
  fi
  if [ -z "${missing}" ]; then
    GPU_TC_LINK_MODE=static
    for d in "${GPU_TC_CUBLAS_LIB}" "${culibos}" "${cudart}"; do
      case " ${ldirs} " in *" ${d} "*) continue ;; esac
      ldirs="${ldirs} ${d}"
      GPU_TC_LINK+=("-L${d}")
    done
    GPU_TC_LINK+=(-lcublas_static -lcublasLt_static -lculibos -cudart static -ldl -lpthread -lrt)
    return 0
  fi
  if [ ! -e "${GPU_TC_CUBLAS_LIB}/libcublas.so" ]; then
    GPU_TC_NOT_RUN="cuBLAS static archives missing (${missing# }) and no libcublas.so in ${GPU_TC_CUBLAS_LIB}"
    return 1
  fi
  GPU_TC_LINK_MODE=shared
  GPU_TC_LINK=("-L${GPU_TC_CUBLAS_LIB}" -lcublas -lcublasLt -cudart static
    -Xlinker -rpath -Xlinker "${GPU_TC_CUBLAS_LIB}")
  GPU_TC_NOTE="static cuBLAS archives missing (${missing# }): linked shared libcublas with rpath ${GPU_TC_CUBLAS_LIB}; root must be able to read that directory"
}
