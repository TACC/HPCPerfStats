"""
Runtime record of the ``(type, event)`` series update_metrics reads per job.

Every ``FrameIndex`` reduction goes through ``FrameIndex._select``, which
calls :func:`record` with the selected series names. Reads are grouped by a
*stage* (``metrics``, ``summary_plot``, ``job_detail``, ``type_detail``)
that call sites set with :func:`consumer_stage`; an untagged read lands in
``other`` so a new read path shows up instead of hiding. Per-type panels that
drop an event because a sample is missing call :func:`record_dropped`.

Recording is opt-in per job id: :func:`begin` opens a jid, reads for jids
that were never opened are ignored (scripts, tests, the web side), and
:func:`flush` (success) or :func:`discard` (error / timeout) closes it. The
registry is keyed by jid, not by table instance, because the table can be
evicted and reopened between the metrics and the Job Detail stages.

``flush`` sends one Valkey pipeline per jid: for each stage an
``HSET metrics_consumed:v1:{stage} "{type}\\t{event}" <epoch>`` with every
pair of that stage, plus an ``EXPIRE``. Overwrites are idempotent, so no
read-modify-write is needed. Valkey errors are logged once per process and
swallowed: the job result never depends on this record.

Attributes:
  CONSUMED_KEY_PREFIX: Valkey hash key prefix before the stage name.
  CONSUMED_KEY_TTL_SECONDS: TTL refreshed on every flush (30 days).
  STAGES: Stage names written by update_metrics, in display order.
  STAGE_OTHER: Stage used when no :func:`consumer_stage` is active.
  STAGE_TYPE_DETAIL_DROPPED: Stage for events dropped from a per-type panel.
  MAX_PAIRS_PER_JID: Hard cap on recorded pairs per jid (memory bound).
  MAX_ACTIVE_JIDS: Hard cap on open jids (oldest is dropped beyond it).
  _stage_var: Context variable holding the active stage name.
  _lock: Guards ``_active`` (the metrics pool runs threads).
  _active: Open jids mapped to ``stage -> set[(type, event)]``.
  _flush_error_logged: True once a Valkey flush error was logged.
  logger: Module logger.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

CONSUMED_KEY_PREFIX = "metrics_consumed:v1:"
CONSUMED_KEY_TTL_SECONDS = 30 * 24 * 3600
STAGE_OTHER = "other"
STAGE_TYPE_DETAIL_DROPPED = "type_detail_dropped"
STAGES = (
  "metrics",
  "summary_plot",
  "job_detail",
  "type_detail",
  STAGE_TYPE_DETAIL_DROPPED,
  STAGE_OTHER,
)
MAX_PAIRS_PER_JID = 20_000
MAX_ACTIVE_JIDS = 4096

_stage_var: ContextVar[str] = ContextVar("consumer_stage", default=STAGE_OTHER)
_lock = threading.Lock()
_active: dict[str, dict[str, set[tuple[str, str]]]] = {}
_flush_error_logged = False


def stage_key(stage: str) -> str:
  """
  Return the Valkey hash key for one stage.

  Args:
    stage (str): Stage name from ``STAGES``.

  Returns:
    str: ``metrics_consumed:v1:{stage}``.

  Examples:
    >>> stage_key("metrics")
    'metrics_consumed:v1:metrics'
  """
  return f"{CONSUMED_KEY_PREFIX}{stage}"


def pair_field(typ: str, event: str) -> str:
  """
  Encode one ``(type, event)`` pair as a hash field (``type\\tevent``).

  Writer, reader, payload and UI all use this one format.

  Args:
    typ (str): ``host_data.type``.
    event (str): ``host_data.event``.

  Returns:
    str: Tab-joined field name.

  Examples:
    >>> pair_field("host_cpu", "user")
    'host_cpu\\tuser'
  """
  return f"{typ}\t{event}"


def parse_pair_field(field: Any) -> tuple[str, str] | None:
  """
  Decode a hash field written by :func:`pair_field`.

  Args:
    field (Any): Field name as ``str`` or ``bytes``.

  Returns:
    tuple[str, str] | None: ``(type, event)``, or None when malformed.

  Examples:
    >>> parse_pair_field(b"host_cpu\\tuser")
    ('host_cpu', 'user')
    >>> parse_pair_field("no-tab") is None
    True
  """
  if isinstance(field, bytes):
    field = field.decode("utf-8", "replace")
  typ, sep, event = str(field).partition("\t")
  if not sep or not typ or not event:
    return None
  return typ, event


@contextlib.contextmanager
def recording_paused() -> Iterator[None]:
  """
  Suspend implicit recording inside the block.

  Used where the caller decides afterwards which stage a read belongs to
  (the per-type panel records kept events as ``type_detail`` and dropped
  ones as ``type_detail_dropped``). Explicit ``stage=`` records still land.

  Yields:
    None: Control returns to the caller with recording suspended.

  Examples:
    >>> begin("doc-7")
    >>> with recording_paused():
    ...   record("doc-7", [("a", "b")])
    >>> take("doc-7")
    {}
  """
  token = _stage_var.set("")
  try:
    yield
  finally:
    _stage_var.reset(token)


def current_stage() -> str:
  """
  Return the stage active in this thread / context.

  Returns:
    str: Stage name (``other`` outside any :func:`consumer_stage`).

  Examples:
    >>> current_stage()
    'other'
  """
  return _stage_var.get()


@contextlib.contextmanager
def consumer_stage(name: str) -> Iterator[None]:
  """
  Tag every recorded read inside the block with stage ``name``.

  The stage lives in a context variable, so each metrics-pool thread keeps
  its own value. Tagging never changes what is read or computed.

  Args:
    name (str): Stage name from ``STAGES``.

  Yields:
    None: Control returns to the caller with the stage set.

  Examples:
    >>> with consumer_stage("metrics"):
    ...   current_stage()
    'metrics'
    >>> current_stage()
    'other'
  """
  token = _stage_var.set(str(name))
  try:
    yield
  finally:
    _stage_var.reset(token)


def begin(jid: Any) -> None:
  """
  Open ``jid`` for recording (idempotent; keeps existing records).

  Beyond ``MAX_ACTIVE_JIDS`` open jids the oldest is dropped, so a missed
  :func:`discard` cannot grow the registry without bound.

  Args:
    jid (Any): Job id (stringified).

  Returns:
    None

  Examples:
    >>> begin("doc-1")
    >>> is_active("doc-1")
    True
    >>> discard("doc-1")
  """
  key = str(jid)
  with _lock:
    if key in _active:
      return
    while len(_active) >= MAX_ACTIVE_JIDS:
      _active.pop(next(iter(_active)))
    _active[key] = {}


def is_active(jid: Any) -> bool:
  """
  Return True when ``jid`` is open for recording.

  Args:
    jid (Any): Job id.

  Returns:
    bool: Whether :func:`begin` opened it and it is not yet closed.

  Examples:
    >>> is_active("never-opened")
    False
  """
  with _lock:
    return str(jid) in _active


def record(
  jid: Any, pairs: Iterable[tuple[str, str]], stage: str | None = None
) -> None:
  """
  Add ``pairs`` to ``jid`` under ``stage`` (default: the active stage).

  A no-op for jids that are not open. Pairs beyond ``MAX_PAIRS_PER_JID``
  are ignored (cost is O(selected series), never O(rows)).

  Args:
    jid (Any): Job id.
    pairs (Iterable[tuple[str, str]]): ``(type, event)`` names as stored in
      ``host_data``.
    stage (str | None): Stage override; None uses :func:`current_stage`.

  Returns:
    None

  Examples:
    >>> begin("doc-2")
    >>> record("doc-2", [("host_cpu", "user")])
    >>> take("doc-2")
    {'other': [('host_cpu', 'user')]}
  """
  key = str(jid)
  name = stage or _stage_var.get()
  if not name:
    return
  with _lock:
    stages = _active.get(key)
    if stages is None:
      return
    used = sum(len(s) for s in stages.values())
    bucket = stages.setdefault(name, set())
    for pair in pairs:
      if used >= MAX_PAIRS_PER_JID:
        break
      item = (str(pair[0]), str(pair[1]))
      if item not in bucket:
        bucket.add(item)
        used += 1


def record_dropped(jid: Any, typ: str, event: str) -> None:
  """
  Record an event the per-type panel dropped (a sample was missing).

  Args:
    jid (Any): Job id.
    typ (str): Type name.
    event (str): Event name.

  Returns:
    None

  Examples:
    >>> begin("doc-3")
    >>> record_dropped("doc-3", "host_cpu", "nice")
    >>> take("doc-3")
    {'type_detail_dropped': [('host_cpu', 'nice')]}
  """
  record(jid, [(typ, event)], stage=STAGE_TYPE_DETAIL_DROPPED)


def take(jid: Any) -> dict[str, list[tuple[str, str]]]:
  """
  Close ``jid`` and return its records (``stage -> sorted pairs``).

  Used by the prewarm worker process to ship its records to the parent.

  Args:
    jid (Any): Job id.

  Returns:
    dict[str, list[tuple[str, str]]]: Records; empty when not open.

  Examples:
    >>> take("never-opened")
    {}
  """
  with _lock:
    stages = _active.pop(str(jid), None) or {}
  return {name: sorted(pairs) for name, pairs in stages.items() if pairs}


def merge(jid: Any, payload: Mapping[str, Iterable[Any]] | None) -> None:
  """
  Merge records returned by :func:`take` (another process) into ``jid``.

  Unknown stages and malformed pairs are skipped; a jid that is not open
  ignores the merge.

  Args:
    jid (Any): Job id.
    payload (Mapping[str, Iterable[Any]] | None): ``stage -> pairs``.

  Returns:
    None

  Examples:
    >>> begin("doc-4")
    >>> merge("doc-4", {"job_detail": [["nfs", "read"]]})
    >>> take("doc-4")
    {'job_detail': [('nfs', 'read')]}
  """
  if not isinstance(payload, Mapping):
    return
  for name, pairs in payload.items():
    if str(name) not in STAGES:
      continue
    good = [
      (p[0], p[1])
      for p in pairs or ()
      if isinstance(p, (list, tuple)) and len(p) == 2
    ]
    record(jid, good, stage=str(name))


def discard(jid: Any) -> None:
  """
  Drop ``jid``'s records without writing them (error / timeout paths).

  Args:
    jid (Any): Job id.

  Returns:
    None

  Examples:
    >>> begin("doc-5")
    >>> discard("doc-5")
    >>> is_active("doc-5")
    False
  """
  with _lock:
    _active.pop(str(jid), None)


def flush(jid: Any, client: Any = None, *, now: float | None = None) -> int:
  """
  Close ``jid`` and write its records to Valkey in one pipeline.

  Args:
    jid (Any): Job id.
    client (Any): valkey-py client; None uses
      ``cache_utils.get_valkey_client()``.
    now (float | None): Epoch seconds stored as the field value (default:
      ``time.time()``).

  Returns:
    int: Number of fields written (0 when nothing was recorded, no client
    exists, or Valkey failed).

  Examples:
    >>> class _P:
    ...   def __init__(self):
    ...     self.calls = []
    ...
    ...   def hset(self, key, mapping):
    ...     self.calls.append((key, mapping))
    ...
    ...   def expire(self, key, ttl):
    ...     pass
    ...
    ...   def execute(self):
    ...     return []
    >>> class _C:
    ...   def __init__(self):
    ...     self.p = _P()
    ...
    ...   def pipeline(self, transaction=False):
    ...     return self.p
    >>> c = _C()
    >>> begin("doc-6")
    >>> record("doc-6", [("a", "b")], "metrics")
    >>> flush("doc-6", c, now=5)
    1
    >>> c.p.calls
    [('metrics_consumed:v1:metrics', {'a\\tb': 5})]
  """
  global _flush_error_logged
  records = take(jid)
  if not records:
    return 0
  if client is None:
    try:
      from hpcperfstats.site.lib.machine.cache_utils import get_valkey_client

      client = get_valkey_client()
    except Exception:
      client = None
  if client is None:
    return 0
  stamp = int(time.time() if now is None else now)
  written = 0
  try:
    pipe = client.pipeline(transaction=False)
    for name, pairs in records.items():
      key = stage_key(name)
      pipe.hset(key, mapping={pair_field(t, e): stamp for t, e in pairs})
      pipe.expire(key, CONSUMED_KEY_TTL_SECONDS)
      written += len(pairs)
    pipe.execute()
  except Exception as exc:
    if not _flush_error_logged:
      _flush_error_logged = True
      logger.warning(
        "consumed_series flush failed jid=%s (logged once): %s", jid, exc
      )
    return 0
  return written
