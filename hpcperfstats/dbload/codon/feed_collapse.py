"""Codon-NumPy feed + group-sum collapse compile unit (parse hot path).

Native ``import numpy`` (Codon-NumPy). No ``from python import numpy`` /
pandas on the hot path. Mirrors IncrementalStatsParser feed + plain-host
collapse; GPU keep-``dev`` and ``j:`` / ``host_mem_job`` stamps match
``_shared_row_dev``.

Also mirrors the full feed -> counter-delta -> group collapse -> arc-rate
pipeline (``_apply_counter_deltas`` / ``_collapse_stats_with_deltas`` /
``_apply_arc_and_finalize`` in ``sync_timedb_parsing.py``) via
``feed_delta_collapse_arc``, columnar and class-free end to end.

Class-free for Codon 0.20.4 ``-pyext`` (no custom ``class`` / ``__to_py__``).
Schema columns are a 4-tuple of parallel lists; feed state is free functions
over mutable lists/dicts; ``HOST_PROC_KEYS`` stays module-level only.
"""

import math

import numpy as np

N_SAMPLES = 400
REPS = 80
# 10000-01-01T00:00:00Z: the pyext COPY writer (gmtime_r, %04d year) and
# datetime stop at year 9999 (sync_timedb_parsing.MAX_SAMPLE_TIME_S).
MAX_SAMPLE_TIME_S = 253402300800.0

# Literal mirrors of ``monitor_naming.canonical`` (this compile unit cannot
# import Python modules). ``test_keep_dev_type_set_parity.py`` locks both:
# GPU_TYPES == GPU_STATS_TYPES (shared-node stamping skips them) and
# KEEP_DEV_TYPES == KEEP_DEV_COLLAPSE_TYPES (collapse keeps their ``dev``).
GPU_TYPES = ["nvidia_gpu", "amd_gpu", "intel_gpu"]
KEEP_DEV_TYPES = ["nvidia_gpu", "amd_gpu", "intel_gpu", "host_power_socket"]
# Shared stamps carry the hardware dev (``j:<jid>\x1f<dev>``) through the
# counter-delta step; collapse cuts it back to ``j:<jid>`` (parity with
# ``sync_timedb_parsing._JOB_STAMP_DEV_SEP``).
JOB_STAMP_DEV_SEP = "\x1f"

# Value sentinels: literal mirrors of ``hpcperfstats.lib.monitor_sentinel``
# (bridgeable codes) and of the reserved ``DeltaCarryState.raw`` keys in
# ``sync_timedb_parsing``; locked by ``test_sentinel_codon_literals``.
SENTINEL_BRIDGEABLE_CODES = [-1.0, -3.0, -7.0, -8.0, -9.0]
SENTINEL_BRIDGE_MAX_DEFAULT = 3
CARRY_BUILD_KEY = "\0build\0"
CARRY_RESTART_KEY = "\0restart\0"
CARRY_HEADER_PENDING_KEY = "\0header\0"
CARRY_BRIDGE_MAX_KEY = "\0cfg\0bridge_max"
CARRY_RESTART_KEEP = 8

HOST_PROC_KEYS = [
  "uid",
  "vm_peak",
  "vm_size",
  "vm_lck",
  "vm_hwm",
  "vm_rss",
  "vm_data",
  "vm_stk",
  "vm_exe",
  "vm_lib",
  "vm_pte",
  "vm_swap",
  "threads",
]

# Legacy CTL/CTR archives (mirror sync_timedb_parsing_legacy + monitor_naming.legacy;
# locked by test_codon_legacy_decode_parity.py). Int keys are hex event codes
# read from CTL values; str keys are FIXED_CTR schema tokens.
LEGACY_EVENTMAP_INT: dict[str, dict[int, str]] = {
  "amd64_pmc": {
    0x43FF03: "FLOPS,W=48",
    0x4300C2: "BRANCH_INST_RETIRED,W=48",
    0x4300C3: "BRANCH_INST_RETIRED_MISS,W=48",
    0x4308AF: "DISPATCH_STALL_CYCLES1,W=48",
    0x43FFAE: "DISPATCH_STALL_CYCLES0,W=48",
  },
  "amd64_df": {
    0x403807: "MBW_CHANNEL_0,W=48,U=64B",
    0x403847: "MBW_CHANNEL_1,W=48,U=64B",
    0x403887: "MBW_CHANNEL_2,W=48,U=64B",
    0x4038C7: "MBW_CHANNEL_3,W=48,U=64B",
    0x433907: "MBW_CHANNEL_4,W=48,U=64B",
    0x433947: "MBW_CHANNEL_5,W=48,U=64B",
    0x433987: "MBW_CHANNEL_6,W=48,U=64B",
    0x4339C7: "MBW_CHANNEL_7,W=48,U=64B",
  },
  "intel_8pmc3": {
    0x4301C7: "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE,W=48,U=1",
    0x4302C7: "FP_ARITH_INST_RETIRED_SCALAR_SINGLE,W=48,U=1",
    0x4304C7: "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE,W=48,U=2",
    0x4308C7: "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE,W=48,U=4",
    0x4310C7: "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE,W=48,U=4",
    0x4320C7: "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE,W=48,U=8",
    0x4340C7: "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE,W=48,U=8",
    0x4380C7: "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE,W=48,U=16",
    0x438010: "SSE_DOUBLE_SCALAR,W=48,U=1",
    0x431010: "SSE_DOUBLE_PACKED,W=48,U=2",
    0x430211: "SIMD_DOUBLE_256,W=48,U=4",
    0x439010: "SSE_DOUBLE_ALL,W=48,U=1",
  },
  "intel_4pmc3": {
    0x4301C7: "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE,W=48,U=1",
    0x4302C7: "FP_ARITH_INST_RETIRED_SCALAR_SINGLE,W=48,U=1",
    0x4304C7: "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE,W=48,U=2",
    0x4308C7: "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE,W=48,U=4",
    0x4310C7: "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE,W=48,U=4",
    0x4320C7: "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE,W=48,U=8",
    0x4340C7: "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE,W=48,U=8",
    0x4380C7: "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE,W=48,U=16",
    0x438010: "SSE_DOUBLE_SCALAR,W=48,U=1",
    0x431010: "SSE_DOUBLE_PACKED,W=48,U=2",
    0x430211: "SIMD_DOUBLE_256,W=48,U=4",
    0x439010: "SSE_DOUBLE_ALL,W=48,U=1",
  },
  "intel_snb_imc": {
    0x400304: "CAS_READS,W=48",
    0x400B04: "CAS_WRITES,W=48",
  },
  "intel_ivb_imc": {
    0x400304: "CAS_READS,W=48",
    0x400B04: "CAS_WRITES,W=48",
  },
  "intel_hsw_imc": {
    0x400304: "CAS_READS,W=48",
    0x400B04: "CAS_WRITES,W=48",
  },
  "intel_bdw_imc": {
    0x400304: "CAS_READS,W=48",
    0x400B04: "CAS_WRITES,W=48",
  },
  "intel_knl_mc_dclk": {
    0x300301: "CAS_READS,W=48",
    0x300309: "CAS_WRITES,W=48",
  },
  "intel_knl_mc": {
    0x300301: "CAS_READS,W=48",
    0x300309: "CAS_WRITES,W=48",
  },
  "intel_skx_imc": {
    0x400304: "CAS_READS,W=48",
    0x400C04: "CAS_WRITES,W=48",
    0x400B01: "ACT_COUNT,W=48",
    0x400102: "PRE_COUNT_MISS,W=48",
  },
}
LEGACY_EVENTMAP_STR: dict[str, dict[str, str]] = {
  "intel_8pmc3": {
    "FIXED_CTR0": "INST_RETIRED,W=48",
    "FIXED_CTR1": "APERF,W=48",
    "FIXED_CTR2": "MPERF,W=48",
  },
  "intel_4pmc3": {
    "FIXED_CTR0": "INST_RETIRED,W=48",
    "FIXED_CTR1": "APERF,W=48",
    "FIXED_CTR2": "MPERF,W=48",
  },
}
LEGACY_TYPE_NORMALIZE: dict[str, str] = {
  "intel_knl_mc": "intel_knl_mc_dclk",
}
LEGACY_REMOVED_SYMBOLS = [
  "CTL0",
  "CTL1",
  "CTL2",
  "CTL3",
  "CTL4",
  "CTL5",
  "CTL6",
  "CTL7",
  "CTR0",
  "CTR1",
  "CTR2",
  "CTR3",
  "CTR4",
  "CTR5",
  "CTR6",
  "CTR7",
  "FIXED_CTR",
  "FIXED_CTR0",
  "FIXED_CTR1",
  "FIXED_CTR2",
  "amd_x86_pmc",
  "amd_x86_uncore_df",
  "host_ib_ext",
  "host_ib_sw",
  "host_mic",
  "intel_knl",
  "intel_knl_edc",
  "intel_knl_mc",
  "intel_x86_pmc_knl",
  "intel_x86_uncore_edc_knl",
  "intel_x86_uncore_mc_knl",
]
LEGACY_HARDWARE_DECODE_TYPES = [
  "amd64_df",
  "amd64_pmc",
  "intel_4pmc3",
  "intel_8pmc3",
  "intel_bdw_imc",
  "intel_hsw_imc",
  "intel_ivb_imc",
  "intel_knl_mc",
  "intel_knl_mc_dclk",
  "intel_skx_imc",
  "intel_snb_imc",
]

# SchemaCols: (events, wids, mults, units)
SchemaCols = tuple[list[str], list[int], list[float], list[str]]


def schema_key_basename(token: str) -> str:
  if not token:
    return ""
  return token.split(",", 1)[0]


def _parse_jobs_jids(line: str) -> list[str]:
  """Return jid tokens from ``%jobs jids=``; empty when not a jobs mark."""
  if len(line) < 5 or line[0:5] != "%jobs":
    return []
  parts = line.split()
  if len(parts) < 2:
    return []
  token = parts[1]
  if len(token) < 5 or token[0:5] != "jids=":
    return []
  raw = token[5:]
  out: list[str] = []
  for piece in raw.split(","):
    jid = piece.strip()
    if jid and jid != "-" and jid != "0" and len(jid) <= 32:
      out.append(jid)
  return out


def _parse_affinity_mark(line: str) -> dict[str, str]:
  """Parse ``%affinity`` into string fields; empty ``jid`` when unusable."""
  empty: dict[str, str] = {
    "kind": "",
    "jid": "",
    "cpus": "",
    "gpus": "",
    "cg": "",
  }
  if len(line) < 9 or line[0:9] != "%affinity":
    return empty
  fields: dict[str, str] = {}
  parts = line.split()
  for ti in range(1, len(parts)):
    tok = parts[ti]
    eq = -1
    for ci in range(len(tok)):
      if tok[ci] == "=":
        eq = ci
        break
    if eq <= 0:
      continue
    fields[tok[0:eq]] = tok[eq + 1 :]
  jid = ""
  if "jid" in fields:
    jid = fields["jid"].strip()
  if not jid or jid == "-" or jid == "0" or len(jid) > 32:
    return empty
  cpus = ""
  if "cpus" in fields:
    cpus = fields["cpus"]
  gpus = "-"
  if "gpus" in fields:
    gpus = fields["gpus"]
  cg = "sched"
  if "cg" in fields:
    cg = fields["cg"]
  return {
    "kind": "affinity",
    "jid": jid,
    "cpus": cpus,
    "gpus": gpus,
    "cg": cg,
  }


def _schema_token_is_slow_tier(token: str) -> bool:
  parts = token.split(",")
  if len(parts) < 2:
    return False
  return any("R=S" in ele for ele in parts[1:])


def _fast_schema_keys(full_events: list[str]) -> list[str]:
  out: list[str] = []
  for t in full_events:
    if not _schema_token_is_slow_tier(t):
      out.append(t)
  return out


def _compile_schema_token(token: str) -> tuple[str, int, float, str]:
  eve_parts = token.split(",")
  width = 64
  mult = 1.0
  unit = "#"
  for ele in eve_parts[1:]:
    if ele.startswith("W=") and ele[2:].isdigit():
      width = int(ele[2:])
    if ele.startswith("U="):
      rest = ele[2:]
      digits = "".join(c for c in rest if c.isdigit())
      if digits:
        mult = float(digits)
      # Match CPython: a numeric-only ``U=`` clears the default ``#``.
      unit = "".join(c for c in rest if c.isalpha())
  return eve_parts[0], width, mult, unit


def _compile_schema_tokens(tokens: list[str]) -> SchemaCols:
  events: list[str] = []
  wids: list[int] = []
  mults: list[float] = []
  units: list[str] = []
  for token in tokens:
    event, wid, mult, unit = _compile_schema_token(token)
    events.append(event)
    wids.append(wid)
    mults.append(mult)
    units.append(unit)
  return (events, wids, mults, units)


def schema_needs_legacy_decode(typ: str, tokens: list[str]) -> bool:
  """Mirror ``monitor_naming.resolve.schema_needs_legacy_hardware_decode``."""
  for token in tokens:
    base = token.split(",")[0]
    if base in LEGACY_REMOVED_SYMBOLS:
      return True
    if "CTL" in base or "CTR" in base:
      return True
  return typ in LEGACY_HARDWARE_DECODE_TYPES


def legacy_decode_tokens(
  typ: str, tokens: list[str], vals: list[str]
) -> tuple[list[str], list[str]]:
  """Port of ``map_hardware_counter_vals`` plus its event->value dict.

  CTL values pick event names from the type's hex eventmap (``OTHER`` when
  unknown), CTRs take the name their CTL picked, FIXED_CTRs map by token,
  other tokens keep only their base name. A repeated name keeps its first
  position with the last value (dict semantics). A FIXED_CTR or CTR with no
  mapping raises ``KeyError`` like CPython.
  """
  codes = LEGACY_EVENTMAP_INT[typ]
  by_ctl: dict[str, str] = {}
  names: list[str] = []
  rm_idx: list[int] = []
  for idx in range(len(tokens)):
    eve = tokens[idx].split(",")[0]
    if "CTL" in eve:
      name = "OTHER"
      if idx < len(vals):
        try:
          name = codes.get(int(vals[idx]), "OTHER")
        except ValueError:
          name = "OTHER"
      by_ctl[eve.lstrip("CTL")] = name
      rm_idx.append(idx)
    elif "FIXED_CTR" in eve:
      if typ not in LEGACY_EVENTMAP_STR or eve not in LEGACY_EVENTMAP_STR[typ]:
        raise KeyError(eve)
      names.append(LEGACY_EVENTMAP_STR[typ][eve])
    elif "CTR" in eve:
      ctr = eve.lstrip("CTR")
      if ctr not in by_ctl:
        raise KeyError(ctr)
      names.append(by_ctl[ctr])
    else:
      names.append(eve)
  kept = list(vals)
  for j in range(len(rm_idx) - 1, -1, -1):
    if rm_idx[j] >= len(kept):
      raise IndexError("legacy CTL index past the value list")
    del kept[rm_idx[j]]
  out_names: list[str] = []
  out_vals: list[str] = []
  for i in range(min(len(names), len(kept))):
    pos = -1
    for k in range(len(out_names)):
      if out_names[k] == names[i]:
        pos = k
        break
    if pos >= 0:
      out_vals[pos] = kept[i]
    else:
      out_names.append(names[i])
      out_vals.append(kept[i])
  return out_names, out_vals


def _jobs_line_arms(line: str) -> bool:
  return (
    len(line) >= 5 and line[0:5] == "%jobs" and len(_parse_jobs_jids(line)) > 0
  )


def trailing_jobs_arm(lines: list[str]) -> bool:
  """True when a ``%jobs`` line follows the last sample header in ``lines``.

  The file session flushes on the next sample's header, so that ``%jobs``
  stays in the closing window; the next window starts armed with it.
  """
  for i in range(len(lines) - 1, -1, -1):
    s = lines[i].lstrip()
    if not s:
      continue
    if s[0].isdigit():
      return False
    if _jobs_line_arms(s):
      return True
  return False


def _cpu_e6_schema_tokens() -> list[str]:
  # Keep in sync with experiments/codon/e6_corpus.py @full cpu schema.
  return [
    "user,W=64,U=1",
    "nice,W=64,U=1",
    "system,W=64,U=1",
    "idle,W=64,U=1",
    "iowait,W=64,U=1",
    "irq,W=64,U=1",
    "softirq,W=64,U=1",
  ]


def build_e6_payload_lines(n_samples: int) -> list[str]:
  lines: list[str] = []
  lines.append("!cpu " + " ".join(_cpu_e6_schema_tokens()))
  lines.append(
    "!host_proc uid,W=32 vm_peak,W=64 vm_size,W=64 vm_lck,W=64 "
    "vm_hwm,W=64 vm_rss,W=64 vm_data,W=64 vm_stk,W=64 vm_exe,W=64 "
    "vm_lib,W=64 vm_pte,W=64 vm_swap,W=64 threads,W=32"
  )
  t0 = 1_700_000_000.0
  for i in range(n_samples):
    t = t0 + float(i)
    lines.append(str(t) + " JID1 hostA")
    vals = []
    for k in range(7):
      vals.append(str(float(i * 7 + k)))
    lines.append("cpu 0 @full " + " ".join(vals))
    vals2 = []
    for k in range(7):
      vals2.append(str(float(i * 7 + k + 0.5)))
    lines.append("cpu 1 @full " + " ".join(vals2))
    lines.append(
      "host_proc 1234 @full "
      + str(1000 + i)
      + " "
      + str(2000 + i)
      + " "
      + str(3000 + i)
      + " 0 "
      + str(4000 + i)
      + " "
      + str(5000 + i)
      + " "
      + str(6000 + i)
      + " "
      + str(7000 + i)
      + " "
      + str(8000 + i)
      + " "
      + str(9000 + i)
      + " "
      + str(10000 + i)
      + " 0 "
      + str(2 + (i % 4))
    )
  return lines


def _shared_row_dev(dev: str, typ: str, jid: str, shared: bool) -> str:
  """Stamp shared-node / host_mem_job rows; keep GPU hardware ``dev``."""
  for g in GPU_TYPES:
    if typ == g:
      return dev
  if (not jid) or jid == "-" or jid == "0":
    return dev
  if typ == "host_mem_job":
    if len(dev) >= 2 and dev[0] == "j" and dev[1] == ":":
      return dev
    return "j:" + jid
  if not shared:
    return dev
  return "j:" + jid + JOB_STAMP_DEV_SEP + dev


def _append_stats(
  tag_time: float,
  tag_host: str,
  tag_jid: str,
  typ: str,
  dev: str,
  compiled: SchemaCols,
  vals: list[str],
  stats_time: list[float],
  stats_host: list[str],
  stats_jid: list[str],
  stats_type: list[str],
  stats_dev: list[str],
  stats_event: list[str],
  stats_value: list[float],
  stats_wid: list[int],
  stats_mult: list[float],
  stats_unit: list[str],
) -> None:
  events, wids, mults, units = compiled
  n = len(events)
  # A value count that differs from the schema, or a non-numeric token, drops
  # the line (as IncrementalStatsParser does); an uncaught ValueError in an
  # @export entry aborts the whole worker process.
  if len(vals) != n:
    return
  try:
    fvals = [float(v) for v in vals]
  except ValueError:
    return
  for i in range(n):
    stats_time.append(tag_time)
    stats_host.append(tag_host)
    stats_jid.append(tag_jid)
    stats_type.append(typ)
    stats_dev.append(dev)
    stats_event.append(events[i])
    # Pre-mult raw counter (match IncrementalStatsParser); mult applies once on delta.
    stats_value.append(fvals[i])
    stats_wid.append(wids[i])
    stats_mult.append(mults[i])
    stats_unit.append(units[i])


def _merge_proc(
  jid: str,
  host: str,
  proc: str,
  device: str,
  fields: dict[str, int],
  nulls: list[str],
  proc_jid: list[str],
  proc_host: list[str],
  proc_proc: list[str],
  proc_device: list[str],
  proc_fields: list[dict[str, int]],
  proc_by_key: dict[str, int],
) -> None:
  """Online ``(jid, host, proc)`` merge: peaks max, other fields last-write.

  ``nulls`` are non-numeric fields (CPython ``None``): a null peak never
  wins, a null non-peak overwrites to absent (COPY writes absent as NULL).
  """
  key = jid + "\0" + host + "\0" + proc
  idx = proc_by_key.get(key, -1)
  if idx < 0:
    proc_by_key[key] = len(proc_jid)
    proc_jid.append(jid)
    proc_host.append(host)
    proc_proc.append(proc)
    proc_device.append(device)
    proc_fields.append(fields)
    return
  proc_device[idx] = device
  old = proc_fields[idx]
  for metric, nv in fields.items():
    if _is_proc_peak_key(metric):
      if metric not in old or nv > old[metric]:
        old[metric] = nv
    else:
      old[metric] = nv
  for metric in nulls:
    if not _is_proc_peak_key(metric) and metric in old:
      del old[metric]


def _is_proc_peak_key(metric: str) -> bool:
  return (
    metric == "vm_peak"
    or metric == "vm_hwm"
    or metric == "vm_stk"
    or metric == "vm_exe"
    or metric == "vm_lib"
  )


def feed_lines(
  lines: list[str],
  start_idx: int = 0,
) -> tuple[
  list[float],
  list[str],
  list[str],
  list[str],
  list[str],
  list[str],
  list[float],
  list[int],
  list[float],
  list[str],
  int,
]:
  """Parse stats lines into columnar lists; return columns + proc row count."""
  (
    stats_time,
    stats_host,
    stats_jid,
    stats_type,
    stats_dev,
    stats_event,
    stats_value,
    stats_wid,
    stats_mult,
    stats_unit,
    proc_jid,
    _proc_host,
    _proc_proc,
    _proc_fields,
    _proc_device,
    _marks,
  ) = feed_lines_full(lines, start_idx)
  return (
    stats_time,
    stats_host,
    stats_jid,
    stats_type,
    stats_dev,
    stats_event,
    stats_value,
    stats_wid,
    stats_mult,
    stats_unit,
    len(proc_jid),
  )


def feed_lines_full(
  lines: list[str],
  start_idx: int = 0,
  exclude_types: list[str] = [],  # noqa: B006 (Codon: never mutated)
  arm_shared_in: bool = False,
) -> tuple[
  list[float],
  list[str],
  list[str],
  list[str],
  list[str],
  list[str],
  list[float],
  list[int],
  list[float],
  list[str],
  list[str],
  list[str],
  list[str],
  list[dict[str, int]],
  list[str],
  list[dict[str, str]],
]:
  """Parse stats lines; return stats columns, proc cols, and bound marks.

  Marks are ``dict[str, str]`` records (``kind`` ``jobs``/``affinity``) with
  ``time``/``host`` bound on the next digit sample header. Unbound trailing
  marks (no digit after) are omitted from the returned list.

  ``exclude_types`` mirrors ``IncrementalStatsParser.exclude_types_list``: a
  data line whose first token is listed is skipped before any host/proc work.
  ``start_idx`` is the resume gate: sample headers at line indexes below it
  open no sample (schema and ``%jobs`` arm state still accumulate, marks bound
  to those samples are dropped). ``arm_shared_in`` seeds the ``%jobs`` arm a
  previous file-session window ended with (``trailing_jobs_arm``).
  """
  excl: set[str] = set()
  for et in exclude_types:
    excl.add(et)
  insert = False
  has_tags = False
  tag_time = 0.0
  tag_jid = ""
  tag_host = ""
  arm_shared = arm_shared_in
  sample_shared = False
  below_gate = False
  schema: dict[str, list[str]] = {}
  schema_fast: dict[str, list[str]] = {}
  schema_compiled: dict[str, SchemaCols] = {}
  schema_fast_compiled: dict[str, SchemaCols] = {}
  schema_bare: dict[str, list[str]] = {}
  schema_fast_bare: dict[str, list[str]] = {}
  schema_legacy: dict[str, bool] = {}
  stats_time: list[float] = []
  stats_host: list[str] = []
  stats_jid: list[str] = []
  stats_type: list[str] = []
  stats_dev: list[str] = []
  stats_event: list[str] = []
  stats_value: list[float] = []
  stats_wid: list[int] = []
  stats_mult: list[float] = []
  stats_unit: list[str] = []
  proc_jid: list[str] = []
  proc_host: list[str] = []
  proc_proc: list[str] = []
  proc_device: list[str] = []
  proc_fields: list[dict[str, int]] = []
  proc_by_key: dict[str, int] = {}
  marks: list[dict[str, str]] = []
  pending_marks: list[dict[str, str]] = []

  line_index = -1
  for raw in lines:
    line_index += 1
    if not raw:
      continue
    s = raw
    if s[0].isspace():
      s = s.lstrip()
      if not s:
        continue
    if s[0] == "%":
      rec: dict[str, str] = {}
      if _jobs_line_arms(s):
        arm_shared = True
        rec = {
          "kind": "jobs",
          "jids": ",".join(_parse_jobs_jids(s)),
          "jid": "",
          "cpus": "",
          "gpus": "",
          "cg": "",
          "time": "",
          "host": "",
        }
      elif len(s) >= 9 and s[0:9] == "%affinity":
        aff = _parse_affinity_mark(s)
        if aff["jid"] != "":
          rec = {
            "kind": "affinity",
            "jids": "",
            "jid": aff["jid"],
            "cpus": aff["cpus"],
            "gpus": aff["gpus"],
            "cg": aff["cg"],
            "time": "",
            "host": "",
          }
      # Marks of a sample below the resume gate were ingested with it.
      if len(rec) == 0 or below_gate:
        continue
      if has_tags:
        rec["time"] = str(tag_time)
        rec["host"] = tag_host
        if rec["kind"] == "jobs":
          sample_shared = tag_jid != "-" and tag_jid != "0" and tag_jid != ""
      else:
        pending_marks.append(rec)
      marks.append(rec)
      continue
    if insert and has_tags and s[0].isalpha():
      parts = s.split()
      if len(parts) < 3:
        continue
      typ = parts[0]
      if typ in excl:
        continue
      dev = parts[1]
      vals = parts[2:]
      tier = ""
      if vals[0] == "@fast" or vals[0] == "@full":
        tier = vals[0]
        vals = vals[1:]
      if typ == "host_proc" or typ == "proc":
        src = ""
        for alias in (typ, "host_proc", "proc"):
          if src == "" and alias in schema_bare:
            src = alias
        bare_keys = HOST_PROC_KEYS
        if src != "":
          bare_keys = (
            schema_fast_bare[src] if tier == "@fast" else schema_bare[src]
          )
        fields: dict[str, int] = {}
        nulls: list[str] = []
        for bi in range(min(len(bare_keys), len(vals))):
          bare = bare_keys[bi]
          if bare not in HOST_PROC_KEYS:
            continue
          try:
            fv = float(vals[bi])
          except ValueError:
            nulls.append(bare)
            continue
          if math.isnan(fv) or math.isinf(fv):
            nulls.append(bare)
            continue
          fields[bare] = int(fv)
        slash = dev.find("/")
        _merge_proc(
          tag_jid,
          tag_host,
          dev[:slash] if slash >= 0 else dev,
          dev,
          fields,
          nulls,
          proc_jid,
          proc_host,
          proc_proc,
          proc_device,
          proc_fields,
          proc_by_key,
        )
        continue
      if typ not in schema:
        continue
      out_typ = typ
      if schema_legacy[typ]:
        if tier == "@fast":
          continue
        if typ not in LEGACY_EVENTMAP_INT:
          continue
        names, vals = legacy_decode_tokens(typ, schema[typ], vals)
        compiled = _compile_schema_tokens(names)
        out_typ = LEGACY_TYPE_NORMALIZE.get(typ, typ)
      elif tier == "@fast":
        compiled = schema_fast_compiled[typ]
      else:
        compiled = schema_compiled[typ]
      _append_stats(
        tag_time,
        tag_host,
        tag_jid,
        out_typ,
        _shared_row_dev(dev, out_typ, tag_jid, sample_shared),
        compiled,
        vals,
        stats_time,
        stats_host,
        stats_jid,
        stats_type,
        stats_dev,
        stats_event,
        stats_value,
        stats_wid,
        stats_mult,
        stats_unit,
      )
      continue

    if s[0].isdigit():
      parts = s.split()
      if len(parts) < 3:
        continue
      if line_index < start_idx:
        # Below the resume gate: close the sample and drop marks waiting on
        # it; a later %jobs still arms the first emitted sample.
        insert = False
        below_gate = True
        arm_shared = False
        bound: list[dict[str, str]] = []
        for m in marks:
          if m["time"] != "":
            bound.append(m)
        marks = bound
        pending_marks = []
        continue
      below_gate = False
      try:
        tag_time = float(parts[0])
      except ValueError:
        tag_time = -1.0
      # A bad or out-of-range (nan, inf, 1e300) sample time drops that
      # sample's lines (they need ``insert``).
      insert = 0.0 <= tag_time < MAX_SAMPLE_TIME_S
      if not insert:
        continue
      tag_jid = parts[1]
      tag_host = parts[2]
      has_tags = True
      sample_shared = (
        arm_shared and tag_jid != "-" and tag_jid != "0" and tag_jid != ""
      )
      arm_shared = False
      for pm in pending_marks:
        if pm["time"] == "":
          pm["time"] = str(tag_time)
          pm["host"] = tag_host
      pending_marks = []
    elif s[0] == "!":
      pieces = s.split()
      if len(pieces) < 2:
        continue
      typ = pieces[0][1:]
      ev_list = pieces[1:]
      schema[typ] = ev_list
      schema_fast[typ] = _fast_schema_keys(ev_list)
      schema_compiled[typ] = _compile_schema_tokens(ev_list)
      schema_fast_compiled[typ] = _compile_schema_tokens(schema_fast[typ])
      schema_legacy[typ] = schema_needs_legacy_decode(typ, ev_list)
      bare: list[str] = []
      for tok in ev_list:
        bare.append(schema_key_basename(tok))
      schema_bare[typ] = bare
      bare_fast: list[str] = []
      for tok in schema_fast[typ]:
        bare_fast.append(schema_key_basename(tok))
      schema_fast_bare[typ] = bare_fast

  ready: list[dict[str, str]] = []
  for m in marks:
    if m["time"] != "" and m["host"] != "":
      ready.append(m)
  return (
    stats_time,
    stats_host,
    stats_jid,
    stats_type,
    stats_dev,
    stats_event,
    stats_value,
    stats_wid,
    stats_mult,
    stats_unit,
    proc_jid,
    proc_host,
    proc_proc,
    proc_fields,
    proc_device,
    ready,
  )


def _intern_codes(col: list[str]) -> list[int]:
  """Map each string to a dense code. The same text always shares a code."""
  table: dict[str, int] = {}
  codes: list[int] = []
  nxt = 0
  for item in col:
    key = item if item else ""
    cid = table.get(key, -1)
    if cid < 0:
      cid = nxt
      table[key] = cid
      nxt += 1
    codes.append(cid)
  return codes


def _time_same(left: float, right: float) -> bool:
  """True when two timestamps belong in one collapse group, including NaN."""
  left_nan = math.isnan(left)
  right_nan = math.isnan(right)
  if left_nan or right_nan:
    return left_nan and right_nan
  return left == right


def _group_order(
  hosts: list[str],
  types: list[str],
  devs: list[str],
  events: list[str],
  units: list[str],
  times: list[float],
) -> tuple[list[int], list[int]]:
  """Return row order and run starts for host/type/dev/event/unit/time."""
  n = len(times)
  hc = _intern_codes(hosts)
  tc = _intern_codes(types)
  dc = _intern_codes(devs)
  ec = _intern_codes(events)
  uc = _intern_codes(units)
  keyed: list[tuple[int, int, int, int, int, int, float, int]] = []
  for i in range(n):
    if math.isnan(times[i]):
      keyed.append((hc[i], tc[i], dc[i], ec[i], uc[i], 1, 0.0, i))
    else:
      keyed.append((hc[i], tc[i], dc[i], ec[i], uc[i], 0, times[i], i))
  keyed.sort()
  order: list[int] = []
  for item in keyed:
    order.append(item[7])
  starts: list[int] = [0]
  for j in range(1, n):
    a = order[j - 1]
    b = order[j]
    if (
      hc[a] != hc[b]
      or tc[a] != tc[b]
      or dc[a] != dc[b]
      or ec[a] != ec[b]
      or uc[a] != uc[b]
      or not _time_same(times[a], times[b])
    ):
      starts.append(j)
  return order, starts


def _reduceat_sum_min1(ordered: list[float], starts: list[int]) -> list[float]:
  """Sum each run with pandas ``sum(min_count=1)``.

  Codon-NumPy exposes ``np.add.reduce`` and not ``reduceat``, so each
  already-contiguous run is one reduce. The reduced value is the result.
  """
  n = len(ordered)
  filled: list[float] = []
  counts: list[float] = []
  for v in ordered:
    if math.isnan(v):
      filled.append(0.0)
      counts.append(0.0)
    else:
      filled.append(float(v))
      counts.append(1.0)
  filled_a = np.array(filled)
  counts_a = np.array(counts)
  out: list[float] = []
  g = 0
  ng = len(starts)
  while g < ng:
    end = starts[g + 1] if g + 1 < ng else n
    total = float(np.add.reduce(filled_a[starts[g] : end]))
    valid = float(np.add.reduce(counts_a[starts[g] : end]))
    if valid > 0.0:
      out.append(total)
    else:
      out.append(float("nan"))
    g += 1
  return out


def fill_reuse_bank(dst: list, src: list) -> int:
  """Overwrite ``dst`` through ``len(src)`` and return that end mark.

  Capacity past the mark stays unused. A shorter window moves the mark
  backward so a reader that stops at the mark cannot see the previous tail.
  """
  n = len(src)
  have = len(dst)
  if have < n:
    dst.extend(src[have:])
  i = 0
  while i < n:
    dst[i] = src[i]
    i += 1
  return n


def collapse_plain_host_sum(
  hosts: list[str],
  types: list[str],
  events: list[str],
  units: list[str],
  times: list[float],
  values: list[float],
  gpu_types: list[str],
) -> tuple[
  list[str], list[str], list[str], list[str], list[float], list[float]
]:
  """Groupby-sum on host/type/event/unit/time for non-GPU rows.

  Group identity is integer codes. Each contiguous run is one
  ``np.add.reduce`` with ``sum(min_count=1)``. Empty input does not
  build a NumPy buffer.
  """
  n = len(values)
  out_host: list[str] = []
  out_type: list[str] = []
  out_event: list[str] = []
  out_unit: list[str] = []
  out_time: list[float] = []
  sums: list[float] = []
  if n == 0:
    return (out_host, out_type, out_event, out_unit, out_time, sums)

  kh: list[str] = []
  kt: list[str] = []
  ke: list[str] = []
  ku: list[str] = []
  ktime: list[float] = []
  kv: list[float] = []
  for i in range(n):
    is_gpu = False
    for g in gpu_types:
      if types[i] == g:
        is_gpu = True
        break
    if not is_gpu:
      kh.append(hosts[i])
      kt.append(types[i])
      ke.append(events[i])
      ku.append(units[i])
      ktime.append(times[i])
      kv.append(values[i])

  if not kh:
    return (out_host, out_type, out_event, out_unit, out_time, sums)

  blanks: list[str] = []
  for _i in range(len(kh)):
    blanks.append("")
  order, starts = _group_order(kh, kt, blanks, ke, ku, ktime)
  ordered: list[float] = []
  for j in range(len(order)):
    ordered.append(kv[order[j]])
  reduced = _reduceat_sum_min1(ordered, starts)
  g = 0
  while g < len(starts):
    i0 = order[starts[g]]
    out_host.append(kh[i0])
    out_type.append(kt[i0])
    out_event.append(ke[i0])
    out_unit.append(ku[i0])
    out_time.append(ktime[i0])
    sums.append(reduced[g])
    g += 1
  return (out_host, out_type, out_event, out_unit, out_time, sums)


# ---------------------------------------------------------------------------
# feed -> counter-delta -> full group collapse -> arc-rate pipeline.
#
# Mirrors ``_apply_counter_deltas`` / ``_collapse_stats_with_deltas`` /
# ``_apply_arc_and_finalize`` in ``sync_timedb_parsing.py``. ``_append_stats``
# already stores pre-mult raw counter values, so ``value`` columns below are
# never re-multiplied; only ``delta`` is wrap-corrected and scaled by
# ``mult``.
# ---------------------------------------------------------------------------

DCGM_BLANK = 140737488355328.0  # 2**47; DCGM FP64 blank-family base.

_CCM_TYPES = ["host_cpu_hw", "cpu_counter_metrics"]
_CCM_POWER_EVENTS = [
  "dcgm_cpu_power_util_w",
  "dcgm_cpu_power_limit_w",
  "DCGM_CPU_POWER_UTIL_W",
  "DCGM_CPU_POWER_LIMIT_W",
]
_NVIDIA_MAX_EVENTS = ["module_power_usage", "sysio_power_usage", "gpu_count"]
_NVIDIA_MEAN_EVENTS = ["temperature"]
_NVIDIA_OR_EVENTS = ["clocks_event_reasons"]

_KIND_SUM = 0
_KIND_CCM_POWER = 1
_KIND_NVIDIA_MAX = 2
_KIND_NVIDIA_MEAN = 3
_KIND_NVIDIA_OR = 4


def _counter_key(host: str, typ: str, dev: str, event: str) -> str:
  """Build the counter/arc group key; empty/None ``dev`` collapses to ``""``."""
  d = dev if dev else ""
  return host + "\0" + typ + "\0" + d + "\0" + event


def _is_keep_dev_type(typ: str) -> bool:
  return any(typ == g for g in KEEP_DEV_TYPES)


def _is_finite_val(v: float) -> bool:
  if math.isnan(v):
    return False
  return not (v == float("inf") or v == float("-inf"))


def _dcg_delta_gap_threshold(dvals: list[float]) -> float:
  """Port of ``_dcg_delta_gap_threshold``: dynamic ``0.05*max(|delta|)`` gap."""
  d_gap = 1e-6
  max_abs = 0.0
  found = False
  for d in dvals:
    if _is_finite_val(d):
      ad = abs(d)
      if (not found) or ad > max_abs:
        max_abs = ad
        found = True
  if found and max_abs > 0.0:
    cand = 0.05 * max_abs
    d_gap = cand if cand > 1e-9 else 1e-9
  return d_gap


def _nvidia_or_values(values: list[float]) -> float:
  """Bitwise OR of finite non-blank values (``clocks_event_reasons``)."""
  acc: int = 0
  for v in values:
    if _is_finite_val(v) and v < DCGM_BLANK:
      acc |= int(v)
  return float(acc)


def _max_skipna(values: list[float]) -> float:
  best = float("nan")
  found = False
  for v in values:
    if not math.isnan(v) and ((not found) or v > best):
      best = v
      found = True
  return best


def _mean_skipna(values: list[float]) -> float:
  total = 0.0
  count = 0
  for v in values:
    if not math.isnan(v):
      total += v
      count += 1
  if count == 0:
    return float("nan")
  return total / float(count)


def _collapse_kind(typ: str, event: str) -> int:
  """Classify (type, event) into a collapse aggregator kind code."""
  if typ == "nvidia_gpu":
    for e in _NVIDIA_MAX_EVENTS:
      if event == e:
        return _KIND_NVIDIA_MAX
    for e in _NVIDIA_MEAN_EVENTS:
      if event == e:
        return _KIND_NVIDIA_MEAN
    for e in _NVIDIA_OR_EVENTS:
      if event == e:
        return _KIND_NVIDIA_OR
    return _KIND_SUM
  is_ccm_type = False
  for t in _CCM_TYPES:
    if typ == t:
      is_ccm_type = True
      break
  if is_ccm_type:
    for e in _CCM_POWER_EVENTS:
      if event == e:
        return _KIND_CCM_POWER
  return _KIND_SUM


def _header_bridge_flag(slug: str) -> float:
  """1.0 when ``$build`` proves ``host_cpu_hw`` bridges (no ``likwid`` token, or ``x86perf``)."""
  likwid = False
  x86perf = False
  for tok in slug.split("-"):
    if tok == "likwid":
      likwid = True
    elif tok == "x86perf":
      x86perf = True
  return 1.0 if (not likwid) or x86perf else 0.0


def scan_monitor_headers(
  lines: list[str], carry: dict[str, list[float]]
) -> None:
  """Port of the ``IncrementalStatsParser`` ``$`` header capture.

  A run of ``$`` lines (its ``$build`` slug, if any) belongs to the next digit
  sample header: that header's host gets ``CARRY_BUILD_KEY`` (bridge flag) and
  its time appended to ``CARRY_RESTART_KEY``. A per-host
  ``CARRY_HEADER_PENDING_KEY`` left by listend's ``note_monitor_header`` also
  records the restart time. A header still waiting at the end of ``lines``
  (file-session window cut) is parked under the bare pending key. Mutates
  ``carry``.
  """
  pending = False
  flag = 0.0
  if CARRY_HEADER_PENDING_KEY in carry:
    pending = True
    flag = carry[CARRY_HEADER_PENDING_KEY][0]
    del carry[CARRY_HEADER_PENDING_KEY]
  for raw in lines:
    if not raw:
      continue
    s = raw
    if s[0].isspace():
      s = s.lstrip()
      if not s:
        continue
    if s[0] == "$":
      if not pending:
        pending = True
        flag = 0.0
      if s.startswith("$build"):
        parts = s.split()
        flag = _header_bridge_flag(parts[1]) if len(parts) > 1 else 0.0
    elif s[0].isdigit():
      parts = s.split()
      if len(parts) < 3:
        continue
      host = parts[2]
      host_key = CARRY_HEADER_PENDING_KEY + host
      if not pending and host_key not in carry:
        continue
      if pending:
        carry[CARRY_BUILD_KEY + host] = [flag]
        pending = False
      carry.pop(host_key, None)
      try:
        t = float(parts[0])
      except ValueError:
        continue
      rk = CARRY_RESTART_KEY + host
      times: list[float] = []
      if rk in carry:
        for h in carry[rk]:
          times.append(h)
      times.append(t)
      if len(times) > CARRY_RESTART_KEEP:
        times = times[len(times) - CARRY_RESTART_KEEP :]
      carry[rk] = times
  if pending:
    carry[CARRY_HEADER_PENDING_KEY] = [flag]


def _sentinel_breaks(
  v: float, typ: str, host: str, carry: dict[str, list[float]]
) -> bool:
  """Port of ``_sentinel_chain_breaks`` for one sentinel row."""
  bridgeable = False
  for c in SENTINEL_BRIDGEABLE_CODES:
    if v == c:
      bridgeable = True
  if not bridgeable:
    return True
  for t in _CCM_TYPES:
    if typ == t:
      bk = CARRY_BUILD_KEY + host
      return not (bk in carry and carry[bk][0] == 1.0)
  return False


def _restart_in_gap(
  carry: dict[str, list[float]], host: str, t0: float, t1: float
) -> bool:
  """Port of ``_restart_between``: a ``$`` header sample in ``(t0, t1]``."""
  rk = CARRY_RESTART_KEY + host
  if rk not in carry:
    return False
  return any(t0 < h and h <= t1 for h in carry[rk])


def apply_counter_deltas_cols(
  times: list[float],
  hosts: list[str],
  jids: list[str],
  types: list[str],
  devs: list[str],
  events: list[str],
  units: list[str],
  values: list[float],
  wids: list[int],
  mults: list[float],
  carry_raw: dict[str, list[float]],
) -> tuple[
  list[float],
  list[str],
  list[str],
  list[str],
  list[str],
  list[str],
  list[str],
  list[float],
  list[float],
  dict[str, list[float]],
  list[float],
]:
  """Columnar port of ``_apply_counter_deltas``.

  Groups by host/type/dev/event, sorts by (key, time), computes
  ``value.diff()`` per group (first sample ``NaN`` unless ``carry_raw``
  has a hit), adds one modulus only when the previous sample sat in the
  top half of the counter and the drop is smaller than one modulus, otherwise
  a finite negative delta becomes NaN (so does any drop across a ``$``
  restart header), then
  scales by ``mult`` via Codon-NumPy contiguous buffers when ``n>0``.
  ``wid``/``mult`` columns are dropped from the output. ``carry_raw`` is
  updated (as a copy) with ``[value, wid, mult, time, missed, broken]`` per
  group: the last valid raw value/time, the sentinel rows since it and
  whether one of them breaks the chain.

  Value sentinels (``value < 0``) follow ``_sentinel_deltas``: a sentinel row
  gets NaN, and the next valid row diffs against the last valid value when
  the gap holds at most the bridge limit (``CARRY_BRIDGE_MAX_KEY`` in the
  carry, else ``SENTINEL_BRIDGE_MAX_DEFAULT``) rows, none chain-breaking and
  no ``$`` header sample. The 11th return is that bridged delta's elapsed
  time per output row (NaN when not bridged).
  """
  n = len(times)
  new_carry: dict[str, list[float]] = {}
  for k, v in carry_raw.items():
    new_carry[k] = v

  out_time: list[float] = []
  out_host: list[str] = []
  out_jid: list[str] = []
  out_type: list[str] = []
  out_dev: list[str] = []
  out_event: list[str] = []
  out_unit: list[str] = []
  out_value: list[float] = []
  out_delta: list[float] = []
  if n == 0:
    return (
      out_time,
      out_host,
      out_jid,
      out_type,
      out_dev,
      out_event,
      out_unit,
      out_value,
      out_delta,
      new_carry,
      [],
    )

  keys: list[str] = []
  for i in range(n):
    keys.append(_counter_key(hosts[i], types[i], devs[i], events[i]))

  combo: list[tuple[str, float, int]] = []
  for i in range(n):
    combo.append((keys[i], times[i], i))
  combo.sort()
  order: list[int] = []
  for c in combo:
    order.append(c[2])

  for j in range(n):
    idx = order[j]
    out_time.append(times[idx])
    out_host.append(hosts[idx])
    out_jid.append(jids[idx])
    out_type.append(types[idx])
    out_dev.append(devs[idx])
    out_event.append(events[idx])
    out_unit.append(units[idx])
    out_value.append(values[idx])

  nan = float("nan")
  bridge_max = float(SENTINEL_BRIDGE_MAX_DEFAULT)
  if CARRY_BRIDGE_MAX_KEY in carry_raw:
    bridge_max = carry_raw[CARRY_BRIDGE_MAX_KEY][0]
  raw_deltas: list[float] = []
  bridge_dt: list[float] = []
  wid_f: list[float] = []
  mult_f: list[float] = []
  v_arr = np.array(out_value)
  prev_v = nan
  prev_t = nan
  missed = 0.0
  broken = False
  had_valid = False
  carried_ok = False
  for j in range(n):
    idx = order[j]
    key = keys[idx]
    if j == 0 or keys[order[j - 1]] != key:
      prev_v = nan
      prev_t = nan
      missed = 0.0
      broken = False
      had_valid = False
      carried_ok = False
      if key in carry_raw:
        carried = carry_raw[key]
        if not math.isnan(carried[0]):
          carried_ok = True
          prev_v = carried[0]
          prev_t = carried[3]
          if len(carried) > 4:
            missed = carried[4]
          if len(carried) > 5:
            broken = carried[5] > 0.0
    v = float(v_arr[j])
    t = out_time[j]
    delta = nan
    bdt = nan
    if v < 0.0:
      missed += 1.0
      if _sentinel_breaks(v, out_type[j], out_host[j], carry_raw):
        broken = True
    else:
      if not math.isnan(prev_v):
        # A drop across a ``$`` restart header is a reset, not a wrap.
        if missed == 0.0 and not (
          v < prev_v and _restart_in_gap(carry_raw, out_host[j], prev_t, t)
        ):
          delta = v - prev_v
        elif (
          missed <= bridge_max
          and not broken
          and not _restart_in_gap(carry_raw, out_host[j], prev_t, t)
        ):
          delta = v - prev_v
          bdt = t - prev_t
      prev_v = v
      prev_t = t
      missed = 0.0
      broken = False
      had_valid = True
    raw_deltas.append(delta)
    bridge_dt.append(bdt)
    wid_f.append(float(wids[idx]))
    mult_f.append(mults[idx])
    is_last = (j == n - 1) or (keys[order[j + 1]] != key)
    if is_last:
      if had_valid:
        new_carry[key] = [
          prev_v,
          float(wids[idx]),
          mults[idx],
          prev_t,
          missed,
          1.0 if broken else 0.0,
        ]
      elif carried_ok:
        carried = carry_raw[key]
        new_carry[key] = [
          carried[0],
          carried[1],
          carried[2],
          carried[3],
          missed,
          1.0 if broken else 0.0,
        ]
  d_arr = np.array(raw_deltas)
  w_arr = np.array(wid_f)
  m_arr = np.array(mult_f)
  for j in range(n):
    delta = float(d_arr[j])
    # One modulus only from the top half. A gauge dip stays NaN.
    if delta < 0.0 and not math.isnan(delta):
      modulus = 2.0 ** float(w_arr[j])
      prev = float(v_arr[j]) - delta
      one_wrap = (
        (not math.isnan(prev))
        and (not math.isinf(prev))
        and (prev >= (modulus * 0.5))
        and (delta > -modulus)
      )
      delta = modulus + delta if one_wrap else nan
    out_delta.append(delta * float(m_arr[j]))

  return (
    out_time,
    out_host,
    out_jid,
    out_type,
    out_dev,
    out_event,
    out_unit,
    out_value,
    out_delta,
    new_carry,
    bridge_dt,
  )


def _cluster_mean_reduceat(values: list[float], gap: float) -> float:
  """Cluster-mean sum via ``np.add.reduceat`` after blank values are missing."""
  kept: list[float] = []
  for v in values:
    if _is_finite_val(v) and v < DCGM_BLANK:
      kept.append(v)
  if not kept:
    return float("nan")
  kept.sort()
  n = len(kept)
  if n == 1:
    return kept[0]
  buf = np.array(kept)
  split_at: list[int] = [0]
  for i in range(1, n):
    if float(buf[i]) - float(buf[i - 1]) > gap:
      split_at.append(i)
  total = 0.0
  g = 0
  while g < len(split_at):
    end = split_at[g + 1] if g + 1 < len(split_at) else n
    cluster = float(np.add.reduce(buf[split_at[g] : end]))
    total += cluster / float(end - split_at[g])
    g += 1
  return total


def _or_reduceat(values: list[float]) -> float:
  """Bitwise OR of finite non-blank values. Same result as ``bitwise_or.reduce``."""
  return _nvidia_or_values(values)


def collapse_full_cols(
  times: list[float],
  hosts: list[str],
  jids: list[str],
  types: list[str],
  devs: list[str],
  events: list[str],
  units: list[str],
  values: list[float],
  deltas: list[float],
) -> tuple[
  list[float],
  list[str],
  list[str],
  list[str],
  list[str],
  list[str],
  list[str],
  list[float],
  list[float],
]:
  """Columnar port of ``_collapse_stats_with_deltas``.

  Keep-``dev`` types (``KEEP_DEV_TYPES``: GPUs and ``host_power_socket``)
  keep hardware ``dev`` in the group key and ``j:`` shared-node stamps stay
  distinct the same way. Every other (whole-node) row has ``dev`` blanked
  first, so CPU/CCM/host rows sum across devices to ``dev=''``;
  ``host_power_socket`` sums per socket (plain ``_KIND_SUM``).
  Host-sum groups use ``sum(min_count=1)`` NaN semantics (all-NaN -> NaN).
  CCM power gauges (``host_cpu_hw``/``cpu_counter_metrics`` x DCGM CPU power
  events) use the cluster-mean-sum port. ``nvidia_gpu`` rows nan-out DCGM
  blank values (``>= 2**47``) before per-event-class aggregation (max/mean/
  OR/sum); ``amd_gpu``/``intel_gpu`` always sum with ``dev``. ``jid`` is
  first-in-group. Output is sorted by ``(host, type, dev, event, time)``.
  """
  n = len(times)
  out_time: list[float] = []
  out_host: list[str] = []
  out_jid: list[str] = []
  out_type: list[str] = []
  out_dev: list[str] = []
  out_event: list[str] = []
  out_unit: list[str] = []
  out_value: list[float] = []
  out_delta: list[float] = []
  if n == 0:
    return (
      out_time,
      out_host,
      out_jid,
      out_type,
      out_dev,
      out_event,
      out_unit,
      out_value,
      out_delta,
    )

  # ``j:`` first: a stamped keep-dev row (never GPU, ``_shared_row_dev``)
  # groups on its job like CPython's ``_partition_job_stamped_rows``.
  devs = [
    devs[i].split(JOB_STAMP_DEV_SEP)[0]
    if devs[i].startswith("j:")
    else devs[i]
    if _is_keep_dev_type(types[i])
    else ""
    for i in range(n)
  ]
  vals_work: list[float] = []
  for i in range(n):
    v = values[i]
    if types[i] == "nvidia_gpu" and (not math.isnan(v)) and v >= DCGM_BLANK:
      v = float("nan")
    vals_work.append(v)
  order, starts = _group_order(hosts, types, devs, events, units, times)
  ordered_v: list[float] = []
  ordered_d: list[float] = []
  for j in range(n):
    src = order[j]
    ordered_v.append(vals_work[src])
    ordered_d.append(deltas[src])
  sum_v = _reduceat_sum_min1(ordered_v, starts)
  sum_d = _reduceat_sum_min1(ordered_d, starts)
  ng = len(starts)
  g = 0
  while g < ng:
    s = starts[g]
    end = starts[g + 1] if g + 1 < ng else n
    i0 = order[s]
    kind = _collapse_kind(types[i0], events[i0])
    slice_v = ordered_v[s:end]
    slice_d = ordered_d[s:end]
    if kind == _KIND_CCM_POWER:
      vagg = _cluster_mean_reduceat(slice_v, 1.0)
      dagg = _cluster_mean_reduceat(slice_d, _dcg_delta_gap_threshold(slice_d))
    elif kind == _KIND_NVIDIA_MAX:
      vagg = _max_skipna(slice_v)
      dagg = _mean_skipna(slice_d)
    elif kind == _KIND_NVIDIA_MEAN:
      vagg = _mean_skipna(slice_v)
      dagg = _mean_skipna(slice_d)
    elif kind == _KIND_NVIDIA_OR:
      vagg = _or_reduceat(slice_v)
      dagg = sum_d[g]
    else:
      vagg = sum_v[g]
      dagg = sum_d[g]
    out_time.append(times[i0])
    out_host.append(hosts[i0])
    out_jid.append(jids[i0])
    out_type.append(types[i0])
    out_dev.append(devs[i0])
    out_event.append(events[i0])
    out_unit.append(units[i0])
    out_value.append(vagg)
    out_delta.append(dagg)
    g += 1

  combo: list[tuple[str, str, str, str, float, int]] = []
  for pos in range(len(out_time)):
    combo.append(
      (
        out_host[pos],
        out_type[pos],
        out_dev[pos],
        out_event[pos],
        out_time[pos],
        pos,
      )
    )
  combo.sort()

  f_time: list[float] = []
  f_host: list[str] = []
  f_jid: list[str] = []
  f_type: list[str] = []
  f_dev: list[str] = []
  f_event: list[str] = []
  f_unit: list[str] = []
  f_value: list[float] = []
  f_delta: list[float] = []
  for c in combo:
    pos = c[5]
    f_time.append(out_time[pos])
    f_host.append(out_host[pos])
    f_jid.append(out_jid[pos])
    f_type.append(out_type[pos])
    f_dev.append(out_dev[pos])
    f_event.append(out_event[pos])
    f_unit.append(out_unit[pos])
    f_value.append(out_value[pos])
    f_delta.append(out_delta[pos])

  return (
    f_time,
    f_host,
    f_jid,
    f_type,
    f_dev,
    f_event,
    f_unit,
    f_value,
    f_delta,
  )


def apply_arc_cols(
  times: list[float],
  hosts: list[str],
  jids: list[str],
  types: list[str],
  devs: list[str],
  events: list[str],
  units: list[str],
  values: list[float],
  deltas: list[float],
  carry_arc: dict[str, float],
) -> tuple[
  list[float],
  list[str],
  list[str],
  list[str],
  list[str],
  list[str],
  list[str],
  list[float],
  list[float],
  list[float],
  dict[str, float],
]:
  """Columnar port of ``_apply_arc_and_finalize``.

  Groups by host/type/dev/event, sorts by time, and computes
  ``arc = delta / dt`` when ``dt > 0``. ``carry_arc`` (a copy) supplies the
  previous flush's last sample time for each group's first row, and is
  updated with this flush's last time per group. Rows missing ``time`` or
  ``value`` (NaN) are dropped (host/type/event are never-``None`` strings
  here, so only the numeric columns can trigger the drop).
  """
  n = len(times)
  new_carry: dict[str, float] = {}
  for k, v in carry_arc.items():
    new_carry[k] = v

  if n == 0:
    return ([], [], [], [], [], [], [], [], [], [], new_carry)

  keys: list[str] = []
  for i in range(n):
    keys.append(_counter_key(hosts[i], types[i], devs[i], events[i]))

  combo: list[tuple[str, float, int]] = []
  for i in range(n):
    combo.append((keys[i], times[i], i))
  combo.sort()
  order: list[int] = []
  for c in combo:
    order.append(c[2])

  out_time: list[float] = []
  out_host: list[str] = []
  out_jid: list[str] = []
  out_type: list[str] = []
  out_dev: list[str] = []
  out_event: list[str] = []
  out_unit: list[str] = []
  out_value: list[float] = []
  out_delta: list[float] = []
  out_arc: list[float] = []
  for j in range(n):
    idx = order[j]
    out_time.append(times[idx])
    out_host.append(hosts[idx])
    out_jid.append(jids[idx])
    out_type.append(types[idx])
    out_dev.append(devs[idx])
    out_event.append(events[idx])
    out_unit.append(units[idx])
    out_value.append(values[idx])
    out_delta.append(deltas[idx])

  nan = float("nan")
  for j in range(n):
    idx = order[j]
    key = keys[idx]
    arc = nan
    if j > 0 and keys[order[j - 1]] == key:
      prev_idx = order[j - 1]
      dt = times[idx] - times[prev_idx]
      if dt > 0.0 and not math.isnan(deltas[idx]):
        arc = deltas[idx] / dt
    elif key in carry_arc:
      prev_time = carry_arc[key]
      dt = times[idx] - prev_time
      if dt > 0.0 and not math.isnan(deltas[idx]):
        arc = deltas[idx] / dt
    out_arc.append(arc)

  for j in range(n):
    idx = order[j]
    key = keys[idx]
    is_last = (j == n - 1) or (keys[order[j + 1]] != key)
    if is_last:
      new_carry[key] = times[idx]

  f_time: list[float] = []
  f_host: list[str] = []
  f_jid: list[str] = []
  f_type: list[str] = []
  f_dev: list[str] = []
  f_event: list[str] = []
  f_unit: list[str] = []
  f_value: list[float] = []
  f_delta: list[float] = []
  f_arc: list[float] = []
  for j in range(n):
    if math.isnan(out_time[j]) or math.isnan(out_value[j]):
      continue
    f_time.append(out_time[j])
    f_host.append(out_host[j])
    f_jid.append(out_jid[j])
    f_type.append(out_type[j])
    f_dev.append(out_dev[j])
    f_event.append(out_event[j])
    f_unit.append(out_unit[j])
    f_value.append(out_value[j])
    f_delta.append(out_delta[j])
    f_arc.append(out_arc[j])

  return (
    f_time,
    f_host,
    f_jid,
    f_type,
    f_dev,
    f_event,
    f_unit,
    f_value,
    f_delta,
    f_arc,
    new_carry,
  )


def collapse_host_finished_copy_unique(
  time: list[float],
  host: list[str],
  jid: list[str],
  typ: list[str],
  dev: list[str],
  event: list[str],
  unit: list[str],
  value: list[float],
  delta: list[float],
  arc: list[float],
) -> tuple[
  list[float],
  list[str],
  list[str],
  list[str],
  list[str],
  list[str],
  list[str],
  list[float],
  list[float],
  list[float],
]:
  """Last-wins on host COPY conflict cols (matches ``_collapse_host_columnar``)."""
  n = len(time)
  if n == 0:
    return (time, host, jid, typ, dev, event, unit, value, delta, arc)
  by_key: dict[tuple[float, str, str, str, str], int] = {}
  order: list[int] = []
  for i in range(n):
    time_key = time[i] if i < len(time) else 0.0
    if type(time_key) is not float or math.isnan(time_key):
      time_key = 0.0
    host_key = host[i] if i < len(host) else ""
    type_key = typ[i] if i < len(typ) else ""
    event_key = event[i] if i < len(event) else ""
    dev_key = dev[i] if i < len(dev) else ""
    key = (time_key, host_key, type_key, event_key, dev_key)
    if key in by_key:
      prev = by_key[key]
      order[prev] = -1
    by_key[key] = len(order)
    order.append(i)
  keep = [idx for idx in order if idx >= 0]
  if len(keep) == n:
    return (time, host, jid, typ, dev, event, unit, value, delta, arc)

  out_time: list[float] = []
  out_host: list[str] = []
  out_jid: list[str] = []
  out_typ: list[str] = []
  out_dev: list[str] = []
  out_event: list[str] = []
  out_unit: list[str] = []
  out_value: list[float] = []
  out_delta: list[float] = []
  out_arc: list[float] = []
  ki = 0
  while ki < len(keep):
    idx = keep[ki]
    if idx < len(time):
      out_time.append(time[idx])
    if idx < len(host):
      out_host.append(host[idx])
    if idx < len(jid):
      out_jid.append(jid[idx])
    if idx < len(typ):
      out_typ.append(typ[idx])
    if idx < len(dev):
      out_dev.append(dev[idx])
    if idx < len(event):
      out_event.append(event[idx])
    if idx < len(unit):
      out_unit.append(unit[idx])
    if idx < len(value):
      out_value.append(value[idx])
    if idx < len(delta):
      out_delta.append(delta[idx])
    if idx < len(arc):
      out_arc.append(arc[idx])
    ki += 1
  return (
    out_time,
    out_host,
    out_jid,
    out_typ,
    out_dev,
    out_event,
    out_unit,
    out_value,
    out_delta,
    out_arc,
  )


def normalize_host_finished_for_copy(
  time: list[float],
  host: list[str],
  jid: list[str],
  typ: list[str],
  dev: list[str],
  event: list[str],
  unit: list[str],
  value: list[float],
  delta: list[float],
  arc: list[float],
) -> tuple[
  list[float | None],
  list[str],
  list[str | None],
  list[str],
  list[str],
  list[str],
  list[str],
  list[float | None],
  list[float | None],
  list[float | None],
]:
  """Row-wise COPY scalar normalization on finished host columns (post-collapse)."""
  n = len(time)
  out_time: list[float | None] = []
  out_host: list[str] = []
  out_jid: list[str | None] = []
  out_typ: list[str] = []
  out_dev: list[str] = []
  out_event: list[str] = []
  out_unit: list[str] = []
  out_value: list[float | None] = []
  out_delta: list[float | None] = []
  out_arc: list[float | None] = []
  i = 0
  while i < n:
    tcell = time[i] if i < len(time) else 0.0
    if type(tcell) is float and not math.isnan(tcell):
      out_time.append(tcell)
    else:
      out_time.append(None)
    out_host.append(host[i] if i < len(host) else "")
    jcell = jid[i] if i < len(jid) else ""
    if jcell == "-" or jcell == "":
      out_jid.append(None)
    else:
      out_jid.append(jcell)
    out_typ.append(typ[i] if i < len(typ) else "")
    dcell = dev[i] if i < len(dev) else ""
    if (
      dcell is None or type(dcell) is float or dcell == "nan" or dcell == "NaN"
    ):
      out_dev.append("")
    else:
      out_dev.append(dcell)
    out_event.append(event[i] if i < len(event) else "")
    out_unit.append(unit[i] if i < len(unit) else "")
    vcell = value[i] if i < len(value) else 0.0
    if type(vcell) is float and not math.isnan(vcell):
      out_value.append(vcell)
    else:
      out_value.append(None)
    dlt = delta[i] if i < len(delta) else 0.0
    if type(dlt) is float and not math.isnan(dlt):
      out_delta.append(dlt)
    else:
      out_delta.append(None)
    acell = arc[i] if i < len(arc) else 0.0
    if type(acell) is float and not math.isnan(acell):
      out_arc.append(acell)
    else:
      out_arc.append(None)
    i += 1
  return (
    out_time,
    out_host,
    out_jid,
    out_typ,
    out_dev,
    out_event,
    out_unit,
    out_value,
    out_delta,
    out_arc,
  )


def normalize_proc_finished_for_copy(
  proc_jid: list[str],
  proc_host: list[str],
  proc_proc: list[str],
  proc_fields: list[dict[str, int]],
  proc_device: list[str],
) -> tuple[list[str], list[str], list[str], list[dict[str, int]], list[str]]:
  """Normalize proc peak-merge fields for COPY (ints only; nulls omitted)."""
  n = len(proc_jid)
  if n == 0:
    return (proc_jid, proc_host, proc_proc, proc_fields, proc_device)
  out_f: list[dict[str, int]] = []
  for i in range(n):
    fld = proc_fields[i] if i < len(proc_fields) else {}
    normed: dict[str, int] = {}
    for metric in HOST_PROC_KEYS:
      if metric in fld:
        raw_m = fld[metric]
        if type(raw_m) is int:
          normed[metric] = raw_m
        elif type(raw_m) is float and not math.isnan(raw_m):
          normed[metric] = int(raw_m)
    out_f.append(normed)
  return (proc_jid, proc_host, proc_proc, out_f, proc_device)


def collapse_proc_copy_unique(
  proc_jid: list[str],
  proc_host: list[str],
  proc_proc: list[str],
  proc_fields: list[dict[str, int]],
  proc_device: list[str],
) -> tuple[list[str], list[str], list[str], list[dict[str, int]], list[str]]:
  """One row per (jid, host, proc). Metrics stay parallel ints until emit."""
  n = len(proc_jid)
  if n == 0:
    return (proc_jid, proc_host, proc_proc, proc_fields, proc_device)
  width = len(HOST_PROC_KEYS)
  by_key: dict[tuple[str, str, str], list[int]] = {}
  dev_by_key: dict[tuple[str, str, str], str] = {}
  keys_order: list[tuple[str, str, str]] = []
  for i in range(n):
    key = (proc_jid[i], proc_host[i], proc_proc[i])
    row: list[int] = []
    fld = proc_fields[i]
    k = 0
    while k < width:
      metric = HOST_PROC_KEYS[k]
      val = -1
      if metric in fld:
        raw_m = fld[metric]
        if type(raw_m) is int:
          val = raw_m
        elif type(raw_m) is float and not math.isnan(raw_m):
          val = int(raw_m)
      row.append(val)
      k += 1
    device = proc_proc[i]
    if i < len(proc_device):
      device = proc_device[i]
    if key in by_key:
      dst = by_key[key]
      k = 0
      while k < width:
        nv = row[k]
        if nv >= 0:
          metric = HOST_PROC_KEYS[k]
          peak = (
            metric == "vm_peak"
            or metric == "vm_hwm"
            or metric == "vm_stk"
            or metric == "vm_exe"
            or metric == "vm_lib"
          )
          prev = dst[k]
          if peak and prev >= 0 and prev >= nv:
            pass
          else:
            dst[k] = nv
        k += 1
    else:
      by_key[key] = row
      keys_order.append(key)
    dev_by_key[key] = device
  out_j: list[str] = []
  out_h: list[str] = []
  out_p: list[str] = []
  out_f: list[dict[str, int]] = []
  out_d: list[str] = []
  for key in keys_order:
    metrics = by_key[key]
    out_j.append(key[0])
    out_h.append(key[1])
    out_p.append(key[2])
    fields: dict[str, int] = {}
    k = 0
    while k < width:
      if metrics[k] >= 0:
        fields[HOST_PROC_KEYS[k]] = metrics[k]
      k += 1
    out_f.append(fields)
    out_d.append(dev_by_key[key])
  return (out_j, out_h, out_p, out_f, out_d)


def finalize_copy_unique_emit(
  finished: tuple[
    list[float],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[float],
    list[float],
    list[float],
  ],
  proc: tuple[list[str], list[str], list[str], list[dict[str, int]], list[str]],
) -> tuple[
  tuple[
    list[float],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[float],
    list[float],
    list[float],
  ],
  tuple[list[str], list[str], list[str], list[dict[str, int]], list[str]],
]:
  """DB-key pre-COPY collapse + proc peak-merge (before scalar normalization)."""
  host = collapse_host_finished_copy_unique(*finished)
  proc_out = collapse_proc_copy_unique(*proc)
  return (host, proc_out)


def normalize_copy_unique_scalars(
  finished: tuple[
    list[float],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[float],
    list[float],
    list[float],
  ],
  proc: tuple[list[str], list[str], list[str], list[dict[str, int]], list[str]],
) -> tuple[
  tuple[
    list[float | None],
    list[str],
    list[str | None],
    list[str],
    list[str],
    list[str],
    list[str],
    list[float | None],
    list[float | None],
    list[float | None],
  ],
  tuple[list[str], list[str], list[str], list[dict[str, int]], list[str]],
]:
  """COPY cell coercion on collapsed SoA (kernel emit; before pyext slot banks)."""
  host = normalize_host_finished_for_copy(*finished)
  proc_out = normalize_proc_finished_for_copy(*proc)
  return (host, proc_out)


def _collapse_dev_of(dev: str, typ: str) -> str:
  """The ``dev`` a row has after ``collapse_full_cols``."""
  if dev.startswith("j:"):
    return dev.split(JOB_STAMP_DEV_SEP)[0]
  if _is_keep_dev_type(typ):
    return dev
  return ""


def _sentinel_run_mark(
  host: str,
  typ: str,
  dev: str,
  event: str,
  code: float,
  run: list[float],
) -> dict[str, str]:
  """One ``kind=sentinel`` side mark (``_sentinel_run_marks`` record shape)."""
  prev = "" if math.isnan(run[4]) else str(run[4])
  payload = (
    typ
    + "\t"
    + dev
    + "\t"
    + event
    + "\t"
    + str(int(code))
    + "\t"
    + str(run[1])
    + "\t"
    + str(int(run[2]))
    + "\t"
    + str(int(run[3]))
    + "\t"
    + prev
  )
  return {
    "kind": "sentinel",
    "jids": payload,
    "jid": "",
    "cpus": "",
    "gpus": "",
    "cg": "",
    "time": str(run[0]),
    "host": host,
  }


def sentinel_collapse_prep(
  times: list[float],
  hosts: list[str],
  jids: list[str],
  types: list[str],
  devs: list[str],
  events: list[str],
  units: list[str],
  values: list[float],
  deltas: list[float],
  bridge_dt: list[float],
  carry_arc: dict[str, float],
) -> tuple[
  list[float],
  list[tuple[str, str, str, str, str, float, float, str]],
  dict[tuple[str, str, str, str, str, float], list[float]],
  list[dict[str, str]],
]:
  """Port of ``_sentinel_collapse_prep``.

  Returns ``(values_for_collapse, restore, bridge)``: sentinel values become
  NaN; ``restore`` lists ``(host, type, dev, event, unit, time, code, jid)``
  for each collapsed key with no valid row (modal code, ties to the smallest
  magnitude, jid of its first row); ``bridge`` maps a collapsed key to
  ``[bridged_delta_sum, bridged_rate_sum, sum_kind]``; the 4th return is
  the flush's ``kind=sentinel`` run marks (``_sentinel_run_marks``), with each
  series' previous sample time taken from ``carry_arc``.
  """
  n = len(times)
  # Run building: series (host, type, collapsed dev, event) -> time -> codes.
  s_codes: dict[tuple[str, str, str, str], dict[float, dict[float, int]]] = {}
  s_total: dict[tuple[str, str, str, str], dict[float, int]] = {}
  s_has_sent: dict[tuple[str, str, str, str], bool] = {}
  out_v: list[float] = []
  valid_n: dict[tuple[str, str, str, str, str, float], int] = {}
  code_n: dict[tuple[str, str, str, str, str, float], dict[float, int]] = {}
  code_jid: dict[tuple[str, str, str, str, str, float], dict[float, str]] = {}
  order_keys: list[tuple[str, str, str, str, str, float]] = []
  bridge: dict[tuple[str, str, str, str, str, float], list[float]] = {}
  for i in range(n):
    key = (
      hosts[i],
      types[i],
      _collapse_dev_of(devs[i], types[i]),
      events[i],
      units[i],
      times[i],
    )
    v = values[i]
    series = (key[0], key[1], key[2], key[3])
    if series not in s_total:
      s_total[series] = {}
      s_codes[series] = {}
    tot = s_total[series]
    tot[key[5]] = tot.get(key[5], 0) + 1
    if key[5] not in s_codes[series]:
      s_codes[series][key[5]] = {}
    if v < 0.0:
      s_has_sent[series] = True
      at = s_codes[series][key[5]]
      at[v] = at.get(v, 0) + 1
      out_v.append(float("nan"))
      if key not in code_n:
        code_n[key] = {}
        code_jid[key] = {}
        order_keys.append(key)
      counts = code_n[key]
      if v in counts:
        counts[v] = counts[v] + 1
      else:
        counts[v] = 1
        code_jid[key][v] = jids[i]
    else:
      out_v.append(v)
      valid_n[key] = valid_n.get(key, 0) + 1
    if not math.isnan(bridge_dt[i]):
      kind = _collapse_kind(types[i], events[i])
      sum_kind = 1.0 if kind in (_KIND_SUM, _KIND_NVIDIA_OR) else 0.0
      rate = deltas[i] / bridge_dt[i]
      if key in bridge:
        b = bridge[key]
        bridge[key] = [b[0] + deltas[i], b[1] + rate, b[2]]
      else:
        bridge[key] = [deltas[i], rate, sum_kind]
  restore: list[tuple[str, str, str, str, str, float, float, str]] = []
  for key in order_keys:
    if key in valid_n:
      continue
    best = 0.0
    best_n = -1
    for code, cnt in code_n[key].items():
      if cnt > best_n or (cnt == best_n and code > best):
        best = code
        best_n = cnt
    restore.append(
      (
        key[0],
        key[1],
        key[2],
        key[3],
        key[4],
        key[5],
        best,
        code_jid[key][best],
      )
    )
  marks: list[dict[str, str]] = []
  for series in s_has_sent:
    ck = _counter_key(series[0], series[1], series[2], series[3])
    prev_t = carry_arc[ck] if ck in carry_arc else float("nan")
    open_runs: dict[float, list[float]] = {}
    by_time = s_codes[series]
    for t in sorted(by_time.keys()):
      total = float(s_total[series][t])
      present = by_time[t]
      gone: list[float] = []
      for code in open_runs:
        if code not in present:
          gone.append(code)
      for code in gone:
        marks.append(
          _sentinel_run_mark(
            series[0], series[1], series[2], series[3], code, open_runs[code]
          )
        )
        del open_runs[code]
      for code, cnt in present.items():
        if code in open_runs:
          run = open_runs[code]
          if run[2] == float(cnt) and run[3] == total and run[1] == prev_t:
            run[1] = t
            continue
          marks.append(
            _sentinel_run_mark(
              series[0], series[1], series[2], series[3], code, run
            )
          )
        open_runs[code] = [t, t, float(cnt), total, prev_t]
      prev_t = t
    for code, run in open_runs.items():
      marks.append(
        _sentinel_run_mark(
          series[0], series[1], series[2], series[3], code, run
        )
      )
  return (out_v, restore, bridge, marks)


def sentinel_restore_cols(
  times: list[float],
  hosts: list[str],
  jids: list[str],
  types: list[str],
  devs: list[str],
  events: list[str],
  units: list[str],
  values: list[float],
  deltas: list[float],
  restore: list[tuple[str, str, str, str, str, float, float, str]],
  bridge: dict[tuple[str, str, str, str, str, float], list[float]],
) -> tuple[
  tuple[
    list[float],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[str],
    list[float],
    list[float],
  ],
  dict[tuple[str, str, str, str, str, float], list[float]],
]:
  """Port of ``_sentinel_restore_rows``.

  Rows of restored keys (a reducer's leftover such as the NVIDIA OR 0.0) are
  replaced by the code row. A row with a bridged device keeps the non-bridged
  part of its delta for arc; ``fix`` maps its key to
  ``[full_delta, bridged_rate_sum, sum_kind]``.
  """
  gone: dict[tuple[str, str, str, str, str, float], bool] = {}
  for r in restore:
    gone[(r[0], r[1], r[2], r[3], r[4], r[5])] = True
  o_time: list[float] = []
  o_host: list[str] = []
  o_jid: list[str] = []
  o_type: list[str] = []
  o_dev: list[str] = []
  o_event: list[str] = []
  o_unit: list[str] = []
  o_value: list[float] = []
  o_delta: list[float] = []
  fix: dict[tuple[str, str, str, str, str, float], list[float]] = {}
  for i in range(len(times)):
    key = (hosts[i], types[i], devs[i], events[i], units[i], times[i])
    if key in gone:
      continue
    d = deltas[i]
    if key in bridge:
      b = bridge[key]
      fix[key] = [d, b[1], b[2]]
      d = d - b[0]
    o_time.append(times[i])
    o_host.append(hosts[i])
    o_jid.append(jids[i])
    o_type.append(types[i])
    o_dev.append(devs[i])
    o_event.append(events[i])
    o_unit.append(units[i])
    o_value.append(values[i])
    o_delta.append(d)
  for r in restore:
    o_time.append(r[5])
    o_host.append(r[0])
    o_jid.append(r[7])
    o_type.append(r[1])
    o_dev.append(r[2])
    o_event.append(r[3])
    o_unit.append(r[4])
    o_value.append(r[6])
    o_delta.append(float("nan"))
  return (
    (o_time, o_host, o_jid, o_type, o_dev, o_event, o_unit, o_value, o_delta),
    fix,
  )


def sentinel_bridge_arc_cols(
  times: list[float],
  hosts: list[str],
  types: list[str],
  devs: list[str],
  events: list[str],
  units: list[str],
  deltas: list[float],
  arcs: list[float],
  fix: dict[tuple[str, str, str, str, str, float], list[float]],
) -> tuple[list[float], list[float]]:
  """Port of ``_sentinel_bridge_arc``: per-device bridged rates, full delta back."""
  if len(fix) == 0:
    return (deltas, arcs)
  out_d: list[float] = []
  out_a: list[float] = []
  for i in range(len(times)):
    key = (hosts[i], types[i], devs[i], events[i], units[i], times[i])
    if key not in fix:
      out_d.append(deltas[i])
      out_a.append(arcs[i])
      continue
    f = fix[key]
    part = 0.0 if deltas[i] == 0.0 else arcs[i]
    out_a.append(part + f[1] if f[2] > 0.0 else float("nan"))
    out_d.append(f[0])
  return (out_d, out_a)


def feed_delta_collapse_arc(
  lines: list[str],
  carry_raw: dict[str, list[float]],
  carry_arc: dict[str, float],
  start_idx: int = 0,
  exclude_types: list[str] = [],  # noqa: B006 (Codon: never mutated)
  arm_shared_in: bool = False,
) -> tuple[
  tuple[
    list[float | None],
    list[str],
    list[str | None],
    list[str],
    list[str],
    list[str],
    list[str],
    list[float | None],
    list[float | None],
    list[float | None],
  ],
  tuple[
    list[str],
    list[str],
    list[str],
    list[dict[str, int]],
    list[str],
  ],
  dict[str, list[float]],
  dict[str, float],
  list[dict[str, str]],
  int,
]:
  """Full feed -> delta -> collapse -> arc pipeline for one incremental flush.

  Mirrors ``compute_deltas_and_arc_chunk`` wired to ``IncrementalStatsParser``
  feed + ``DeltaCarryState``. Returns ``(finished_cols, proc_cols,
  carry_raw_out, carry_arc_out, marks, parsed_host_rows)`` where
  ``parsed_host_rows`` is the host record count fed in before delta/collapse
  (the RC-0 zero-host gate input) and ``finished_cols`` are parallel
  lists ``(time, host, jid, type, dev, event, unit, value, delta, arc)``
  (``wid``/``mult`` dropped), ``proc_cols`` mirrors ``feed_lines_full``'s
  proc jid/host/proc/fields/device tuple, and ``marks`` are bound ``%jobs``/
  ``%affinity`` records. ``carry_raw``/``carry_arc`` are read-only on
  input; the returned carries are independent copies updated with this
  flush's state, so the caller's dicts are never mutated in place.
  """
  (
    stats_time,
    stats_host,
    stats_jid,
    stats_type,
    stats_dev,
    stats_event,
    stats_value,
    stats_wid,
    stats_mult,
    stats_unit,
    proc_jid,
    proc_host,
    proc_proc,
    proc_fields,
    proc_device,
    marks,
  ) = feed_lines_full(lines, start_idx, exclude_types, arm_shared_in)

  work_carry: dict[str, list[float]] = {}
  for k, v in carry_raw.items():
    work_carry[k] = v
  scan_monitor_headers(lines, work_carry)

  (
    d_time,
    d_host,
    d_jid,
    d_type,
    d_dev,
    d_event,
    d_unit,
    d_value,
    d_delta,
    carry_raw_out,
    d_bridge_dt,
  ) = apply_counter_deltas_cols(
    stats_time,
    stats_host,
    stats_jid,
    stats_type,
    stats_dev,
    stats_event,
    stats_unit,
    stats_value,
    stats_wid,
    stats_mult,
    work_carry,
  )

  collapse_v, restore, bridge, sentinel_marks = sentinel_collapse_prep(
    d_time,
    d_host,
    d_jid,
    d_type,
    d_dev,
    d_event,
    d_unit,
    d_value,
    d_delta,
    d_bridge_dt,
    carry_arc,
  )
  for m in sentinel_marks:
    marks.append(m)
  (
    c_time,
    c_host,
    c_jid,
    c_type,
    c_dev,
    c_event,
    c_unit,
    c_value,
    c_delta,
  ) = collapse_full_cols(
    d_time,
    d_host,
    d_jid,
    d_type,
    d_dev,
    d_event,
    d_unit,
    collapse_v,
    d_delta,
  )
  fix: dict[tuple[str, str, str, str, str, float], list[float]] = {}
  if len(restore) > 0 or len(bridge) > 0:
    (
      (c_time, c_host, c_jid, c_type, c_dev, c_event, c_unit, c_value, c_delta),
      fix,
    ) = sentinel_restore_cols(
      c_time,
      c_host,
      c_jid,
      c_type,
      c_dev,
      c_event,
      c_unit,
      c_value,
      c_delta,
      restore,
      bridge,
    )

  (
    a_time,
    a_host,
    a_jid,
    a_type,
    a_dev,
    a_event,
    a_unit,
    a_value,
    a_delta,
    a_arc,
    carry_arc_out,
  ) = apply_arc_cols(
    c_time,
    c_host,
    c_jid,
    c_type,
    c_dev,
    c_event,
    c_unit,
    c_value,
    c_delta,
    carry_arc,
  )
  a_delta, a_arc = sentinel_bridge_arc_cols(
    a_time, a_host, a_type, a_dev, a_event, a_unit, a_delta, a_arc, fix
  )

  finished = (
    a_time,
    a_host,
    a_jid,
    a_type,
    a_dev,
    a_event,
    a_unit,
    a_value,
    a_delta,
    a_arc,
  )
  proc = (proc_jid, proc_host, proc_proc, proc_fields, proc_device)
  finished, proc = finalize_copy_unique_emit(finished, proc)
  finished, proc = normalize_copy_unique_scalars(finished, proc)
  return (finished, proc, carry_raw_out, carry_arc_out, marks, len(stats_time))


def feed_and_collapse(lines: list[str]) -> tuple[int, int, float, float]:
  """Feed lines then plain-host collapse; return fingerprint + collapse rows."""
  (
    stats_time,
    stats_host,
    _stats_jid,
    stats_type,
    _stats_dev,
    stats_event,
    stats_value,
    _stats_wid,
    _stats_mult,
    stats_unit,
    proc_count,
  ) = feed_lines(lines, 0)
  gpu = GPU_TYPES
  oh, _ot, _oe, _ou, _oti, vals = collapse_plain_host_sum(
    stats_host,
    stats_type,
    stats_event,
    stats_unit,
    stats_time,
    stats_value,
    gpu,
  )
  value_sum = 0.0
  for v in vals:
    value_sum += float(v)
  return (len(stats_time), len(oh), float(value_sum), float(proc_count))


def feed_and_collapse_fingerprint(
  lines: list[str],
) -> tuple[int, int, float, float]:
  """Fingerprint API used by sync_timedb_codon_bridge / CPython tests."""
  return feed_and_collapse(lines)


def stats_file_decode_line(raw) -> str:
  """Decode one stats file line as ASCII (replace errors)."""
  if type(raw) is str:
    return raw
  return raw.decode("ascii", "replace")


def stats_file_line_starts_time_sample(
  line: str, line_index: int, start_idx: int
) -> bool:
  """True when ``line`` begins a new time sample (digit-led, past emission gate)."""
  if not line:
    return False
  stripped = line.lstrip()
  if not stripped:
    return False
  if not stripped[0].isdigit():
    return False
  return line_index >= start_idx


def main():
  """Optional ``codon run`` bench entry (experiments; not imported by pyext bake)."""
  import time

  lines = build_e6_payload_lines(N_SAMPLES)
  warm = feed_and_collapse(lines)
  print(
    "codon_feed_collapse_fingerprint raw_stats="
    + str(warm[0])
    + " collapsed_rows="
    + str(warm[1])
    + " collapsed_value_sum="
    + str(warm[2])
    + " proc_rows="
    + str(warm[3]),
  )

  t0 = time.perf_counter()
  for _ in range(REPS):
    _ = feed_and_collapse(lines)
  elapsed = time.perf_counter() - t0
  print(
    "codon_feed_collapse_elapsed_s="
    + str(elapsed)
    + " reps="
    + str(REPS)
    + " samples="
    + str(N_SAMPLES),
  )


# pyext must not auto-run main (Codon may treat ``__main__`` as true at import).
# Bench scripts sed this to True before ``codon run``.
RUN_MAIN = False
if RUN_MAIN:
  main()
