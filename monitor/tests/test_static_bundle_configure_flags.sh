#!/bin/sh
# Regression: build_static_bundle.sh --print-configure-flags and prepare_rpmbuild reuse.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

test -x ./scripts/build_static_bundle.sh

flags="$(./scripts/build_static_bundle.sh --print-configure-flags 2>/dev/null)"

if ! test -f /usr/include/gpu_performance_api/gpu_perf_api.h \
   && ! test -f /usr/local/include/gpu_performance_api/gpu_perf_api.h; then
  echo "${flags}" | grep -q -- '--disable-amd-gpu' \
    || { echo "expected --disable-amd-gpu when GPUPerfAPI header is absent" >&2; exit 1; }
fi

# x86 LIKWID fleet builds compile nvidia_gpu with runtime DCGM dlopen; missing
# link-time libdcgm must not force --disable-gpu.
case "$(uname -m)" in
  x86_64|amd64|i?86)
    if echo "${flags}" | grep -q -- '--disable-gpu'; then
      echo "x86 build must not pass --disable-gpu for missing link-time libdcgm (runtime dlopen)" >&2
      exit 1
    fi
    ;;
esac

grep -q 'print-configure-flags' ./scripts/prepare_rpmbuild_dirs.sh \
  || { echo "prepare_rpmbuild_dirs.sh must invoke --print-configure-flags" >&2; exit 1; }

echo "test_static_bundle_configure_flags passed"
