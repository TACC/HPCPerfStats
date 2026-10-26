"""
Per-file wait and CPU attribution for sync_timedb ingest (default off).

Closed-book stage holds measure wall seconds, so a thread that was runnable
but not scheduled, blocked on a write-queue slot, or parked behind a lock
looks the same as a thread burning CPU. This module splits that wall into
on-CPU, run-queue wait and sleep from ``/proc/thread-self/schedstat``, times
the parse -> write-queue handoff, and exposes system-level pressure tokens
(PSI, run queue, Boehm GC counters) for the 60 s census. It is measurement
only: it never blocks, kills or reorders work. The process-wide switch rides
``reset_parse_stage_timing`` so one INI key (``sync_ingest_telemetry``)
controls all ingest telemetry.

Attributes:
  WAIT_LOG_KEYS: Keys appended to ingest outcome lines when enabled.
  WAIT_COUNT_KEYS: Subset of ``WAIT_LOG_KEYS`` logged as integers.
  _SCHEDSTAT_PATH: Per-thread scheduler statistics file.
  _STAGE_CPU_GROUPS: Hold-key -> logged CPU group for stage CPU roll-up.
  _PSI_FILES: PSI file path -> census token name.
  _BOEHM_GETTERS: Boehm GC getter -> (token, divisor) pairs.
  _ROLE_PREFIXES: Python thread-name prefix -> census role.
  _PYGC_PAUSES: Lock-free queue of automatic-GC pauses ``(seconds, generation)``.
  _PYGC_INSTALLED: Set once the ``gc.callbacks`` hook is registered.
  _pygc_tls: Per-thread start stamp for the GC callback.
  _enabled: Process-wide switch (sticky across per-file resets).
  _ledger: Per-context ledger for the file being ingested.
"""

from __future__ import annotations

import collections
import contextlib
import contextvars
import gc
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

WAIT_LOG_KEYS: tuple[str, ...] = (
  "thr_cpu_s",
  "thr_runq_s",
  "thr_sleep_s",
  "stages_cpu_s",
  "feed_cpu_s",
  "build_df_cpu_s",
  "delta_cpu_s",
  "collapse_cpu_s",
  "arc_cpu_s",
  "wq_put_s",
  "wq_resid_s",
  "wq_join_s",
  "wq_n",
  "w_exec_s",
  "w_cpu_s",
  "w_runq_s",
  "slot_wait_s",
  "codon_primary",
  "emit_start",
)
WAIT_COUNT_KEYS: tuple[str, ...] = ("wq_n", "codon_primary", "emit_start")
_SCHEDSTAT_PATH = "/proc/thread-self/schedstat"
_STAGE_CPU_GROUPS: dict[str, str] = {
  "feed_s": "feed_cpu_s",
  "proc_merge_s": "build_df_cpu_s",
  "hw_df_s": "build_df_cpu_s",
  "proc_df_s": "build_df_cpu_s",
  "delta_s": "delta_cpu_s",
  "collapse_normalize_s": "collapse_cpu_s",
  "collapse_host_sum_s": "collapse_cpu_s",
  "collapse_ccm_s": "collapse_cpu_s",
  "collapse_gpu_s": "collapse_cpu_s",
  "collapse_concat_s": "collapse_cpu_s",
  "collapse_sort_s": "collapse_cpu_s",
  "arc_s": "arc_cpu_s",
}
_PSI_FILES: tuple[tuple[str, str], ...] = (
  ("/proc/pressure/cpu", "psi_cpu10"),
  ("/proc/pressure/memory", "psi_mem10"),
  ("/proc/pressure/io", "psi_io10"),
)
_BOEHM_GETTERS: tuple[tuple[str, str, int], ...] = (
  ("GC_get_gc_no", "boehm_gc_no", 1),
  ("GC_get_heap_size", "boehm_heap_mib", 1024 * 1024),
  ("GC_get_free_bytes", "boehm_free_mib", 1024 * 1024),
  ("GC_get_full_gc_total_time", "boehm_gc_ms", 1),
)
_ROLE_PREFIXES: tuple[tuple[str, str], ...] = (
  ("ingest-write", "write"),
  ("ingest-pool", "ingest"),
  ("ingest-coordinator", "coord"),
  ("append-coordinator", "coord"),
  ("day-close-coordinator", "coord"),
  ("reconstruct", "coord"),
  ("MainThread", "coord"),
  ("archive", "archive"),
  ("append-pool", "archive"),
  ("populate", "archive"),
)
_PYGC_PAUSES: collections.deque[tuple[float, int]] = collections.deque(
  maxlen=100_000
)
_PYGC_INSTALLED = threading.Event()
_pygc_tls = threading.local()
_enabled = False


@dataclass
class _Ledger:
  """
  Mutable accumulator for one file on one ingest thread.

  Attributes:
    acc: Seconds (or counts) keyed by ``WAIT_LOG_KEYS`` entries.
    wall0: ``perf_counter`` at ``begin_file``.
    cpu0_ns: Thread on-CPU nanoseconds at ``begin_file`` (or ``None``).
    runq0_ns: Thread run-queue wait nanoseconds at ``begin_file``.
  """

  acc: dict[str, float] = field(default_factory=dict)
  wall0: float = 0.0
  cpu0_ns: int | None = None
  runq0_ns: int = 0


_ledger: contextvars.ContextVar[_Ledger | None] = contextvars.ContextVar(
  "wait_telemetry_ledger", default=None
)


def set_enabled(on: bool) -> None:
  """
  Set the process-wide wait-telemetry switch.

  Args:
    on (bool): True to record, False to make every entry point a no-op.

  Returns:
    None

  Examples:
    >>> set_enabled(False)
    >>> enabled()
    False
  """
  global _enabled
  _enabled = bool(on)
  if _enabled:
    install_pygc_callbacks()


def enabled() -> bool:
  """
  Return whether wait telemetry is currently recording.

  Returns:
    bool: Current process-wide switch.

  Examples:
    >>> set_enabled(False)
    >>> enabled()
    False
  """
  return _enabled


def parse_schedstat(text: str | None) -> tuple[int, int] | None:
  """
  Parse ``cpu_ns runq_ns timeslices`` from a schedstat file body.

  Args:
    text (str | None): File contents, or ``None`` when unreadable.

  Returns:
    tuple[int, int] | None: ``(on_cpu_ns, run_queue_wait_ns)`` or ``None``.

  Examples:
    >>> parse_schedstat("123 456 7\\n")
    (123, 456)
    >>> parse_schedstat("garbage") is None
    True
  """
  parts = (text or "").split()
  if len(parts) < 2:
    return None
  try:
    return int(parts[0]), int(parts[1])
  except ValueError:
    return None


def read_thread_schedstat() -> tuple[int, int] | None:
  """
  Read this thread's on-CPU and run-queue-wait nanoseconds.

  Returns:
    tuple[int, int] | None: ``(cpu_ns, runq_ns)`` or ``None`` off Linux or
      when ``CONFIG_SCHEDSTATS`` hides the file.

  Examples:
    >>> read_thread_schedstat() is None or len(read_thread_schedstat()) == 2
    True
  """
  try:
    with open(_SCHEDSTAT_PATH, encoding="ascii") as fh:
      return parse_schedstat(fh.read())
  except OSError:
    return None


def decompose_wall(
  wall_s: float, cpu_s: float, runq_s: float
) -> tuple[float, float, float]:
  """
  Split wall seconds into on-CPU, run-queue wait and sleep.

  Schedstat and the wall clock are read at slightly different instants, so
  ``cpu + runq`` can exceed ``wall`` by microseconds; sleep clamps at zero
  and the CPU/runq pair is scaled back to fit.

  Args:
    wall_s (float): Elapsed wall seconds.
    cpu_s (float): On-CPU seconds.
    runq_s (float): Seconds runnable but not scheduled.

  Returns:
    tuple[float, float, float]: ``(cpu, runq, sleep)``, all non-negative and
      summing to at most ``wall_s``.

  Examples:
    >>> decompose_wall(10.0, 4.0, 3.0)
    (4.0, 3.0, 3.0)
  """
  wall = max(0.0, wall_s)
  cpu, runq = max(0.0, cpu_s), max(0.0, runq_s)
  busy = cpu + runq
  if busy > wall and busy > 0.0:
    cpu, runq = cpu * wall / busy, runq * wall / busy
    busy = wall
  return cpu, runq, max(0.0, wall - busy)


def begin_file() -> None:
  """
  Start a fresh per-file ledger on this thread (no-op when disabled).

  Returns:
    None

  Examples:
    >>> set_enabled(False)
    >>> begin_file()
  """
  if not _enabled:
    _ledger.set(None)
    return
  sched = read_thread_schedstat()
  _ledger.set(
    _Ledger(
      acc=dict.fromkeys(WAIT_LOG_KEYS, 0.0),
      wall0=time.perf_counter(),
      cpu0_ns=sched[0] if sched else None,
      runq0_ns=sched[1] if sched else 0,
    )
  )


def add(key: str, delta_s: float) -> None:
  """
  Add a positive amount to a ledger key on this thread.

  Args:
    key (str): A member of ``WAIT_LOG_KEYS``; others are ignored.
    delta_s (float): Seconds (or a count for ``WAIT_COUNT_KEYS``).

  Returns:
    None

  Examples:
    >>> set_enabled(True)
    ... begin_file()
    ... add("wq_put_s", 0.5)
    >>> snapshot()["wq_put_s"]
    0.5
    >>> set_enabled(False)
  """
  led = _ledger.get()
  if led is None or delta_s <= 0.0 or key not in led.acc:
    return
  led.acc[key] += float(delta_s)


def add_stage_cpu(stage: str, cpu_s: float) -> None:
  """
  Credit on-CPU seconds spent inside a parse-stage hold.

  Args:
    stage (str): A ``PARSE_STAGE_HOLD_KEYS`` entry.
    cpu_s (float): Thread CPU seconds consumed inside the (exclusive) hold.

  Returns:
    None

  Examples:
    >>> set_enabled(True)
    ... begin_file()
    ... add_stage_cpu("arc_s", 1.0)
    >>> snapshot()["arc_cpu_s"]
    1.0
    >>> set_enabled(False)
  """
  if cpu_s <= 0.0:
    return
  add("stages_cpu_s", cpu_s)
  group = _STAGE_CPU_GROUPS.get(stage)
  if group is not None:
    add(group, cpu_s)


def snapshot() -> dict[str, float]:
  """
  Return this file's wait ledger with the thread wall decomposed.

  Returns:
    dict[str, float]: Empty when disabled; else every ``WAIT_LOG_KEYS`` key.

  Examples:
    >>> set_enabled(False)
    >>> snapshot()
    {}
  """
  led = _ledger.get()
  if led is None:
    return {}
  out = dict(led.acc)
  sched = read_thread_schedstat()
  if sched is not None and led.cpu0_ns is not None:
    cpu, runq, sleep = decompose_wall(
      time.perf_counter() - led.wall0,
      (sched[0] - led.cpu0_ns) / 1e9,
      (sched[1] - led.runq0_ns) / 1e9,
    )
    out.update(thr_cpu_s=cpu, thr_runq_s=runq, thr_sleep_s=sleep)
  return out


def stamp_enqueue(payload: Any) -> None:
  """
  Record the monotonic enqueue instant on a write payload.

  Args:
    payload (Any): ``IngestWritePayload`` carrying a ``wait`` dict.

  Returns:
    None

  Examples:
    >>> from types import SimpleNamespace
    >>> p = SimpleNamespace(wait={})
    ... set_enabled(False)
    ... stamp_enqueue(p)
    >>> p.wait
    {}
  """
  if _enabled:
    payload.wait["enq_mono"] = time.monotonic()


@contextlib.contextmanager
def writer_exec_scope(payload: Any) -> Iterator[None]:
  """
  Stamp dequeue time and measure the write thread's exec wall/CPU/runq.

  Args:
    payload (Any): ``IngestWritePayload`` carrying a ``wait`` dict.

  Yields:
    None

  Examples:
    >>> from types import SimpleNamespace
    >>> p = SimpleNamespace(wait={})
    ... set_enabled(False)
    >>> with writer_exec_scope(p):
    ...   pass
    >>> p.wait
    {}
  """
  if not _enabled:
    yield
    return
  start = time.monotonic()
  payload.wait["deq_mono"] = start
  before = read_thread_schedstat()
  try:
    yield
  finally:
    payload.wait["exec_s"] = time.monotonic() - start
    after = read_thread_schedstat()
    if before is not None and after is not None:
      payload.wait["cpu_s"] = (after[0] - before[0]) / 1e9
      payload.wait["runq_s"] = (after[1] - before[1]) / 1e9


def apply_payload(payload: Any, join_wait_s: float) -> None:
  """
  Fold one finished write payload into the parse thread's ledger.

  Args:
    payload (Any): Finished ``IngestWritePayload`` with stamped ``wait``.
    join_wait_s (float): Wall seconds the parse thread blocked on ``done``.

  Returns:
    None

  Examples:
    >>> from types import SimpleNamespace
    >>> set_enabled(True)
    ... begin_file()
    >>> apply_payload(SimpleNamespace(wait={}), 1.5)
    >>> snapshot()["wq_join_s"]
    1.5
    >>> set_enabled(False)
  """
  w = payload.wait
  add("wq_n", 1.0)
  add("wq_join_s", join_wait_s)
  if "enq_mono" in w and "deq_mono" in w:
    add("wq_resid_s", w["deq_mono"] - w["enq_mono"])
  add("w_exec_s", w.get("exec_s", 0.0))
  add("w_cpu_s", w.get("cpu_s", 0.0))
  add("w_runq_s", w.get("runq_s", 0.0))


def format_system_wait_census_token(
  read_text: Callable[[str], str | None] | None = None,
) -> str:
  """
  Return CPU/memory/IO pressure (PSI avg10) and runnable-thread census tokens.

  Args:
    read_text (Callable[[str], str | None] | None): Path -> contents reader
      (``None`` result means unreadable); defaults to reading ``/proc``.

  Returns:
    str: Leading-space tokens, or ``""`` when nothing is readable.

  Examples:
    >>> format_system_wait_census_token(read_text=lambda _p: None)
    ''
  """

  def _read(path: str) -> str | None:
    """
    Read one ``/proc`` text file, returning ``None`` when unreadable.

    Args:
      path (str): Absolute ``/proc`` file path.

    Returns:
      str | None: File contents, or ``None`` on ``OSError``.

    Examples:
      >>> _read("/nonexistent/file") is None
      True
    """
    try:
      with open(path, encoding="ascii") as fh:
        return fh.read()
    except OSError:
      return None

  reader = read_text or _read
  parts: list[str] = []
  for path, name in _PSI_FILES:
    for line in (reader(path) or "").splitlines():
      if line.startswith("some "):
        for tok in line.split():
          if tok.startswith("avg10="):
            parts.append(f"{name}={tok[6:]}")
  load = (reader("/proc/loadavg") or "").split()
  if len(load) >= 4 and "/" in load[3]:
    parts.append(f"sys_runnable={load[3].split('/')[0]}")
  return "".join(f" {p}" for p in parts)


def format_boehm_census_token(lib: Any) -> str:
  """
  Return Boehm GC counters (collections, heap, free, GC ms) for the census.

  Args:
    lib (Any): ``ctypes`` handle to the Codon runtime, or ``None``. Getters
      missing from the handle are skipped; a raising getter is skipped.

  Returns:
    str: Leading-space tokens, or ``""`` when ``lib`` is ``None``.

  Examples:
    >>> format_boehm_census_token(None)
    ''
  """
  if lib is None:
    return ""
  parts: list[str] = []
  for getter, name, div in _BOEHM_GETTERS:
    fn = getattr(lib, getter, None)
    if fn is None:
      continue
    try:
      parts.append(f" {name}={int(fn()) // div}")
    except OSError, ValueError, TypeError:
      continue
  return "".join(parts)


def classify_thread_role(name: str | None) -> str:
  """
  Map a Python thread name to a CPU-census role.

  Args:
    name (str | None): ``threading.Thread.name``, or ``None`` for a native
      thread that Python does not know (jemalloc, OpenMP, Codon runtime).

  Returns:
    str: ``ingest``, ``write``, ``coord``, ``archive``, ``native`` or
      ``other``.

  Examples:
    >>> classify_thread_role("ingest-pool_3")
    'ingest'
    >>> classify_thread_role(None)
    'native'
  """
  if name is None:
    return "native"
  for prefix, role in _ROLE_PREFIXES:
    if name.startswith(prefix):
      return role
  return "other"


def _read_all_task_sched() -> dict[int, tuple[int, int]]:
  """
  Read ``(cpu_ns, runq_ns)`` for every thread of this process.

  Returns:
    dict[int, tuple[int, int]]: Native thread id -> schedstat pair; threads
      that exit mid-scan are skipped.

  Examples:
    >>> isinstance(_read_all_task_sched(), dict)
    True
  """
  out: dict[int, tuple[int, int]] = {}
  try:
    tids = os.listdir("/proc/self/task")
  except OSError:
    return out
  for tid in tids:
    try:
      with open(f"/proc/self/task/{tid}/schedstat", encoding="ascii") as fh:
        pair = parse_schedstat(fh.read())
    except OSError:
      continue
    if pair is not None:
      out[int(tid)] = pair
  return out


def _python_thread_names() -> dict[int, str]:
  """
  Return native thread id -> Python thread name for live threads.

  Returns:
    dict[int, str]: Mapping from ``Thread.native_id`` to ``Thread.name``.

  Examples:
    >>> isinstance(_python_thread_names(), dict)
    True
  """
  return {
    t.native_id: t.name
    for t in threading.enumerate()
    if t.native_id is not None
  }


class ThreadRoleCensus:
  """
  Window CPU and run-queue wait per thread role, in core equivalents.

  The first ``format_token`` call primes the baseline and returns ``""``;
  later calls report ``cpu_<role>=`` and ``runq_<role>=`` (seconds of CPU or
  run-queue wait per wall second) for the interval since the previous call.

  Attributes:
    _read_tasks: Callable returning ``{tid: (cpu_ns, runq_ns)}``.
    _thread_names: Callable returning ``{tid: python_thread_name}``.
    _now: Monotonic clock callable.
    _prev: Previous ``(timestamp, per-tid snapshot)`` or ``None``.
  """

  def __init__(
    self,
    read_tasks: Callable[[], dict[int, tuple[int, int]]] | None = None,
    thread_names: Callable[[], dict[int, str]] | None = None,
    now: Callable[[], float] | None = None,
  ) -> None:
    """
    Build a census with injectable readers (defaults read ``/proc``).

    Args:
      read_tasks (Callable[[], dict[int, tuple[int, int]]] | None): Per-thread
        schedstat reader; ``None`` reads ``/proc/self/task``.
      thread_names (Callable[[], dict[int, str]] | None): Native id -> Python
        name; ``None`` uses ``threading.enumerate``.
      now (Callable[[], float] | None): Clock; ``None`` uses
        ``time.monotonic``.

    Returns:
      None

    Examples:
      >>> ThreadRoleCensus().format_token() == ""
      True
    """
    self._read_tasks = read_tasks or _read_all_task_sched
    self._thread_names = thread_names or _python_thread_names
    self._now = now or time.monotonic
    self._prev: tuple[float, dict[int, tuple[int, int]]] | None = None

  def format_token(self) -> str:
    """
    Return role tokens for the window since the previous call.

    Returns:
      str: Leading-space ``cpu_<role>=… runq_<role>=…`` pairs, or ``""`` on
        the priming call or a non-positive window.

    Examples:
      >>> ThreadRoleCensus().format_token()
      ''
    """
    cur, stamp = self._read_tasks(), self._now()
    prev, self._prev = self._prev, (stamp, cur)
    if prev is None or stamp <= prev[0]:
      return ""
    window = stamp - prev[0]
    names = self._thread_names()
    cpu: dict[str, float] = collections.defaultdict(float)
    runq: dict[str, float] = collections.defaultdict(float)
    for tid, (c_ns, r_ns) in cur.items():
      pc, pr = prev[1].get(tid, (0, 0))
      role = classify_thread_role(names.get(tid))
      cpu[role] += max(0, c_ns - pc) / 1e9
      runq[role] += max(0, r_ns - pr) / 1e9
    return "".join(
      f" cpu_{r}={cpu[r] / window:.1f} runq_{r}={runq[r] / window:.1f}"
      for r in sorted(cpu)
    )


def record_pygc_pause(seconds: float, generation: int) -> None:
  """
  Queue one automatic-GC pause (lock-free; safe inside GC callbacks).

  Args:
    seconds (float): Wall seconds the collection took on the calling thread.
    generation (int): Generation collected (2 is a full collection).

  Returns:
    None

  Examples:
    >>> reset_pygc_for_tests()
    ... record_pygc_pause(0.01, 0)
  """
  _PYGC_PAUSES.append((float(seconds), int(generation)))


def _pygc_callback(phase: str, info: dict[str, int]) -> None:
  """
  ``gc.callbacks`` hook that times each collection on its own thread.

  Takes no locks: on free-threaded CPython other threads are stopped while
  a collection runs, so a lock here could deadlock against a stopped holder.

  Args:
    phase (str): ``start`` or ``stop`` as passed by ``gc``.
    info (dict[str, int]): ``gc`` info dict with the ``generation`` key.

  Returns:
    None

  Examples:
    >>> _pygc_callback("start", {"generation": 0})
  """
  if not _enabled:
    return
  if phase == "start":
    _pygc_tls.t0 = time.perf_counter()
  elif phase == "stop" and getattr(_pygc_tls, "t0", None) is not None:
    record_pygc_pause(
      time.perf_counter() - _pygc_tls.t0, int(info.get("generation", -1))
    )
    _pygc_tls.t0 = None


def install_pygc_callbacks() -> None:
  """
  Register the GC pause hook once per process.

  Returns:
    None

  Examples:
    >>> install_pygc_callbacks()
    ... install_pygc_callbacks()
  """
  if _PYGC_INSTALLED.is_set():
    return
  _PYGC_INSTALLED.set()
  gc.callbacks.append(_pygc_callback)


def reset_pygc_for_tests() -> None:
  """
  Drop queued GC pauses so a test starts from an empty window.

  Returns:
    None

  Examples:
    >>> reset_pygc_for_tests()
  """
  _PYGC_PAUSES.clear()


def format_pygc_census_token() -> str:
  """
  Drain queued GC pauses into a window token (count, seconds, max ms).

  Returns:
    str: `` pygc_n=… pygc_s=… pygc_max_ms=…`` for the window since the last
      call, or ``""`` when telemetry is off.

  Examples:
    >>> set_enabled(False)
    >>> format_pygc_census_token()
    ''
  """
  if not _enabled:
    return ""
  pauses: list[float] = []
  while True:
    try:
      pauses.append(_PYGC_PAUSES.popleft()[0])
    except IndexError:
      break
  top = max(pauses, default=0.0)
  return f" pygc_n={len(pauses)} pygc_s={sum(pauses):.3f} pygc_max_ms={int(top * 1000)}"
