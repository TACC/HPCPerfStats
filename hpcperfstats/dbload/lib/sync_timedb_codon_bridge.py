"""Fail-closed Codon parse/collapse bridge for sync_timedb hot path.

Loads an optional Codon ``pyext`` module built against ``/opt/python3.14t``.
When the extension is missing, unloadable, flips the process GIL on, or returns
a fingerprint mismatch sentinel, callers keep the CPython
``IncrementalStatsParser`` path.

Production Codon entry is **in-process** with ``N`` sticky slots (per-slot
locks). ``N`` is ``sync_ingest_pool_processes`` from the INI, capped at
``MAX_CODON_SLOTS``. Popen stdio workers are not the pipeline path. No
process-wide serialize lock.

Attributes:
  logger: Module logger for import/runtime Codon bridge diagnostics.
  CODON_EXT_ENV: Legacy env override for extension module import name (tests).
  CODON_FORCE_DISABLE_ENV: When set to a truthy value, always use CPython.
  CODON_PROCESS_POOL_ENV: When ``1``/true, opt in to legacy Popen workers (tests).
  CODON_PARSE_DIR: Baked pyext directory (``/opt/hpcperfstats/codon_parse``).
  MAX_CODON_SLOTS: Hard cap matching Codon kernel banks (128).
  DEFAULT_CONTEXT_POOL_FRACTION: Default stripe fraction (1.0).
  _codon_mod: Cached imported Codon extension module or ``False`` if unavailable.
  _codon_probe_done: Whether the import probe has run once.
  _codon_gil_ok: True when FT build kept GIL off after Codon import.
  _slot_n: Active stripe / Codon-worker count.
  _slot_locks: Per-slot ``threading.Lock`` list (in-process / test path).
  _slot_held: Current native Codon hold count (census).
  _slot_held_lock: Guards ``_slot_held``.
  _codon_workers: Popen Codon stdio servers for FT baked ``.so`` path.
  _codon_worker_cv: Condition guarding worker checkout / busy flags.
  _codon_atexit_registered: Whether ``atexit`` kill-only shutdown is registered.
  _codon_load_lock: Serializes the first Codon import and bank warmup.
  _gc_lib: Loaded ``libgc`` handle after ``GC_allow_register_threads``.
  _gc_tls: Per-thread Boehm registration flag.
  _sticky_codon_slot: Ingest thread id -> sticky slot index.
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import importlib
import logging
import os
import struct
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any

from hpcperfstats.dbload.lib.codon_process_worker import (
  _RESP_HDR,
  decode_response,
  encode_request,
)
from hpcperfstats.dbload.lib.print_utils import log_print

logger = logging.getLogger(__name__)

CODON_EXT_ENV = "HPCPERFSTATS_CODON_PARSE_MODULE"
CODON_FORCE_DISABLE_ENV = "HPCPERFSTATS_CODON_PARSE_DISABLE"
CODON_PROCESS_POOL_ENV = "HPCPERFSTATS_CODON_PROCESS_POOL"
# Dockerfile codon-build install dir (supervisord PYTHONPATH; also self-wired).
CODON_PARSE_DIR = "/opt/hpcperfstats/codon_parse"
MAX_CODON_SLOTS = 128
DEFAULT_CONTEXT_POOL_FRACTION = 1.0
_sticky_codon_slot = threading.local()

_codon_mod: Any | bool | None = None
_codon_probe_done: bool = False
_codon_gil_ok: bool = False
_slot_n: int = 1
_slot_locks: list[threading.Lock] = [threading.Lock()]
_slot_held: int = 0
_slot_held_lock = threading.Lock()
_codon_workers: list[dict[str, Any]] = []
_codon_worker_cv = threading.Condition()
_codon_atexit_registered: bool = False
_codon_load_lock = threading.Lock()


def _truthy(raw: str | None) -> bool:
  """
  Return True when ``raw`` is a common truthy string.

  Args:
    raw (str | None): Environment value or None.

  Returns:
    bool: True for 1/true/yes/on (case-insensitive).

  Examples:
    >>> _truthy("yes")
    True
    >>> _truthy("0")
    False
  """
  if raw is None:
    return False
  return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_gil_enabled() -> bool | None:
  """
  Return process GIL enabled flag, or None when the probe is unavailable.

  Returns:
    bool | None: ``sys._is_gil_enabled()`` result, or None on older/missing API.

  Examples:
    >>> _is_gil_enabled() in (True, False, None)
    True
  """
  probe = getattr(sys, "_is_gil_enabled", None)
  if probe is None:
    return None
  try:
    return bool(probe())
  except Exception:
    return None


def _is_free_threaded_build() -> bool:
  """
  Return True when this interpreter was built with ``Py_GIL_DISABLED``.

  Host GIL ``.venv`` must not fail-closed Codon fakes; enforce GIL-off only
  on pipeline ``3.14t``.

  Returns:
    bool: True on free-threaded builds.

  Examples:
    >>> isinstance(_is_free_threaded_build(), bool)
    True
  """
  try:
    import sysconfig

    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
  except Exception:
    return False


def derive_codon_context_pool_size(
  parse_pool: int,
  fraction: float = DEFAULT_CONTEXT_POOL_FRACTION,
) -> int:
  """
  Derive striped Codon slot count from ingest parse width.

  Args:
    parse_pool (int): ``sync_ingest_pool_processes`` (or test override).
    fraction (float): ``sync_codon_context_pool_fraction`` (default 1.0).

  Returns:
    int: ``N`` in ``[1, MAX_CODON_SLOTS]``.

  Examples:
    >>> derive_codon_context_pool_size(64, 0.5)
    32
    >>> derive_codon_context_pool_size(64, 0.125)
    8
  """
  pool = max(1, int(parse_pool))
  frac = float(fraction)
  if frac <= 0.0:
    return 1
  if frac >= 1.0:
    return min(MAX_CODON_SLOTS, pool)
  return min(MAX_CODON_SLOTS, max(1, round(pool * frac)))


def _configure_slot_pool(n: int) -> None:
  """
  Install ``n`` per-slot locks / Codon-worker width for the process.

  Recreates the spawn executor when width changes so ``max_workers`` matches.

  Args:
    n (int): Slot count (>= 1).

  Returns:
    None

  Examples:
    >>> _configure_slot_pool(1)
  """
  global _slot_n, _slot_locks
  n = max(1, min(MAX_CODON_SLOTS, int(n)))
  prev = _slot_n
  _slot_n = n
  _slot_locks = [threading.Lock() for _ in range(n)]
  if prev != n:
    _shutdown_codon_pool()


def configure_codon_context_pool_from_ini() -> int:
  """
  Configure Codon slot count from ``sync_ingest_pool_processes``.

  One slot per ingest thread. ``sync_codon_context_pool_fraction`` is not
  applied. The count is capped at ``MAX_CODON_SLOTS``.

  Returns:
    int: Configured slot count ``N``.

  Examples:
    >>> configure_codon_context_pool_from_ini() >= 1  # doctest: +SKIP
    True
  """
  try:
    from hpcperfstats.dbload.lib import conf_parser as cfg

    pool = int(cfg.get_sync_ingest_pool_processes())
  except Exception:
    pool = 64
  n = min(MAX_CODON_SLOTS, max(1, int(pool)))
  _configure_slot_pool(n)
  return n


def bind_codon_worker_slot(slot: int) -> None:
  """
  Pin the current ingest thread to Codon Popen worker index ``slot``.

  When set, checkout uses worker ``slot`` exclusively (1:1 with ingest width).

  Args:
    slot (int): Worker index in ``[0, N)``.

  Returns:
    None

  Examples:
    >>> bind_codon_worker_slot(0)
  """
  _sticky_codon_slot.index = max(0, int(slot))


def codon_sticky_worker_slot() -> int | None:
  """
  Return the sticky Codon worker index for this thread, if bound.

  Returns:
    int | None: Bound index or None when using shared checkout.

  Examples:
    >>> codon_sticky_worker_slot() is None or codon_sticky_worker_slot() >= 0
    True
  """
  return getattr(_sticky_codon_slot, "index", None)


def codon_context_pool_size() -> int:
  """
  Return the configured Codon stripe slot count.

  Returns:
    int: Current ``N``.

  Examples:
    >>> codon_context_pool_size() >= 1
    True
  """
  return max(1, int(_slot_n))


def codon_slot_occupancy() -> tuple[int, int]:
  """
  Return ``(held, N)`` for census ``codon_slot_occ=held/N``.

  Returns:
    tuple[int, int]: Slots currently under lock and configured ``N``.

  Examples:
    >>> held, n = codon_slot_occupancy()
    >>> held >= 0 and n >= 1
    True
  """
  with _slot_held_lock:
    return int(_slot_held), codon_context_pool_size()


def format_codon_slot_occ_census_token() -> str:
  """
  Return greppable `` codon_slot_occ=held/N`` (leading space) or empty.

  Returns:
    str: Census token when Codon is enabled; else empty string.

  Examples:
    >>> isinstance(format_codon_slot_occ_census_token(), str)
    True
  """
  if not codon_ingest_parse_enabled():
    return ""
  held, n = codon_slot_occupancy()
  return f" codon_slot_occ={held}/{n}"


def _slot_for_thread() -> int:
  """
  Bind the calling thread to a stripe slot.

  Returns:
    int: Slot index in ``[0, N)``.

  Examples:
    >>> 0 <= _slot_for_thread() < codon_context_pool_size()
    True
  """
  n = codon_context_pool_size()
  return hash(threading.get_ident()) % n


def _call_with_slot_lock(fn: Callable[..., Any]) -> Any:
  """
  Run ``fn(slot=…)`` under the stripe lock for this thread; update occupancy.

  Args:
    fn (Callable[..., Any]): Callable accepting keyword ``slot``.

  Returns:
    Any: ``fn`` return value.

  Examples:
    >>> _call_with_slot_lock(lambda slot: slot) in range(MAX_CODON_SLOTS)
    True
  """
  global _slot_held
  _codon_gc_enter()
  slot = _slot_for_thread()
  lock = _slot_locks[slot]
  lock.acquire()
  with _slot_held_lock:
    _slot_held += 1
  try:
    return fn(slot=slot)
  finally:
    with _slot_held_lock:
      _slot_held = max(0, _slot_held - 1)
    lock.release()
    _codon_gc_leave()


def _use_codon_process_pool() -> bool:
  """
  Return True only when tests opt into the legacy Popen Codon pool.

  Pipeline ingest stays in-process. Host fakes set ``CODON_EXT_ENV``.

  Returns:
    bool: True only for FT + explicit ``HPCPERFSTATS_CODON_PROCESS_POOL=1``.

  Examples:
    >>> isinstance(_use_codon_process_pool(), bool)
    True
  """
  if not _is_free_threaded_build():
    return False
  if os.environ.get(CODON_EXT_ENV):
    return False
  # Pipeline ingest is in-process. Popen pool is test-only opt-in.
  raw = os.environ.get(CODON_PROCESS_POOL_ENV, "")
  if raw.strip().lower() not in {"1", "true", "yes", "on"}:
    return False
  so_path = os.path.join(CODON_PARSE_DIR, "hpcperfstats_codon_parse.so")
  return os.path.isfile(so_path)


def _spawn_codon_worker() -> dict[str, Any]:
  """
  Start one Codon stdio server process (``codon_process_worker``).

  Returns:
    dict[str, Any]: ``{proc, lock, busy}`` worker record.

  Examples:
    >>> sorted(_spawn_codon_worker.__annotations__)
    ['return']
  """
  env = os.environ.copy()
  env.setdefault("OMP_NUM_THREADS", "1")
  env["PYTHONPATH"] = f"{CODON_PARSE_DIR}:{env.get('PYTHONPATH', '')}".rstrip(
    ":"
  )
  proc = subprocess.Popen(
    [sys.executable, "-m", "hpcperfstats.dbload.lib.codon_process_worker"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    env=env,
    bufsize=0,
  )
  return {"proc": proc, "lock": threading.Lock(), "busy": False}


def _get_codon_pool() -> list[dict[str, Any]]:
  """
  Lazily start ``N`` Codon Popen stdio servers.

  Returns:
    list[dict[str, Any]]: Worker records.

  Examples:
    >>> isinstance(_get_codon_pool, Callable)
    True
  """
  global _codon_workers, _codon_atexit_registered
  with _codon_worker_cv:
    if not _codon_workers:
      n = codon_context_pool_size()
      _codon_workers = [_spawn_codon_worker() for _ in range(n)]
      if not _codon_atexit_registered:
        atexit.register(_shutdown_codon_pool)
        _codon_atexit_registered = True
    return _codon_workers


def _checkout_codon_worker() -> tuple[dict[str, Any], bool]:
  """
  Check out one Codon Popen worker (sticky index or shared CV pool).

  Returns:
    tuple[dict[str, Any], bool]: Worker record and whether occupancy was bumped.

  Raises:
    RuntimeError: When checkout fails.

  Examples:
    >>> isinstance(_checkout_codon_worker, Callable)
    True
  """
  global _slot_held
  workers = _get_codon_pool()
  sticky = codon_sticky_worker_slot()
  if sticky is not None:
    idx = int(sticky) % len(workers)
    worker = workers[idx]
    with _slot_held_lock:
      _slot_held += 1
    return worker, True
  worker: dict[str, Any] | None = None
  with _codon_worker_cv:
    while worker is None:
      for cand in workers:
        if not cand["busy"]:
          cand["busy"] = True
          worker = cand
          break
      if worker is None:
        _codon_worker_cv.wait(timeout=1.0)
        workers = _codon_workers or workers
  if worker is None:
    raise RuntimeError("codon worker checkout failed")
  with _slot_held_lock:
    _slot_held += 1
  return worker, True


def _release_codon_worker(worker: dict[str, Any], *, sticky: bool) -> None:
  """
  Release a Codon worker after RPC (skip CV notify when sticky 1:1).

  Args:
    worker (dict[str, Any]): Worker record from checkout.
    sticky (bool): True when this thread owns a fixed worker index.

  Returns:
    None

  Examples:
    >>> _release_codon_worker({"busy": False}, sticky=True)
  """
  global _slot_held
  with _slot_held_lock:
    _slot_held = max(0, _slot_held - 1)
  if sticky:
    return
  with _codon_worker_cv:
    worker["busy"] = False
    _codon_worker_cv.notify()


def _popen_read_parse_response(proc: subprocess.Popen[bytes]) -> Any:
  """
  Read one ``parse`` response frame from a Codon worker stdout.

  Args:
    proc (subprocess.Popen[bytes]): Worker process with open stdout.

  Returns:
    Any: Decoded parse result dict.

  Raises:
    RuntimeError: On EOF or wire errors.

  Examples:
    >>> callable(_popen_read_parse_response)
    True
  """
  if proc.stdout is None:
    raise RuntimeError("codon worker pipes missing")
  hdr = proc.stdout.read(_RESP_HDR.size)
  if not hdr or len(hdr) < _RESP_HDR.size:
    raise RuntimeError("codon worker EOF")
  _ver, _status, n = struct.unpack_from("!BBI", hdr, 0)
  body = proc.stdout.read(n) if n else b""
  if len(body) < n:
    raise RuntimeError("codon worker EOF")
  return decode_response("parse", hdr + body)


def _popen_rpc(
  kind: str,
  lines: list[str],
  carry_raw: dict[str, list[float]] | None = None,
  carry_arc: dict[str, float] | None = None,
) -> Any:
  """
  Send binary ``kind``/lines frame to a free Codon Popen worker; return body.

  Occupancy (``_slot_held``) increments only while a worker is checked out,
  so ``held`` never exceeds ``N``. Wire format is length-prefixed UTF-8 /
  JSON (pickle forbidden); see ``codon_process_worker``.

  Args:
    kind (str): ``fp`` or ``parse``.
    lines (list[str]): Stats lines.
    carry_raw (dict[str, list[float]] | None): ``parse`` only — cross-flush
      counter carry to seed before this window.
    carry_arc (dict[str, float] | None): ``parse`` only — cross-flush
      arc-rate carry to seed before this window.

  Returns:
    Any: Worker return payload.

  Raises:
    RuntimeError: When the worker returns an error or EOF.

  Examples:
    >>> callable(_popen_rpc)
    True
  """
  sticky = codon_sticky_worker_slot() is not None
  worker, _held = _checkout_codon_worker()
  try:
    with worker["lock"]:
      proc = worker["proc"]
      if proc.poll() is not None:
        worker["proc"] = _spawn_codon_worker()["proc"]
        proc = worker["proc"]
      if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("codon worker pipes missing")
      frame = encode_request(kind, list(lines), carry_raw, carry_arc)
      proc.stdin.write(frame)
      proc.stdin.flush()
      if kind == "fp":
        hdr = proc.stdout.read(_RESP_HDR.size)
        if not hdr or len(hdr) < _RESP_HDR.size:
          raise RuntimeError("codon worker EOF")
        _ver, _status, n = struct.unpack_from("!BBI", hdr, 0)
        body = proc.stdout.read(n) if n else b""
        if len(body) < n:
          raise RuntimeError("codon worker EOF")
        return decode_response(kind, hdr + body)
      return _popen_read_parse_response(proc)
  finally:
    _release_codon_worker(worker, sticky=sticky)


def _call_via_codon_process_pool(
  kind: str,
  lines: list[str],
  carry_raw: dict[str, list[float]] | None = None,
  carry_arc: dict[str, float] | None = None,
) -> Any:
  """
  Run Codon ``kind`` on the Popen worker pool; update occupancy.

  Args:
    kind (str): ``fp`` or ``parse``.
    lines (list[str]): Stats lines.
    carry_raw (dict[str, list[float]] | None): ``parse`` only — cross-flush
      counter carry to seed before this window.
    carry_arc (dict[str, float] | None): ``parse`` only — cross-flush
      arc-rate carry to seed before this window.

  Returns:
    Any: Worker return value.

  Examples:
    >>> isinstance(_call_via_codon_process_pool, Callable)
    True
  """
  return _popen_rpc(kind, lines, carry_raw=carry_raw, carry_arc=carry_arc)


def _shutdown_codon_pool() -> None:
  """
  SIGKILL Codon Popen workers (FT-safe; no multiprocessing teardown).

  Returns:
    None

  Examples:
    >>> _shutdown_codon_pool()
  """
  global _codon_workers
  with _codon_worker_cv:
    workers = list(_codon_workers)
    _codon_workers = []
    _codon_worker_cv.notify_all()
  for worker in workers:
    proc = worker.get("proc")
    if proc is None:
      continue
    with contextlib.suppress(Exception):
      if proc.stdin is not None:
        proc.stdin.close()
    with contextlib.suppress(Exception):
      proc.kill()
    # Do not wait/poll — under FT, Popen.wait can spin the caller thread.


def shutdown_codon_parse_pool() -> None:
  """
  Public teardown for Codon spawn workers (supervisor exit / process finally).

  Returns:
    None

  Examples:
    >>> shutdown_codon_parse_pool()
  """
  _shutdown_codon_pool()


def reset_codon_bridge_for_tests() -> None:
  """
  Clear the cached Codon extension probe (unit tests only).

  Returns:
    None

  Examples:
    >>> reset_codon_bridge_for_tests()
  """
  global _codon_mod, _codon_probe_done, _codon_gil_ok, _slot_held
  _shutdown_codon_pool()
  _codon_mod = None
  _codon_probe_done = False
  _codon_gil_ok = False
  _slot_held = 0
  _configure_slot_pool(1)


def codon_parse_available() -> bool:
  """
  Return whether a Codon parse extension is importable and enabled.

  Returns:
    bool: True when the extension module loaded successfully.

  Examples:
    >>> isinstance(codon_parse_available(), bool)
    True
  """
  return _load_codon_module() is not None


def codon_ingest_parse_enabled() -> bool:
  """
  Return whether Codon will divert ``parse_stats_lines`` (full parse API).

  Fingerprint-only modules count as unavailable for ingest divert.

  Returns:
    bool: True when ``parse_stats_lines_codon`` is present on the loaded ext.

  Examples:
    >>> isinstance(codon_ingest_parse_enabled(), bool)
    True
  """
  mod = _load_codon_module()
  if mod is None:
    return False
  return (
    callable(getattr(mod, "parse_stats_lines_codon", None))
    and callable(getattr(mod, "parse_stats_file_codon_open", None))
    and callable(getattr(mod, "parse_stats_file_codon_next", None))
  )


def format_codon_parse_startup_line() -> str:
  """
  Build a greppable Codon ingest-parse status line for daemon startup.

  Returns:
    str: ``codon_parse status=enabled|disabled …`` for operator logs.

  Examples:
    >>> line = format_codon_parse_startup_line()
    >>> line.startswith("codon_parse status=")
    True
  """
  if _truthy(os.environ.get(CODON_FORCE_DISABLE_ENV)):
    return "codon_parse status=disabled reason=HPCPERFSTATS_CODON_PARSE_DISABLE"
  mod = _load_codon_module()
  if mod is None:
    gil = _is_gil_enabled()
    if gil is True and _codon_probe_done and not _codon_gil_ok:
      return "codon_parse status=disabled reason=gil_enabled_after_import"
    return "codon_parse status=disabled reason=extension_unavailable"
  mod_name = getattr(mod, "__name__", "unknown")
  if not callable(getattr(mod, "parse_stats_lines_codon", None)):
    return (
      "codon_parse status=disabled "
      f"reason=missing_parse_stats_lines_codon module={mod_name}"
    )
  if not callable(
    getattr(mod, "parse_stats_file_codon_open", None)
  ) or not callable(getattr(mod, "parse_stats_file_codon_next", None)):
    return (
      "codon_parse status=disabled "
      f"reason=missing_parse_stats_file_codon module={mod_name}"
    )
  n = codon_context_pool_size()
  gil_tok = "gil=off" if _codon_gil_ok else "gil=unknown"
  return (
    f"codon_parse status=enabled module={mod_name} "
    f"api=parse_stats_lines_codon {gil_tok} contexts={n} "
    f"codon_mode={'process_pool' if _use_codon_process_pool() else 'in_process'} "
    "codonrt_serialize=0"
  )


def log_codon_parse_startup(
  *,
  printer: Callable[..., Any] | None = None,
) -> str:
  """
  Log Codon ingest-parse status once via ``log_print`` (or a test double).

  Configures the stripe pool from INI before formatting the status line.

  Args:
    printer (Callable[..., Any] | None): Optional print-like callable with
      ``flush`` support. Defaults to ``log_print``.

  Returns:
    str: The line that was logged.

  Examples:
    >>> logged: list[str] = []
    >>> def _capture(msg: str, flush: bool = False) -> None:
    ...   logged.append(msg)
    >>> out = log_codon_parse_startup(printer=_capture)
    >>> out.startswith("codon_parse status=")
    True
    >>> logged == [out]
    True
  """
  configure_codon_context_pool_from_ini()
  line = format_codon_parse_startup_line()
  emit = log_print if printer is None else printer
  emit(line, flush=True)
  return line


def _ensure_codon_parse_dir_on_sys_path() -> None:
  """
  Put the baked Codon pyext directory on ``sys.path`` when present.

  Supervisord sets ``PYTHONPATH=/opt/hpcperfstats/codon_parse``; self-wire
  when that env is missing so the baked ``.so`` still loads.

  Returns:
    None

  Examples:
    >>> isinstance(CODON_PARSE_DIR, str)
    True
  """
  if os.path.isdir(CODON_PARSE_DIR) and CODON_PARSE_DIR not in sys.path:
    sys.path.insert(0, CODON_PARSE_DIR)


def _deepbind_codon_libomp() -> None:
  """
  Load bundled ``libomp.so`` with ``RTLD_DEEPBIND`` before the pyext import.

  NumPy/MKL pulls in ``libiomp5.so``. Codon's ``libomp.so`` then resolves
  ``__kmp_acquire_ticket_lock`` into that Intel runtime and spins forever in
  ``__kmp_unregister_root_current_thread`` when a Codon-calling thread exits.
  Deep-binding Codon's copy keeps its OpenMP locks inside that library.

  Returns:
    None

  Examples:
    >>> callable(_deepbind_codon_libomp)
    True
  """
  lib = os.path.join(CODON_PARSE_DIR, "libomp.so")
  if not os.path.isfile(lib):
    return
  mode = os.RTLD_NOW | os.RTLD_DEEPBIND | os.RTLD_GLOBAL
  ctypes.CDLL(lib, mode=mode)


class _GcStackBase(ctypes.Structure):
  """Boehm ``GC_stack_base`` (x86_64: one stack pointer)."""

  _fields_ = [("mem_base", ctypes.c_void_p)]


_gc_lib: Any = None
_gc_tls = threading.local()


def _codon_gc_allow() -> None:
  """
  Enable ``GC_register_my_thread`` on Codon's Boehm collector.

  Unregistered threads segfault in ``GC_malloc_kind_global`` once more than
  about twenty of them allocate. Call this on the loader thread before any
  worker enters Codon.

  Returns:
    None

  Examples:
    >>> callable(_codon_gc_allow)
    True
  """
  global _gc_lib
  lib_path = os.path.join(CODON_PARSE_DIR, "libcodonrt.so")
  if not os.path.isfile(lib_path):
    return
  lib = ctypes.CDLL(lib_path, mode=os.RTLD_NOW | os.RTLD_GLOBAL)
  lib.GC_allow_register_threads.restype = None
  lib.GC_get_stack_base.argtypes = [ctypes.POINTER(_GcStackBase)]
  lib.GC_get_stack_base.restype = ctypes.c_int
  lib.GC_register_my_thread.argtypes = [ctypes.POINTER(_GcStackBase)]
  lib.GC_register_my_thread.restype = ctypes.c_int
  lib.GC_unregister_my_thread.argtypes = []
  lib.GC_unregister_my_thread.restype = ctypes.c_int
  lib.GC_allow_register_threads()
  _gc_lib = lib


def _codon_gc_enter() -> None:
  """
  Register this thread with Codon's GC on the outermost native entry.

  Returns:
    None

  Examples:
    >>> callable(_codon_gc_enter)
    True
  """
  if _gc_lib is None:
    return
  depth = int(getattr(_gc_tls, "depth", 0))
  if depth == 0:
    base = _GcStackBase()
    _gc_lib.GC_get_stack_base(ctypes.byref(base))
    # 0 = newly registered. 1 = already the primordial thread; do not
    # unregister that one on the way out.
    _gc_tls.ours = int(_gc_lib.GC_register_my_thread(ctypes.byref(base))) == 0
  _gc_tls.depth = depth + 1


def _codon_gc_leave() -> None:
  """
  Unregister this thread when the outermost native Codon call returns.

  Returns:
    None

  Examples:
    >>> callable(_codon_gc_leave)
    True
  """
  if _gc_lib is None:
    return
  depth = int(getattr(_gc_tls, "depth", 0))
  if depth <= 0:
    return
  depth -= 1
  _gc_tls.depth = depth
  if depth == 0 and getattr(_gc_tls, "ours", False):
    _gc_lib.GC_unregister_my_thread()
    _gc_tls.ours = False


def _warm_codon_slot_banks(mod: Any) -> None:
  """
  Initialize Codon per-slot banks on the loading thread.

  ``_ensure_slot_banks`` is not safe if many threads enter it together.
  One empty parse on the importer thread sets the ready flag first.

  Args:
    mod (Any): Loaded Codon extension.

  Returns:
    None

  Examples:
    >>> callable(_warm_codon_slot_banks)
    True
  """
  parse = getattr(mod, "parse_stats_lines_codon", None)
  if not callable(parse):
    return
  try:
    parse([], 0)
  except TypeError:
    parse([])


def _load_codon_module() -> Any | None:
  """
  Import and cache the Codon pyext module, or return None (fail closed).

  After a successful import, fail closed if ``sys._is_gil_enabled()`` is True
  (``Py_MOD_GIL_USED`` or accidental GIL re-enable).

  Returns:
    Any | None: Loaded module, or None when disabled/missing/broken.

  Examples:
    >>> _load_codon_module() is None or hasattr(
    ...   _load_codon_module(), "__name__"
    ... )
    True
  """
  global _codon_mod, _codon_probe_done, _codon_gil_ok
  if _truthy(os.environ.get(CODON_FORCE_DISABLE_ENV)):
    return None
  with _codon_load_lock:
    return _load_codon_module_locked()


def _load_codon_module_locked() -> Any | None:
  """
  Import the Codon extension once; caller holds ``_codon_load_lock``.

  Returns:
    Any | None: Loaded module, or None when disabled/missing/broken.

  Examples:
    >>> _load_codon_module_locked() is None or True  # doctest: +SKIP
    True
  """
  global _codon_mod, _codon_probe_done, _codon_gil_ok
  if _codon_probe_done:
    return None if _codon_mod is False else _codon_mod
  _codon_probe_done = True
  mod_name = (
    os.environ.get(CODON_EXT_ENV) or "hpcperfstats_codon_parse"
  ).strip()
  if not mod_name:
    _codon_mod = False
    return None
  _ensure_codon_parse_dir_on_sys_path()
  # Avoid nested OpenMP under multi-slot Codon entry (bundled libomp).
  os.environ.setdefault("OMP_NUM_THREADS", "1")
  if not os.environ.get(CODON_EXT_ENV):
    _deepbind_codon_libomp()
  try:
    mod = importlib.import_module(mod_name)
  except Exception as exc:
    logger.debug("codon parse extension unavailable: %s", exc)
    _codon_mod = False
    return None
  if not hasattr(mod, "feed_and_collapse_fingerprint"):
    logger.warning(
      "codon parse module %s missing feed_and_collapse_fingerprint; "
      "using CPython",
      mod_name,
    )
    _codon_mod = False
    return None
  if _is_free_threaded_build():
    gil = _is_gil_enabled()
    if gil is True:
      logger.warning(
        "codon parse module %s re-enabled process GIL; fail-closed to CPython",
        mod_name,
      )
      _codon_mod = False
      _codon_gil_ok = False
      return None
    _codon_gil_ok = gil is False
  else:
    # Host GIL ABI / unit fakes: do not require gil=off.
    _codon_gil_ok = True
  if not os.environ.get(CODON_EXT_ENV):
    _codon_gc_allow()
    _warm_codon_slot_banks(mod)
  _codon_mod = mod
  return mod


def _invoke_mod_fn(mod: Any, name: str, lines: list[str], slot: int) -> Any:
  """
  Call ``mod.name(lines, slot)`` with one-arg fallback for test doubles.

  Args:
    mod (Any): Loaded extension or fake module.
    name (str): Attribute name of the callable.
    lines (list[str]): Stats lines.
    slot (int): Stripe slot index.

  Returns:
    Any: Callable return value.

  Examples:
    >>> class _M:
    ...   @staticmethod
    ...   def feed_and_collapse_fingerprint(lines, slot=0):
    ...     return (0, 0, 0.0, 0.0)
    >>> _invoke_mod_fn(_M(), "feed_and_collapse_fingerprint", [], 0)
    (0, 0, 0.0, 0.0)
  """
  fn = getattr(mod, name)
  try:
    return fn(list(lines), slot)
  except TypeError:
    return fn(list(lines))


def _invoke_parse_mod_fn(
  mod: Any,
  lines: list[str],
  slot: int,
  carry_raw: dict[str, list[float]],
  carry_arc: dict[str, float],
) -> Any:
  """
  Call ``mod.parse_stats_lines_codon`` with carry kwargs; fall back for fakes.

  Real pyext modules accept ``(lines, slot, carry_raw=, carry_arc=)``. Host
  unit-test fakes written against the pre-carry API accept only
  ``(lines, slot)`` or ``(lines)``; both fall back cleanly via ``TypeError``.

  Args:
    mod (Any): Loaded extension or fake module.
    lines (list[str]): Stats lines for one flush window.
    slot (int): Stripe slot index.
    carry_raw (dict[str, list[float]]): Cross-flush counter carry to seed.
    carry_arc (dict[str, float]): Cross-flush arc-rate carry to seed.

  Returns:
    Any: Callable return value — a finished-contract dict (real module) or
    a legacy ``(stats, procs)`` tuple (pre-carry fake).

  Examples:
    >>> class _M:
    ...   @staticmethod
    ...   def parse_stats_lines_codon(lines, slot=0):
    ...     return (lines, [])
    >>> _invoke_parse_mod_fn(_M(), ["x"], 0, {}, {})
    (['x'], [])
  """
  fn = mod.parse_stats_lines_codon
  try:
    return fn(list(lines), slot, carry_raw=carry_raw, carry_arc=carry_arc)
  except TypeError:
    pass
  try:
    return fn(list(lines), slot)
  except TypeError:
    return fn(list(lines))


def _normalize_codon_marks(raw_marks: Any) -> list[dict[str, Any]]:
  """
  Convert Codon string-field mark dicts into ``persist_monitor_marks`` shape.

  Args:
    raw_marks (Any): List of mark dicts (string ``time``/``host``/fields)
      from the Codon finished-contract, or empty/``None``.

  Returns:
    list[dict[str, Any]]: Marks with float ``time`` and list ``jids`` for
      ``jobs`` kind; affinity marks keep scalar ``jid``.

  Examples:
    >>> _normalize_codon_marks(
    ...   [
    ...     {
    ...       "kind": "affinity",
    ...       "jid": "9",
    ...       "time": "1.0",
    ...       "host": "h",
    ...       "cpus": "0",
    ...       "gpus": "-",
    ...       "cg": "sched",
    ...       "jids": "",
    ...     }
    ...   ]
    ... )[0]["jid"]
    '9'
  """
  if not raw_marks:
    return []
  out: list[dict[str, Any]] = []
  for rec in raw_marks:
    if not isinstance(rec, dict):
      continue
    kind = str(rec.get("kind") or "")
    host = rec.get("host") or ""
    time_raw = rec.get("time") or ""
    if not host or time_raw == "" or time_raw is None:
      continue
    try:
      when = float(time_raw)
    except TypeError, ValueError:
      continue
    if kind == "affinity":
      jid = str(rec.get("jid") or "")
      if not jid:
        continue
      out.append(
        {
          "kind": "affinity",
          "jid": jid,
          "cpus": str(rec.get("cpus") or ""),
          "gpus": str(rec.get("gpus") or "-"),
          "cg": str(rec.get("cg") or "sched"),
          "time": when,
          "host": str(host),
        }
      )
    elif kind == "jobs":
      jids_csv = str(rec.get("jids") or "")
      jids = [p for p in jids_csv.split(",") if p]
      if not jids:
        continue
      out.append(
        {
          "kind": "jobs",
          "jids": jids,
          "time": when,
          "host": str(host),
        }
      )
  return out


def _finish_codon_parse_result(
  result: Any,
  carry: Any | None,
  *,
  sync_carry: bool = True,
) -> tuple[Any, Any]:
  """
  Normalize a Codon parse result; apply ``carry`` when the result is finished.

  A finished-contract dict (``{"stats", "procs", "marks", "carry_raw",
  "carry_arc", "finished": True}``) updates ``carry.raw``/``carry.arc``,
  stores normalized ``carry.codon_marks``, and sets
  ``carry.codon_finished_chunk = True`` so the caller skips
  ``compute_deltas_and_arc_chunk``. ``stats`` may be columnar SoA Mapping.
  A legacy ``(stats, procs)`` tuple (old pre-carry host fakes) is returned
  unchanged and never sets the flag.

  Args:
    result (Any): Return value from the Codon parse call.
    carry (Any | None): ``DeltaCarryState``-like object with ``.raw``/
      ``.arc`` dict attributes, or None when the caller tracks no carry.
    sync_carry (bool): When False, leave ``carry.raw`` and ``carry.arc``
      unchanged (file sessions keep carry inside the kernel).

  Returns:
    tuple[Any, Any]: ``(stats_records_or_soa, proc_rows)``.

  Examples:
    >>> _finish_codon_parse_result(([], []), None)
    ([], [])
  """
  if isinstance(result, dict):
    stats = result.get("stats", [])
    procs = result.get("procs", [])
    marks = _normalize_codon_marks(result.get("marks"))
    if carry is not None:
      if sync_carry:
        raw_out = result.get("carry_raw")
        arc_out = result.get("carry_arc")
        if raw_out is not None:
          carry.raw = dict(raw_out)
        if arc_out is not None:
          carry.arc = dict(arc_out)
      if result.get("finished"):
        carry.codon_finished_chunk = True
        carry.codon_marks = marks
        carry.codon_copy_unique = bool(result.get("copy_unique", False))
    return stats, procs
  return result


def try_codon_feed_collapse_fingerprint(
  lines: list[str],
) -> dict[str, Any] | None:
  """
  Run Codon feed+collapse fingerprint on ``lines``, or None to use CPython.

  Args:
    lines (list[str]): Stats file text lines (same contract as feed_lines).

  Returns:
    dict[str, Any] | None: Fingerprint keys ``raw_stats``, ``collapsed_rows``,
    ``collapsed_value_sum``, ``proc_rows`` when Codon ran; otherwise None.

  Examples:
    >>> try_codon_feed_collapse_fingerprint([]) is None or isinstance(
    ...   try_codon_feed_collapse_fingerprint([]), dict
    ... )
    True
  """
  mod = _load_codon_module()
  if mod is None:
    return None

  try:
    if _use_codon_process_pool():
      raw_stats, collapsed_rows, value_sum, proc_rows = (
        _call_via_codon_process_pool("fp", lines)
      )
    else:
      raw_stats, collapsed_rows, value_sum, proc_rows = _call_with_slot_lock(
        lambda *, slot: _invoke_mod_fn(
          mod, "feed_and_collapse_fingerprint", lines, slot
        )
      )
  except Exception as exc:
    logger.warning("codon feed_and_collapse failed; using CPython: %s", exc)
    return None
  return {
    "raw_stats": int(raw_stats),
    "collapsed_rows": int(collapsed_rows),
    "collapsed_value_sum": float(value_sum),
    "proc_rows": int(proc_rows),
  }


def try_codon_parse_collapse(
  lines: list[str],
  carry: Any | None = None,
) -> tuple[Any, Any] | None:
  """
  Prefer Codon feed+delta+collapse+arc when the extension exposes full parse.

  When only the fingerprint API is present (microbench extension), return
  None so callers keep CPython ``IncrementalStatsParser`` for ORM ingest.
  Once the extension is loaded and exposes the full parse API, any
  exception from the call itself is logged and **re-raised** — there is no
  silent fail-soft to CPython mid-file (that erases the parse-kernel win
  and can double-apply counter deltas). ``carry`` round-trips cross-flush
  counter/arc-rate continuity when the result is the new finished-contract
  dict; legacy pre-carry host fakes return a plain tuple and leave
  ``carry`` untouched.

  Args:
    lines (list[str]): Stats file text lines.
    carry (Any | None): ``DeltaCarryState``-like object with ``.raw``/
      ``.arc`` dict attributes to seed and update, or None.

  Returns:
    tuple[Any, Any] | None: ``(stats_records, proc_rows)``, or None only
    when the Codon extension (or its full parse API) is unavailable.

  Raises:
    Exception: Re-raises any error from the loaded Codon extension call
      (process-pool RPC or in-process invocation).

  Examples:
    >>> try_codon_parse_collapse([]) is None or isinstance(
    ...   try_codon_parse_collapse([]), tuple
    ... )
    True
  """
  mod = _load_codon_module()
  if mod is None:
    return None
  if getattr(mod, "parse_stats_lines_codon", None) is None:
    return None

  carry_raw = (
    dict(getattr(carry, "raw", None) or {}) if carry is not None else {}
  )
  carry_arc = (
    dict(getattr(carry, "arc", None) or {}) if carry is not None else {}
  )
  try:
    if _use_codon_process_pool():
      result = _call_via_codon_process_pool(
        "parse", lines, carry_raw=carry_raw, carry_arc=carry_arc
      )
    else:
      result = _call_with_slot_lock(
        lambda *, slot: _invoke_parse_mod_fn(
          mod, lines, slot, carry_raw, carry_arc
        )
      )
  except Exception:
    logger.error(
      "codon parse_stats_lines_codon failed (fail-hard; no CPython fallback "
      "mid-file)",
      exc_info=True,
    )
    raise
  return _finish_codon_parse_result(result, carry)


def _iter_codon_parse_file_frames(
  path: str,
  flush_max_bytes: int,
  resume_byte: int = 0,
) -> Any:
  """
  Yield parse result dicts for one file-owned Codon session.

  All file I/O and flush batching run inside the Codon worker process (or
  in-process ``codon_worker_parse_file_frames`` under the pyext slot lock).
  Counter/arc carry is not exported on IPC frames.

  Args:
    path (str): Absolute stats path.
    flush_max_bytes (int): Soft max bytes per window.
    resume_byte (int): Byte offset before reading.

  Yields:
    dict[str, Any]: Decoded parse frames until ``eof`` is True.

  Examples:
    >>> list(_iter_codon_parse_file_frames("/x", 1))  # doctest: +SKIP
    []
  """
  from hpcperfstats.dbload.lib.codon_process_worker import (
    codon_worker_parse_file_frames,
  )

  sticky = codon_sticky_worker_slot()
  slot = (
    int(sticky) % codon_context_pool_size()
    if sticky is not None
    else _slot_for_thread()
  )
  lock = _slot_locks[slot]
  global _slot_held
  _codon_gc_enter()
  lock.acquire()
  with _slot_held_lock:
    _slot_held += 1
  try:
    yield from codon_worker_parse_file_frames(
      path,
      int(flush_max_bytes),
      int(resume_byte),
      slot=slot,
    )
  finally:
    with _slot_held_lock:
      _slot_held = max(0, _slot_held - 1)
    lock.release()
    _codon_gc_leave()


def try_codon_parse_file_stream(
  path: str,
  flush_max_bytes: int,
  carry: Any | None,
  on_chunk: Callable[[Any, Any], None],
) -> None:
  """
  File-owned Codon parse: stream SoA frames to ``on_chunk``.

  Args:
    path (str): Absolute stats file path.
    flush_max_bytes (int): Soft max bytes per flush window.
    carry (Any | None): ``DeltaCarryState`` for carry/marks.
    on_chunk (Callable[[Any, Any], None]): Per-frame callback.

  Returns:
    None

  Raises:
    RuntimeError: When Codon is enabled but unavailable mid-file.
    Exception: Re-raises Codon/IPC failures (no CPython mid-divert fallback).

  Examples:
    >>> try_codon_parse_file_stream("/missing", 1024, None, lambda s, p: None)
  """
  mod = _load_codon_module()
  if mod is None:
    raise RuntimeError(
      "codon parse file stream unavailable (extension missing)"
    )
  if getattr(mod, "parse_stats_lines_codon", None) is None:
    raise RuntimeError(
      "codon parse file stream unavailable (no full parse API)"
    )
  try:
    for result in _iter_codon_parse_file_frames(path, int(flush_max_bytes)):
      stats, procs = _finish_codon_parse_result(
        result,
        carry,
        sync_carry=False,
      )
      marks = _normalize_codon_marks(result.get("marks"))
      if carry is not None and marks:
        from hpcperfstats.dbload.lib.monitor_mark_side_ingest import (
          persist_monitor_marks,
        )

        persist_monitor_marks(marks)
        carry.codon_marks = []
      on_chunk(stats, procs)
  except Exception:
    logger.error(
      "codon parse_file failed (fail-hard; no CPython fallback mid-file)",
      exc_info=True,
    )
    raise


def try_codon_feed_batch_parse(
  lines: list[str],
  carry: Any | None = None,
) -> tuple[Any, Any] | None:
  """
  Parse one streaming flush window via ``parse_stats_lines_codon``.

  Schema must be included in ``lines`` (or prior lines in the same call).
  Fail-closed to None only when the extension lacks the full parse export;
  once loaded and enabled, failures raise instead of falling back (see
  :func:`try_codon_parse_collapse`).

  Args:
    lines (list[str]): Stats lines for one take/finish boundary.
    carry (Any | None): ``DeltaCarryState``-like object to seed/update with
      cross-flush counter/arc-rate carry, or None.

  Returns:
    tuple[Any, Any] | None: ``(stats_records, proc_rows)`` or None.

  Raises:
    Exception: Re-raises any error from the loaded Codon extension call.

  Examples:
    >>> try_codon_feed_batch_parse([]) is None or isinstance(
    ...   try_codon_feed_batch_parse([]), tuple
    ... )
    True
  """
  return try_codon_parse_collapse(lines, carry=carry)
