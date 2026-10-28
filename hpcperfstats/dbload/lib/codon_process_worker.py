"""Minimal spawn-worker / Popen server for Codon parse (no Django imports).

Used both as:
- picklable callables for unit tests (rare), and
- ``python -m hpcperfstats.dbload.lib.codon_process_worker`` stdio servers
  (one codonrt per OS process; parent SIGKILLs on shutdown).

IPC is length-prefixed binary (version + kind/status + nbytes + payload).
Fingerprint requests/responses carry raw UTF-8 line blobs (request) / a
``struct "!iidd"`` 4-tuple (response). Parse requests carry a compact UTF-8
JSON envelope ``{"lines": [...], "carry_raw": {...}, "carry_arc": {...}}``
(cross-flush counter/arc-rate carry round-trip). Parse responses carry length-prefixed **columnar SoA bytes** for host
``stats`` plus proc SoA bytes and a UTF-8 JSON sidecar for ``marks``,
``finished``, ``eof``, ``copy_unique`` (stdlib ``json`` only — pickle/msgpack
forbidden on 3.14t). **``KIND_PARSE_FILE``** sessions keep counter/arc carry
inside the worker only (not on the wire). Legacy ``KIND_PARSE`` (lines JSON)
may still round-trip ``carry_*`` in the sidecar.

Attributes:
  CODON_PARSE_DIR: Baked pyext directory (``/opt/hpcperfstats/codon_parse``).
  PROTO_VER: Wire-protocol version (mismatch fail-closed; 3 = columnar
    SoA parse response + JSON sidecar).
  KIND_FP: Request kind for fingerprint.
  KIND_PARSE: Request kind for full parse.
  STATUS_OK: Response status success.
  STATUS_ERR: Response status error.
  FP_STRUCT: Fingerprint payload packer.
  _REQ_HDR: Request frame header (``!BBI`` ver/kind/nbytes).
  _RESP_HDR: Response frame header (``!BBI`` ver/status/nbytes).
  _HOST_FLOAT_COLS: Finished host float SoA column order.
  _HOST_STR_COLS: Finished host string SoA column order.
  _U32: ``struct.Struct("!I")`` for SoA lengths.
  _F64: ``struct.Struct("!d")`` for SoA float columns.
  CODON_EXT_ENV: Env name that overrides the Codon extension import.
  KIND_PARSE_FILE: Request kind for a file-owned parse session.
  _I32: ``struct.Struct("!i")`` for 32-bit proc cells.
  _I64: ``struct.Struct("!q")`` for 64-bit proc cells.
  _PROC_I32_COLS: Proc columns packed as 32-bit integers.
  _PROC_I64_COLS: Proc columns packed as 64-bit integers.
  _PROC_STR_COLS: Proc columns packed as strings.
  WORKER_MEM_ENV: Env flag (``1``) that makes file sessions log ``codon_worker_mem``.
  _BOEHM_STATS: ``(field, Boehm getter)`` pairs sampled for worker memory lines.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import math
import os
import struct
import sys
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from hpcperfstats.dbload.lib.date_utils import to_pydatetime_or_none

CODON_PARSE_DIR = "/opt/hpcperfstats/codon_parse"
CODON_EXT_ENV = "HPCPERFSTATS_CODON_PARSE_MODULE"
PROTO_VER = 4
KIND_FP = 1
KIND_PARSE = 2
KIND_PARSE_FILE = 3
STATUS_OK = 0
STATUS_ERR = 1
FP_STRUCT = struct.Struct("!iidd")
_REQ_HDR = struct.Struct("!BBI")
_RESP_HDR = struct.Struct("!BBI")
_HOST_FLOAT_COLS: tuple[str, ...] = ("time", "value", "delta", "arc")
_HOST_STR_COLS: tuple[str, ...] = (
  "host",
  "jid",
  "type",
  "dev",
  "event",
  "unit",
)
_U32 = struct.Struct("!I")
_F64 = struct.Struct("!d")
_I64 = struct.Struct("!q")
_I32 = struct.Struct("!i")
_PROC_STR_COLS: tuple[str, ...] = ("jid", "host", "proc", "device")
_PROC_I64_COLS: tuple[str, ...] = (
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
)
_PROC_I32_COLS: tuple[str, ...] = ("threads",)


def _import_codon_parse_mod() -> Any:
  """
  Load the Codon pyext module (baked ``.so`` or test override env).

  Returns:
    Any: Module exposing ``parse_stats_lines_codon``.

  Examples:
    >>> callable(_import_codon_parse_mod)
    True
  """
  override = os.environ.get(CODON_EXT_ENV)
  if override:
    return importlib.import_module(override)
  _ensure_codon_on_path()
  return importlib.import_module("hpcperfstats_codon_parse")


def _ensure_codon_on_path() -> None:
  """
  Insert baked pyext dir on ``sys.path`` when present.

  Returns:
    None: Always ``None``.

  Examples:
    >>> _ensure_codon_on_path() is None
    True
  """
  os.environ.setdefault("OMP_NUM_THREADS", "1")
  if CODON_PARSE_DIR not in sys.path and os.path.isdir(CODON_PARSE_DIR):
    sys.path.insert(0, CODON_PARSE_DIR)


def codon_worker_fingerprint(lines: list[str]) -> tuple[Any, ...]:
  """
  Fingerprint via baked Codon ``.so`` (slot 0 in this process).

  Args:
    lines (list[str]): Stats lines.

  Returns:
    tuple[Any, ...]: ``(raw_stats, collapsed_rows, value_sum, proc_rows)``.

  Examples:
    >>> callable(codon_worker_fingerprint)
    True
  """
  mod = _import_codon_parse_mod()
  return mod.feed_and_collapse_fingerprint(list(lines), 0)


def codon_worker_parse(
  lines: list[str],
  carry_raw: dict[str, list[float]] | None = None,
  carry_arc: dict[str, float] | None = None,
  *,
  slot: int = 0,
  exclude: list[str] | None = None,
) -> dict[str, Any]:
  """
  Full feed->delta->collapse->arc parse via baked Codon ``.so`` (slot 0).

  Args:
    lines (list[str]): Stats lines for one flush window.
    carry_raw (dict[str, list[float]] | None): Cross-flush counter carry
      (``\\0``-joined keys -> ``[value, wid, mult, time]``), or None/empty
      for a fresh start.
    carry_arc (dict[str, float] | None): Cross-flush arc-rate carry
      (``\\0``-joined keys -> last unix-seconds time), or None/empty.
    slot (int): Codon pyext stripe slot.
    exclude (list[str] | None): Event types to skip (``None``/empty = none).

  Returns:
    dict[str, Any]: ``{"stats", "procs", "carry_raw", "carry_arc",
    "finished"}`` from ``parse_stats_lines_codon``.

  Examples:
    >>> callable(codon_worker_parse)
    True
  """
  mod = _import_codon_parse_mod()
  parsed = mod.parse_stats_lines_codon(
    list(lines),
    int(slot),
    carry_raw or {},
    carry_arc or {},
    list(exclude) if exclude else None,
  )
  if isinstance(parsed, dict):
    return _finalize_codon_parse_copy_emit(parsed)
  return parsed


def _finalize_codon_parse_copy_emit(body: dict[str, Any]) -> dict[str, Any]:
  """
  Mark Codon-emitted frames ``copy_unique`` after kernel COPY normalization.

  Scalar coercion (``jid``/``dev``/measures) runs in the baked kernel
  (``normalize_copy_unique_scalars`` before slot banks). When ``copy_unique``
  is already true the frame is returned unchanged. Otherwise this shim
  applies host/proc COPY helpers and sets ``copy_unique``.

  Args:
    body (dict[str, Any]): Codon parse result with ``stats``/``procs`` payloads.

  Returns:
    dict[str, Any]: Same dict with columnar SoA and ``copy_unique=True``.

  Examples:
    >>> _finalize_codon_parse_copy_emit({"stats": {"time": []}, "procs": {}})[
    ...   "copy_unique"
    ... ]
    True
  """
  if body.get("copy_unique") is True:
    return body
  from hpcperfstats.dbload.lib.sync_timedb_host_data_insert import (
    coerce_host_stats_to_columnar,
    normalize_host_columnar_for_copy,
  )
  from hpcperfstats.dbload.lib.sync_timedb_proc_data_insert import (
    coerce_proc_stats_to_columnar,
    normalize_proc_columnar_for_copy,
  )

  body["stats"] = normalize_host_columnar_for_copy(
    coerce_host_stats_to_columnar(body.get("stats"))
  )
  body["procs"] = normalize_proc_columnar_for_copy(
    coerce_proc_stats_to_columnar(body.get("procs"))
  )
  body["copy_unique"] = True
  return body


def encode_request(
  kind: str,
  lines: list[str],
  carry_raw: dict[str, list[float]] | None = None,
  carry_arc: dict[str, float] | None = None,
  exclude: list[str] | None = None,
) -> bytes:
  """
  Encode one parent→child request frame (header + UTF-8 body).

  Args:
    kind (str): ``fp`` or ``parse``.
    lines (list[str]): Stats lines.
    carry_raw (dict[str, list[float]] | None): ``parse`` only — cross-flush
      counter carry to seed before this window; ignored for ``fp``.
    carry_arc (dict[str, float] | None): ``parse`` only — cross-flush
      arc-rate carry to seed before this window; ignored for ``fp``.
    exclude (list[str] | None): ``parse`` only — event types to skip; the
      ``exclude`` key is omitted from the envelope when empty.

  Returns:
    bytes: Wire bytes including ``PROTO_VER`` header.

  Raises:
    ValueError: When ``kind`` is not ``fp`` or ``parse``.

  Examples:
    >>> encode_request("fp", ["a"])[:2]
    b'\\x02\\x01'
  """
  if kind == "fp":
    kind_b = KIND_FP
    body = "\n".join(lines).encode("utf-8")
  elif kind == "parse":
    kind_b = KIND_PARSE
    envelope = {
      "lines": list(lines),
      "carry_raw": carry_raw or {},
      "carry_arc": carry_arc or {},
    }
    if exclude:
      envelope["exclude"] = list(exclude)
    body = json.dumps(
      envelope, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
  else:
    raise ValueError(f"unknown codon ipc kind={kind!r}")
  return _REQ_HDR.pack(PROTO_VER, kind_b, len(body)) + body


def decode_request(frame: bytes) -> tuple[str, Any]:
  """
  Decode one request frame into ``(kind, payload)``.

  Args:
    frame (bytes): Full request including header.

  Returns:
    tuple[str, Any]: ``("fp", list[str])`` lines, or ``("parse", dict)``
    envelope with ``lines``/``carry_raw``/``carry_arc`` keys.

  Raises:
    RuntimeError: On version/kind/length mismatch.
    ValueError: When header is truncated.

  Examples:
    >>> decode_request(encode_request("parse", ["x", "y"]))
    ('parse', {'lines': ['x', 'y'], 'carry_raw': {}, 'carry_arc': {}})
  """
  if len(frame) < _REQ_HDR.size:
    raise ValueError("codon ipc request truncated")
  ver, kind_b, n = _REQ_HDR.unpack_from(frame, 0)
  if ver != PROTO_VER:
    raise RuntimeError(f"codon ipc version mismatch: {ver}")
  body = frame[_REQ_HDR.size : _REQ_HDR.size + n]
  if len(body) != n:
    raise RuntimeError("codon ipc request body short")
  if kind_b == KIND_FP:
    lines = [] if n == 0 else body.decode("utf-8").split("\n")
    return "fp", lines
  if kind_b == KIND_PARSE:
    envelope = (
      {"lines": [], "carry_raw": {}, "carry_arc": {}}
      if n == 0
      else json.loads(body.decode("utf-8"))
    )
    return "parse", envelope
  if kind_b == KIND_PARSE_FILE:
    envelope = (
      {"path": "", "flush_max_bytes": 0, "resume_byte": 0}
      if n == 0
      else json.loads(body.decode("utf-8"))
    )
    return "parse_file", envelope
  raise RuntimeError(f"codon ipc unknown kind={kind_b}")


def _stats_rows_to_columnar(stats: Any) -> dict[str, list[Any]]:
  """
  Normalize parse ``stats`` to parallel lists (SoA).

  Args:
    stats (Any): Columnar Mapping or sequence of row dicts.

  Returns:
    dict[str, list[Any]]: Host float/str columns (empty lists when none).

  Examples:
    >>> _stats_rows_to_columnar([{"time": 1.0, "host": "h"}])["host"]
    ['h']
  """
  cols = {c: [] for c in _HOST_FLOAT_COLS + _HOST_STR_COLS}
  if stats is None:
    return cols
  if isinstance(stats, Mapping) and "time" in stats:
    n = len(stats.get("time") or [])
    for c in cols:
      seq = stats.get(c)
      cols[c] = list(seq) if seq is not None else [None] * n
    return cols
  for row in stats or []:
    if not isinstance(row, Mapping):
      continue
    for c in cols:
      cols[c].append(row.get(c))
  return cols


def _host_float_wire_value(col: str, value: Any) -> float:
  """
  Pack one host float SoA cell for IPC (unix seconds or NaN for NULL).

  Args:
    col (str): Column name in ``_HOST_FLOAT_COLS``.
    value (Any): COPY-normalized cell (``datetime`` for ``time``).

  Returns:
    float: ``f64`` wire value.

  Examples:
    >>> import math
    >>> math.isnan(_host_float_wire_value("value", None))
    True
  """
  if value is None:
    return float("nan")
  if col == "time":
    dt = to_pydatetime_or_none(value)
    if dt is None:
      return float("nan")
    if isinstance(dt, datetime):
      return float(dt.timestamp())
    return float(dt)
  try:
    fv = float(value)
  except TypeError, ValueError:
    return float("nan")
  if isinstance(fv, float) and math.isnan(fv):
    return float("nan")
  return fv


def _host_float_from_wire(col: str, fv: float) -> Any:
  """
  Decode one host float SoA cell from IPC.

  Args:
    col (str): Column name in ``_HOST_FLOAT_COLS``.
    fv (float): Raw ``f64`` from the wire.

  Returns:
    Any: COPY-ready Python scalar.

  Examples:
    >>> _host_float_from_wire("value", float("nan")) is None
    True
  """
  if isinstance(fv, float) and math.isnan(fv):
    return None
  if col == "time":
    return to_pydatetime_or_none(fv)
  return fv


def encode_host_soa_bytes(stats: Any) -> bytes:
  """
  Encode finished host stats as length-prefixed columnar SoA bytes.

  Layout: ``u32 n`` then float cols (``n`` x ``f64`` each) then str cols
  (``n`` x (``u32`` len + UTF-8)).

  Args:
    stats (Any): Columnar Mapping or sequence of row dicts.

  Returns:
    bytes: SoA blob (``n=0`` is four zero bytes).

  Examples:
    >>> encode_host_soa_bytes([]) == b"\\x00\\x00\\x00\\x00"
    True
  """
  cols = _stats_rows_to_columnar(stats)
  n = len(cols["time"])
  parts: list[bytes] = [_U32.pack(n)]
  for c in _HOST_FLOAT_COLS:
    for v in cols[c]:
      parts.append(_F64.pack(_host_float_wire_value(c, v)))
  for c in _HOST_STR_COLS:
    for v in cols[c]:
      if v is None and c == "jid":
        raw = b""
      else:
        raw = b"" if v is None else str(v).encode("utf-8")
      parts.append(_U32.pack(len(raw)))
      parts.append(raw)
  return b"".join(parts)


def decode_host_soa_bytes(blob: bytes) -> dict[str, list[Any]]:
  """
  Decode ``encode_host_soa_bytes`` into a columnar Mapping.

  Args:
    blob (bytes): SoA payload starting with ``u32 n``.

  Returns:
    dict[str, list[Any]]: Parallel host columns.

  Raises:
    ValueError: When the blob is truncated or malformed.

  Examples:
    >>> decode_host_soa_bytes(encode_host_soa_bytes([]))["time"]
    []
  """
  if len(blob) < 4:
    raise ValueError("codon soa truncated header")
  (n,) = _U32.unpack_from(blob, 0)
  off = 4
  cols: dict[str, list[Any]] = {
    c: [] for c in _HOST_FLOAT_COLS + _HOST_STR_COLS
  }
  for c in _HOST_FLOAT_COLS:
    for _ in range(n):
      if off + 8 > len(blob):
        raise ValueError("codon soa truncated float")
      (fv,) = _F64.unpack_from(blob, off)
      off += 8
      cols[c].append(_host_float_from_wire(c, fv))
  for c in _HOST_STR_COLS:
    for _ in range(n):
      if off + 4 > len(blob):
        raise ValueError("codon soa truncated str len")
      (ln,) = _U32.unpack_from(blob, off)
      off += 4
      if off + ln > len(blob):
        raise ValueError("codon soa truncated str")
      text = blob[off : off + ln].decode("utf-8")
      off += ln
      if c == "jid" and ln == 0:
        cols[c].append(None)
      else:
        cols[c].append(text)
  if off != len(blob):
    raise ValueError("codon soa trailing bytes")
  return cols


def _procs_payload_to_columnar(procs: Any) -> dict[str, list[Any]]:
  """
  Normalize proc rows to parallel lists (SoA).

  Args:
    procs (Any): Columnar Mapping or sequence of row dicts.

  Returns:
    dict[str, list[Any]]: Proc columns (empty lists when none).

  Examples:
    >>> _procs_payload_to_columnar([{"proc": "p"}])["proc"]
    ['p']
  """
  all_cols = _PROC_STR_COLS + _PROC_I64_COLS + _PROC_I32_COLS
  cols = {c: [] for c in all_cols}
  if procs is None:
    return cols
  if isinstance(procs, Mapping) and "proc" in procs:
    n = len(procs.get("proc") or [])
    for c in all_cols:
      seq = procs.get(c)
      cols[c] = list(seq) if seq is not None else [None] * n
    return cols
  for row in procs or []:
    if not isinstance(row, Mapping):
      continue
    for c in all_cols:
      cols[c].append(row.get(c))
  return cols


def _proc_i64_value(v: Any) -> int:
  """
  Pack one proc metric for the 64-bit SoA wire, or the null sentinel.

  Args:
    v (Any): Metric cell (int, float, or None).

  Returns:
    int: The integer value, or ``-(2**63)`` when the cell is null.

  Examples:
    >>> _proc_i64_value(None) == -(2**63)
    True
  """
  if v is None:
    return -(2**63)
  try:
    return int(v)
  except TypeError, ValueError:
    return -(2**63)


def encode_proc_soa_bytes(procs: Any) -> bytes:
  """
  Encode proc rows as length-prefixed columnar SoA bytes.

  Args:
    procs (Any): Columnar Mapping or sequence of row dicts.

  Returns:
    bytes: SoA blob (``n=0`` is four zero bytes).

  Examples:
    >>> encode_proc_soa_bytes([]) == b"\\x00\\x00\\x00\\x00"
    True
  """
  cols = _procs_payload_to_columnar(procs)
  n = len(cols["proc"])
  parts: list[bytes] = [_U32.pack(n)]
  for c in _PROC_STR_COLS:
    for v in cols[c]:
      raw = b"" if v is None else str(v).encode("utf-8")
      parts.append(_U32.pack(len(raw)))
      parts.append(raw)
  for c in _PROC_I64_COLS:
    for v in cols[c]:
      parts.append(_I64.pack(_proc_i64_value(v)))
  for c in _PROC_I32_COLS:
    for v in cols[c]:
      try:
        iv = int(v) if v is not None else -(2**31)
      except TypeError, ValueError:
        iv = -(2**31)
      parts.append(_I32.pack(iv))
  return b"".join(parts)


def decode_proc_soa_bytes(blob: bytes) -> dict[str, list[Any]]:
  """
  Decode ``encode_proc_soa_bytes`` into a columnar Mapping.

  Args:
    blob (bytes): SoA payload starting with ``u32 n``.

  Returns:
    dict[str, list[Any]]: Parallel proc columns.

  Raises:
    ValueError: When the blob is truncated or malformed.

  Examples:
    >>> decode_proc_soa_bytes(encode_proc_soa_bytes([]))["proc"]
    []
  """
  if len(blob) < 4:
    raise ValueError("codon proc soa truncated header")
  (n,) = _U32.unpack_from(blob, 0)
  off = 4
  cols: dict[str, list[Any]] = {
    c: [] for c in _PROC_STR_COLS + _PROC_I64_COLS + _PROC_I32_COLS
  }
  null_i64 = -(2**63)
  null_i32 = -(2**31)
  for c in _PROC_STR_COLS:
    for _ in range(n):
      if off + 4 > len(blob):
        raise ValueError("codon proc soa truncated str len")
      (ln,) = _U32.unpack_from(blob, off)
      off += 4
      if off + ln > len(blob):
        raise ValueError("codon proc soa truncated str")
      cols[c].append(blob[off : off + ln].decode("utf-8") if ln else "")
      off += ln
  for c in _PROC_I64_COLS:
    for _ in range(n):
      if off + 8 > len(blob):
        raise ValueError("codon proc soa truncated i64")
      (iv,) = _I64.unpack_from(blob, off)
      off += 8
      cols[c].append(None if iv == null_i64 else iv)
  for c in _PROC_I32_COLS:
    for _ in range(n):
      if off + 4 > len(blob):
        raise ValueError("codon proc soa truncated i32")
      (iv,) = _I32.unpack_from(blob, off)
      off += 4
      cols[c].append(None if iv == null_i32 else iv)
  if off != len(blob):
    raise ValueError("codon proc soa trailing bytes")
  return cols


def encode_parse_file_request(
  path: str,
  flush_max_bytes: int,
  resume_byte: int = 0,
  exclude: list[str] | None = None,
  emit_insert: int = 0,
) -> bytes:
  """
  Encode a PROTO 4 file-owned parse request (no ``lines`` array).

  Counter/arc carry stays in the Codon worker for the whole session; it is
  not sent on the request.

  Args:
    path (str): Absolute stats file path.
    flush_max_bytes (int): Soft max bytes per flush window.
    resume_byte (int): Byte offset to resume reading.
    exclude (list[str] | None): Event types to skip; key omitted when empty.
    emit_insert (int): ``1`` asks for per-row INSERT frames (default COPY
      bytes); key omitted when ``0``.

  Returns:
    bytes: Wire bytes including ``PROTO_VER`` header.

  Examples:
    >>> encode_parse_file_request("/x", 1024)[:2]
    b'\\x04\\x03'
  """
  envelope = {
    "path": str(path),
    "flush_max_bytes": int(flush_max_bytes),
    "resume_byte": int(resume_byte),
  }
  if exclude:
    envelope["exclude"] = list(exclude)
  if emit_insert:
    envelope["emit_insert"] = 1
  body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode(
    "utf-8"
  )
  return _REQ_HDR.pack(PROTO_VER, KIND_PARSE_FILE, len(body)) + body


def _is_copy_frame(body: Mapping[str, Any]) -> bool:
  """
  Return True when ``body`` carries prebuilt COPY bytes (``stats["host_copy"]``).

  Args:
    body (Mapping[str, Any]): Parse result before binary encode.

  Returns:
    bool: True for COPY-bytes file-session frames.

  Examples:
    >>> _is_copy_frame({"stats": {"host_copy": b"x"}})
    True
    >>> _is_copy_frame({"stats": {}})
    False
  """
  stats = body.get("stats")
  return isinstance(stats, Mapping) and "host_copy" in stats


def _parse_response_sidecar(body: Mapping[str, Any]) -> dict[str, Any]:
  """
  Build the JSON sidecar for a parse IPC response.

  File-owned sessions set ``file_session`` on ``body`` and omit carry on the
  wire. Legacy line-batch parse may include ``carry_raw``/``carry_arc``.

  Args:
    body (Mapping[str, Any]): Parse result before binary SoA encode.

  Returns:
    dict[str, Any]: Sidecar dict for ``json.dumps``.

  Examples:
    >>> _parse_response_sidecar({"file_session": True, "marks": []})["marks"]
    []
  """
  side: dict[str, Any] = {
    "marks": body.get("marks", []),
    "finished": bool(body.get("finished", True)),
    "eof": bool(body.get("eof", False)),
    "copy_unique": bool(body.get("copy_unique", False)),
    "copy_frame": _is_copy_frame(body),
  }
  if not body.get("file_session"):
    side["carry_raw"] = body.get("carry_raw", {})
    side["carry_arc"] = body.get("carry_arc", {})
  return side


def _ipc_frame_from_parsed(
  parsed: dict[str, Any],
  *,
  eof: bool,
) -> dict[str, Any]:
  """
  Strip internal carry and mark a file-session IPC frame.

  Args:
    parsed (dict[str, Any]): Finalized Codon parse dict (may include carry).
    eof (bool): Whether this is the last frame for the file.

  Returns:
    dict[str, Any]: Payload for ``encode_response_ok("parse", ...)``.

  Examples:
    >>> _ipc_frame_from_parsed({"stats": {}, "procs": {}}, eof=True)["eof"]
    True
  """
  return {
    "stats": parsed.get("stats"),
    "procs": parsed.get("procs"),
    "marks": parsed.get("marks", []),
    "finished": bool(parsed.get("finished", True)),
    "copy_unique": bool(parsed.get("copy_unique", True)),
    "eof": eof,
    "file_session": True,
  }


def encode_response_ok(kind: str, body: Any) -> bytes:
  """
  Encode a successful child→parent response for ``kind``.

  Args:
    kind (str): ``fp`` or ``parse``.
    body (Any): Fingerprint 4-tuple, or the parse result dict
      ``{"stats", "procs", "marks", "carry_raw", "carry_arc", "finished"}``.

  Returns:
    bytes: Wire bytes including ``PROTO_VER`` header.

  Raises:
    ValueError: When ``kind`` is unknown.

  Examples:
    >>> len(encode_response_ok("fp", (1, 1, 0.0, 0.0))) > 6
    True
  """
  if kind == "fp":
    raw_stats, collapsed_rows, value_sum, proc_rows = body
    payload = FP_STRUCT.pack(
      int(raw_stats),
      int(collapsed_rows),
      float(value_sum),
      float(proc_rows),
    )
  elif kind == "parse":
    # Host + proc columnar SoA bytes + JSON sidecar (no pickle/msgpack).
    body = body or {}
    if _is_copy_frame(body):
      stats_blob = bytes(body["stats"].get("host_copy") or b"")
      proc_blob = bytes(body["stats"].get("proc_copy") or b"")
    else:
      stats_blob = encode_host_soa_bytes(body.get("stats"))
      proc_blob = encode_proc_soa_bytes(body.get("procs"))
    side = _parse_response_sidecar(body)
    side_bytes = json.dumps(
      side, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    payload = (
      _U32.pack(len(stats_blob))
      + stats_blob
      + _U32.pack(len(proc_blob))
      + proc_blob
      + side_bytes
    )
  else:
    raise ValueError(f"unknown codon ipc kind={kind!r}")
  return _RESP_HDR.pack(PROTO_VER, STATUS_OK, len(payload)) + payload


def encode_response_err(message: str) -> bytes:
  """
  Encode an error child→parent response.

  Args:
    message (str): UTF-8 error text.

  Returns:
    bytes: Wire bytes including ``PROTO_VER`` header.

  Examples:
    >>> encode_response_err("x")[1]
    1
  """
  payload = message.encode("utf-8")
  return _RESP_HDR.pack(PROTO_VER, STATUS_ERR, len(payload)) + payload


def decode_response(kind: str, frame: bytes) -> Any:
  """
  Decode one response frame for the request ``kind``.

  Args:
    kind (str): ``fp`` or ``parse`` (selects payload layout).
    frame (bytes): Full response including header.

  Returns:
    Any: Fingerprint 4-tuple, or the parse result dict ``{"stats", "procs",
    "marks", "carry_raw", "carry_arc", "finished"}`` with columnar ``stats``.

  Raises:
    RuntimeError: On version/status/length errors or worker err payload.
    ValueError: When header is truncated.

  Examples:
    >>> decode_response("fp", encode_response_ok("fp", (1, 2, 3.0, 4.0)))
    (1, 2, 3.0, 4.0)
  """
  if len(frame) < _RESP_HDR.size:
    raise ValueError("codon ipc response truncated")
  ver, status, n = _RESP_HDR.unpack_from(frame, 0)
  if ver != PROTO_VER:
    raise RuntimeError(f"codon ipc version mismatch: {ver}")
  payload = frame[_RESP_HDR.size : _RESP_HDR.size + n]
  if len(payload) != n:
    raise RuntimeError("codon ipc response body short")
  if status == STATUS_ERR:
    raise RuntimeError(payload.decode("utf-8", errors="replace"))
  if status != STATUS_OK:
    raise RuntimeError(f"codon ipc unknown status={status}")
  if kind == "fp":
    if len(payload) != FP_STRUCT.size:
      raise RuntimeError("codon ipc fp payload size mismatch")
    return FP_STRUCT.unpack(payload)
  if kind == "parse":
    if len(payload) < 8:
      raise RuntimeError("codon ipc parse payload truncated")
    off = 0
    (host_n,) = _U32.unpack_from(payload, off)
    off += 4
    host_blob = payload[off : off + host_n]
    off += host_n
    if len(host_blob) != host_n:
      raise RuntimeError("codon ipc host soa body short")
    (proc_n,) = _U32.unpack_from(payload, off)
    off += 4
    proc_blob = payload[off : off + proc_n]
    off += proc_n
    if len(proc_blob) != proc_n:
      raise RuntimeError("codon ipc proc soa body short")
    side = json.loads(payload[off:].decode("utf-8"))
    copy_frame = bool(side.get("copy_frame", False))
    return {
      "stats": (
        {"host_copy": host_blob, "proc_copy": proc_blob}
        if copy_frame
        else decode_host_soa_bytes(host_blob)
      ),
      "procs": {} if copy_frame else decode_proc_soa_bytes(proc_blob),
      "marks": side.get("marks", []),
      "carry_raw": side.get("carry_raw", {}),
      "carry_arc": side.get("carry_arc", {}),
      "finished": bool(side.get("finished", True)),
      "eof": bool(side.get("eof", False)),
      "copy_unique": bool(side.get("copy_unique", False)),
    }
  raise ValueError(f"unknown codon ipc kind={kind!r}")


WORKER_MEM_ENV = "HPCPERFSTATS_CODON_WORKER_MEM"
_BOEHM_STATS = (
  ("heap_mib", "GC_get_heap_size"),
  ("free_mib", "GC_get_free_bytes"),
  ("unmapped_mib", "GC_get_unmapped_bytes"),
  ("since_gc_mib", "GC_get_bytes_since_gc"),
  ("alloc_total_mib", "GC_get_total_bytes"),
)


def _proc_status_mib() -> dict[str, int]:
  """
  Return this process's ``VmRSS`` / ``RssAnon`` / ``RssFile`` / ``VmHWM`` in MiB.

  Returns:
    dict[str, int]: Lowercased ``/proc/self/status`` fields in MiB (empty when
      ``/proc`` is unavailable).

  Examples:
    >>> isinstance(_proc_status_mib(), dict)
    True
  """
  want = {
    "VmRSS": "rss",
    "RssAnon": "rss_anon",
    "RssFile": "rss_file",
    "VmHWM": "hwm",
  }
  out: dict[str, int] = {}
  try:
    with open("/proc/self/status", encoding="ascii") as fh:
      for line in fh:
        key, _, rest = line.partition(":")
        if key in want:
          out[want[key]] = int(rest.split()[0]) // 1024
  except OSError, ValueError, IndexError:
    pass
  return out


def _boehm_stats_mib() -> dict[str, int]:
  """
  Return Boehm GC heap statistics (MiB) plus ``gc_no`` from the loaded runtime.

  The Codon runtime (``libcodonrt``) is already mapped in this process, so
  loading it again by path returns the same collector instance.

  Returns:
    dict[str, int]: ``heap_mib``, ``free_mib``, ``unmapped_mib``,
      ``since_gc_mib``, ``alloc_total_mib`` and ``gc_no``; empty when the
      runtime is not loadable.

  Examples:
    >>> isinstance(_boehm_stats_mib(), dict)
    True
  """
  import ctypes

  path = os.path.join(CODON_PARSE_DIR, "libcodonrt.so")
  try:
    lib = ctypes.CDLL(path)
    out: dict[str, int] = {}
    for key, fn in _BOEHM_STATS:
      getter = getattr(lib, fn)
      getter.restype = ctypes.c_size_t
      out[key] = int(getter()) >> 20
    lib.GC_get_gc_no.restype = ctypes.c_size_t
    out["gc_no"] = int(lib.GC_get_gc_no())
    return out
  except OSError, AttributeError:
    return {}


def format_worker_mem_line(tag: str, **extra: Any) -> str:
  """
  Build one greppable ``codon_worker_mem`` line (process RSS split + Boehm).

  Args:
    tag (str): Sample point, ``open`` / ``win`` / ``eof``.
    **extra (Any): Caller fields (``file``, ``win``, ``frame_kib`` ...).

  Returns:
    str: ``codon_worker_mem tag=… pid=… rss=… rss_anon=… rss_file=… hwm=… heap_mib=…``.

  Examples:
    >>> format_worker_mem_line("open", file="f").startswith(
    ...   "codon_worker_mem tag=open"
    ... )
    True
  """
  fields = {"tag": tag, "pid": os.getpid(), **extra}
  fields.update(_proc_status_mib())
  fields.update(_boehm_stats_mib())
  return "codon_worker_mem " + " ".join(f"{k}={v}" for k, v in fields.items())


def codon_worker_parse_file_frames(
  path: str,
  flush_max_bytes: int,
  resume_byte: int = 0,
  *,
  slot: int = 0,
  exclude: list[str] | None = None,
  emit_insert: int | None = None,
) -> Any:
  """
  Open ``path`` in this process and yield one IPC-ready frame per flush window.

  File read, byte/sample batching, counter/arc carry, and Codon parse all run
  here. Frames omit ``carry_*`` (session state stays in-process until EOF).

  Args:
    path (str): Absolute stats file path.
    flush_max_bytes (int): Soft max bytes per window.
    resume_byte (int): Byte offset before reading.
    slot (int): Codon pyext stripe slot.
    exclude (list[str] | None): Event types to skip (``None``/empty = none);
      replaces the slot's previous list on every call.
    emit_insert (int | None): ``1`` for per-row INSERT frames, ``0`` for COPY
      bytes; ``None`` reads the calling task's ``current_ingest_emit()``.

  Yields:
    dict[str, Any]: Parse bodies suitable for ``encode_response_ok("parse",…)``.

  Raises:
    RuntimeError: When the baked pyext has no file-session exports.

  Examples:
    >>> list(codon_worker_parse_file_frames("/missing", 1024))  # doctest: +SKIP
    []
  """
  mod = _import_codon_parse_mod()
  open_fn = getattr(mod, "parse_stats_file_codon_open", None)
  next_fn = getattr(mod, "parse_stats_file_codon_next", None)
  if not callable(open_fn) or not callable(next_fn):
    raise RuntimeError(
      "Codon pyext missing parse_stats_file_codon_open/next "
      "(file-owned parse requires baked kernel)"
    )
  opened = open_fn(
    path,
    int(flush_max_bytes),
    int(resume_byte),
    int(slot),
    list(exclude) if exclude else None,
  )
  if not opened:
    return
  mem_on = os.environ.get(WORKER_MEM_ENV, "") == "1"
  fname = os.path.basename(path)
  if mem_on:
    print(
      format_worker_mem_line("open", file=fname), file=sys.stderr, flush=True
    )
  if emit_insert is None:
    from hpcperfstats.dbload.sync_timedb import current_ingest_emit

    emit_insert = 1 if current_ingest_emit() == "insert" else 0
  pending: dict[str, Any] | None = None
  win = 0
  while True:
    parsed = next_fn(int(slot), emit_insert=int(emit_insert))
    if mem_on:
      stats = (parsed or {}).get("stats") or {}
      print(
        format_worker_mem_line(
          "win" if parsed is not None else "eof",
          file=fname,
          win=win,
          host_kib=len(stats.get("host_copy") or b"") >> 10,
          proc_kib=len(stats.get("proc_copy") or b"") >> 10,
        ),
        file=sys.stderr,
        flush=True,
      )
    win += 1
    if parsed is None:
      break
    if pending is not None:
      yield pending
    pending = _ipc_frame_from_parsed(parsed, eof=False)
  if pending is not None:
    pending["eof"] = True
    yield pending


def _recv_frame(stdin: Any, hdr_size: int) -> bytes | None:
  """
  Read one length-prefixed frame (``!BBI`` header + body) from ``stdin``.

  Args:
    stdin (Any): File-like with ``.buffer`` (``sys.stdin``).
    hdr_size (int): Header byte count (``_REQ_HDR.size`` / ``_RESP_HDR.size``).

  Returns:
    bytes | None: Full frame, or ``None`` on EOF/short read.

  Examples:
    >>> callable(_recv_frame)
    True
  """
  raw_hdr = stdin.buffer.read(hdr_size)
  if not raw_hdr or len(raw_hdr) < hdr_size:
    return None
  _ver, _kind_or_status, n = struct.unpack_from("!BBI", raw_hdr, 0)
  body = stdin.buffer.read(n) if n else b""
  if len(body) < n:
    return None
  return raw_hdr + body


def _send_frame(stdout: Any, frame: bytes) -> None:
  """
  Write one complete frame to ``stdout`` and flush.

  Args:
    stdout (Any): File-like with ``.buffer`` (``sys.stdout``).
    frame (bytes): Encoded request or response.

  Returns:
    None: Always ``None``.

  Examples:
    >>> callable(_send_frame)
    True
  """
  stdout.buffer.write(frame)
  stdout.buffer.flush()


def main() -> int:
  """
  Stdio request loop: ``fp``, ``parse_file``, and legacy ``parse`` frames.

  Returns:
    int: Process exit code (``0``).

  Examples:
    >>> callable(main)
    True
  """
  _ensure_codon_on_path()
  while True:
    frame = _recv_frame(sys.stdin, _REQ_HDR.size)
    if frame is None:
      break
    try:
      kind, payload = decode_request(frame)
      if kind == "fp":
        _send_frame(
          sys.stdout,
          encode_response_ok("fp", codon_worker_fingerprint(list(payload))),
        )
      elif kind == "parse_file":
        sent = False
        for frame in codon_worker_parse_file_frames(
          str(payload.get("path") or ""),
          int(payload.get("flush_max_bytes") or 1),
          int(payload.get("resume_byte") or 0),
          slot=0,
          exclude=payload.get("exclude") or None,
          emit_insert=int(payload.get("emit_insert") or 0),
        ):
          _send_frame(sys.stdout, encode_response_ok("parse", frame))
          sent = True
        if not sent:
          # No frames (empty file): the parent waits for an ``eof`` frame.
          _send_frame(
            sys.stdout,
            encode_response_ok(
              "parse",
              _ipc_frame_from_parsed(
                {"stats": {"host_copy": b"", "proc_copy": b""}},
                eof=True,
              ),
            ),
          )
      elif kind == "parse":
        if os.environ.get(
          "HPCPERFSTATS_CODON_IPC_LEGACY_PARSE", ""
        ).strip().lower() not in {"1", "true", "yes", "on"}:
          _send_frame(
            sys.stdout,
            encode_response_err("legacy parse IPC disabled"),
          )
        else:
          _send_frame(
            sys.stdout,
            encode_response_ok(
              "parse",
              codon_worker_parse(
                list(payload.get("lines") or []),
                payload.get("carry_raw") or {},
                payload.get("carry_arc") or {},
                exclude=payload.get("exclude") or None,
              ),
            ),
          )
      else:
        _send_frame(
          sys.stdout,
          encode_response_err(f"unknown codon ipc kind={kind!r}"),
        )
    except Exception as exc:
      _send_frame(
        sys.stdout,
        encode_response_err(f"{type(exc).__name__}: {exc}"),
      )
  return 0


def run_worker() -> None:
  """
  Run ``main()`` then leave without interpreter finalization.

  After stdin EOF (parent exited or closed the pipe) the Codon/Boehm runtime
  threads can spin in ``sched_yield`` during normal shutdown and never exit
  (hpcperfstats04 2026-10-27: 40 workers at 100% CPU, no files open). Flush
  the stdio buffers and ``os._exit`` so EOF always ends the process.

  Returns:
    None

  Examples:
    >>> callable(run_worker)
    True
  """
  code = 1
  try:
    code = main()
  finally:
    for stream in (sys.stdout, sys.stderr):
      with contextlib.suppress(Exception):
        stream.flush()
    os._exit(code)


if __name__ == "__main__":
  run_worker()
