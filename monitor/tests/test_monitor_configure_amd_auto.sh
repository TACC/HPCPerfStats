#!/bin/sh
# Regression: auto-detect AMD GPU without GPUPerfAPI headers degrades; explicit enable fails.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/hps_cfg_amd.XXXXXX")"
trap 'rm -rf "${TMP}"' EXIT

if ! test -x "${ROOT}/configure"; then
  echo "skip: ${ROOT}/configure not found (run autoreconf -fi first)" >&2
  exit 77
fi

case "${CC:-gcc}" in
*qemu*|*toolwrap*|*cross*)
  echo "skip: native configure smoke unsuitable under cross CC=${CC}" >&2
  exit 77
  ;;
esac

PREFIX=""
for candidate in \
  "${MONITOR_STATIC_PREFIX:-}" \
  "${ROOT}/../../.build/prefix-static" \
  "${ROOT}/.build/prefix-static" \
  "${ROOT}/embedded-static-prefix"
do
  if test -n "${candidate}" && test -f "${candidate}/include/rabbitmq-c/amqp.h"; then
    PREFIX="${candidate}"
    break
  fi
done
if test -z "${PREFIX}"; then
  echo "skip: rabbitmq-c headers not found under a static PREFIX (build deps first)" >&2
  exit 77
fi

export CPPFLAGS="-I${PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${PREFIX}/lib -L${PREFIX}/lib64 ${LDFLAGS:-}"

mkdir -p "${TMP}/bin"
cat > "${TMP}/bin/lspci" <<'EOF'
#!/bin/sh
echo '01:00.0 3D controller: Advanced Micro Devices, Inc. [AMD/ATI] Device [1002:abcd]'
EOF
chmod +x "${TMP}/bin/lspci"
export PATH="${TMP}/bin:${PATH}"

BUILDDIR="${TMP}/build-auto"
mkdir -p "${BUILDDIR}"
if ! (cd "${BUILDDIR}" && "${ROOT}/configure" --with-systemduserunitdir=no >"${TMP}/auto.log" 2>&1); then
  cat "${TMP}/auto.log" >&2
  echo "configure auto-detect should succeed without GPUPerfAPI headers" >&2
  exit 1
fi
grep -q 'disabling amd_gpu' "${TMP}/auto.log" \
  || { echo "expected soft-disable notice in auto.log" >&2; cat "${TMP}/auto.log" >&2; exit 1; }

BUILDDIR_EX="${TMP}/build-explicit"
mkdir -p "${BUILDDIR_EX}"
if (cd "${BUILDDIR_EX}" && "${ROOT}/configure" --with-systemduserunitdir=no --enable-amd-gpu=yes >"${TMP}/explicit.log" 2>&1); then
  cat "${TMP}/explicit.log" >&2
  echo "configure --enable-amd-gpu=yes should fail without GPUPerfAPI headers" >&2
  exit 1
fi
grep -q 'gpu_performance_api/gpu_perf_api.h' "${TMP}/explicit.log" \
  || { echo "expected GPUPerfAPI error in explicit.log" >&2; cat "${TMP}/explicit.log" >&2; exit 1; }

echo "test_monitor_configure_amd_auto passed"
