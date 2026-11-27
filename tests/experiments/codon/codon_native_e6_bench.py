"""Single-file Codon compile unit: E6 feed-only microbench.

Codon 0.18 cannot import sibling modules or use heterogeneous dict-of-lists
the way CPython ``parse_kernel.FeedKernel`` does. This file is the native
arm: typed parallel lists + Dict[str, List[str]] schema only.
"""

from typing import Dict, List, Optional, Tuple

N_SAMPLES = 400
REPS = 80

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


def schema_key_basename(token: str) -> str:
  if not token:
    return ""
  return token.split(",", 1)[0]


def _schema_token_is_slow_tier(token: str) -> bool:
  parts = token.split(",")
  if len(parts) < 2:
    return False
  for ele in parts[1:]:
    if "R=S" in ele:
      return True
  return False


def _fast_schema_keys(full_events: List[str]) -> List[str]:
  out: List[str] = []
  for t in full_events:
    if not _schema_token_is_slow_tier(t):
      out.append(t)
  return out


def _compile_schema_token(token: str) -> Tuple[str, int, float, str]:
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
      letters = "".join(c for c in rest if c.isalpha())
      if letters:
        unit = letters
  return eve_parts[0], width, mult, unit


class CompiledSchema:
  events: List[str]
  wids: List[int]
  mults: List[float]
  units: List[str]

  def __init__(self):
    self.events = []
    self.wids = []
    self.mults = []
    self.units = []


def _compile_schema_tokens(tokens: List[str]) -> CompiledSchema:
  out = CompiledSchema()
  for token in tokens:
    event, wid, mult, unit = _compile_schema_token(token)
    out.events.append(event)
    out.wids.append(wid)
    out.mults.append(mult)
    out.units.append(unit)
  return out


class ProcRow:
  jid: str
  host: str
  proc: str
  time: float
  device: str
  vm_peak: Optional[int]
  vm_hwm: Optional[int]
  vm_stk: Optional[int]
  vm_exe: Optional[int]
  vm_lib: Optional[int]
  threads: Optional[int]

  def __init__(
      self,
      jid: str,
      host: str,
      proc: str,
      time: float,
      device: str,
  ):
    self.jid = jid
    self.host = host
    self.proc = proc
    self.time = time
    self.device = device
    self.vm_peak = None
    self.vm_hwm = None
    self.vm_stk = None
    self.vm_exe = None
    self.vm_lib = None
    self.threads = None


def _nullable_int_max(left: Optional[int], right: Optional[int]) -> Optional[int]:
  if left is None:
    return right
  if right is None:
    return left
  if left >= right:
    return left
  return right


def build_e6_payload_lines(n_samples: int = 400) -> List[str]:
  lines: List[str] = [
      "1709123456 job1 cn001\n",
      "!cpu user,W=48 sys,W=48 idle,W=48\n",
      "!host_proc vm_peak,U=kB rss_peak,U=kB threads\n",
  ]
  t0 = 1709123456
  for i in range(n_samples):
    t = t0 + i
    lines.append(str(t) + " job1 cn001\n")
    lines.append(
        "cpu 0 @full "
        + str(10 + i)
        + " "
        + str(20 + i)
        + " "
        + str(30 + i)
        + "\n",
    )
    lines.append(
        "host_proc bash/1/0/0 @full "
        + str(100 + i)
        + " "
        + str(50 + i)
        + " "
        + str(4 + (i % 8))
        + "\n",
    )
  return lines


class FeedKernel:
  """Codon-native feed-only parser for the E6 corpus shape."""

  start_idx: int
  _line_index: int
  insert: bool
  schema: Dict[str, List[str]]
  schema_fast: Dict[str, List[str]]
  schema_compiled: Dict[str, CompiledSchema]
  schema_fast_compiled: Dict[str, CompiledSchema]
  schema_bare: Dict[str, List[str]]
  schema_fast_bare: Dict[str, List[str]]
  tag_time: float
  tag_host: str
  tag_jid: str
  has_tags: bool
  stats_time: List[float]
  stats_host: List[str]
  stats_jid: List[str]
  stats_type: List[str]
  stats_dev: List[str]
  stats_event: List[str]
  stats_value: List[float]
  stats_wid: List[int]
  stats_mult: List[float]
  stats_unit: List[str]
  proc_rows: List[ProcRow]

  def __init__(self, start_idx: int = 0):
    self.start_idx = start_idx
    self._line_index = 0
    self.insert = False
    self.schema = {}
    self.schema_fast = {}
    self.schema_compiled = {}
    self.schema_fast_compiled = {}
    self.schema_bare = {}
    self.schema_fast_bare = {}
    self.tag_time = 0.0
    self.tag_host = ""
    self.tag_jid = ""
    self.has_tags = False
    self.stats_time = []
    self.stats_host = []
    self.stats_jid = []
    self.stats_type = []
    self.stats_dev = []
    self.stats_event = []
    self.stats_value = []
    self.stats_wid = []
    self.stats_mult = []
    self.stats_unit = []
    self.proc_rows = []

  def _compiled_schema_for(self, typ: str, fast: bool) -> CompiledSchema:
    store = self.schema_fast_compiled if fast else self.schema_compiled
    if typ in store:
      return store[typ]
    schema_keys = self.schema_fast[typ] if fast and typ in self.schema_fast else (
        self.schema[typ] if typ in self.schema else []
    )
    if not schema_keys:
      empty = CompiledSchema()
      store[typ] = empty
      return empty
    compiled = _compile_schema_tokens(schema_keys)
    store[typ] = compiled
    return compiled

  def _append_stats(
      self,
      typ: str,
      dev: str,
      compiled: CompiledSchema,
      vals: List[str],
  ):
    n = len(compiled.events)
    if len(vals) != n:
      return
    for i in range(n):
      self.stats_time.append(self.tag_time)
      self.stats_host.append(self.tag_host)
      self.stats_jid.append(self.tag_jid)
      self.stats_type.append(typ)
      self.stats_dev.append(dev)
      self.stats_event.append(compiled.events[i])
      self.stats_wid.append(compiled.wids[i])
      self.stats_mult.append(compiled.mults[i])
      self.stats_unit.append(compiled.units[i])
      self.stats_value.append(float(vals[i]))

  def _merge_proc(self, row: ProcRow):
    for existing in self.proc_rows:
      if (
          existing.jid == row.jid
          and existing.host == row.host
          and existing.proc == row.proc
      ):
        existing.vm_peak = _nullable_int_max(existing.vm_peak, row.vm_peak)
        existing.vm_hwm = _nullable_int_max(existing.vm_hwm, row.vm_hwm)
        existing.vm_stk = _nullable_int_max(existing.vm_stk, row.vm_stk)
        existing.vm_exe = _nullable_int_max(existing.vm_exe, row.vm_exe)
        existing.vm_lib = _nullable_int_max(existing.vm_lib, row.vm_lib)
        existing.threads = row.threads
        existing.time = row.time
        existing.device = row.device
        return
    self.proc_rows.append(row)

  def feed_line(self, line: str):
    i = self._line_index
    self._line_index += 1
    if not line:
      return
    s = line.lstrip()
    if not s:
      return

    if s[0].isalpha() and self.insert:
      parts = s.split()
      if len(parts) < 3:
        return
      typ = parts[0]
      dev = parts[1]
      vals = parts[2:]

      if typ == "proc" or typ == "host_proc":
        proc_name = dev.split("/", 1)[0]
        full_schema_keys: List[str] = []
        if typ in self.schema:
          full_schema_keys = self.schema[typ]
        elif "host_proc" in self.schema:
          full_schema_keys = self.schema["host_proc"]
        elif "proc" in self.schema:
          full_schema_keys = self.schema["proc"]
        else:
          full_schema_keys = list(HOST_PROC_KEYS)
        tier_marker = ""
        if vals and (vals[0] == "@fast" or vals[0] == "@full"):
          tier_marker = vals[0]
          vals = vals[1:]
        use_fast = tier_marker == "@fast"
        if use_fast:
          if typ in self.schema_fast:
            schema_keys = self.schema_fast[typ]
          else:
            schema_keys = _fast_schema_keys(full_schema_keys)
        else:
          schema_keys = full_schema_keys
        if not self.has_tags:
          return
        row = ProcRow(
            self.tag_jid,
            self.tag_host,
            proc_name,
            self.tag_time,
            dev,
        )
        bare_keys: List[str] = []
        for tok in schema_keys:
          bare_keys.append(schema_key_basename(tok))
        for idx in range(len(bare_keys)):
          if idx >= len(vals):
            break
          bare = bare_keys[idx]
          raw = vals[idx]
          parsed: Optional[int] = None
          try:
            parsed = int(raw)
          except ValueError:
            try:
              parsed = int(float(raw))
            except ValueError:
              parsed = None
          if bare == "vm_peak":
            row.vm_peak = parsed
          elif bare == "vm_hwm":
            row.vm_hwm = parsed
          elif bare == "vm_stk":
            row.vm_stk = parsed
          elif bare == "vm_exe":
            row.vm_exe = parsed
          elif bare == "vm_lib":
            row.vm_lib = parsed
          elif bare == "threads":
            row.threads = parsed
        self._merge_proc(row)
        return

      if typ not in self.schema:
        return
      tier_marker = ""
      if vals and (vals[0] == "@fast" or vals[0] == "@full"):
        tier_marker = vals[0]
        vals = vals[1:]
      if not self.has_tags:
        return
      compiled = self._compiled_schema_for(typ, tier_marker == "@fast")
      self._append_stats(typ, dev, compiled, vals)
      return

    if i >= self.start_idx and s[0].isdigit():
      parts = s.split()
      if len(parts) < 3:
        return
      self.insert = True
      self.tag_time = float(parts[0])
      self.tag_jid = parts[1]
      self.tag_host = parts[2]
      self.has_tags = True
    elif s[0] == "!":
      pieces = s.split()
      if len(pieces) < 2:
        return
      typ = pieces[0][1:]
      ev_list = pieces[1:]
      self.schema[typ] = ev_list
      self.schema_fast[typ] = _fast_schema_keys(ev_list)
      self.schema_compiled[typ] = _compile_schema_tokens(ev_list)
      self.schema_fast_compiled[typ] = _compile_schema_tokens(
          self.schema_fast[typ],
      )
      bare: List[str] = []
      for tok in ev_list:
        bare.append(schema_key_basename(tok))
      self.schema_bare[typ] = bare
      bare_fast: List[str] = []
      for tok in self.schema_fast[typ]:
        bare_fast.append(schema_key_basename(tok))
      self.schema_fast_bare[typ] = bare_fast

  def feed_lines(self, lines: List[str]):
    for line in lines:
      self.feed_line(line)

  def stats_len(self) -> int:
    return len(self.stats_time)

  def fingerprint(self) -> Tuple[int, int, float, int]:
    value_sum = 0.0
    for v in self.stats_value:
      value_sum += v
    vm_peak_sum = 0
    for row in self.proc_rows:
      if row.vm_peak is not None:
        vm_peak_sum += row.vm_peak
    return (self.stats_len(), len(self.proc_rows), value_sum, vm_peak_sum)


def main():
  import time

  lines = build_e6_payload_lines(N_SAMPLES)
  warm = FeedKernel(0)
  warm.feed_lines(lines)
  fp = warm.fingerprint()
  print(
      "codon_native_fingerprint stats_rows="
      + str(fp[0])
      + " proc_rows="
      + str(fp[1])
      + " stats_value_sum="
      + str(fp[2])
      + " proc_vm_peak_sum="
      + str(fp[3]),
  )

  t0 = time.perf_counter()
  for _ in range(REPS):
    k = FeedKernel(0)
    k.feed_lines(lines)
    _ = k.stats_len()
  elapsed = time.perf_counter() - t0
  print(
      "codon_native_elapsed_s="
      + str(elapsed)
      + " reps="
      + str(REPS)
      + " samples="
      + str(N_SAMPLES),
  )


if __name__ == "__main__":
  main()
