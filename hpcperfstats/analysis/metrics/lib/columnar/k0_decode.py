"""
K0: decode PostgreSQL ``COPY … (FORMAT BINARY)`` host_data tuples (NumPy
reference).

Stream layout (PostgreSQL binary COPY): 11-byte signature, int32 flags,
int32 header-extension length, then per tuple an int16 field count followed
by ``int32 length`` + payload per field (``-1`` length is NULL), ending with
an int16 ``-1`` trailer. The job fetch selects, in this order:
``time`` (timestamptz, int64 microseconds since 2000-01-01), ``host``,
``type``, ``event``, ``dev`` (text), ``value``, ``arc``, ``delta``
(``real``, big-endian float32) and optionally ``owned`` (int4).

This module is the oracle for the Codon decoder; it favours clarity over
speed and stays pandas-free.

Attributes:
  COPY_SIGNATURE: Bytes every binary COPY stream starts with.
  PG_EPOCH_OFFSET_US: Microseconds between the Unix and PostgreSQL epochs.
  FETCH_COLUMNS: Column names in stream order (without ``owned``).
  _H: Big-endian int16 struct (field count / trailer).
  _I: Big-endian int32 struct (lengths, flags, ``owned``).
  _Q: Big-endian int64 struct (timestamptz microseconds).
  _F: Big-endian float32 struct (``real`` columns).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar import codon_bridge
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import (
  ID_DTYPE,
  TIME_DTYPE,
  VALUE_DTYPE,
  NameInterner,
)

COPY_SIGNATURE = b"PGCOPY\n\xff\r\n\x00"
PG_EPOCH_OFFSET_US = 946_684_800_000_000
FETCH_COLUMNS: tuple[str, ...] = (
  "time",
  "host",
  "type",
  "event",
  "dev",
  "value",
  "arc",
  "delta",
)

_H = struct.Struct(">h")
_I = struct.Struct(">i")
_Q = struct.Struct(">q")
_F = struct.Struct(">f")


@dataclass
class DecodedChunk:
  """
  Columns decoded from one COPY stream, ids per the shared interner.

  Attributes:
    time_us: int64 epoch microseconds.
    host_id: int32 host ids.
    type_id: int32 type ids.
    event_id: int32 event ids.
    dev_id: int32 signed device ids.
    owned: uint8 ownership flags.
    value: float64 values (NaN for NULL).
    arc: float64 arcs (NaN for NULL).
    delta: float64 deltas (NaN for NULL).
  """

  time_us: np.ndarray
  host_id: np.ndarray
  type_id: np.ndarray
  event_id: np.ndarray
  dev_id: np.ndarray
  owned: np.ndarray
  value: np.ndarray
  arc: np.ndarray
  delta: np.ndarray

  def __len__(self) -> int:
    """
    Row count.

    Returns:
      int: Rows in the chunk.

    Examples:
      >>> len(empty_chunk())
      0
    """
    return int(self.time_us.shape[0])


def empty_chunk() -> DecodedChunk:
  """
  Return a zero-row chunk with the contract dtypes.

  Returns:
    DecodedChunk: Empty columns.

  Examples:
    >>> empty_chunk().value.dtype
    dtype('float64')
  """
  return DecodedChunk(
    time_us=np.empty(0, dtype=TIME_DTYPE),
    host_id=np.empty(0, dtype=ID_DTYPE),
    type_id=np.empty(0, dtype=ID_DTYPE),
    event_id=np.empty(0, dtype=ID_DTYPE),
    dev_id=np.empty(0, dtype=ID_DTYPE),
    owned=np.empty(0, dtype=np.uint8),
    value=np.empty(0, dtype=VALUE_DTYPE),
    arc=np.empty(0, dtype=VALUE_DTYPE),
    delta=np.empty(0, dtype=VALUE_DTYPE),
  )


def concat_chunks(chunks: list[DecodedChunk]) -> DecodedChunk:
  """
  Concatenate decoded chunks (same interner) into one chunk.

  Args:
    chunks (list[DecodedChunk]): Chunks in any order.

  Returns:
    DecodedChunk: Combined columns (an empty chunk for no input).

  Examples:
    >>> len(concat_chunks([empty_chunk(), empty_chunk()]))
    0
  """
  parts = [c for c in chunks if len(c)]
  if not parts:
    return empty_chunk()
  if len(parts) == 1:
    return parts[0]
  return DecodedChunk(
    **{
      name: np.concatenate([getattr(c, name) for c in parts])
      for name in (
        "time_us",
        "host_id",
        "type_id",
        "event_id",
        "dev_id",
        "owned",
        "value",
        "arc",
        "delta",
      )
    }
  )


def _float4_or_nan(buf: bytes, off: int, length: int) -> float:
  """
  Decode one ``real`` field (``-1`` length is NULL -> NaN).

  Args:
    buf (bytes): Stream buffer.
    off (int): Offset of the payload.
    length (int): Field length from the stream.

  Returns:
    float: Value as float64 (exact float32 widening) or NaN.

  Examples:
    >>> import struct
    >>> _float4_or_nan(struct.pack(">f", 1.5), 0, 4)
    1.5
  """
  if length < 0:
    return float("nan")
  return float(_F.unpack_from(buf, off)[0])


def decode_copy_binary(
  data: bytes | bytearray | memoryview,
  interner: NameInterner,
  *,
  has_owned_column: bool = False,
) -> DecodedChunk:
  """
  Decode one binary COPY stream: the Codon K0 kernel when baked, else the
  pure-Python reference (:func:`decode_copy_binary_reference`).

  Args:
    data (bytes | bytearray | memoryview): Complete COPY stream.
    interner (NameInterner): Shared name tables for this job.
    has_owned_column (bool): Ninth ``owned`` int4 column present.

  Returns:
    DecodedChunk: Decoded columns.

  Raises:
    ValueError: Malformed stream (reference path).
    CodonKernelError: Malformed stream or kernel failure (Codon path).

  Examples:
    >>> len(decode_copy_binary(encode_copy_binary([]), NameInterner()))
    0
  """
  if codon_bridge.kernels() is not None:
    return _decode_copy_binary_codon(
      data, interner, has_owned_column=has_owned_column
    )
  return decode_copy_binary_reference(
    data, interner, has_owned_column=has_owned_column
  )


def _decode_copy_binary_codon(
  data: bytes | bytearray | memoryview,
  interner: NameInterner,
  *,
  has_owned_column: bool,
) -> DecodedChunk:
  """
  Codon K0 decode: numeric columns from the kernel, names interned here.

  The kernel returns table ids plus one distinct-name table for the four
  text columns; only those few names cross into Python and the per-row ids
  are remapped with one ``take`` per column.

  Args:
    data (bytes | bytearray | memoryview): Complete COPY stream.
    interner (NameInterner): Shared name tables for this job.
    has_owned_column (bool): Ninth ``owned`` int4 column present.

  Returns:
    DecodedChunk: Decoded columns.

  Examples:
    >>> _decode_copy_binary_codon(
    ...   b"", NameInterner(), has_owned_column=False
    ... )  # doctest: +SKIP
  """
  cols, name_col, names = codon_bridge.copy_decode(
    data, has_owned=has_owned_column
  )
  intern = (interner.host, interner.type, interner.event, interner.dev)
  table = np.array(
    [intern[int(c)](n) for c, n in zip(name_col.tolist(), names, strict=True)],
    dtype=np.int64,
  )

  def remap(local: np.ndarray) -> np.ndarray:
    """
    Table ids to interner ids.

    Args:
      local (np.ndarray): int32 table ids.

    Returns:
      np.ndarray: int32 interner ids.

    Examples:
      >>> True
      True
    """
    if local.shape[0] == 0:
      return np.empty(0, dtype=ID_DTYPE)
    return table[local.astype(np.int64)].astype(ID_DTYPE)

  return DecodedChunk(
    time_us=cols["time_us"],
    host_id=remap(cols["host"]),
    type_id=remap(cols["type"]),
    event_id=remap(cols["event"]),
    dev_id=remap(cols["dev"]),
    owned=cols["owned"],
    value=cols["value"],
    arc=cols["arc"],
    delta=cols["delta"],
  )


def decode_copy_binary_reference(
  data: bytes | bytearray | memoryview,
  interner: NameInterner,
  *,
  has_owned_column: bool = False,
) -> DecodedChunk:
  """
  Decode one binary COPY stream of host_data tuples into id/float columns
  (pure-Python oracle for the Codon kernel).

  Args:
    data (bytes | bytearray | memoryview): Complete COPY stream (header,
      tuples, trailer).
    interner (NameInterner): Shared name tables for this job.
    has_owned_column (bool): True when a ninth ``owned`` int4 column follows
      ``delta`` (shared-node fetch); otherwise every row is owned.

  Returns:
    DecodedChunk: Decoded columns.

  Raises:
    ValueError: The stream does not start with the COPY signature, a tuple
      has an unexpected field count, or a NULL ``time``.

  Examples:
    >>> from hpcperfstats.analysis.metrics.lib.columnar.k0_decode import (
    ...   encode_copy_binary,
    ... )
    >>> raw = encode_copy_binary(
    ...   [
    ...     (1_700_000_000_000_000, "h1", "cpu", "user", None, 1.0, None, 2.0),
    ...   ]
    ... )
    >>> ch = decode_copy_binary_reference(raw, NameInterner())
    >>> ch.time_us.tolist(), float(ch.value[0]), float(ch.arc[0])
    ([1700000000000000], 1.0, nan)
  """
  buf = bytes(data)
  if not buf.startswith(COPY_SIGNATURE):
    raise ValueError("not a PostgreSQL binary COPY stream")
  off = len(COPY_SIGNATURE)
  _flags = _I.unpack_from(buf, off)[0]
  off += 4
  ext_len = _I.unpack_from(buf, off)[0]
  off += 4 + max(0, ext_len)
  expected_fields = 9 if has_owned_column else 8
  times: list[int] = []
  hosts: list[int] = []
  types: list[int] = []
  events: list[int] = []
  devs: list[int] = []
  owned: list[int] = []
  values: list[float] = []
  arcs: list[float] = []
  deltas: list[float] = []
  end = len(buf)
  host_cache: dict[bytes, int] = {}
  type_cache: dict[bytes, int] = {}
  event_cache: dict[bytes, int] = {}
  dev_cache: dict[bytes, int] = {b"": 0}
  while off < end:
    nf = _H.unpack_from(buf, off)[0]
    off += 2
    if nf == -1:
      break
    if nf != expected_fields:
      raise ValueError(f"unexpected field count {nf}")
    # time (int64 µs since 2000-01-01)
    ln = _I.unpack_from(buf, off)[0]
    off += 4
    if ln != 8:
      raise ValueError("NULL or malformed time field")
    times.append(_Q.unpack_from(buf, off)[0] + PG_EPOCH_OFFSET_US)
    off += 8
    # host / type / event / dev (text)
    ids: list[int] = []
    for cache, intern in (
      (host_cache, interner.host),
      (type_cache, interner.type),
      (event_cache, interner.event),
      (dev_cache, interner.dev),
    ):
      ln = _I.unpack_from(buf, off)[0]
      off += 4
      if ln < 0:
        raw = b""
      else:
        raw = buf[off : off + ln]
        off += ln
      ident = cache.get(raw)
      if ident is None:
        ident = intern(raw.decode("utf-8"))
        cache[raw] = ident
      ids.append(ident)
    hosts.append(ids[0])
    types.append(ids[1])
    events.append(ids[2])
    devs.append(ids[3])
    # value / arc / delta (real)
    for sink in (values, arcs, deltas):
      ln = _I.unpack_from(buf, off)[0]
      off += 4
      sink.append(_float4_or_nan(buf, off, ln))
      if ln > 0:
        off += ln
    if has_owned_column:
      ln = _I.unpack_from(buf, off)[0]
      off += 4
      if ln == 4:
        owned.append(1 if _I.unpack_from(buf, off)[0] else 0)
        off += 4
      else:
        owned.append(0)
        if ln > 0:
          off += ln
    else:
      owned.append(1)
  return DecodedChunk(
    time_us=np.asarray(times, dtype=TIME_DTYPE),
    host_id=np.asarray(hosts, dtype=ID_DTYPE),
    type_id=np.asarray(types, dtype=ID_DTYPE),
    event_id=np.asarray(events, dtype=ID_DTYPE),
    dev_id=np.asarray(devs, dtype=ID_DTYPE),
    owned=np.asarray(owned, dtype=np.uint8),
    value=np.asarray(values, dtype=VALUE_DTYPE),
    arc=np.asarray(arcs, dtype=VALUE_DTYPE),
    delta=np.asarray(deltas, dtype=VALUE_DTYPE),
  )


def encode_copy_binary(
  rows: list[tuple[Any, ...]],
  *,
  with_owned: bool = False,
) -> bytes:
  """
  Encode host_data rows as a binary COPY stream (tests and fixtures).

  Rows are ``(time_us, host, type, event, dev, value, arc, delta)`` plus an
  ``owned`` int when ``with_owned`` is set; ``None`` encodes NULL.

  Args:
    rows (list[tuple[Any, ...]]): Row tuples.
    with_owned (bool): Append the ``owned`` int4 column.

  Returns:
    bytes: Complete COPY stream with header and trailer.

  Examples:
    >>> encode_copy_binary([])[:11] == COPY_SIGNATURE
    True
  """
  out = bytearray(COPY_SIGNATURE)
  out += _I.pack(0)
  out += _I.pack(0)
  nf = 9 if with_owned else 8
  for row in rows:
    out += _H.pack(nf)
    t = row[0]
    out += _I.pack(8) + _Q.pack(int(t) - PG_EPOCH_OFFSET_US)
    for text in row[1:5]:
      if text is None:
        out += _I.pack(-1)
      else:
        raw = str(text).encode("utf-8")
        out += _I.pack(len(raw)) + raw
    for num in row[5:8]:
      if num is None:
        out += _I.pack(-1)
      else:
        out += _I.pack(4) + _F.pack(float(num))
    if with_owned:
      own = row[8] if len(row) > 8 else 1
      if own is None:
        out += _I.pack(-1)
      else:
        out += _I.pack(4) + _I.pack(int(bool(own)))
  out += _H.pack(-1)
  return bytes(out)
