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

# Stampede3 fleet marker forces MAD dlopen (never PCI-only --disable-infiniband).
fleet_flags="$(HPCS_BUNDLE_FLEET=stampede3 ./scripts/build_static_bundle.sh --print-configure-flags 2>/dev/null)"
echo "${fleet_flags}" | grep -q -- '--enable-ib-mad-dlopen' \
  || { echo "stampede3 fleet must pass --enable-ib-mad-dlopen" >&2; exit 1; }
echo "${fleet_flags}" | grep -q -- '--enable-opa-mad-dlopen' \
  || { echo "stampede3 fleet must pass --enable-opa-mad-dlopen" >&2; exit 1; }
echo "${fleet_flags}" | grep -q -- '--disable-amd-gpu' \
  || { echo "stampede3 fleet must pass --disable-amd-gpu" >&2; exit 1; }
if echo "${fleet_flags}" | grep -q -- '--disable-infiniband'; then
  echo "stampede3 fleet must not pass --disable-infiniband" >&2
  exit 1
fi

echo "test_static_bundle_configure_flags passed"
