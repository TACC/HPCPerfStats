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

grep -q 'monitor_gpu_lspci_probe_script' ./scripts/build_static_bundle.sh \
  || { echo "build_static_bundle.sh must guard gpu_lspci_probe when scripts are absent" >&2; exit 1; }

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

# Without fleet env and without stampede3.force / ls6.force, do not apply MAD dlopen matrix.
if test -f ./scripts/fleet/stampede3.force; then
  echo "scripts/fleet/stampede3.force must not be present for default-flag regression" >&2
  exit 1
fi
if test -f ./scripts/fleet/ls6.force; then
  echo "scripts/fleet/ls6.force must not be present for default-flag regression" >&2
  exit 1
fi
if echo "${flags}" | grep -q -- '--enable-ib-mad-dlopen'; then
  echo "default prepare must not pass --enable-ib-mad-dlopen without fleet" >&2
  exit 1
fi
if echo "${flags}" | grep -q -- '--enable-opa-mad-dlopen'; then
  echo "default prepare must not pass --enable-opa-mad-dlopen without fleet" >&2
  exit 1
fi

# Lonestar6 fleet: IB MAD dlopen, no OPA MAD, disable amd+intel GPU.
ls6_flags="$(HPCS_BUNDLE_FLEET=ls6 ./scripts/build_static_bundle.sh --print-configure-flags 2>/dev/null)"
echo "${ls6_flags}" | grep -q -- '--enable-ib-mad-dlopen' \
  || { echo "ls6 fleet must pass --enable-ib-mad-dlopen" >&2; exit 1; }
if echo "${ls6_flags}" | grep -q -- '--enable-opa-mad-dlopen'; then
  echo "ls6 fleet must not pass --enable-opa-mad-dlopen" >&2
  exit 1
fi
echo "${ls6_flags}" | grep -q -- '--disable-amd-gpu' \
  || { echo "ls6 fleet must pass --disable-amd-gpu" >&2; exit 1; }
echo "${ls6_flags}" | grep -q -- '--disable-intel-gpu' \
  || { echo "ls6 fleet must pass --disable-intel-gpu" >&2; exit 1; }
if echo "${ls6_flags}" | grep -q -- '--disable-infiniband'; then
  echo "ls6 fleet must not pass --disable-infiniband" >&2
  exit 1
fi

# aarch64: vendored XPUM alone must not enable intel-gpu; opt-in env restores it.
case "$(uname -m)" in
  aarch64 | arm64)
    echo "${flags}" | grep -q -- '--disable-intel-gpu' \
      || { echo "aarch64 default must pass --disable-intel-gpu without fleet/opt-in" >&2; exit 1; }
    opt_in_flags="$(HPCS_BUNDLE_ENABLE_INTEL_GPU=1 ./scripts/build_static_bundle.sh --print-configure-flags 2>/dev/null)"
    echo "${opt_in_flags}" | grep -q -- '--enable-intel-gpu' \
      || { echo "HPCS_BUNDLE_ENABLE_INTEL_GPU=1 must pass --enable-intel-gpu on aarch64" >&2; exit 1; }
    fleet_intel="$(HPCS_BUNDLE_FLEET=stampede3 ./scripts/build_static_bundle.sh --print-configure-flags 2>/dev/null)"
    echo "${fleet_intel}" | grep -q -- '--enable-intel-gpu' \
      || { echo "stampede3 fleet must pass --enable-intel-gpu when XPUM headers exist" >&2; exit 1; }
    ;;
esac

# LIKWID pinlib (liblikwidpin.so) is still built with SHARED_LIBRARY=false; its
# GCC link omits LFLAGS (-pthread). Bundle must pass -pthread or LS6 fails with
# undefined reference to pthread_setaffinity_np. Command-line LIBS= must also
# include -ldl (makefile LIBS += -ldl does not append to cmdline vars).
if ! awk '/^build_likwid\(\)/{p=1} p&&/^}$/{print; exit} p' ./scripts/build_static_bundle.sh \
  | grep -q -- '-pthread'; then
  echo "build_likwid must pass -pthread for liblikwidpin.so (Lonestar6 link)" >&2
  exit 1
fi
if ! awk '/^build_likwid\(\)/{p=1} p&&/^}$/{print; exit} p' ./scripts/build_static_bundle.sh \
  | grep -q 'SHARED_LFLAGS=.*-pthread'; then
  echo "build_likwid must set SHARED_LFLAGS with -pthread for pinlib" >&2
  exit 1
fi
if ! awk '/^build_likwid\(\)/{p=1} p&&/^}$/{print; exit} p' ./scripts/build_static_bundle.sh \
  | grep -q 'LIBS=.*-ldl'; then
  echo "build_likwid LIBS= must include -ldl (cmdline override drops makefile += -ldl)" >&2
  exit 1
fi
if ! awk '/^build_likwid\(\)/{p=1} p&&/^}$/{print; exit} p' ./scripts/build_static_bundle.sh \
  | grep -q 'ACCESSMODE=perf_event'; then
  echo "build_likwid must use ACCESSMODE=perf_event (runtime PERF needs LIKWID_USE_PERFEVENT)" >&2
  exit 1
fi
if awk '/^build_likwid\(\)/{p=1} p&&/^}$/{print; exit} p' ./scripts/build_static_bundle.sh \
  | grep -E '[[:space:]]ACCESSMODE=direct([[:space:]]|$)' >/dev/null; then
  echo "build_likwid must not use ACCESSMODE=direct (ENODEV under runtime PERF)" >&2
  exit 1
fi

echo "test_static_bundle_configure_flags passed"
