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
from typing import Any

import numpy as np

N_SAMPLES = 400
REPS = 80

GPU_TYPES = ["nvidia_gpu", "amd_gpu", "intel_gpu"]

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
    if ele.startswith("W="):
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
  return "j:" + jid


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
  m = len(vals)
  use = n if n < m else m
  for i in range(use):
    stats_time.append(tag_time)
    stats_host.append(tag_host)
    stats_jid.append(tag_jid)
    stats_type.append(typ)
    stats_dev.append(dev)
    stats_event.append(events[i])
    # Pre-mult raw counter (match IncrementalStatsParser); mult applies once on delta.
    stats_value.append(float(vals[i]))
    stats_wid.append(wids[i])
    stats_mult.append(mults[i])
    stats_unit.append(units[i])


def _merge_proc(
  jid: str,
  host: str,
  proc: str,
  device: str,
  fields: dict[str, int],
  proc_jid: list[str],
  proc_host: list[str],
  proc_proc: list[str],
  proc_device: list[str],
  proc_fields: list[dict[str, int]],
  proc_by_key: dict[str, int],
) -> None:
  """Online ``(jid, host, proc)`` merge: peaks max, other fields last-write."""
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
    if (
      metric == "vm_peak"
      or metric == "vm_hwm"
      or metric == "vm_stk"
      or metric == "vm_exe"
      or metric == "vm_lib"
    ):
      if metric not in old or nv > old[metric]:
        old[metric] = nv
    else:
      old[metric] = nv


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
  """
  excl: set[str] = set()
  for et in exclude_types:
    excl.add(et)
  insert = False
  has_tags = False
  tag_time = 0.0
  tag_jid = ""
  tag_host = ""
  arm_shared = False
  sample_shared = False
  schema: dict[str, list[str]] = {}
  schema_fast: dict[str, list[str]] = {}
  schema_compiled: dict[str, SchemaCols] = {}
  schema_fast_compiled: dict[str, SchemaCols] = {}
  schema_bare: dict[str, list[str]] = {}
  schema_fast_bare: dict[str, list[str]] = {}
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

  for s in lines:
    if not s:
      continue
    if s[0] == "%":
      if len(s) >= 5 and s[0:5] == "%jobs":
        jids = _parse_jobs_jids(s)
        if jids:
          arm_shared = True
          jids_csv = ",".join(jids)
          rec_j: dict[str, str] = {
            "kind": "jobs",
            "jids": jids_csv,
            "jid": "",
            "cpus": "",
            "gpus": "",
            "cg": "",
            "time": "",
            "host": "",
          }
          if has_tags:
            rec_j["time"] = str(tag_time)
            rec_j["host"] = tag_host
            marks.append(rec_j)
          else:
            pending_marks.append(rec_j)
            marks.append(rec_j)
      elif len(s) >= 9 and s[0:9] == "%affinity":
        aff = _parse_affinity_mark(s)
        if aff["jid"] != "":
          rec_a: dict[str, str] = {
            "kind": "affinity",
            "jids": "",
            "jid": aff["jid"],
            "cpus": aff["cpus"],
            "gpus": aff["gpus"],
            "cg": aff["cg"],
            "time": "",
            "host": "",
          }
          if has_tags:
            rec_a["time"] = str(tag_time)
            rec_a["host"] = tag_host
            marks.append(rec_a)
          else:
            pending_marks.append(rec_a)
            marks.append(rec_a)
      continue
    if insert and has_tags and (s[0].isalpha() or s[0] == "_"):
      parts = s.split()
      if len(parts) < 2:
        continue
      typ = parts[0]
      if typ in excl:
        continue
      rest = parts[1:]
      if not rest:
        continue
      dev = rest[0]
      vals = rest[1:]
      if typ == "host_proc":
        if not vals:
          continue
        tier = ""
        if vals[0] == "@fast" or vals[0] == "@full":
          tier = vals[0]
          vals = vals[1:]
        bare_keys = schema_bare.get("host_proc", HOST_PROC_KEYS)
        if tier == "@fast":
          bare_keys = schema_fast_bare.get("host_proc", bare_keys)
        fields: dict[str, int] = {}
        for bi in range(len(bare_keys)):
          if bi >= len(vals):
            break
          bare = bare_keys[bi]
          try:
            parsed = int(float(vals[bi]))
          except Exception:
            continue
          fields[bare] = parsed
        proc_name = dev
        slash = dev.find("/")
        if slash >= 0:
          proc_name = dev[:slash]
        _merge_proc(
          tag_jid,
          tag_host,
          proc_name,
          dev,
          fields,
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
      tier_marker = ""
      if vals and (vals[0] == "@fast" or vals[0] == "@full"):
        tier_marker = vals[0]
        vals = vals[1:]
      if not has_tags:
        continue
      if tier_marker == "@fast":
        compiled = schema_fast_compiled[typ]
      else:
        compiled = schema_compiled[typ]
      stamped = _shared_row_dev(dev, typ, tag_jid, sample_shared)
      _append_stats(
        tag_time,
        tag_host,
        tag_jid,
        typ,
        stamped,
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

    if start_idx >= 0 and s[0].isdigit():
      parts = s.split()
      if len(parts) < 3:
        continue
      insert = True
      tag_time = float(parts[0])
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


def _is_gpu_type(typ: str) -> bool:
  return any(typ == g for g in GPU_TYPES)


def _is_finite_val(v: float) -> bool:
  if math.isnan(v):
    return False
  return not (v == float("inf") or v == float("-inf"))


def _cluster_mean_sum(values: list[float], gap_threshold: float) -> float:
  """Port of ``_cluster_mean_sum_sorted``: mean-of-cluster, summed across clusters."""
  finite: list[float] = []
  for v in values:
    if _is_finite_val(v) and v < DCGM_BLANK:
      finite.append(v)
  if not finite:
    return float("nan")
  finite.sort()
  n = len(finite)
  if n == 1:
    return finite[0]
  total = 0.0
  cluster_sum = finite[0]
  cluster_count = 1
  for i in range(1, n):
    if finite[i] - finite[i - 1] > gap_threshold:
      total += cluster_sum / float(cluster_count)
      cluster_sum = finite[i]
      cluster_count = 1
    else:
      cluster_sum += finite[i]
      cluster_count += 1
  total += cluster_sum / float(cluster_count)
  return total


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


def _sum_min_count1(values: list[float]) -> float:
  """Sum ignoring NaN; all-NaN group stays NaN (pandas ``sum(min_count=1)``)."""
  total = 0.0
  any_valid = False
  for v in values:
    if not math.isnan(v):
      total += v
      any_valid = True
  if not any_valid:
    return float("nan")
  return total


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
]:
  """Columnar port of ``_apply_counter_deltas``.

  Groups by host/type/dev/event, sorts by (key, time), computes
  ``value.diff()`` per group (first sample ``NaN`` unless ``carry_raw``
  has a hit), adds one modulus only when the previous sample sat in the
  top half of the counter and the drop is smaller than one modulus, otherwise
  a finite negative delta becomes NaN, then
  scales by ``mult`` via Codon-NumPy contiguous buffers when ``n>0``.
  ``wid``/``mult`` columns are dropped from the output. ``carry_raw`` is
  updated (as a copy) with the last raw ``(value, wid, mult, time)`` row
  per group.
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
  raw_deltas: list[float] = []
  wid_f: list[float] = []
  mult_f: list[float] = []
  v_arr = np.array(out_value)
  for j in range(n):
    idx = order[j]
    key = keys[idx]
    if j > 0 and keys[order[j - 1]] == key:
      delta = float(v_arr[j]) - float(v_arr[j - 1])
    else:
      delta = nan
      if key in carry_raw:
        carried = carry_raw[key]
        prev_val = carried[0]
        if not math.isnan(prev_val):
          delta = float(v_arr[j]) - prev_val
    raw_deltas.append(delta)
    wid_f.append(float(wids[idx]))
    mult_f.append(mults[idx])
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

  for j in range(n):
    idx = order[j]
    key = keys[idx]
    is_last = (j == n - 1) or (keys[order[j + 1]] != key)
    if is_last:
      new_carry[key] = [
        values[idx],
        float(wids[idx]),
        mults[idx],
        times[idx],
      ]

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

  GPU types keep hardware ``dev`` in the group key and ``j:`` shared-node
  stamps stay distinct the same way. Every other (whole-node) row has ``dev``
  blanked first, so CPU/CCM/host rows sum across devices to ``dev=''``.
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

  devs = [
    devs[i] if _is_gpu_type(types[i]) or devs[i].startswith("j:") else ""
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


HOST_COPY_CONFLICT_COLS = ["time", "host", "type", "event", "dev"]

HOST_PROC_PEAK_KEYS = (
  "vm_peak",
  "vm_hwm",
  "vm_stk",
  "vm_exe",
  "vm_lib",
)


def _float_is_nan(value) -> bool:
  return bool(type(value) is float and math.isnan(value))


def _host_conflict_cell(col: str, value: Any) -> Any:
  """Conflict-key cell for DB ON CONFLICT (time, host, type, event, dev)."""
  if col == "time":
    if value is None:
      return None
    if type(value) is float:
      if math.isnan(value):
        return None
      return value
    if type(value) is int:
      return float(value)
    return None
  if col == "dev":
    if value is None or _float_is_nan(value):
      return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
      return ""
    return text
  return value


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


def _nullable_int_max(left: Any, right: Any) -> Any:
  if type(left) is int and type(right) is int:
    return left if left >= right else right
  if type(left) is int:
    return left
  if type(right) is int:
    return right
  return right


def merge_proc_row_dicts_codon(
  earlier: dict[str, int],
  later: dict[str, int],
) -> dict[str, int]:
  """Peak-merge two host_proc rows (``dedupe_proc_stats_peak_merge`` semantics)."""
  for key, val in later.items():
    if type(val) is not int:
      continue
    if key in HOST_PROC_PEAK_KEYS:
      prev = earlier.get(key)
      if type(prev) is int and prev >= val:
        earlier[key] = prev
      else:
        earlier[key] = val
    else:
      earlier[key] = val
  return earlier


def _copy_dev_text(value: str) -> str:
  """Blank device for COPY (matches ``_copy_dev_text`` in host insert)."""
  if value is None:
    return ""
  text = value.strip()
  if not text or text == "nan" or text == "NaN":
    return ""
  return text


def _host_emit_copy_cell(col: str, value: Any) -> Any:
  """Per-cell COPY coercion (matches ``_host_frame_cell`` except ``time`` stays unix float)."""
  if col == "time":
    if value is None or _float_is_nan(value):
      return None
    return float(value)
  if col == "jid":
    if value is None:
      return None
    text = str(value)
    if text == "-":
      return None
    return text
  if col == "dev":
    return _copy_dev_text(value)
  if col in ("value", "delta", "arc"):
    if value is None or _float_is_nan(value):
      return None
    return float(value)
  return value


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


def _proc_emit_copy_scalar(value: Any) -> Any:
  """Metric cell for proc COPY (matches ``_proc_copy_scalar``)."""
  if value is None:
    return None
  if type(value) is int:
    return value
  if isinstance(value, float):
    if math.isnan(value):
      return None
    return int(value)
  return None


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


def feed_delta_collapse_arc(
  lines: list[str],
  carry_raw: dict[str, list[float]],
  carry_arc: dict[str, float],
  start_idx: int = 0,
  exclude_types: list[str] = [],  # noqa: B006 (Codon: never mutated)
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
]:
  """Full feed -> delta -> collapse -> arc pipeline for one incremental flush.

  Mirrors ``compute_deltas_and_arc_chunk`` wired to ``IncrementalStatsParser``
  feed + ``DeltaCarryState``. Returns ``(finished_cols, proc_cols,
  carry_raw_out, carry_arc_out, marks)`` where ``finished_cols`` are parallel
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
  ) = feed_lines_full(lines, start_idx, exclude_types)

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
    carry_raw,
  )

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
    d_value,
    d_delta,
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
  return (finished, proc, carry_raw_out, carry_arc_out, marks)


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
  gpu = ["nvidia_gpu", "amd_gpu", "intel_gpu"]
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
