"""
Bounded feed-only parse kernel (Codon compile target).

Mirrors IncrementalStatsParser.feed_line for @full cpu/host_proc samples
and ``!`` schema lines used by the E6 corpus and host_proc @full fixture.
No Django, pandas, or numpy.
"""

HOST_PROC_KEYS = (
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
)
HOST_PROC_KEY_SET = set(HOST_PROC_KEYS)
HOST_PROC_PEAK_KEYS = set(["vm_peak", "vm_hwm", "vm_stk", "vm_exe", "vm_lib"])
TIER_MARKERS = set(["@fast", "@full"])
SLOW_TIER_OPT = "R=S"
STATS_COL_NAMES = (
    "time",
    "host",
    "jid",
    "type",
    "dev",
    "event",
    "value",
    "wid",
    "mult",
    "unit",
)


def schema_key_basename(token):
  if not token:
    return ""
  return token.split(",", 1)[0]


def _schema_token_is_slow_tier(token):
  parts = token.split(",")
  if len(parts) < 2:
    return False
  for ele in parts[1:]:
    if SLOW_TIER_OPT in ele:
      return True
  return False


def _fast_schema_keys(full_events):
  return [t for t in full_events if not _schema_token_is_slow_tier(t)]


def _compile_schema_token(token):
  eve_parts = token.split(",")
  width = 64
  mult = 1.0
  unit = "#"
  for ele in eve_parts[1:]:
    if ele.startswith("W="):
      width = int(ele[3:])
    if ele.startswith("U="):
      ele = ele[2:]
      digits = "".join(c for c in ele if c.isdigit())
      if digits:
        mult = float(digits)
      letters = "".join(c for c in ele if c.isalpha())
      if letters:
        unit = letters
  return eve_parts[0], width, mult, unit


def _compile_schema_tokens(tokens):
  out = {"events": [], "wids": [], "mults": [], "units": []}
  for token in tokens:
    event, wid, mult, unit = _compile_schema_token(token)
    out["events"].append(event)
    out["wids"].append(wid)
    out["mults"].append(mult)
    out["units"].append(unit)
  return out


def _compile_schema_bare_names(tokens):
  return [schema_key_basename(t) for t in tokens]


def _empty_stats_columns():
  return {name: [] for name in STATS_COL_NAMES}


def _nullable_int_max(left, right):
  if type(left) is int and type(right) is int:
    return left if left >= right else right
  left_ok = None
  right_ok = None
  try:
    left_ok = None if left is None else int(left)
  except (TypeError, ValueError):
    left_ok = None
  try:
    right_ok = None if right is None else int(right)
  except (TypeError, ValueError):
    right_ok = None
  if left_ok is None:
    return right_ok
  if right_ok is None:
    return left_ok
  return max(left_ok, right_ok)


def merge_proc_row_dicts(earlier, later):
  for key, value in later.items():
    if key not in HOST_PROC_PEAK_KEYS:
      earlier[key] = value
  for key in HOST_PROC_PEAK_KEYS:
    earlier[key] = _nullable_int_max(earlier.get(key), later.get(key))
  return earlier


def _append_compiled_stats_columns(cols, time, host, jid, typ, dev, compiled, vals):
  events = compiled["events"]
  n = len(events)
  if len(vals) != n:
    return
  cols["time"].extend([time] * n)
  cols["host"].extend([host] * n)
  cols["jid"].extend([jid] * n)
  cols["type"].extend([typ] * n)
  cols["dev"].extend([dev] * n)
  cols["event"].extend(events)
  cols["wid"].extend(compiled["wids"])
  cols["mult"].extend(compiled["mults"])
  cols["unit"].extend(compiled["units"])
  cols["value"].extend([float(v) for v in vals])


def _digit_line_identity(s):
  parts = s.split()
  if len(parts) < 3:
    return None
  return (parts[0], parts[1], parts[2])


class FeedKernel:
  """Stateful feed-only parser subset for Codon microbench."""

  start_idx: int
  _line_index: int
  insert: bool

  def __init__(self, start_idx: int = 0):
    self.start_idx = start_idx
    self._line_index = 0
    self.schema = {}
    self.schema_fast = {}
    self.schema_compiled = {}
    self.schema_fast_compiled = {}
    self.schema_bare = {}
    self.schema_fast_bare = {}
    self._stats_cols = _empty_stats_columns()
    self._proc_by_key = {}
    self.insert = False
    self.line_ctx = {"tags": None, "tags2": None}

  def _merge_proc_row_online(self, row):
    key = (row.get("jid"), row.get("host"), row.get("proc"))
    existing = self._proc_by_key.get(key)
    if existing is None:
      self._proc_by_key[key] = row
    else:
      merge_proc_row_dicts(existing, row)

  def _compiled_schema_for(self, typ, fast):
    store = self.schema_fast_compiled if fast else self.schema_compiled
    if typ in store:
      return store[typ]
    schema_keys = self.schema_fast.get(typ) if fast else self.schema.get(typ)
    if not schema_keys:
      empty = {"events": [], "wids": [], "mults": [], "units": []}
      store[typ] = empty
      return empty
    compiled = _compile_schema_tokens(list(schema_keys))
    store[typ] = compiled
    return compiled

  def _proc_bare_keys_for(self, typ, schema_keys, fast):
    bare_store = self.schema_fast_bare if fast else self.schema_bare
    if typ in bare_store:
      return bare_store[typ]
    bare = _compile_schema_bare_names(schema_keys)
    bare_store[typ] = bare
    return bare

  def feed_line(self, line):
    i = self._line_index
    self._line_index += 1
    if not line:
      return
    s = line.lstrip()
    if not s:
      return

    if s[0].isalpha() and self.insert:
      parts = s.split(maxsplit=2)
      if len(parts) < 3:
        return
      typ, dev, val_str = parts[0], parts[1], parts[2]
      vals = val_str.split()

      if typ in ("proc", "host_proc"):
        proc_name = dev.split("/", 1)[0]
        full_schema_keys = (
            self.schema.get(typ)
            or self.schema.get("host_proc")
            or self.schema.get("proc")
            or list(HOST_PROC_KEYS)
        )
        tier_marker = None
        if vals and vals[0] in TIER_MARKERS:
          tier_marker = vals[0]
          vals = vals[1:]
        use_fast = tier_marker == "@fast"
        if use_fast:
          schema_keys = self.schema_fast.get(typ) or _fast_schema_keys(
              list(full_schema_keys),
          )
        else:
          schema_keys = full_schema_keys
        bare_keys = self._proc_bare_keys_for(typ, list(schema_keys), use_fast)
        tags2 = self.line_ctx["tags2"]
        if tags2 is None:
          return
        row = {
            "time": tags2["time"],
            "host": tags2["host"],
            "jid": tags2["jid"],
            "proc": proc_name,
            "device": dev,
        }
        for idx, bare in enumerate(bare_keys):
          if idx >= len(vals):
            break
          if bare not in HOST_PROC_KEY_SET:
            continue
          raw = vals[idx]
          try:
            row[bare] = int(raw)
          except (TypeError, ValueError):
            try:
              row[bare] = int(float(raw))
            except (TypeError, ValueError):
              row[bare] = None
        self._merge_proc_row_online(row)
        return

      if typ not in self.schema:
        return

      tier_marker = None
      if vals and vals[0] in TIER_MARKERS:
        tier_marker = vals[0]
        vals = vals[1:]

      if tier_marker == "@fast":
        compiled = self._compiled_schema_for(typ, True)
      else:
        compiled = self._compiled_schema_for(typ, False)

      tags = self.line_ctx["tags"]
      if tags is None:
        return
      _append_compiled_stats_columns(
          self._stats_cols,
          float(tags["time"]),
          tags["host"],
          tags["jid"],
          typ,
          dev,
          compiled,
          vals,
      )
      return

    if i >= self.start_idx and s[0].isdigit():
      parsed = _digit_line_identity(s)
      if parsed is None:
        return
      t, jid, host = parsed
      self.insert = True
      ctx = {"time": float(t), "host": host, "jid": jid}
      self.line_ctx["tags"] = ctx
      self.line_ctx["tags2"] = ctx
    elif s[0] == "!":
      label, events = s.split(maxsplit=1)
      typ = label[1:]
      ev_list = events.split()
      self.schema[typ] = ev_list
      self.schema_fast[typ] = _fast_schema_keys(ev_list)
      self.schema_compiled[typ] = _compile_schema_tokens(ev_list)
      self.schema_fast_compiled[typ] = _compile_schema_tokens(
          self.schema_fast[typ],
      )
      self.schema_bare[typ] = _compile_schema_bare_names(ev_list)
      self.schema_fast_bare[typ] = _compile_schema_bare_names(
          self.schema_fast[typ],
      )

  def feed_lines(self, lines):
    for line in lines:
      self.feed_line(line)

  @property
  def stats_len(self):
    return len(self._stats_cols["time"])

  def proc_stats(self):
    return list(self._proc_by_key.values())


def fingerprint_from_kernel(kernel):
  """Stable digest for parity vs IncrementalStatsParser."""
  stats_n = kernel.stats_len
  procs = kernel.proc_stats()
  value_sum = 0.0
  for v in kernel._stats_cols["value"]:
    value_sum += float(v)
  vm_peak_sum = 0
  for row in procs:
    vp = row.get("vm_peak")
    if type(vp) is int:
      vm_peak_sum += vp
  return {
      "stats_rows": stats_n,
      "proc_rows": len(procs),
      "stats_value_sum": value_sum,
      "proc_vm_peak_sum": vm_peak_sum,
  }


def feed_fingerprint(lines):
  k = FeedKernel(0)
  k.feed_lines(lines)
  return fingerprint_from_kernel(k)

