"""
K0: decode PostgreSQL ``COPY … (FORMAT BINARY)`` host_data tuples (NumPy
reference).

Stream layout (PostgreSQL binary COPY): 11-byte signature, int32 flags,
int32 header-extension length, then per tuple an int16 field count followed
by ``int32 length`` + payload per field (``-1`` length is NULL), ending with
an int16 ``-1`` trailer. The job fetch selects, in this order:
``time`` (timestamptz, int64 microseconds since 2000-01-01), ``host``,
``type``, ``event``, ``dev`` (text), ``value``, ``arc``, ``delta``
(``real`` or ``double precision``: big-endian float32 or float64, told apart
by the field length) and optionally ``owned`` (int4).

This module is the oracle for the Codon decoder; it favours clarity over
speed and stays pandas-free.

Attributes:
  COPY_SIGNATURE: Bytes every binary COPY stream starts with.
  PG_EPOCH_OFFSET_US: Microseconds between the Unix and PostgreSQL epochs.
  _H: Big-endian int16 struct (field count / trailer).
  _I: Big-endian int32 struct (lengths, flags, ``owned``).
  _Q: Big-endian int64 struct (timestamptz microseconds).
  _FLOAT_BY_LEN: Big-endian float struct per field length (4 is
    ``real``, 8 is ``double precision``).
"""

from __future__ import annotations

import struct
from dataclasses import fields
from typing import Any

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar import codon_bridge
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import (
  ID_DTYPE,
  TIME_DTYPE,
  VALUE_DTYPE,
  DecodedChunk,
  NameInterner,
)

COPY_SIGNATURE = b"PGCOPY\n\xff\r\n\x00"
PG_EPOCH_OFFSET_US = 946_684_800_000_000

_H = struct.Struct(">h")
_I = struct.Struct(">i")
_Q = struct.Struct(">q")
_FLOAT_BY_LEN = {4: struct.Struct(">f"), 8: struct.Struct(">d")}


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
  Concatenate decoded chunks (same interner) into one chunk, consuming them.

  ``chunks`` is emptied and every input column is popped as soon as its
  combined column exists, so the peak is the inputs plus one column, not
  two copies of the job.

  Args:
    chunks (list[DecodedChunk]): Chunks in any order (cleared in place).

  Returns:
    DecodedChunk: Combined columns (an empty chunk for no input).

  Examples:
    >>> parts = [empty_chunk(), empty_chunk()]
    >>> len(concat_chunks(parts)), parts
    (0, [])
  """
  parts = [c for c in chunks if len(c)]
  chunks.clear()
  if len(parts) <= 1:
    return parts[0] if parts else empty_chunk()
  return DecodedChunk(
    **{
      f.name: np.concatenate([c.pop(f.name) for c in parts])
      for f in fields(DecodedChunk)
    }
  )


def _float_or_nan(buf: bytes, off: int, length: int) -> float:
  """
  Decode one ``real`` (length 4) or ``double precision`` (length 8) field;
  length ``-1`` is NULL -> NaN.

  Args:
    buf (bytes): Stream buffer.
    off (int): Offset of the payload.
    length (int): Field length from the stream.

  Returns:
    float: Value as float64 (float32 widens exactly) or NaN.

  Raises:
    ValueError: Any other field length.

  Examples:
    >>> import struct
    >>> _float_or_nan(struct.pack(">f", 1.5), 0, 4)
    1.5
    >>> _float_or_nan(struct.pack(">d", 0.1), 0, 8)
    0.1
  """
  if length == -1:
    return float("nan")
  fmt = _FLOAT_BY_LEN.get(length)
  if fmt is None:
    raise ValueError(f"float field length {length} is not 4, 8 or -1")
  return float(fmt.unpack_from(buf, off)[0])


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
  are remapped with one ``take`` per column. The kernel writes the float
  columns as float64 (``VALUE_DTYPE``), so they are used as returned.

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
    value=cols.pop("value"),
    arc=cols.pop("arc"),
    delta=cols.pop("delta"),
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
      has an unexpected field count, a NULL ``time``, or a float field
      whose length is not 4, 8 or -1.

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
    # time (int64 us since 2000-01-01)
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
    # value / arc / delta (real or double precision)
    for sink in (values, arcs, deltas):
      ln = _I.unpack_from(buf, off)[0]
      off += 4
      sink.append(_float_or_nan(buf, off, ln))
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
  float_width: int = 4,
) -> bytes:
  """
  Encode host_data rows as a binary COPY stream (tests and fixtures).

  Rows are ``(time_us, host, type, event, dev, value, arc, delta)`` plus an
  ``owned`` int when ``with_owned`` is set; ``None`` encodes NULL.

  Args:
    rows (list[tuple[Any, ...]]): Row tuples.
    with_owned (bool): Append the ``owned`` int4 column.
    float_width (int): Float field length: 4 encodes the value columns as
      ``real`` (PG15), 8 as ``double precision`` (PG18).

  Returns:
    bytes: Complete COPY stream with header and trailer.

  Examples:
    >>> encode_copy_binary([])[:11] == COPY_SIGNATURE
    True
    >>> row = [(0, "h", "t", "e", None, 1, 1, 1)]
    >>> wide = encode_copy_binary(row, float_width=8)
    >>> len(wide) - len(encode_copy_binary(row))
    12
  """
  fmt = _FLOAT_BY_LEN[float_width]
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
        out += _I.pack(float_width) + fmt.pack(float(num))
    if with_owned:
      own = row[8] if len(row) > 8 else 1
      if own is None:
        out += _I.pack(-1)
      else:
        out += _I.pack(4) + _I.pack(int(bool(own)))
  out += _H.pack(-1)
  return bytes(out)
