"""Codon-only C ABI for free-threaded pyext trampoline (``--lib`` build).

Not imported under CPython — ``@export`` / ``Ptr[cobj]`` are Codon constructs.
Built by Dockerfile ``codon-build`` / ``build_codon_ext.sh`` into
``libhpcperfstats_codon_kernel.so``.

Per-slot cache banks (``MAX_CODON_SLOTS``) isolate concurrent callers under
bridge per-slot locks. Slot id is passed on every run/read export.

``parse_stats_lines_codon_run_c`` runs the full feed -> counter-delta ->
collapse -> arc-rate pipeline (``feed_collapse.feed_delta_collapse_arc``) and
stores the *finished* columns (``time``/``host``/``jid``/``type``/``dev``/
``event``/``unit``/``value``/``delta``/``arc``) in the slot bank; ``wid``/
``mult`` are internal to the pipeline and are not cached. Cross-flush counter
and arc-rate carry state lives in Python (``DeltaCarryState``); the pyext
layer pushes it into the slot's carry dict via the ``codon_carry_*_set_c``
exports before each run and reads it back via ``codon_carry_*_out_*_c`` after.
"""

import os

from feed_collapse import (
  HOST_PROC_KEYS,
  feed_and_collapse,
  feed_delta_collapse_arc,
  fill_reuse_bank,
  stats_file_decode_line,
  stats_file_line_starts_time_sample,
  trailing_jobs_arm,
)

MAX_CODON_SLOTS = 128

_cache_time: list[list[float | None]] = []
_cache_host: list[list[str]] = []
_cache_jid: list[list[str | None]] = []
_cache_type: list[list[str]] = []
_cache_dev: list[list[str]] = []
_cache_event: list[list[str]] = []
_cache_value: list[list[float | None]] = []
_cache_delta: list[list[float | None]] = []
_cache_arc: list[list[float | None]] = []
_cache_unit: list[list[str]] = []
_cache_host_n: list[int] = []
_cache_host_parsed_n: list[int] = []
_cache_proc_n: list[int] = []
_cache_proc_flat: list[list[int]] = []
_cache_proc_jid: list[list[str]] = []
_cache_proc_host: list[list[str]] = []
_cache_proc_proc: list[list[str]] = []
_cache_proc_fields: list[list[dict[str, int]]] = []
_cache_proc_device: list[list[str]] = []
_cache_carry_raw: list[dict[str, list[float]]] = []
_cache_carry_arc: list[dict[str, float]] = []
_cache_carry_raw_keys: list[list[str]] = []
_cache_carry_arc_keys: list[list[str]] = []
_cache_marks: list[list[dict[str, str]]] = []
_cache_exclude: list[list[str]] = []
# Resume gate (line index): set by the file open or codon_start_idx_set_c.
_cache_start_idx: list[int] = []
_fs_active: list[int] = []
_fs_flush_bytes: list[int] = []
_fs_schema: list[list[str]] = []
_fs_window: list[list[str]] = []
_fs_window_start: list[int] = []
_fs_line_index: list[int] = []
_fs_pending_flush: list[int] = []
_fs_read_batch: list[int] = []
_fs_eof: list[int] = []
_fs_fd: list = []
_fs_read_buf: list = []
# Window lines below the resume gate, and the %jobs arm the last window ended with.
_fs_below: list[int] = []
_fs_arm: list[int] = []
# Last exception text per slot ("" = none); read by codon_last_error_c.
_cache_err: list[str] = []
_banks_ready: int = 0


def _stats_file_fd_tell(slot: int) -> int:
  """Current byte offset for an open stats-file fd on ``slot``."""
  fd = _fs_fd[slot]
  return os.lseek(fd, 0, os.SEEK_CUR)


def _stats_file_fd_readline(slot: int) -> str:
  """Read one line (including ``\\n`` when present) from ``slot``'s fd."""
  buf: str = _fs_read_buf[slot]
  while True:
    nl = buf.find("\n")
    if nl >= 0:
      line = buf[: nl + 1]
      _fs_read_buf[slot] = buf[nl + 1 :]
      return line
    chunk_b = os.read(_fs_fd[slot], 65536)
    if len(chunk_b) == 0:
      if len(buf) > 0:
        _fs_read_buf[slot] = ""
        return buf
      return ""
    chunk = chunk_b.decode("ascii", "replace")
    buf = buf + chunk
    _fs_read_buf[slot] = buf


def _ensure_slot_banks() -> None:
  """Allocate empty per-slot cache banks once."""
  global _cache_time
  global _cache_host
  global _cache_jid
  global _cache_type
  global _cache_dev
  global _cache_event
  global _cache_value
  global _cache_delta
  global _cache_arc
  global _cache_unit
  global _cache_host_n
  global _cache_host_parsed_n
  global _cache_proc_n
  global _cache_proc_flat
  global _cache_proc_jid
  global _cache_proc_host
  global _cache_proc_proc
  global _cache_proc_device
  global _cache_proc_fields
  global _cache_carry_raw
  global _cache_carry_arc
  global _cache_carry_raw_keys
  global _cache_carry_arc_keys
  global _cache_marks
  global _cache_exclude
  global _cache_start_idx
  global _fs_below
  global _fs_arm
  global _fs_active
  global _fs_flush_bytes
  global _fs_schema
  global _fs_window
  global _fs_window_start
  global _fs_line_index
  global _fs_pending_flush
  global _fs_read_batch
  global _fs_eof
  global _fs_fd
  global _fs_read_buf
  global _cache_err
  global _banks_ready
  if _banks_ready == 1:
    return
  _cache_err = []
  _cache_time = []
  _cache_host = []
  _cache_jid = []
  _cache_type = []
  _cache_dev = []
  _cache_event = []
  _cache_value = []
  _cache_delta = []
  _cache_arc = []
  _cache_unit = []
  _cache_host_n = []
  _cache_host_parsed_n = []
  _cache_proc_n = []
  _cache_proc_flat = []
  _cache_proc_jid = []
  _cache_proc_host = []
  _cache_proc_proc = []
  _cache_proc_device = []
  _cache_proc_fields = []
  _cache_carry_raw = []
  _cache_carry_arc = []
  _cache_carry_raw_keys = []
  _cache_carry_arc_keys = []
  _cache_marks = []
  _cache_exclude = []
  _cache_start_idx = []
  _fs_below = []
  _fs_arm = []
  i = 0
  while i < MAX_CODON_SLOTS:
    empty_f: list[float | None] = []
    _cache_time.append(empty_f)
    empty_f2: list[float | None] = []
    _cache_value.append(empty_f2)
    empty_f3: list[float | None] = []
    _cache_delta.append(empty_f3)
    empty_f4: list[float | None] = []
    _cache_arc.append(empty_f4)
    empty_s1: list[str] = []
    _cache_host.append(empty_s1)
    empty_s2: list[str | None] = []
    _cache_jid.append(empty_s2)
    empty_s3: list[str] = []
    _cache_type.append(empty_s3)
    empty_s4: list[str] = []
    _cache_dev.append(empty_s4)
    empty_s5: list[str] = []
    _cache_event.append(empty_s5)
    empty_s6: list[str] = []
    _cache_unit.append(empty_s6)
    _cache_host_n.append(0)
    _cache_host_parsed_n.append(0)
    _cache_proc_n.append(0)
    empty_flat: list[int] = []
    _cache_proc_flat.append(empty_flat)
    empty_s7: list[str] = []
    _cache_proc_jid.append(empty_s7)
    empty_s8: list[str] = []
    _cache_proc_host.append(empty_s8)
    empty_s9: list[str] = []
    _cache_proc_proc.append(empty_s9)
    empty_s10: list[str] = []
    _cache_proc_device.append(empty_s10)
    empty_d1: list[dict[str, int]] = []
    _cache_proc_fields.append(empty_d1)
    empty_cr: dict[str, list[float]] = {}
    _cache_carry_raw.append(empty_cr)
    empty_ca: dict[str, float] = {}
    _cache_carry_arc.append(empty_ca)
    empty_crk: list[str] = []
    _cache_carry_raw_keys.append(empty_crk)
    empty_cak: list[str] = []
    _cache_carry_arc_keys.append(empty_cak)
    empty_marks: list[dict[str, str]] = []
    _cache_marks.append(empty_marks)
    empty_excl: list[str] = []
    _cache_exclude.append(empty_excl)
    _fs_active.append(0)
    _fs_flush_bytes.append(0)
    empty_fs_schema: list[str] = []
    _fs_schema.append(empty_fs_schema)
    empty_fs_win: list[str] = []
    _fs_window.append(empty_fs_win)
    _fs_window_start.append(0)
    _fs_line_index.append(0)
    _fs_pending_flush.append(0)
    _fs_read_batch.append(4096)
    _fs_eof.append(0)
    _fs_fd.append(0)
    _fs_read_buf.append("")
    _cache_start_idx.append(0)
    _fs_below.append(0)
    _fs_arm.append(0)
    _cache_err.append("")
    i += 1
  _banks_ready = 1


def _fill_proc_flat(slot: int, fields: list[dict[str, int]], n: int) -> None:
  """Store proc metrics as parallel ints. -1 means the key is absent."""
  global _cache_proc_flat
  width = len(HOST_PROC_KEYS)
  need = n * width
  bank = _cache_proc_flat[slot]
  if len(bank) < need:
    extra: list[int] = []
    k = len(bank)
    while k < need:
      extra.append(-1)
      k += 1
    bank.extend(extra)
  i = 0
  while i < n:
    fld = fields[i]
    k = 0
    while k < width:
      metric = HOST_PROC_KEYS[k]
      val = -1
      if metric in fld and type(fld[metric]) is int:
        val = fld[metric]
      bank[i * width + k] = val
      k += 1
    i += 1


def _apply_lines_to_slot(
  slot: int, lines: list[str], start_idx: int, arm_shared_in: bool
) -> None:
  """Run feed_delta_collapse_arc for one window; update slot banks and carry."""
  global _cache_time
  global _cache_host
  global _cache_jid
  global _cache_type
  global _cache_dev
  global _cache_event
  global _cache_value
  global _cache_delta
  global _cache_arc
  global _cache_unit
  global _cache_host_n
  global _cache_host_parsed_n
  global _cache_proc_n
  global _cache_proc_flat
  global _cache_proc_jid
  global _cache_proc_host
  global _cache_proc_proc
  global _cache_proc_device
  global _cache_proc_fields
  global _cache_carry_raw
  global _cache_carry_arc
  global _cache_carry_raw_keys
  global _cache_carry_arc_keys
  global _cache_marks
  (finished, proc, carry_raw_out, carry_arc_out, marks, parsed_n) = (
    feed_delta_collapse_arc(
      lines,
      _cache_carry_raw[slot],
      _cache_carry_arc[slot],
      start_idx,
      _cache_exclude[slot],
      arm_shared_in,
    )
  )
  _cache_marks[slot] = marks
  _cache_host_parsed_n[slot] = parsed_n
  (
    fin_time,
    fin_host,
    fin_jid,
    fin_type,
    fin_dev,
    fin_event,
    fin_unit,
    fin_value,
    fin_delta,
    fin_arc,
  ) = finished
  _cache_host_n[slot] = fill_reuse_bank(_cache_time[slot], fin_time)
  fill_reuse_bank(_cache_host[slot], fin_host)
  fill_reuse_bank(_cache_jid[slot], fin_jid)
  fill_reuse_bank(_cache_type[slot], fin_type)
  fill_reuse_bank(_cache_dev[slot], fin_dev)
  fill_reuse_bank(_cache_event[slot], fin_event)
  fill_reuse_bank(_cache_unit[slot], fin_unit)
  fill_reuse_bank(_cache_value[slot], fin_value)
  fill_reuse_bank(_cache_delta[slot], fin_delta)
  fill_reuse_bank(_cache_arc[slot], fin_arc)
  (
    fin_pj,
    fin_ph,
    fin_pp,
    fin_pf,
    fin_pd,
  ) = proc
  _cache_proc_n[slot] = fill_reuse_bank(_cache_proc_jid[slot], fin_pj)
  fill_reuse_bank(_cache_proc_host[slot], fin_ph)
  fill_reuse_bank(_cache_proc_proc[slot], fin_pp)
  fill_reuse_bank(_cache_proc_fields[slot], fin_pf)
  fill_reuse_bank(_cache_proc_device[slot], fin_pd)
  _fill_proc_flat(slot, fin_pf, _cache_proc_n[slot])
  _cache_carry_raw[slot] = carry_raw_out
  _cache_carry_arc[slot] = carry_arc_out
  _cache_carry_raw_keys[slot] = list(carry_raw_out.keys())
  _cache_carry_arc_keys[slot] = list(carry_arc_out.keys())


def stats_file_session_close_c(slot: int) -> None:
  """Close an open stats file session on ``slot`` (no-op when inactive)."""
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return
  if _fs_active[slot] == 1:
    fd = _fs_fd[slot]
    if fd != 0:
      os.close(fd)
    _fs_fd[slot] = 0
    _fs_read_buf[slot] = ""
  _fs_active[slot] = 0
  _fs_eof[slot] = 0
  empty_schema: list[str] = []
  _fs_schema[slot] = empty_schema
  empty_win: list[str] = []
  _fs_window[slot] = empty_win
  _fs_window_start[slot] = 0
  _fs_line_index[slot] = 0
  _fs_pending_flush[slot] = 0
  _fs_below[slot] = 0
  _fs_arm[slot] = 0


@export
def codon_last_error_c(slot: int) -> str:
  """``Type: message`` of the exception that ended the slot's last call.

  ``""`` when the last open/next/run call returned normally. An exception
  that escapes an @export entry aborts the process (SIGABRT), so each entry
  catches it here and the pyext raises it as a Python ``RuntimeError``.
  """
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return ""
  return _cache_err[slot]


def _record_kernel_error(slot: int, e) -> None:
  _cache_err[slot] = e.typename + ": " + e.message


@export
def parse_stats_file_codon_open_c(
  slot: int,
  path_ptr: Ptr[UInt[8]],
  flush_max_bytes: int,
  start_idx: int,
) -> int:
  """Open ``path`` for Codon-owned read/batch on ``slot``. Returns 1 on success.

  ``start_idx`` is the resume gate (line index): the whole file is read so the
  ``!`` schema accumulates, and samples below the gate emit nothing.
  """
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return 0
  _cache_err[slot] = ""
  try:
    return _file_open_body(slot, path_ptr, flush_max_bytes, start_idx)
  except Exception as e:
    stats_file_session_close_c(slot)
    _record_kernel_error(slot, e)
    return 0


def _file_open_body(
  slot: int,
  path_ptr: Ptr[UInt[8]],
  flush_max_bytes: int,
  start_idx: int,
) -> int:
  stats_file_session_close_c(slot)
  path = str.from_ptr(path_ptr)
  flush_n = flush_max_bytes
  if flush_n < 1:
    flush_n = 1
  fd = os.open(path, os.O_RDONLY)
  codon_carry_clear_c(slot)
  _cache_start_idx[slot] = start_idx
  _fs_fd[slot] = fd
  _fs_read_buf[slot] = ""
  _fs_active[slot] = 1
  _fs_flush_bytes[slot] = flush_n
  _fs_window_start[slot] = _stats_file_fd_tell(slot)
  _fs_eof[slot] = 0
  empty_schema: list[str] = []
  _fs_schema[slot] = empty_schema
  empty_win: list[str] = []
  _fs_window[slot] = empty_win
  _fs_line_index[slot] = 0
  _fs_pending_flush[slot] = 0
  return 1


@export
def parse_stats_file_codon_next_c(
  slot: int,
  out_has_frame: Ptr[int],
  out_eof: Ptr[int],
  out_ns: Ptr[int],
  out_np: Ptr[int],
) -> None:
  """Read next flush window from the open file and parse into slot banks."""
  _ensure_slot_banks()
  out_has_frame[0] = 0
  out_eof[0] = 0
  out_ns[0] = 0
  out_np[0] = 0
  if 0 <= slot < MAX_CODON_SLOTS:
    _cache_err[slot] = ""
  try:
    _file_next_body(slot, out_has_frame, out_eof, out_ns, out_np)
  except Exception as e:
    stats_file_session_close_c(slot)
    _record_kernel_error(slot, e)
    out_has_frame[0] = 0
    out_eof[0] = 1


def _file_next_body(
  slot: int,
  out_has_frame: Ptr[int],
  out_eof: Ptr[int],
  out_ns: Ptr[int],
  out_np: Ptr[int],
) -> None:
  if slot < 0 or slot >= MAX_CODON_SLOTS or _fs_active[slot] != 1:
    out_eof[0] = 1
    return
  if _fs_eof[slot] == 1:
    out_eof[0] = 1
    return
  flush_bytes_n = _fs_flush_bytes[slot]
  batch_n = _fs_read_batch[slot]
  bundled: list[str] = []
  rel_start = 0
  arm_in = False
  while len(bundled) == 0:
    raw_batch: list[str] = []
    bi = 0
    while bi < batch_n:
      raw = _stats_file_fd_readline(slot)
      if len(raw) == 0:
        break
      raw_batch.append(raw)
      bi += 1
    if len(raw_batch) == 0:
      if len(_fs_window[slot]) > 0:
        bundled, rel_start, arm_in = _fs_cut_window(slot)
      _fs_eof[slot] = 1
      stats_file_session_close_c(slot)
      break
    batch_end = _stats_file_fd_tell(slot)
    for raw in raw_batch:
      line = stats_file_decode_line(raw)
      # The next sample's header triggers the flush and opens the new window
      # (skipping it once orphaned that sample's lines). One flush per call.
      if (
        len(bundled) == 0
        and len(_fs_window[slot]) > 0
        and stats_file_line_starts_time_sample(
          line, _fs_line_index[slot], _cache_start_idx[slot]
        )
        and (
          _fs_pending_flush[slot] == 1
          or (batch_end - _fs_window_start[slot]) >= flush_bytes_n
        )
      ):
        bundled, rel_start, arm_in = _fs_cut_window(slot)
        _fs_window_start[slot] = batch_end
        _fs_pending_flush[slot] = 0
      _fs_take_line(slot, line)
      if (batch_end - _fs_window_start[slot]) >= flush_bytes_n:
        _fs_pending_flush[slot] = 1
  if len(bundled) == 0:
    out_eof[0] = 1
    return
  _apply_lines_to_slot(slot, bundled, rel_start, arm_in)
  out_has_frame[0] = 1
  out_ns[0] = _cache_host_n[slot]
  out_np[0] = _cache_proc_n[slot]
  if _fs_eof[slot] == 1:
    out_eof[0] = 1


def _fs_take_line(slot: int, line: str) -> None:
  """Buffer one file line into the slot's window, honouring the resume gate.

  Below the gate only the last sample header and the ``%`` lines after it are
  kept: they carry the ``%jobs`` arm into the first emitted sample, and the
  kernel drops their marks (that prefix was ingested already).
  """
  li = _fs_line_index[slot]
  _fs_line_index[slot] = li + 1
  if line.startswith("!"):
    _fs_schema[slot].append(line)
    return
  if li >= _cache_start_idx[slot]:
    _fs_window[slot].append(line)
    return
  s = line.lstrip()
  if len(s) > 0 and s[0].isdigit():
    head: list[str] = [line]
    _fs_window[slot] = head
    _fs_below[slot] = 1
  elif len(s) > 0 and s[0] == "%":
    _fs_window[slot].append(line)
    _fs_below[slot] += 1


def _fs_cut_window(slot: int) -> tuple[list[str], int, bool]:
  """Detach the window as ``(schema + window, rel_start, arm_in)``.

  ``rel_start`` is the resume gate relative to the bundle; ``arm_in`` is the
  ``%jobs`` arm the previous window ended with, and this window's trailing arm
  is kept for the next one.
  """
  window = _fs_window[slot]
  bundled = _fs_schema[slot] + window
  rel_start = len(_fs_schema[slot]) + _fs_below[slot]
  arm_in = _fs_arm[slot] == 1
  _fs_arm[slot] = 1 if trailing_jobs_arm(window) else 0
  _fs_below[slot] = 0
  empty_win: list[str] = []
  _fs_window[slot] = empty_win
  return bundled, rel_start, arm_in


def _lines_from_ptr(n: int, ptr: Ptr[cobj]) -> list[str]:
  lines: list[str] = []
  i = 0
  while i < n:
    lines.append(str.from_ptr(ptr[i]))
    i += 1
  return lines


@export
def codon_exclude_set_c(slot: int, n: int, ptr: Ptr[cobj]) -> None:
  """Replace slot ``slot``'s excluded event-type list with ``n`` strings.

  ``n == 0`` clears it. The pyext calls this before every open/parse so a
  sticky slot never keeps the previous caller's list.
  """
  global _cache_exclude
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return
  _cache_exclude[slot] = _lines_from_ptr(n, ptr)


@export
def codon_start_idx_set_c(slot: int, start_idx: int) -> None:
  """Set the line-batch resume gate for ``slot`` (the pyext sets it every call)."""
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return
  _cache_start_idx[slot] = start_idx


@export
def feed_and_collapse_fingerprint_c(
  slot: int,
  n: int,
  ptr: Ptr[cobj],
  out_raw: Ptr[int],
  out_collapsed: Ptr[int],
  out_value_sum: Ptr[float],
  out_proc_rows: Ptr[float],
):
  """C-ABI fingerprint (avoids Codon Tuple sret). Slot-scoped; caller locks."""
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    out_raw[0] = 0
    out_collapsed[0] = 0
    out_value_sum[0] = 0.0
    out_proc_rows[0] = 0.0
    return
  lines = _lines_from_ptr(n, ptr)
  fp = feed_and_collapse(lines)
  out_raw[0] = fp[0]
  out_collapsed[0] = fp[1]
  out_value_sum[0] = fp[2]
  out_proc_rows[0] = fp[3]


@export
def codon_carry_clear_c(slot: int):
  """Clear a slot's carry_raw/carry_arc dicts before loading fresh carry."""
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return
  empty_cr: dict[str, list[float]] = {}
  _cache_carry_raw[slot] = empty_cr
  empty_ca: dict[str, float] = {}
  _cache_carry_arc[slot] = empty_ca
  empty_crk: list[str] = []
  _cache_carry_raw_keys[slot] = empty_crk
  empty_cak: list[str] = []
  _cache_carry_arc_keys[slot] = empty_cak


def _carry_key_from_c(key_ptr: Ptr[byte], key_len: int) -> str:
  """Own a copy of a carry key passed with an explicit length.

  Counter keys join host/type/dev/event with NUL and reserved keys start with
  NUL, so a NUL-terminated read (``str.from_ptr``) would cut them short; the
  caller frees its buffer after the call, so the bytes are copied.
  """
  buf = Ptr[byte](key_len)
  for i in range(key_len):
    buf[i] = key_ptr[i]
  return str(buf, key_len)


@export
def codon_carry_raw_set_c(
  slot: int,
  key_ptr: Ptr[byte],
  key_len: int,
  n: int,
  vals_ptr: Ptr[float],
):
  """Set one ``carry_raw`` entry (key bytes + length) pre-run.

  ``vals_ptr`` holds ``n`` floats: a counter entry is ``[value, wid, mult,
  time, missed, broken]``; reserved ``\0``-prefixed keys (``$build`` flag,
  restart times, bridge limit) carry their own lengths.
  """
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return
  key = _carry_key_from_c(key_ptr, key_len)
  vals: list[float] = []
  for k in range(n):
    vals.append(vals_ptr[k])
  _cache_carry_raw[slot][key] = vals


@export
def codon_carry_arc_set_c(
  slot: int, key_ptr: Ptr[byte], key_len: int, value: float
):
  """Set one ``carry_arc`` entry (key bytes + length) pre-run."""
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return
  key = _carry_key_from_c(key_ptr, key_len)
  _cache_carry_arc[slot][key] = value


@export
def parse_stats_lines_codon_run_c(
  slot: int,
  n: int,
  ptr: Ptr[cobj],
  out_ns: Ptr[int],
  out_np: Ptr[int],
):
  """Feed+delta+collapse+arc one flush window into slot cache banks.

  Reads the slot's current ``carry_raw``/``carry_arc`` (filled by
  ``codon_carry_*_set_c`` before this call), runs
  ``feed_delta_collapse_arc``, then overwrites the slot's carry banks with
  the returned ``carry_*_out`` so ``codon_carry_*_out_*_c`` can read them
  back after this call.
  """
  global _cache_time
  global _cache_host
  global _cache_jid
  global _cache_type
  global _cache_dev
  global _cache_event
  global _cache_value
  global _cache_delta
  global _cache_arc
  global _cache_unit
  global _cache_proc_jid
  global _cache_proc_host
  global _cache_proc_proc
  global _cache_proc_device
  global _cache_proc_fields
  global _cache_carry_raw
  global _cache_carry_arc
  global _cache_carry_raw_keys
  global _cache_carry_arc_keys
  global _cache_marks
  _ensure_slot_banks()
  out_ns[0] = 0
  out_np[0] = 0
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return
  _cache_err[slot] = ""
  try:
    _apply_lines_to_slot(
      slot, _lines_from_ptr(n, ptr), _cache_start_idx[slot], False
    )
  except Exception as e:
    _record_kernel_error(slot, e)
    return
  out_ns[0] = _cache_host_n[slot]
  out_np[0] = _cache_proc_n[slot]


@export
def codon_host_parsed_n_c(slot: int) -> int:
  """Host records parsed before collapse in the slot's last window (RC-0).

  Read after ``parse_stats_lines_codon_run_c`` / ``parse_stats_file_codon_next_c``;
  a getter keeps both exports' signatures unchanged.
  """
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return 0
  return _cache_host_parsed_n[slot]


@export
def codon_carry_raw_out_count_c(slot: int) -> int:
  """Number of ``carry_raw_out`` entries for ``slot`` after a run."""
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return 0
  return len(_cache_carry_raw_keys[slot])


@export
def codon_carry_raw_out_key_c(slot: int, i: int) -> str:
  return _cache_carry_raw_keys[slot][i]


@export
def codon_carry_raw_out_len_c(slot: int, i: int) -> int:
  """Number of floats in carry_raw entry ``i`` of ``slot`` after a run."""
  key = _cache_carry_raw_keys[slot][i]
  return len(_cache_carry_raw[slot][key])


@export
def codon_carry_raw_out_value_c(slot: int, i: int, idx: int) -> float:
  """Component ``idx`` of carry_raw entry ``i`` (see ``codon_carry_raw_set_c``)."""
  key = _cache_carry_raw_keys[slot][i]
  vals = _cache_carry_raw[slot][key]
  if idx < 0 or idx >= len(vals):
    return 0.0
  return vals[idx]


@export
def codon_carry_arc_out_count_c(slot: int) -> int:
  """Number of ``carry_arc_out`` entries for ``slot`` after a run."""
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return 0
  return len(_cache_carry_arc_keys[slot])


@export
def codon_carry_arc_out_key_c(slot: int, i: int) -> str:
  return _cache_carry_arc_keys[slot][i]


@export
def codon_carry_arc_out_value_c(slot: int, i: int) -> float:
  key = _cache_carry_arc_keys[slot][i]
  return _cache_carry_arc[slot][key]


@export
def psm_count_c(slot: int) -> int:
  """Number of bound ``%jobs``/``%affinity`` marks for ``slot`` after a run."""
  _ensure_slot_banks()
  if slot < 0 or slot >= MAX_CODON_SLOTS:
    return 0
  return len(_cache_marks[slot])


@export
def psm_field_c(slot: int, i: int, field_idx: int) -> str:
  """Mark string field: 0=kind 1=jids 2=jid 3=cpus 4=gpus 5=cg 6=time 7=host."""
  rec = _cache_marks[slot][i]
  if field_idx == 0:
    return rec["kind"]
  if field_idx == 1:
    return rec["jids"]
  if field_idx == 2:
    return rec["jid"]
  if field_idx == 3:
    return rec["cpus"]
  if field_idx == 4:
    return rec["gpus"]
  if field_idx == 5:
    return rec["cg"]
  if field_idx == 6:
    return rec["time"]
  if field_idx == 7:
    return rec["host"]
  return ""


def _opt_float(value: float | None) -> float:
  """NaN stands in for NULL on the float C ABI."""
  if value is None:
    return float("nan")
  return value


def _measure_is_null(value: float | None) -> int:
  if value is None:
    return 1
  return 0


@export
def pss_time_null_c(slot: int, i: int) -> int:
  return _measure_is_null(_cache_time[slot][i])


@export
def pss_value_null_c(slot: int, i: int) -> int:
  return _measure_is_null(_cache_value[slot][i])


@export
def pss_delta_null_c(slot: int, i: int) -> int:
  return _measure_is_null(_cache_delta[slot][i])


@export
def pss_arc_null_c(slot: int, i: int) -> int:
  return _measure_is_null(_cache_arc[slot][i])


@export
def pss_jid_null_c(slot: int, i: int) -> int:
  if _cache_jid[slot][i] is None:
    return 1
  return 0


@export
def pss_time_c(slot: int, i: int) -> float:
  return _opt_float(_cache_time[slot][i])


@export
def pss_value_c(slot: int, i: int) -> float:
  return _opt_float(_cache_value[slot][i])


@export
def pss_delta_c(slot: int, i: int) -> float:
  return _opt_float(_cache_delta[slot][i])


@export
def pss_arc_c(slot: int, i: int) -> float:
  return _opt_float(_cache_arc[slot][i])


@export
def pss_host_c(slot: int, i: int) -> str:
  return _cache_host[slot][i]


@export
def pss_jid_c(slot: int, i: int) -> str:
  jid = _cache_jid[slot][i]
  if jid is None:
    return ""
  return jid


@export
def pss_type_c(slot: int, i: int) -> str:
  return _cache_type[slot][i]


@export
def pss_dev_c(slot: int, i: int) -> str:
  return _cache_dev[slot][i]


@export
def pss_event_c(slot: int, i: int) -> str:
  return _cache_event[slot][i]


@export
def pss_unit_c(slot: int, i: int) -> str:
  return _cache_unit[slot][i]


@export
def psp_jid_c(slot: int, i: int) -> str:
  return _cache_proc_jid[slot][i]


@export
def psp_host_c(slot: int, i: int) -> str:
  return _cache_proc_host[slot][i]


@export
def psp_proc_c(slot: int, i: int) -> str:
  return _cache_proc_proc[slot][i]


@export
def psp_device_c(slot: int, i: int) -> str:
  return _cache_proc_device[slot][i]


@export
def psp_field_c(slot: int, i: int, key_idx: int) -> int:
  """Return host_proc field or -1 when absent (key_idx indexes HOST_PROC_KEYS)."""
  width = len(HOST_PROC_KEYS)
  if key_idx < 0 or key_idx >= width:
    return -1
  if i < 0 or i >= _cache_proc_n[slot]:
    return -1
  return _cache_proc_flat[slot][i * width + key_idx]


@export
def pss_export_f64_c(
  slot: int, col: int, n: int, values: Ptr[float], nulls: Ptr[int]
) -> int:
  """Copy one numeric host column into caller buffers. Returns the end mark."""
  _ensure_slot_banks()
  end = _cache_host_n[slot]
  if n < end:
    end = n
  i = 0
  while i < end:
    src: float | None = None
    if col == 0:
      src = _cache_time[slot][i]
    elif col == 1:
      src = _cache_value[slot][i]
    elif col == 2:
      src = _cache_delta[slot][i]
    else:
      src = _cache_arc[slot][i]
    if _measure_is_null(src) == 1:
      nulls[i] = 1
      values[i] = 0.0
    else:
      nulls[i] = 0
      values[i] = _opt_float(src)
    i += 1
  return end


@export
def pss_pack_str_col_c(
  slot: int, col: int, n: int, lens: Ptr[int], nulls: Ptr[int]
) -> str:
  """Join one host string column. ``nulls`` marks SQL NULL. ``lens`` is bytes."""
  _ensure_slot_banks()
  end = _cache_host_n[slot]
  if n < end:
    end = n
  parts: list[str] = []
  i = 0
  while i < end:
    is_null = 0
    text = ""
    if col == 0:
      text = _cache_host[slot][i]
    elif col == 1:
      jid = _cache_jid[slot][i]
      if jid is None:
        is_null = 1
      else:
        text = jid
    elif col == 2:
      text = _cache_type[slot][i]
    elif col == 3:
      text = _cache_dev[slot][i]
    elif col == 4:
      text = _cache_event[slot][i]
    else:
      text = _cache_unit[slot][i]
    if is_null == 1:
      nulls[i] = 1
      lens[i] = 0
    else:
      nulls[i] = 0
      lens[i] = len(text)
      parts.append(text)
    i += 1
  return "".join(parts)


@export
def psp_pack_str_col_c(
  slot: int, col: int, n: int, lens: Ptr[int], nulls: Ptr[int]
) -> str:
  """Join one proc string column. ``lens`` is the byte length of each row."""
  _ensure_slot_banks()
  end = _cache_proc_n[slot]
  if n < end:
    end = n
  parts: list[str] = []
  i = 0
  while i < end:
    text = ""
    if col == 0:
      text = _cache_proc_jid[slot][i]
    elif col == 1:
      text = _cache_proc_host[slot][i]
    elif col == 2:
      text = _cache_proc_proc[slot][i]
    else:
      text = _cache_proc_device[slot][i]
    nulls[i] = 0
    lens[i] = len(text)
    parts.append(text)
    i += 1
  return "".join(parts)


@export
def psp_export_fields_c(slot: int, n: int, out: Ptr[int]) -> int:
  """Copy proc metric ints. Layout is row-major ``HOST_PROC_KEYS``."""
  _ensure_slot_banks()
  end = _cache_proc_n[slot]
  if n < end:
    end = n
  width = len(HOST_PROC_KEYS)
  i = 0
  while i < end:
    k = 0
    while k < width:
      out[i * width + k] = _cache_proc_flat[slot][i * width + k]
      k += 1
    i += 1
  return end


# Eager once-init under single-threaded import before concurrent native entry.
_ensure_slot_banks()
