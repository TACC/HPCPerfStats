"""
CPU floating-point counter keys and FLOP weights read by the analysis.

Intel ``FP_ARITH_INST_RETIRED`` reaches ``host_cpu_hw`` as lowercase keys
(``fp_arith_inst_retired_<width>_<precision>``, ``E,W=48``, no schema unit),
so their ``arc`` is retired FP instructions per second: ingest undoes the
48-bit wrap per CPU before it sums CPUs, and FLOPs need the lanes per
instruction of each width. Historical rows carry the uppercase names with the
lanes in the schema ``U=`` token, which ingest already multiplied in, so those
arcs are FLOP rates. The lowercase scalar names also exist on Grace
``host_cpu_hw`` rows, where they never carry data.

AMD ``host_cpu_hw`` keys (``E,W=64,U=FLOP``): ``fp_ops_retired`` is total
FLOPs on Zen3, Zen4 and Zen5; ``amd_fp_dp_ops`` is double-precision FLOPs on
Zen5 only, and single precision is the difference (bf16 lands there). A CPU
class that does not program a key stores a static sentinel for it.

``AMD_FP_DP_OPS`` and the lowercase Intel keys (``INTEL_FP_ARITH_KEYS``)
come from ``monitor_naming.canonical``.

Attributes:
  AMD_FP_DP_OPS: Zen5 double-precision FLOP lifetime key (re-exported from
    ``canonical``).
  INTEL_FP_ARITH_DOUBLE_LANES: ``(lowercase key, FP64 lanes)`` per width.
  INTEL_FP_ARITH_SINGLE_LANES: ``(lowercase key, FP32 lanes)`` per width.
  INTEL_FP_ARITH_ALL_LANES: Double then single ``(key, lanes)`` pairs.
  _N_DOUBLE: Number of double-precision widths at the head of
    ``INTEL_FP_ARITH_KEYS``.
"""

from __future__ import annotations

from hpcperfstats.dbload.lib.monitor_naming.canonical import (
  AMD_FP_DP_OPS as AMD_FP_DP_OPS,
  INTEL_FP_ARITH_DOUBLE_EVENTS,
  INTEL_FP_ARITH_KEYS,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
  amd_pmc_type_names,
  host_cpu_hw_type_names,
)

# INTEL_FP_ARITH_KEYS holds the double widths, then the single widths, each
# scalar, 128-bit, 256-bit, 512-bit packed.
_N_DOUBLE = len(INTEL_FP_ARITH_DOUBLE_EVENTS)
INTEL_FP_ARITH_DOUBLE_LANES = tuple(
  zip(INTEL_FP_ARITH_KEYS[:_N_DOUBLE], (1, 2, 4, 8), strict=True)
)
INTEL_FP_ARITH_SINGLE_LANES = tuple(
  zip(INTEL_FP_ARITH_KEYS[_N_DOUBLE:], (1, 4, 8, 16), strict=True)
)
INTEL_FP_ARITH_ALL_LANES = (
  INTEL_FP_ARITH_DOUBLE_LANES + INTEL_FP_ARITH_SINGLE_LANES
)


def amd_flop_type_names() -> tuple[str, ...]:
  """
  Type names that may carry AMD ``fp_ops_retired``, oldest collector first.

  The historical ``amd_x86_pmc`` family comes before ``host_cpu_hw``, where
  the direct-perf monitor publishes the key.

  Returns:
    tuple[str, ...]: AMD PMC type names, then ``host_cpu_hw`` names.

  Examples:
    >>> "host_cpu_hw" in amd_flop_type_names()
    True
  """
  return (*amd_pmc_type_names(), *host_cpu_hw_type_names())
