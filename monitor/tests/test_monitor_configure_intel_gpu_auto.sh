#!/bin/sh
# Regression: --enable-intel-gpu=auto without PVC PCI → off; --enable-intel-gpu=yes compiles with vendored headers.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/hps_cfg_intel.XXXXXX")"
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
# No Intel Data Center GPU lines — auto should leave intel_gpu disabled.
echo '00:00.0 VGA compatible controller [0300]: Matrox Electronics Systems Ltd. Device [102b:0532]'
EOF
chmod +x "${TMP}/bin/lspci"
export PATH="${TMP}/bin:${PATH}"

BUILDDIR="${TMP}/build-auto"
mkdir -p "${BUILDDIR}"
if ! (cd "${BUILDDIR}" && "${ROOT}/configure" --with-systemduserunitdir=no \
    --disable-amd-gpu --disable-opa >"${TMP}/auto.log" 2>&1); then
  cat "${TMP}/auto.log" >&2
  echo "configure auto-detect should succeed without PVC PCI" >&2
  exit 1
fi
grep -q 'Intel GPU summary: disabled' "${TMP}/auto.log" \
  || { echo "expected Intel GPU disabled in auto.log" >&2; cat "${TMP}/auto.log" >&2; exit 1; }

BUILDDIR_EX="${TMP}/build-explicit"
mkdir -p "${BUILDDIR_EX}"
if ! (cd "${BUILDDIR_EX}" && "${ROOT}/configure" --with-systemduserunitdir=no \
    --enable-intel-gpu=yes --disable-amd-gpu --disable-opa >"${TMP}/explicit.log" 2>&1); then
  cat "${TMP}/explicit.log" >&2
  echo "configure --enable-intel-gpu=yes should succeed with vendored XPUM headers" >&2
  exit 1
fi
grep -q 'Intel GPU summary: enabled' "${TMP}/explicit.log" \
  || { echo "expected Intel GPU enabled in explicit.log" >&2; cat "${TMP}/explicit.log" >&2; exit 1; }
grep -q 'MONITOR_WITH_INTEL_GPU\|vendored-hdr' "${TMP}/explicit.log" \
  || grep -q 'runtime libxpum dlopen' "${TMP}/explicit.log" \
  || { echo "expected vendored/dlopen notice" >&2; cat "${TMP}/explicit.log" >&2; exit 1; }

echo "test_monitor_configure_intel_gpu_auto passed"
