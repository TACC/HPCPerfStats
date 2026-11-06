"""
Per-host record of monitor ``$`` rotations, written by listend to Valkey.

Each ``$`` schema rotation makes listend close the host's ``current`` segment
(a digit-epoch file) and start a new one. listend LPUSHes one JSON entry per
rotation to ``ingest_rotation:v1:{fqdn}`` and keeps the newest
``ROTATION_HISTORY_LEN`` entries:

``{rotated_at, closed_epoch, closed_bytes, new_epoch, closed_last_sample_at}``

``closed_last_sample_at`` is the Unix second of the closed file's last sample
line (``read_stats_file_tail_identity``), the same clock as ``host_data.time``.
The ingest coverage sweeper compares it with the host's newest ``host_data``
sample to tell "everything closed is ingested" from a real ingest backlog
(``raw-segment-rotation-freshness-contract.mdc``).

Attributes:
  INGEST_ROTATION_KEY_PREFIX: Valkey list key prefix before the FQDN.
  ROTATION_HISTORY_LEN: Rotations kept per host (``LTRIM 0 len-1``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

INGEST_ROTATION_KEY_PREFIX = "ingest_rotation:v1:"
ROTATION_HISTORY_LEN = 8


def ingest_rotation_key(fqdn: str) -> str:
  """
  Return the Valkey list key holding a host's rotation history.

  Args:
    fqdn (str): Host FQDN.

  Returns:
    str: ``ingest_rotation:v1:{fqdn}``.

  Examples:
    >>> ingest_rotation_key("c101-001.example.com")
    'ingest_rotation:v1:c101-001.example.com'
  """
  return f"{INGEST_ROTATION_KEY_PREFIX}{fqdn}"


def push_rotation_record(
  valkey_client: Any,
  fqdn: str,
  record: Mapping[str, Any],
  *,
  ttl_seconds: int,
) -> None:
  """
  LPUSH one rotation entry, trim the history and refresh the TTL.

  One pipelined round trip; no read-modify-write. Raises whatever the client
  raises (the listend writer thread logs and swallows it).

  Args:
    valkey_client (Any): valkey-py client with ``pipeline()``.
    fqdn (str): Host FQDN (must contain a ``.``; others are ignored).
    record (Mapping[str, Any]): Entry fields (JSON-serialisable).
    ttl_seconds (int): Key TTL (same as ``recent_host``).

  Returns:
    None

  Examples:
    >>> class _P:
    ...   def __init__(self):
    ...     self.ops = []
    ...
    ...   def lpush(self, k, v):
    ...     self.ops.append(("lpush", k))
    ...
    ...   def ltrim(self, k, a, b):
    ...     self.ops.append(("ltrim", a, b))
    ...
    ...   def expire(self, k, t):
    ...     self.ops.append(("expire", t))
    ...
    ...   def execute(self):
    ...     return []
    >>> class _C:
    ...   p = _P()
    ...
    ...   def pipeline(self, transaction=False):
    ...     return self.p
    >>> c = _C()
    >>> push_rotation_record(
    ...   c, "a.example.com", {"rotated_at": 1}, ttl_seconds=60
    ... )
    >>> c.p.ops
    [('lpush', 'ingest_rotation:v1:a.example.com'), ('ltrim', 0, 7), ('expire', 60)]
  """
  if valkey_client is None or not fqdn or "." not in fqdn:
    return
  key = ingest_rotation_key(fqdn)
  payload = json.dumps(dict(record), separators=(",", ":"), sort_keys=True)
  pipe = valkey_client.pipeline(transaction=False)
  pipe.lpush(key, payload)
  pipe.ltrim(key, 0, ROTATION_HISTORY_LEN - 1)
  pipe.expire(key, max(1, int(ttl_seconds)))
  pipe.execute()


def parse_rotation_entries(raw_entries: Any) -> list[dict[str, Any]]:
  """
  Decode an ``LRANGE`` result, newest first, skipping malformed entries.

  Args:
    raw_entries (Any): List of JSON strings / bytes, or None.

  Returns:
    list[dict[str, Any]]: Entries with ``rotated_at`` set.

  Examples:
    >>> parse_rotation_entries([b'{"rotated_at": 5}', "bad", '{"x": 1}'])
    [{'rotated_at': 5}]
  """
  out: list[dict[str, Any]] = []
  for raw in raw_entries or ():
    if isinstance(raw, bytes):
      raw = raw.decode("utf-8", "replace")
    try:
      doc = json.loads(raw)
    except TypeError, ValueError:
      continue
    if isinstance(doc, dict) and doc.get("rotated_at") is not None:
      out.append(doc)
  return out


def load_rotation_entries(
  valkey_client: Any, fqdn: str
) -> list[dict[str, Any]]:
  """
  Read a host's rotation history (newest first); empty on any error.

  Args:
    valkey_client (Any): valkey-py client with ``lrange`` or None.
    fqdn (str): Host FQDN.

  Returns:
    list[dict[str, Any]]: Parsed entries.

  Examples:
    >>> load_rotation_entries(None, "a.example.com")
    []
  """
  if valkey_client is None:
    return []
  try:
    raw = valkey_client.lrange(
      ingest_rotation_key(fqdn), 0, ROTATION_HISTORY_LEN - 1
    )
  except Exception:
    return []
  return parse_rotation_entries(raw)
