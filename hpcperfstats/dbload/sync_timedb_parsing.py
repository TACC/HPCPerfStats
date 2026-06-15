"""Pure parsing helpers for stats files (no Django). Used by sync_timedb and by unit tests."""
import os
import warnings
import numpy as np
import pandas as pd
from pandas import DataFrame, concat, to_datetime

from hpcperfstats.dbload import sync_timedb_parsing_legacy as legacy_parsing
from hpcperfstats.file_locking import LOCK_SUFFIX, file_read_lock_wait
from hpcperfstats.monitor_naming.canonical import (
    DCGM_CPU_POWER_LIMIT_W,
    DCGM_CPU_POWER_UTIL_W,
    HOST_CPU_HW_TYPE,
)
from hpcperfstats.monitor_naming.legacy import LEGACY_HOST_CPU_HW_TYPE
from hpcperfstats.monitor_naming.resolve import schema_needs_legacy_hardware_decode

# Types skipped on ingest (canonical monitor names).
exclude_types = [
    "intel_x86_uncore_cha_skx",
    "host_ps",
    "host_sysv_shm",
    "host_tmpfs",
    "host_vfs",
    # Legacy archives may still use old schema labels.
    "ib",
    "ib_sw",
    "intel_skx_cha",
    "ps",
    "sysv_shm",
    "tmpfs",
    "vfs",
]

# Back-compat re-export for callers/tests that referenced legacy eventmaps.
EVENTMAPS_BY_TYPE = legacy_parsing.EVENTMAPS_BY_TYPE
map_hardware_counter_vals = legacy_parsing.map_hardware_counter_vals

_NVIDIA_GPU_SUM_EVENTS = frozenset({
    "gpu_util",
    "gpu_io_link_total_bytes",
    "mem_util",
    "mem_used_mb",
    "mem_total_mb",
    "gpu_mem_util",
    "gpu_mem_used_mb",
    "gpu_mem_total_mb",
    "fp64_active",
    "fp32_active",
    "fp16_active",
    "sm_active",
    "sm_occupancy",
    "tensor_active",
    "power_usage",
})
_NVIDIA_GPU_MAX_EVENTS = frozenset({
    "module_power_usage",
    "sysio_power_usage",
})
_NVIDIA_GPU_MEAN_EVENTS = frozenset({"temperature"})
_NVIDIA_GPU_OR_EVENTS = frozenset({"clocks_event_reasons"})

_DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS = frozenset({
    DCGM_CPU_POWER_UTIL_W,
    DCGM_CPU_POWER_LIMIT_W,
    "DCGM_CPU_POWER_UTIL_W",
    "DCGM_CPU_POWER_LIMIT_W",
})

_HOST_CPU_HW_TYPES = frozenset({HOST_CPU_HW_TYPE, LEGACY_HOST_CPU_HW_TYPE})

_COLLAPSE_GROUP_COLS = ["host", "type", "event", "unit", "time"]
_COUNTER_GROUP_COLS = ["host", "type", "dev", "event"]
_ARC_GROUP_COLS = ["host", "type", "event"]
_NVIDIA_GROUP_KEY_EVENT_INDEX = _COLLAPSE_GROUP_COLS.index("event")

_SLOW_TIER_OPT = "R=S"
_TIER_MARKERS = frozenset({"@fast", "@full"})


def _schema_token_is_slow_tier(token: str) -> bool:
  """True when a schema entry is marked slow-tier via ,R=S (monitor two-tier collect)."""
  return _SLOW_TIER_OPT in token.split(",")[1:]


def _fast_schema_keys(full_events: list[str]) -> list[str]:
  """Fast-tier schema keys in order (entries without ,R=S)."""
  return [e for e in full_events if not _schema_token_is_slow_tier(e)]


def _zip_schema_vals(schema_keys, vals, typ=None, dev=None):
  """Zip value tokens to schema keys; None when counts disagree (no silent truncation)."""
  if len(vals) != len(schema_keys):
    warnings.warn(
        "stats line value count %d != schema key count %d for type=%s dev=%s"
        % (len(vals), len(schema_keys), typ, dev),
        stacklevel=3,
    )
    return None
  return dict(zip(schema_keys, vals))


def _cluster_mean_sum_sorted(values, gap_threshold):
  v = np.asarray(values, dtype=np.float64)
  v = v[np.isfinite(v)]
  if v.size == 0:
    return float("nan")
  v.sort()
  total = 0.0
  cluster = [float(v[0])]
  for i in range(1, v.size):
    if v[i] - v[i - 1] <= gap_threshold:
      cluster.append(float(v[i]))
    else:
      total += float(np.mean(cluster))
      cluster = [float(v[i])]
  total += float(np.mean(cluster))
  return total


def _collapse_dcg_cpu_power_gauge_group(group):
  vals = group["value"].to_numpy(dtype=np.float64, copy=False)
  dvals = group["delta"].to_numpy(dtype=np.float64, copy=False)
  vtot = _cluster_mean_sum_sorted(vals, 1.0)
  d_gap = 1e-6
  if np.any(np.isfinite(dvals)):
    dabs = np.nanmax(np.abs(dvals[np.isfinite(dvals)]))
    if np.isfinite(dabs) and dabs > 0:
      d_gap = max(1e-9, 0.05 * float(dabs))
  dtot = _cluster_mean_sum_sorted(dvals, d_gap)
  return pd.Series({"value": vtot, "delta": dtot})


def _collapse_nvidia_gpu_group(group):
  key = group.name
  event_name = key[_NVIDIA_GROUP_KEY_EVENT_INDEX] if isinstance(key, tuple) else key
  if event_name in _NVIDIA_GPU_MAX_EVENTS:
    return pd.Series({
        "value": float(group["value"].max()),
        "delta": group["delta"].mean(),
    })
  if event_name in _NVIDIA_GPU_SUM_EVENTS:
    return pd.Series({
        "value": group["value"].sum(min_count=1),
        "delta": group["delta"].sum(min_count=1),
    })
  if event_name in _NVIDIA_GPU_MEAN_EVENTS:
    return pd.Series({
        "value": group["value"].mean(),
        "delta": group["delta"].mean(),
    })
  if event_name in _NVIDIA_GPU_OR_EVENTS:
    acc = 0
    mask64 = (1 << 64) - 1
    for v in group["value"]:
      if pd.notna(v):
        acc |= int(v) & mask64
    return pd.Series({
        "value": float(acc & mask64),
        "delta": group["delta"].sum(min_count=1),
    })
  return pd.Series({
      "value": group["value"].sum(min_count=1),
      "delta": group["delta"].sum(min_count=1),
  })


def _vals_dict_from_line(typ, schema, schema_keys, vals, use_legacy_decode, dev=None):
  if use_legacy_decode:
    decoded = legacy_parsing.decode_counter_line(typ, schema, vals)
    if decoded is None:
      return None
    return decoded
  return _zip_schema_vals(schema_keys, vals, typ=typ, dev=dev)


def _append_stats_rows(stats, rec, vals_dict):
  for eve, val in vals_dict.items():
    eve_parts = eve.split(",")
    width = 64
    mult = 1
    unit = "#"
    for ele in eve_parts[1:]:
      if "W=" in ele:
        width = int(ele.lstrip("W="))
      if "U=" in ele:
        ele = ele.lstrip("U=")
        try:
          mult = float("".join(filter(str.isdigit, ele)))
        except Exception:
          pass
        try:
          unit = "".join(filter(str.isalpha, ele))
        except Exception:
          pass
    stats.append({
        **rec,
        "event": eve_parts[0],
        "value": float(val),
        "wid": width,
        "mult": mult,
        "unit": unit,
    })


def parse_stats_file_path(stats_file):
  parts = stats_file.split("/")
  if len(parts) >= 2:
    return parts[-2], parts[-1]
  return None, None


STREAM_PARSE_LINE_BATCH = 50000


def stats_file_size_bytes(stats_file):
  """Return on-disk size in bytes (0 when missing or unreadable)."""
  try:
    return int(os.path.getsize(stats_file))
  except OSError:
    return 0


_READ_LOOP_DEADLINE_EVERY_LINES = 1000
_READ_LOOP_DEADLINE_EVERY_BYTES = 1 << 20


def _maybe_raise_ingest_read_deadline(line_idx, bytes_read):
  if line_idx and line_idx % _READ_LOOP_DEADLINE_EVERY_LINES == 0:
    from hpcperfstats.dbload.sync_timedb_archive_members_redis import (
        _raise_if_ingest_deadline_exceeded,
    )

    _raise_if_ingest_deadline_exceeded()
  if bytes_read and bytes_read % _READ_LOOP_DEADLINE_EVERY_BYTES == 0:
    from hpcperfstats.dbload.sync_timedb_archive_members_redis import (
        _raise_if_ingest_deadline_exceeded,
    )

    _raise_if_ingest_deadline_exceeded()


def load_stats_file_lines(stats_file, stats_file_contents=None):
  if stats_file_contents is not None:
    return stats_file_contents, None
  lines = []
  bytes_read = 0
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "r") as fd:
        line_idx = 0
        while True:
          line = fd.readline()
          if not line:
            break
          lines.append(line)
          line_idx += 1
          bytes_read += len(line)
          _maybe_raise_ingest_read_deadline(line_idx, bytes_read)
    return lines, None
  except FileNotFoundError:
    return None, "Stats file disappeared: %s" % stats_file
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass


def iter_stats_file_lines(stats_file):
  """Yield lines from a stats file under the read lock (streaming)."""
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "r") as fd:
        line_idx = 0
        bytes_read = 0
        while True:
          line = fd.readline()
          if not line:
            break
          line_idx += 1
          bytes_read += len(line)
          _maybe_raise_ingest_read_deadline(line_idx, bytes_read)
          yield line
  except FileNotFoundError:
    return
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass


def parse_first_timestamp_line(lines):
  for l in lines:
    if not l:
      continue
    try:
      s = l.lstrip()
      if not s:
        continue
      if s[0].isdigit():
        t, jid, host = s.split()
        return (t, jid, host)
    except Exception:
      pass
  return (None, None, None)


def parse_last_timestamp_line(lines):
  """Return last digit-leading stats line identity from an in-memory line list."""
  for line in reversed(lines or ()):
    if not line:
      continue
    try:
      s = line.lstrip()
      if not s:
        continue
      if s[0].isdigit():
        t, jid, host = s.split()
        return (t, jid, host)
    except Exception:
      pass
  return (None, None, None)


def parse_last_timestamp_line_streaming(stats_file, *, tail_read_bytes=65536):
  """Return last digit-leading stats line identity without a full-file scan."""
  from hpcperfstats.dbload.sync_timedb_ingest_worker_diagnostics import (
      update_worker_substage,
  )

  update_worker_substage("parse:tail")
  try:
    size = os.path.getsize(stats_file)
  except OSError:
    return (None, None, None)
  if size <= 0:
    return (None, None, None)
  chunk_size = max(4096, int(tail_read_bytes))
  carry = b""
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "rb") as fd:
        offset = size
        while offset > 0:
          read_size = min(chunk_size, offset)
          offset -= read_size
          fd.seek(offset)
          block = fd.read(read_size) + carry
          parts = block.split(b"\n")
          if offset > 0:
            carry = parts[0]
            parts = parts[1:]
          else:
            carry = b""
          for raw in reversed(parts):
            if not raw:
              continue
            try:
              line = raw.decode("utf-8", errors="replace")
            except Exception:
              continue
            s = line.lstrip()
            if not s or not s[0].isdigit():
              continue
            try:
              t, jid, host = s.split()
              return (t, jid, host)
            except Exception:
              pass
  except FileNotFoundError:
    return (None, None, None)
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass
  return (None, None, None)


def _timestamp_present_for_duplicate(itimes_set, timestamp_present, unix_second):
  if timestamp_present is not None:
    return bool(timestamp_present(unix_second))
  return int(unix_second) in itimes_set


def find_processing_start_index(lines, itimes_set, timestamp_present=None):
  start_idx = -1
  last_idx = 0
  need_archival = True
  for i, line in enumerate(lines):
    if i and i % 1000 == 0:
      from hpcperfstats.dbload.sync_timedb_archive_members_redis import (
          _raise_if_ingest_deadline_exceeded,
      )
      from hpcperfstats.dbload.sync_timedb_ingest_worker_diagnostics import (
          update_worker_substage,
      )

      update_worker_substage("duplicate_scan_lines")
      _raise_if_ingest_deadline_exceeded()
    if not line:
      continue
    s = line.lstrip()
    if not s:
      continue
    if s[0].isdigit():
      t, _jid, _host = s.split()
      if not _timestamp_present_for_duplicate(
          itimes_set, timestamp_present, int(float(t))):
        start_idx = last_idx
        need_archival = True
        break
      last_idx = i
  return start_idx, need_archival


def find_processing_start_index_streaming(
    stats_file,
    itimes_set,
    *,
    timestamp_present=None,
):
  """Scan a stats file without loading it into memory."""
  from hpcperfstats.dbload.sync_timedb_archive_members_redis import (
      _raise_if_ingest_deadline_exceeded,
  )
  from hpcperfstats.dbload.sync_timedb_ingest_worker_diagnostics import (
      update_worker_substage,
  )

  update_worker_substage("duplicate_scan_streaming")
  start_idx = -1
  last_idx = 0
  line_idx = 0
  for line in iter_stats_file_lines(stats_file):
    if line_idx and line_idx % 1000 == 0:
      update_worker_substage("duplicate_scan_streaming")
      _raise_if_ingest_deadline_exceeded()
    if not line:
      line_idx += 1
      continue
    s = line.lstrip()
    if not s:
      line_idx += 1
      continue
    if s[0].isdigit():
      t, _jid, _host = s.split()
      if not _timestamp_present_for_duplicate(
          itimes_set, timestamp_present, int(float(t))):
        start_idx = last_idx
        return start_idx, True
      last_idx = line_idx
    line_idx += 1
  return start_idx, True


def parse_first_timestamp_line_streaming(stats_file):
  """Return first digit-leading stats line identity without ``readlines()``."""
  from hpcperfstats.dbload.sync_timedb_ingest_worker_diagnostics import (
      update_worker_substage,
  )

  update_worker_substage("parse:head")
  for line in iter_stats_file_lines(stats_file):
    if not line:
      continue
    s = line.lstrip()
    if not s:
      continue
    if s[0].isdigit():
      try:
        t, jid, host = s.split()
        return (t, jid, host)
      except Exception:
        pass
  return (None, None, None)


def _collect_tail_timestamp_lines(stats_file, *, max_lines, tail_read_bytes=65536):
  """Collect up to ``max_lines`` digit-leading lines from the file tail (newest first)."""
  try:
    size = os.path.getsize(stats_file)
  except OSError:
    return []
  if size <= 0 or max_lines <= 0:
    return []
  chunk_size = max(4096, int(tail_read_bytes))
  carry = b""
  collected = []
  offset = size
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "rb") as fd:
        while offset > 0 and len(collected) < max_lines:
          read_size = min(chunk_size, offset)
          offset -= read_size
          fd.seek(offset)
          block = fd.read(read_size) + carry
          parts = block.split(b"\n")
          if offset > 0:
            carry = parts[0]
            parts = parts[1:]
          else:
            carry = b""
          for raw in reversed(parts):
            if len(collected) >= max_lines:
              break
            if not raw:
              continue
            try:
              line = raw.decode("utf-8", errors="replace")
            except Exception:
              continue
            s = line.lstrip()
            if not s or not s[0].isdigit():
              continue
            collected.append(line)
  except FileNotFoundError:
    return []
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass
  return collected


def tail_window_timestamps_all_present_streaming(
    stats_file,
    itimes_set,
    *,
    timestamp_present=None,
    max_lines=None,
):
  """True when every timestamp in the tail window is already present in DB/cache."""
  import hpcperfstats.conf_parser as cfg
  from hpcperfstats.dbload.sync_timedb_archive_members_redis import (
      _raise_if_ingest_deadline_exceeded,
  )
  from hpcperfstats.dbload.sync_timedb_ingest_worker_diagnostics import (
      update_worker_substage,
  )

  if max_lines is None:
    max_lines = cfg.get_sync_ingest_db_complete_tail_window_lines()
  update_worker_substage("parse:tail_window")
  lines = _collect_tail_timestamp_lines(stats_file, max_lines=max_lines)
  if not lines:
    return False
  for line in lines:
    _raise_if_ingest_deadline_exceeded()
    s = line.lstrip()
    if not s or not s[0].isdigit():
      continue
    t, _jid, _host = s.split()
    if not _timestamp_present_for_duplicate(
        itimes_set, timestamp_present, int(float(t))):
      return False
  return True


class IncrementalStatsParser:
  """Stateful parser for chunked/streaming stats-file ingest."""

  def __init__(self, start_idx=0, exclude_types_list=None):
    self.start_idx = int(start_idx)
    self.exclude_types_list = (
        exclude_types_list if exclude_types_list is not None else exclude_types
    )
    self._line_index = 0
    self.schema = {}
    self.schema_fast = {}
    self.stats = []
    self.proc_stats = []
    self.insert = False
    self.line_ctx = {"tags": None, "tags2": None}

  def feed_line(self, line):
    i = self._line_index
    self._line_index += 1
    if not line:
      return
    s = line.lstrip()
    if not s:
      return

    if s[0].isalpha() and self.insert:
      typ, dev, vals = s.split(maxsplit=2)
      vals = vals.split()
      if typ in self.exclude_types_list:
        return

      if typ in ("proc", "host_proc"):
        proc_name = (s.split()[1]).split("/")[0]
        self.proc_stats.append({**self.line_ctx["tags2"], "proc": proc_name})
        return

      if typ not in self.schema:
        return

      tier_marker = None
      if vals and vals[0] in _TIER_MARKERS:
        tier_marker = vals[0]
        vals = vals[1:]

      if tier_marker == "@fast":
        if schema_needs_legacy_hardware_decode(typ, self.schema[typ]):
          return
        schema_keys = self.schema_fast.get(typ, [])
        use_legacy = False
      else:
        schema_keys = self.schema[typ]
        use_legacy = schema_needs_legacy_hardware_decode(typ, self.schema[typ])

      vals_dict = _vals_dict_from_line(
          typ, self.schema, schema_keys, vals, use_legacy, dev=dev)
      if vals_dict is None:
        return

      out_typ = legacy_parsing.legacy_output_type(typ) if use_legacy else typ
      rec = {**self.line_ctx["tags"], "type": out_typ, "dev": dev}
      _append_stats_rows(self.stats, rec, vals_dict)

    elif i >= self.start_idx and s[0].isdigit():
      t, jid, host = s.split()
      self.insert = True
      self.line_ctx["tags"] = {"time": float(t), "host": host}
      self.line_ctx["tags2"] = {"time": float(t), "host": host, "jid": jid}
    elif s[0] == "!":
      label, events = s.split(maxsplit=1)
      typ, events = label[1:], events.split()
      self.schema[typ] = events
      self.schema_fast[typ] = _fast_schema_keys(events)

  def feed_lines(self, lines):
    for line in lines:
      self.feed_line(line)

  def finish(self):
    return self.stats, self.proc_stats


def parse_stats_lines(lines, start_idx, eventmaps_by_type=None, exclude_types_list=None):
  """Parse stats and proc_stats from lines starting at start_idx.

  Legacy archives (CTL/CTR or legacy st_name) use sync_timedb_parsing_legacy.
  eventmaps_by_type is ignored (kept for API compat); detection is automatic.
  """
  del eventmaps_by_type  # noqa: F841 — auto-detect legacy vs canonical
  parser = IncrementalStatsParser(start_idx, exclude_types_list)
  parser.feed_lines(lines)
  return parser.finish()


def parse_stats_file_streaming(
    stats_file,
    *,
    start_line_idx=0,
    parse_start_idx=0,
    batch_size=STREAM_PARSE_LINE_BATCH,
    exclude_types_list=None,
):
  """Parse a large stats file in bounded batches without ``readlines()``."""
  parser = IncrementalStatsParser(parse_start_idx, exclude_types_list)
  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "r") as fd:
        for _ in range(int(start_line_idx)):
          if not fd.readline():
            return parser.finish()
        while True:
          batch = []
          for _ in range(int(batch_size)):
            line = fd.readline()
            if not line:
              break
            batch.append(line)
          if not batch:
            break
          parser.feed_lines(batch)
          del batch
  except FileNotFoundError:
    return [], []
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass
  return parser.finish()


def build_stats_dataframes(stats_list, proc_stats_list):
  proc_stats_df = DataFrame.from_records(proc_stats_list).drop_duplicates()
  stats_df = DataFrame.from_records(stats_list)
  return stats_df, proc_stats_df


_EMPTY_DELTA_ARC_COLUMNS = [
    "time", "host", "type", "dev", "event", "unit", "value", "delta", "arc"
]


class DeltaCarryState:
  """Cross-chunk state for counter deltas and arc rates during incremental ingest."""

  __slots__ = ("raw", "arc")

  def __init__(self):
    self.raw = {}
    self.arc = {}


def _empty_delta_arc_frame():
  return DataFrame(columns=_EMPTY_DELTA_ARC_COLUMNS)


def _stats_df_has_required_delta_cols(stats_df):
  required_cols = {
      "host", "type", "dev", "event", "unit", "time", "value", "wid", "mult"
  }
  return (
      not stats_df.empty
      and required_cols.issubset(stats_df.columns)
  )


def _apply_counter_deltas(stats_df, carry=None):
  stats_df = stats_df.sort_values(by=_COUNTER_GROUP_COLS + ["time"]).copy()
  stats_df["delta"] = stats_df.groupby(
      _COUNTER_GROUP_COLS, observed=True)["value"].diff()

  if carry is not None and carry.raw:
    first_rows = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True).head(1)
    for idx, row in first_rows.iterrows():
      key = (row["host"], row["type"], row["dev"], row["event"])
      prev = carry.raw.get(key)
      if prev is None:
        continue
      delta = float(row["value"]) - float(prev["value"])
      if delta < 0:
        delta = 2 ** int(row["wid"]) + delta
      stats_df.at[idx, "delta"] = delta * float(row["mult"])

  stats_df["delta"] = stats_df["delta"].mask(
      stats_df["delta"] < 0, 2 ** stats_df["wid"] + stats_df["delta"])
  stats_df["delta"] = stats_df["delta"] * stats_df["mult"]

  if carry is not None:
    for key, group in stats_df.groupby(_COUNTER_GROUP_COLS, observed=True):
      last = group.iloc[-1]
      carry.raw[key] = {
          "value": float(last["value"]),
          "wid": int(last["wid"]),
          "mult": float(last["mult"]),
          "time": float(last["time"]),
      }

  stats_df.drop(columns=["wid", "mult"], inplace=True)
  return stats_df


def _collapse_stats_with_deltas(stats_df):
  gcols = _COLLAPSE_GROUP_COLS
  nv_mask = stats_df["type"] == "nvidia_gpu"
  nv_df = stats_df[nv_mask]
  rest_df = stats_df[~nv_mask]
  parts = []
  if not rest_df.empty:
    ccm_power_mask = (
        rest_df["type"].isin(_HOST_CPU_HW_TYPES)
        & rest_df["event"].isin(_DCGM_CPU_POWER_SOCKET_GAUGE_EVENTS))
    ccm_power_df = rest_df[ccm_power_mask]
    rest_other = rest_df[~ccm_power_mask]
    if not rest_other.empty:
      parts.append(
          rest_other.groupby(gcols, observed=True).sum(min_count=1).reset_index()
      )
    if not ccm_power_df.empty:
      ccm_collapsed = ccm_power_df.groupby(
          gcols, observed=True).apply(_collapse_dcg_cpu_power_gauge_group)
      ccm_collapsed = ccm_collapsed.reset_index()
      parts.append(ccm_collapsed)
  if not nv_df.empty:
    nv_collapsed = nv_df.groupby(gcols, observed=True).apply(
        _collapse_nvidia_gpu_group,
    )
    nv_collapsed = nv_collapsed.reset_index()
    parts.append(nv_collapsed)

  if not parts:
    return _empty_delta_arc_frame()
  collapsed = concat(parts, ignore_index=True)
  del parts
  return collapsed.sort_values(by=_ARC_GROUP_COLS + ["time"])


def _apply_arc_and_finalize(stats_df, carry=None):
  deltat = stats_df.groupby(_ARC_GROUP_COLS, observed=True)["time"].diff()
  _dy = stats_df["delta"].to_numpy(dtype=np.float64, copy=False)
  _dt = deltat.to_numpy(dtype=np.float64, copy=False)
  _arc = np.full(len(stats_df), np.nan, dtype=np.float64)
  _ok = (_dt > 0) & np.isfinite(_dt)
  np.divide(_dy, _dt, out=_arc, where=_ok)

  if carry is not None and carry.arc:
    first_rows = stats_df.groupby(_ARC_GROUP_COLS, observed=True).head(1)
    for idx, row in first_rows.iterrows():
      key = (row["host"], row["type"], row["event"])
      prev = carry.arc.get(key)
      if prev is None:
        continue
      dt = float(row["time"]) - float(prev["time"])
      if dt > 0 and np.isfinite(row["delta"]):
        _arc[stats_df.index.get_loc(idx)] = float(row["delta"]) / dt

  stats_df = stats_df.copy()
  stats_df["arc"] = _arc

  if carry is not None:
    for key, group in stats_df.groupby(_ARC_GROUP_COLS, observed=True):
      last = group.iloc[-1]
      carry.arc[key] = {"time": float(last["time"])}

  stats_df["time"] = to_datetime(stats_df["time"], unit="s").dt.tz_localize("UTC")
  return stats_df.dropna(subset=["host", "type", "event", "time", "value"])


def compute_deltas_and_arc(stats_df):
  if not _stats_df_has_required_delta_cols(stats_df):
    return _empty_delta_arc_frame()
  stats_df = _apply_counter_deltas(stats_df.copy())
  stats_df = _collapse_stats_with_deltas(stats_df)
  if stats_df.empty:
    return stats_df
  return _apply_arc_and_finalize(stats_df)


def compute_deltas_and_arc_chunk(stats_df, *, carry):
  """Compute deltas/arc for one incremental flush; update ``carry`` in place."""
  if carry is None:
    raise ValueError("carry is required for incremental delta computation")
  if not _stats_df_has_required_delta_cols(stats_df):
    return _empty_delta_arc_frame()
  stats_df = _apply_counter_deltas(stats_df, carry=carry)
  stats_df = _collapse_stats_with_deltas(stats_df)
  if stats_df.empty:
    return stats_df
  return _apply_arc_and_finalize(stats_df, carry=carry)


def _line_starts_time_sample(line, line_index, start_idx):
  if not line:
    return False
  stripped = line.lstrip()
  if not stripped:
    return False
  return stripped[0].isdigit() and line_index >= start_idx


def parse_stats_file_streaming_incremental(
    stats_file,
    *,
    start_line_idx=0,
    parse_start_idx=0,
    flush_rows,
    on_chunk,
    line_batch_size=STREAM_PARSE_LINE_BATCH,
    exclude_types_list=None,
):
  """Parse a large stats file, flushing complete time samples via ``on_chunk``."""
  parser = IncrementalStatsParser(parse_start_idx, exclude_types_list)
  pending_flush = False
  flush_rows = max(1, int(flush_rows))

  def _emit_flush():
    nonlocal pending_flush
    if not parser.stats and not parser.proc_stats:
      pending_flush = False
      return
    on_chunk(parser.stats, parser.proc_stats)
    parser.stats = []
    parser.proc_stats = []
    pending_flush = False

  def _on_time_sample_boundary():
    if pending_flush or len(parser.stats) >= flush_rows:
      _emit_flush()

  try:
    with file_read_lock_wait(stats_file):
      with open(stats_file, "r") as fd:
        for _ in range(int(start_line_idx)):
          if not fd.readline():
            _emit_flush()
            return
        while True:
          got_line = False
          for _ in range(int(line_batch_size)):
            line = fd.readline()
            if not line:
              break
            got_line = True
            if _line_starts_time_sample(
                line, parser._line_index, parser.start_idx):
              _on_time_sample_boundary()
            parser.feed_line(line)
            if len(parser.stats) >= flush_rows:
              pending_flush = True
          if not got_line:
            break
    _emit_flush()
  except FileNotFoundError:
    return
  finally:
    lock_path = "%s%s" % (stats_file, LOCK_SUFFIX)
    try:
      os.remove(lock_path)
    except OSError:
      pass
