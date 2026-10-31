#!/usr/bin/env bash
# In-image Codon FT pyext bake for the update_metrics columnar kernels
# (pipeline_web.Dockerfile codon-build stage). Produces CODON_OUT with:
#   libhpcperfstats_codon_metrics_kernel.so — Codon --lib (@export C entry)
#   hpcperfstats_codon_metrics.so           — pyext_metrics_init.c (Py_MOD_GIL_NOT_USED)
#   libcodonrt.so / libomp.so / libgfortran / libquadmath / libgcc_s
# linked with -Wl,-rpath,$ORIGIN (no /opt/codon at runtime). Mirrors
# hpcperfstats/dbload/codon/build_codon_ext.sh; never run on the host as the
# production artifact path.
set -euo pipefail

CODON_HOME="${CODON_HOME:-/opt/codon}"
CODON_SRC="${CODON_SRC:-/tmp/codon-metrics-src}"
CODON_OUT="${CODON_OUT:-/opt/hpcperfstats/codon_metrics}"
CLANGXX="${CLANGXX:-$(command -v clang++-22 || command -v clang++ || true)}"
CLANGC="${CLANGC:-$(command -v clang-22 || command -v clang || true)}"
FT_PY="${FT_PY:-/opt/python3.14t/bin/python}"

if [[ ! -x "${CODON_HOME}/bin/codon" ]]; then
  echo "missing_codon=${CODON_HOME}/bin/codon" >&2
  exit 1
fi
if [[ -z "${CLANGXX}" || -z "${CLANGC}" ]]; then
  echo "missing_clang_for_codon_pyext" >&2
  exit 1
fi
if [[ ! -f "${CODON_SRC}/metrics_kernels.py" || ! -f "${CODON_SRC}/pyext_metrics_init.c" ]]; then
  echo "missing_codon_sources under ${CODON_SRC}" >&2
  exit 1
fi

mkdir -p "${CODON_OUT}" /opt/codon-shim
ln -sfn "${CLANGXX}" /opt/codon-shim/g++

export PATH="/opt/codon-shim:${CODON_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CODON_HOME}/lib:${CODON_HOME}/lib/codon:/opt/python3.14t/lib:${LD_LIBRARY_PATH:-}"
export CODON_PYTHON="${FT_PY}"

echo "codon=$(codon --version)"
echo "ft314t=$(${FT_PY} -V)"
echo "codon_metrics_build_flags=--mcpu=native --linker-flags -flto=thin"

KERNEL="${CODON_OUT}/libhpcperfstats_codon_metrics_kernel.so"
PYEXT="${CODON_OUT}/hpcperfstats_codon_metrics.so"
cd "${CODON_SRC}"
rm -f "${KERNEL}" "${PYEXT}" "${CODON_OUT}/pyext_metrics_init.o"

# Never pass --disable-native. Bake on the prod host (native CPU).
codon build -release --mcpu=native --linker-flags "-flto=thin" \
  --relocation-model=pic --lib \
  -o "${KERNEL}" \
  metrics_kernels.py

for sym in copy_count_rows_c copy_decode_c group_reduce_c grid_mean_c join_sorted_c; do
  if ! nm -D "${KERNEL}" | awk '{print $3}' | grep -qx "${sym}"; then
    echo "missing_export ${sym}" >&2
    nm -D "${KERNEL}" | head -40 >&2
    exit 1
  fi
  echo "metrics_export_sym=${sym}"
done

_disasm_tmp="$(mktemp "${TMPDIR:-/tmp}/codon_metrics_disasm.XXXXXX")"
if objdump -d "${KERNEL}" >"${_disasm_tmp}" 2>/dev/null; then
  awk '
    /zmm/ {z++}
    /ymm/ {y++}
    /xmm/ {x++}
    END {
      print "metrics_simd_insn_count zmm=" (z + 0) " ymm=" (y + 0) " xmm=" (x + 0)
    }
  ' "${_disasm_tmp}"
else
  echo "metrics_simd_insn_count zmm=0 ymm=0 xmm=0"
fi
rm -f "${_disasm_tmp}"

"${CLANGC}" -fPIC -O2 -march=native -mtune=native -flto=thin \
  -I/opt/python3.14t/include/python3.14t \
  -c "${CODON_SRC}/pyext_metrics_init.c" \
  -o "${CODON_OUT}/pyext_metrics_init.o"

for _rt in libcodonrt.so libomp.so libgcc_s.so.1 libgfortran.so.5 libquadmath.so.0; do
  if [[ -f "${CODON_HOME}/lib/codon/${_rt}" ]]; then
    cp -a "${CODON_HOME}/lib/codon/${_rt}" "${CODON_OUT}/${_rt}"
  elif [[ -f "${CODON_HOME}/lib/${_rt}" ]]; then
    cp -a "${CODON_HOME}/lib/${_rt}" "${CODON_OUT}/${_rt}"
  else
    echo "missing_codon_runtime ${_rt}" >&2
    exit 1
  fi
done

g++ -Wno-unused-command-line-argument -flto=thin \
  "${CODON_OUT}/pyext_metrics_init.o" -shared \
  -L"${CODON_OUT}" -lhpcperfstats_codon_metrics_kernel \
  -Wl,-rpath,'$ORIGIN' \
  -L"${CODON_OUT}" -lcodonrt -lomp \
  -L/opt/python3.14t/lib -Wl,-rpath,/opt/python3.14t/lib \
  -lpthread -ldl -lm -lc \
  -o "${PYEXT}"

if ! command -v patchelf >/dev/null 2>&1; then
  echo "patchelf_required" >&2
  exit 1
fi
patchelf --set-rpath '$ORIGIN' "${KERNEL}"
patchelf --set-rpath '$ORIGIN:/opt/python3.14t/lib' "${PYEXT}"
rm -f "${CODON_OUT}/pyext_metrics_init.o"

readelf -d "${PYEXT}" | grep -E "NEEDED|RPATH|RUNPATH"
readelf -d "${KERNEL}" | grep -E "NEEDED|RPATH|RUNPATH"

echo "=== FT 3.14t import + kernel smoke (hard gate) ==="
# Fail closed to image reality: no /opt/codon on LD_LIBRARY_PATH.
export PYTHONPATH="${CODON_OUT}"
export LD_LIBRARY_PATH="/opt/python3.14t/lib"
export OMP_NUM_THREADS=1
"${FT_PY}" - <<'PY'
import ctypes
import os
import struct
import sys

import numpy as np

# NumPy/MKL loads libiomp5; deep-bind Codon's libomp first (as the bridge
# does) or OpenMP teardown spins forever at interpreter exit.
ctypes.CDLL(
  os.path.join(os.environ["PYTHONPATH"], "libomp.so"),
  mode=os.RTLD_NOW | os.RTLD_DEEPBIND | os.RTLD_GLOBAL,
)
import hpcperfstats_codon_metrics as m  # noqa: E402

gil = sys._is_gil_enabled()
assert gil is False, gil
print("codon_metrics_pyext_314t_gil_off=pass")

# group_reduce: keys [2,1,2,3], mask drops the 3 → groups (1,2).
keys = np.array([2, 1, 2, 3], dtype=np.int64)
vals = np.array([1.0, 2.0, 3.0, 9.0])
mask = np.array([1, 1, 1, 0], dtype=np.uint8)
scratch = np.empty(4 * 4 + 65536, dtype=np.int64)
ok = np.empty(4, dtype=np.int64)
oc = np.empty(4, dtype=np.int64)
osum = np.empty(4, dtype=np.float64)
omax = np.empty(4, dtype=np.float64)
g = m.group_reduce(4, keys, vals, True, mask, scratch, ok, oc, osum, omax)
assert g == 2, g
assert ok[:2].tolist() == [1, 2] and oc[:2].tolist() == [1, 2], (ok, oc)
assert osum[:2].tolist() == [2.0, 4.0] and omax[:2].tolist() == [2.0, 3.0]

# COPY decode: one tuple with a NULL arc and unicode host.
sig = b"PGCOPY\n\xff\r\n\x00" + struct.pack(">ii", 0, 0)
row = struct.pack(">h", 8)
row += struct.pack(">iq", 8, 1_700_000_000_000_000 - 946_684_800_000_000)
for text in ("hé", "cpu", "user", ""):
  raw = text.encode()
  row += struct.pack(">i", len(raw)) + raw
row += struct.pack(">if", 4, 1.5) + struct.pack(">i", -1) + struct.pack(">if", 4, 2.0)
stream = sig + row + struct.pack(">h", -1)
n = m.copy_count_rows(stream, False)
assert n == 1, n
cols = {
  "time": np.empty(1, np.int64),
  "host": np.empty(1, np.int32),
  "type": np.empty(1, np.int32),
  "event": np.empty(1, np.int32),
  "dev": np.empty(1, np.int32),
  "owned": np.empty(1, np.uint8),
  "value": np.empty(1, np.float64),
  "arc": np.empty(1, np.float64),
  "delta": np.empty(1, np.float64),
}
ncol = np.empty(16, np.int64)
noff = np.empty(16, np.int64)
nlen = np.empty(16, np.int64)
nn = m.copy_decode(
  stream, False, 1, cols["time"], cols["host"], cols["type"], cols["event"],
  cols["dev"], cols["owned"], cols["value"], cols["arc"], cols["delta"],
  16, ncol, noff, nlen,
)
assert nn == 4, nn
assert cols["time"][0] == 1_700_000_000_000_000, cols["time"]
assert cols["value"][0] == 1.5 and np.isnan(cols["arc"][0]) and cols["delta"][0] == 2.0
assert cols["owned"][0] == 1
names = [stream[noff[i]:noff[i] + nlen[i]].decode() for i in range(nn)]
assert names == ["hé", "cpu", "user", ""], names
assert ncol[:4].tolist() == [0, 1, 2, 3]
try:
  m.copy_decode(
    stream, False, 1, cols["time"], cols["host"], cols["type"], cols["event"],
    cols["dev"], cols["owned"], cols["value"], cols["arc"], cols["delta"],
    2, ncol, noff, nlen,
  )
except OverflowError:
  print("codon_metrics_name_cap_overflow=pass")
else:
  raise AssertionError("name cap overflow not reported")
assert sys._is_gil_enabled() is False
print("codon_metrics_pyext_314t_import=pass")
PY
echo "codon_metrics_pyext_314t=pass"
ls -la "${CODON_OUT}"
echo "codon_metrics_out=${CODON_OUT}"
