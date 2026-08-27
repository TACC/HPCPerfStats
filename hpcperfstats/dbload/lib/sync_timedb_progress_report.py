"""
In-process day + window ledgers for sync_timedb 10-minute progress reports.

``reconstruct-coordinator`` emits omit-zeros ``progress day=`` lines and a
``status`` footer every :data:`PROGRESS_REPORT_INTERVAL_S` seconds. Subsystems
increment counters via :func:`record` / :func:`record_day` (prefer calendar day
when known).

Attributes:
  PROGRESS_REPORT_INTERVAL_S: Emit interval in seconds (600).
  DayActivityLedger: Per-day counters for one reporting window.
  ProgressReportState: Thread-safe day + window ledgers with emit helpers.
  WindowHealthCounters: Cross-cut / undated counters for one window.
  _BUSY_KIND_ORDER: Stable kind order for ``busy=`` tokens.
  _DAY_COUNTER_KEYS: Ordered day-line counter token names.
  _STATE: Process-wide :class:`ProgressReportState` singleton.
  _STATE_LOCK: Guards lazy creation of ``_STATE``.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

PROGRESS_REPORT_INTERVAL_S = 600.0

# Day-line tokens (prefer day). Values are counter attribute names.
_DAY_COUNTER_KEYS = (
    "discover",
    "discover_skip",
    "unresolved_day",
    "skip_complete",
    "ingest",
    "ingested",
    "db_skip",
    "timeout",
    "fail",
    "archive",
    "gate_skip",
    "ingest_handoff",
    "soft_requeue",
    "append_drop",
    "sealed",
    "dedupe",
    "raw_delete",
    "tar_delete",
    "deferred_age",
    "yielded",
    "dead_letter",
    "attempt_bump",
    "requeue_noprogress",
    "reconstruct_enq",
    "incomplete_seen",
    "populate_wait",
    "populate_degraded",
)

_BUSY_KIND_ORDER = ("ingest", "append", "discover", "day_close")


class DayActivityLedger:
  """
  Mutable per-day activity counters for one progress window.

  Attributes:
    counters: Mapping of token name to non-negative int.
  """

  __slots__ = ("counters",)

  def __init__(self) -> None:
    """
    Initialize zeroed day counters.

    Returns:
      None

    Examples:
      >>> DayActivityLedger().counters["ingest"]
      0
    """
    self.counters: Dict[str, int] = {k: 0 for k in _DAY_COUNTER_KEYS}

  def add(self, key: str, n: int = 1) -> None:
    """
    Increment a day counter when ``key`` is known.

    Args:
      key (str): Token name from :data:`_DAY_COUNTER_KEYS`.
      n (int): Delta (ignored when non-positive).

    Returns:
      None

    Examples:
      >>> d = DayActivityLedger(); d.add("gate_skip", 2); d.counters["gate_skip"]
      2
    """
    if n <= 0 or key not in self.counters:
      return
    self.counters[key] += int(n)

  def has_activity(self) -> bool:
    """
    Return True when any counter is non-zero.

    Returns:
      bool: Whether this day should emit a progress line.

    Examples:
      >>> DayActivityLedger().has_activity()
      False
    """
    return any(v for v in self.counters.values())


class WindowHealthCounters:
  """
  Cross-cut and undated counters for one progress window.

  Attributes:
    undated: Counters for events without a resolvable calendar day.
    dead_letter_by_kind: Undated dead-letter counts keyed by job kind.
    queue_depth_start: Queued depths snapped at window open (kind → depth).
  """

  __slots__ = ("undated", "dead_letter_by_kind", "queue_depth_start")

  def __init__(self) -> None:
    """
    Initialize empty window health counters.

    Returns:
      None

    Examples:
      >>> WindowHealthCounters().undated["skip_complete"]
      0
    """
    self.undated: Dict[str, int] = defaultdict(int)
    self.dead_letter_by_kind: Dict[str, int] = defaultdict(int)
    self.queue_depth_start: Dict[str, int] = {}

  def add_undated(self, key: str, n: int = 1) -> None:
    """
    Increment an undated fallback counter.

    Args:
      key (str): Token name.
      n (int): Delta.

    Returns:
      None

    Examples:
      >>> w = WindowHealthCounters(); w.add_undated("attempt_bump"); w.undated["attempt_bump"]
      1
    """
    if n <= 0:
      return
    self.undated[str(key)] += int(n)


def format_busy_token(busy_kinds: Iterable[str]) -> str:
  """
  Render ``busy=`` listing local in-flight kinds (includes discover).

  Args:
    busy_kinds (Iterable[str]): Kind names that are locally busy.

  Returns:
    str: ``busy=a,b`` or empty string when none.

  Examples:
    >>> format_busy_token(["append", "ingest", "discover"])
    'busy=ingest,append,discover'
    >>> format_busy_token([])
    ''
  """
  present: Set[str] = {str(k).strip() for k in busy_kinds if str(k).strip()}
  ordered = [k for k in _BUSY_KIND_ORDER if k in present]
  for k in sorted(present):
    if k not in ordered:
      ordered.append(k)
  if not ordered:
    return ""
  return "busy=%s" % ",".join(ordered)


def resolve_oldest_queued_day(
  client: Any,
  *,
  now: Optional[datetime] = None,
) -> Tuple[Optional[str], Optional[int]]:
  """
  Resolve the oldest queued catchup ingest or day_close day and its age.

  Prefers the catchup ZSET head (oldest calendar day first). Falls back to the
  day_close LIST head when it looks like ``YYYY-MM-DD``.

  Args:
    client (Any): Redis client.
    now (Optional[datetime]): Clock for age; defaults to UTC now.

  Returns:
    Tuple[Optional[str], Optional[int]]: ``(oldest_day, oldest_age_s)``.

  Examples:
    >>> resolve_oldest_queued_day(None)
    (None, None)
  """
  if client is None:
    return None, None
  from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq

  candidates: List[date] = []
  try:
    zkey = jq.job_queue_key(jq.JOB_KIND_INGEST)
    lo, hi = jq.ingest_score_range("catchup")
    rows = client.zrangebyscore(
        zkey,
        jq._score_arg(lo),
        jq._score_arg(hi),
        start=0,
        num=1,
        withscores=True,
    )
    if rows:
      _member, score = rows[0]
      cal = jq.decode_catchup_calendar_day(score)
      if cal is not None:
        candidates.append(cal)
  except Exception:
    pass
  try:
    lkey = jq.job_queue_key(jq.JOB_KIND_DAY_CLOSE)
    head = client.lindex(lkey, 0)
    text = str(head or "").strip()
    if len(text) >= 10 and text[:10].count("-") == 2:
      candidates.append(date.fromisoformat(text[:10]))
  except Exception:
    pass
  if not candidates:
    return None, None
  oldest = min(candidates)
  clock = now or datetime.now(timezone.utc)
  if clock.tzinfo is None:
    clock = clock.replace(tzinfo=timezone.utc)
  day_start = datetime(
      oldest.year, oldest.month, oldest.day, tzinfo=timezone.utc,
  )
  age_s = max(0, int((clock - day_start).total_seconds()))
  return oldest.isoformat(), age_s


def format_day_progress_line(day: str, ledger: DayActivityLedger) -> str:
  """
  Format one omit-zeros ``queue_orchestrator progress day=`` line.

  Args:
    day (str): ISO calendar day.
    ledger (DayActivityLedger): Day counters.

  Returns:
    str: Log line body, or empty when no activity.

  Examples:
    >>> d = DayActivityLedger(); d.add("gate_skip", 3)
    >>> format_day_progress_line("2025-05-05", d)
    'queue_orchestrator progress day=2025-05-05 gate_skip=3'
  """
  if not ledger.has_activity():
    return ""
  parts = ["queue_orchestrator progress day=%s" % day]
  for key in _DAY_COUNTER_KEYS:
    val = int(ledger.counters.get(key, 0) or 0)
    if val:
      parts.append("%s=%d" % (key, val))
  return " ".join(parts)


def format_orphan_inflight(
  census_inflight: Mapping[str, int],
  busy_kinds: Iterable[str],
) -> str:
  """
  Render Redis inflight kinds that are not locally busy.

  Args:
    census_inflight (Mapping[str, int]): Kind → inflight count.
    busy_kinds (Iterable[str]): Local busy kinds.

  Returns:
    str: ``orphan_inflight=kind:n,...`` or empty.

  Examples:
    >>> format_orphan_inflight({"discover": 2, "ingest": 0}, ["ingest"])
    'orphan_inflight=discover:2'
  """
  busy = {str(k).strip() for k in busy_kinds if str(k).strip()}
  parts = []
  for kind, n in sorted(census_inflight.items()):
    count = int(n or 0)
    if count <= 0:
      continue
    if kind in busy:
      continue
    parts.append("%s:%d" % (kind, count))
  if not parts:
    return ""
  return "orphan_inflight=%s" % ",".join(parts)


def format_queue_ratio_token(name: str, inflight: int, queued: int) -> str:
  """
  Format one ``name=current/queued`` token; omit when both zero.

  Args:
    name (str): Token label (e.g. ``ingest_hot``).
    inflight (int): Current (in-flight) count.
    queued (int): Queued depth.

  Returns:
    str: Token or empty string.

  Examples:
    >>> format_queue_ratio_token("append", 2, 120)
    'append=2/120'
    >>> format_queue_ratio_token("append", 0, 0)
    ''
  """
  i = int(inflight or 0)
  q = int(queued or 0)
  if i == 0 and q == 0:
    return ""
  return "%s=%d/%d" % (name, i, q)


def format_status_line(
  *,
  band_ratios: Mapping[str, Mapping[str, int]],
  queue_deltas: Mapping[str, int],
  busy_kinds: Iterable[str],
  orphan_inflight: Mapping[str, int],
  oldest_day: Optional[str] = None,
  oldest_age_s: Optional[int] = None,
  undated: Optional[Mapping[str, int]] = None,
  dead_letter_by_kind: Optional[Mapping[str, int]] = None,
) -> str:
  """
  Format omit-zeros ``queue_orchestrator status`` footer (always one line).

  Args:
    band_ratios (Mapping[str, Mapping[str, int]]): Name →
      ``{"inflight": n, "queued": n}``.
    queue_deltas (Mapping[str, int]): Queued-depth delta vs window start.
    busy_kinds (Iterable[str]): Local busy kinds.
    orphan_inflight (Mapping[str, int]): Kind → Redis inflight.
    oldest_day (Optional[str]): Oldest queued day token.
    oldest_age_s (Optional[int]): Age of oldest head in seconds.
    undated (Optional[Mapping[str, int]]): Undated fallback counters.
    dead_letter_by_kind (Optional[Mapping[str, int]]): Undated dead letters.

  Returns:
    str: Status line (``status idle`` when nothing else).

  Examples:
    >>> format_status_line(
    ...   band_ratios={"append": {"inflight": 1, "queued": 2}},
    ...   queue_deltas={},
    ...   busy_kinds=["append"],
    ...   orphan_inflight={},
    ... )
    'queue_orchestrator status append=1/2 busy=append'
  """
  parts = ["queue_orchestrator status"]
  for name in (
      "ingest_hot",
      "ingest_catchup",
      "append",
      "discover",
      "day_close",
  ):
    entry = band_ratios.get(name) or {}
    tok = format_queue_ratio_token(
        name,
        int(entry.get("inflight", 0) or 0),
        int(entry.get("queued", 0) or 0),
    )
    if tok:
      parts.append(tok)
  for name, delta in sorted(queue_deltas.items()):
    d = int(delta or 0)
    if d == 0:
      continue
    parts.append("%s_q_delta=%d" % (name, d))
  if oldest_day:
    parts.append("oldest_day=%s" % oldest_day)
  if oldest_age_s is not None and int(oldest_age_s) > 0:
    parts.append("oldest_age_s=%d" % int(oldest_age_s))
  orphan = format_orphan_inflight(orphan_inflight, busy_kinds)
  if orphan:
    parts.append(orphan)
  busy = format_busy_token(busy_kinds)
  if busy:
    parts.append(busy)
  for kind, n in sorted((dead_letter_by_kind or {}).items()):
    if int(n or 0):
      parts.append("dead_letter_%s=%d" % (kind, int(n)))
  for key, n in sorted((undated or {}).items()):
    if int(n or 0):
      parts.append("%s=%d" % (key, int(n)))
  if len(parts) == 1:
    parts.append("idle")
  return " ".join(parts)


class ProgressReportState:
  """
  Thread-safe day + window ledgers shared by orchestrator subsystems.

  Attributes:
    _lock: Guards mutation and emit.
    _days: Calendar day → :class:`DayActivityLedger`.
    window: :class:`WindowHealthCounters` for the open window.
    _window_started_mono: Monotonic time when the current window opened.
  """

  def __init__(self) -> None:
    """
    Create an empty progress state.

    Returns:
      None

    Examples:
      >>> ProgressReportState().snapshot_days()
      {}
    """
    self._lock = threading.Lock()
    self._days: Dict[str, DayActivityLedger] = {}
    self.window = WindowHealthCounters()
    self._window_started_mono = time.monotonic()

  def reset_window(
    self,
    *,
    queue_depth_start: Optional[Mapping[str, int]] = None,
  ) -> None:
    """
    Clear day/window counters and open a new reporting window.

    Args:
      queue_depth_start (Optional[Mapping[str, int]]): Optional queued depths
        to snap for delta computation.

    Returns:
      None

    Examples:
      >>> ProgressReportState().reset_window(queue_depth_start={"append": 3})
    """
    with self._lock:
      self._days = {}
      self.window = WindowHealthCounters()
      if queue_depth_start:
        self.window.queue_depth_start = {
            str(k): int(v or 0) for k, v in queue_depth_start.items()
        }
      self._window_started_mono = time.monotonic()

  def record_day(self, day: Optional[str], key: str, n: int = 1) -> None:
    """
    Record activity on a calendar day, or undated when day is missing.

    Args:
      day (Optional[str]): ISO day or None/empty for undated fallback.
      key (str): Counter token.
      n (int): Delta.

    Returns:
      None

    Examples:
      >>> s = ProgressReportState(); s.record_day("2025-01-01", "ingest", 1)
    """
    if n <= 0:
      return
    text = str(day or "").strip()
    with self._lock:
      if text:
        ledger = self._days.get(text)
        if ledger is None:
          ledger = DayActivityLedger()
          self._days[text] = ledger
        ledger.add(key, n)
      else:
        self.window.add_undated(key, n)

  def record_dead_letter(
    self,
    day: Optional[str],
    kind: str,
    n: int = 1,
  ) -> None:
    """
    Record a dead-letter event (day-attributed when possible).

    Args:
      day (Optional[str]): ISO day or None.
      kind (str): Job kind.
      n (int): Delta.

    Returns:
      None

    Examples:
      >>> s = ProgressReportState(); s.record_dead_letter(None, "ingest", 1)
    """
    if n <= 0:
      return
    text = str(day or "").strip()
    with self._lock:
      if text:
        ledger = self._days.get(text)
        if ledger is None:
          ledger = DayActivityLedger()
          self._days[text] = ledger
        ledger.add("dead_letter", n)
      else:
        self.window.dead_letter_by_kind[str(kind)] += int(n)

  def snapshot_days(self) -> Dict[str, DayActivityLedger]:
    """
    Return a shallow copy of day ledgers under the lock.

    Returns:
      Dict[str, DayActivityLedger]: Day → ledger.

    Examples:
      >>> ProgressReportState().snapshot_days()
      {}
    """
    with self._lock:
      return dict(self._days)

  def emit_lines(
    self,
    *,
    band_ratios: Mapping[str, Mapping[str, int]],
    busy_kinds: Iterable[str],
    census_inflight: Mapping[str, int],
    queue_depth_now: Mapping[str, int],
    oldest_day: Optional[str] = None,
    oldest_age_s: Optional[int] = None,
  ) -> List[str]:
    """
    Build progress + status lines for the current window (does not reset).

    Args:
      band_ratios (Mapping[str, Mapping[str, int]]): Status band ratios.
      busy_kinds (Iterable[str]): Local busy kinds.
      census_inflight (Mapping[str, int]): Kind → Redis inflight.
      queue_depth_now (Mapping[str, int]): Kind → queued depth now.
      oldest_day (Optional[str]): Oldest queued day.
      oldest_age_s (Optional[int]): Oldest head age seconds.

    Returns:
      List[str]: Day lines (if any) then one status line.

    Examples:
      >>> ProgressReportState().emit_lines(
      ...   band_ratios={}, busy_kinds=[], census_inflight={},
      ...   queue_depth_now={},
      ... )
      ['queue_orchestrator status idle']
    """
    with self._lock:
      day_items = sorted(self._days.items())
      undated = dict(self.window.undated)
      dead = dict(self.window.dead_letter_by_kind)
      start = dict(self.window.queue_depth_start)
    lines: List[str] = []
    for day, ledger in day_items:
      line = format_day_progress_line(day, ledger)
      if line:
        lines.append(line)
    deltas: Dict[str, int] = {}
    for kind, now_depth in queue_depth_now.items():
      if kind not in start:
        continue
      deltas[kind] = int(now_depth or 0) - int(start.get(kind, 0) or 0)
    # Prefer ingest_hot / ingest_catchup delta names when present in band map.
    renamed: Dict[str, int] = {}
    for kind, delta in deltas.items():
      if kind == "ingest":
        continue
      renamed[kind] = delta
    lines.append(
        format_status_line(
            band_ratios=band_ratios,
            queue_deltas=renamed,
            busy_kinds=busy_kinds,
            orphan_inflight=census_inflight,
            oldest_day=oldest_day,
            oldest_age_s=oldest_age_s,
            undated=undated,
            dead_letter_by_kind=dead,
        ),
    )
    return lines

  def maybe_emit_and_reset(
    self,
    *,
    now_mono: Optional[float] = None,
    interval_s: float = PROGRESS_REPORT_INTERVAL_S,
    band_ratios: Mapping[str, Mapping[str, int]],
    busy_kinds: Iterable[str],
    census_inflight: Mapping[str, int],
    queue_depth_now: Mapping[str, int],
    oldest_day: Optional[str] = None,
    oldest_age_s: Optional[int] = None,
    log_fn: Optional[Callable[..., None]] = None,
  ) -> bool:
    """
    When the interval has elapsed, emit lines, reset the window, return True.

    Args:
      now_mono (Optional[float]): Monotonic now (tests inject).
      interval_s (float): Emit interval.
      band_ratios (Mapping[str, Mapping[str, int]]): Status band ratios.
      busy_kinds (Iterable[str]): Local busy kinds.
      census_inflight (Mapping[str, int]): Kind → Redis inflight.
      queue_depth_now (Mapping[str, int]): Kind → queued depth now.
      oldest_day (Optional[str]): Oldest queued day.
      oldest_age_s (Optional[int]): Oldest head age seconds.
      log_fn (Optional[Callable[..., None]]): Logger (``log_print``-compatible).

    Returns:
      bool: True when a report was emitted.

    Examples:
      >>> ProgressReportState().maybe_emit_and_reset(
      ...   now_mono=0.0, interval_s=600.0, band_ratios={}, busy_kinds=[],
      ...   census_inflight={}, queue_depth_now={},
      ... )
      False
    """
    now = float(now_mono if now_mono is not None else time.monotonic())
    with self._lock:
      elapsed = now - self._window_started_mono
      if elapsed < float(interval_s):
        return False
    lines = self.emit_lines(
        band_ratios=band_ratios,
        busy_kinds=busy_kinds,
        census_inflight=census_inflight,
        queue_depth_now=queue_depth_now,
        oldest_day=oldest_day,
        oldest_age_s=oldest_age_s,
    )
    quiet = log_fn
    if quiet is not None:
      for line in lines:
        quiet(line, flush=True)
    self.reset_window(queue_depth_start=queue_depth_now)
    return True


_STATE_LOCK = threading.Lock()
_STATE: Optional[ProgressReportState] = None


def get_progress_state() -> ProgressReportState:
  """
  Return the process-wide progress report singleton.

  Returns:
    ProgressReportState: Shared ledger state.

  Examples:
    >>> isinstance(get_progress_state(), ProgressReportState)
    True
  """
  global _STATE
  with _STATE_LOCK:
    if _STATE is None:
      _STATE = ProgressReportState()
    return _STATE


def reset_progress_state_for_tests() -> ProgressReportState:
  """
  Replace the singleton (tests only).

  Returns:
    ProgressReportState: Fresh state.

  Examples:
    >>> isinstance(reset_progress_state_for_tests(), ProgressReportState)
    True
  """
  global _STATE
  with _STATE_LOCK:
    _STATE = ProgressReportState()
    return _STATE


def record(day: Optional[str], key: str, n: int = 1) -> None:
  """
  Record on the process-wide progress state (prefer day).

  Args:
    day (Optional[str]): ISO day or None.
    key (str): Counter token.
    n (int): Delta.

  Returns:
    None

  Examples:
    >>> reset_progress_state_for_tests(); record("2025-01-01", "ingest", 1)
  """
  get_progress_state().record_day(day, key, n)
