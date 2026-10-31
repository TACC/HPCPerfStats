"""
Bridge to the baked Codon kernels of the columnar update_metrics path.

The pyext ``hpcperfstats_codon_metrics`` (built from
``hpcperfstats/analysis/metrics/codon/`` in the Dockerfile ``codon-build``
stage, shipped at :data:`CODON_METRICS_DIR`) exports the segment reductions
and the COPY decoder used by :mod:`segments` and :mod:`k0_decode`. This
module loads it once, keeps every array handed to it C-contiguous with the
documented dtype, and turns any kernel failure into
:class:`CodonKernelError` so the job hard-fails into failure-memory backoff
(there is no NumPy fallback once the kernel is loaded; plan Stage 7).

On the pipeline interpreter (``/opt/python3.14t``) the kernel is required:
:func:`require_codon_metrics` refuses scheduler start when the ``.so`` is
missing. Host test runs (the ``.venv`` interpreter) use the NumPy reference
implementations in :mod:`segments` and :mod:`k0_decode`.

Attributes:
  CODON_METRICS_DIR: Baked pyext directory in the image.
  CODON_METRICS_DIR_ENV: Env override of the pyext directory (host builds,
    container tests).
  CODON_METRICS_REQUIRED_ENV: ``1`` forces the kernel to be required, ``0``
    makes it optional; unset derives the answer from ``sys.executable``.
  CODON_METRICS_DISABLE_ENV: Test-only switch that ignores the kernel.
  PIPELINE_INTERPRETER_PREFIX: Interpreter prefix that requires the kernel.
  MODULE_NAME: Import name of the pyext.
  KERNEL_SO: File name of the ``--lib`` kernel next to the pyext.
  PYEXT_SO: File name of the pyext.
  _KERNEL_METHODS: Methods the pyext must export to count as loaded.
  RADIX_BUCKETS: Count-array length the kernel's radix sort needs in scratch.
  BANK_RETAIN_ROWS: Largest per-thread scratch bank kept between calls.
  _state: Load cache (module, probe flag, GIL flag, failure reason).
  _load_lock: Serializes the first import.
  _gc_tls: Per-thread Boehm registration depth.
  _bank: Per-thread :class:`_ScratchBank`.
"""

from __future__ import annotations

import ctypes
import importlib
import os
import sys
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

CODON_METRICS_DIR = "/opt/hpcperfstats/codon_metrics"
CODON_METRICS_DIR_ENV = "HPCPERFSTATS_CODON_METRICS_DIR"
CODON_METRICS_REQUIRED_ENV = "HPCPERFSTATS_CODON_METRICS_REQUIRED"
CODON_METRICS_DISABLE_ENV = "HPCPERFSTATS_CODON_METRICS_DISABLE"
PIPELINE_INTERPRETER_PREFIX = "/opt/python3.14t/"
MODULE_NAME = "hpcperfstats_codon_metrics"
KERNEL_SO = "libhpcperfstats_codon_metrics_kernel.so"
PYEXT_SO = "hpcperfstats_codon_metrics.so"

_KERNEL_METHODS = (
  "copy_count_rows",
  "copy_decode",
  "group_reduce",
  "grid_mean",
  "join_sorted",
)


class CodonKernelError(RuntimeError):
  """A Codon kernel call failed; the job must fail, never fall back."""


class CodonNameTableOverflow(CodonKernelError):
  """The decoder's distinct-name table was too small (caller retries)."""


class _State:
  """
  Mutable load cache for the bridge.

  Attributes:
    module: Loaded pyext, ``False`` after a failed probe, None before.
    reason: Why the kernel is unavailable (greppable token).
    gil_off: True when the interpreter kept the GIL off after import.
    gc_lib: ``libcodonrt`` handle once Boehm thread registration is enabled.
  """

  def __init__(self) -> None:
    """
    Start unprobed.

    Returns:
      None

    Examples:
      >>> _State().module is None
      True
    """
    self.module: Any = None
    self.reason = "unprobed"
    self.gil_off = False
    self.gc_lib: Any = None


_state = _State()
_load_lock = threading.Lock()
_gc_tls = threading.local()


def _truthy(raw: str | None) -> bool:
  """
  Parse an env flag.

  Args:
    raw (str | None): Raw env value.

  Returns:
    bool: True for ``1``/``yes``/``true`` (case-insensitive).

  Examples:
    >>> _truthy("Yes"), _truthy("0"), _truthy(None)
    (True, False, False)
  """
  return (raw or "").strip().lower() in ("1", "yes", "true")


def codon_metrics_dir() -> str:
  """
  Directory holding the pyext (env override wins).

  Returns:
    str: Directory path.

  Examples:
    >>> import os
    >>> os.environ.pop("HPCPERFSTATS_CODON_METRICS_DIR", None) and None
    >>> codon_metrics_dir()
    '/opt/hpcperfstats/codon_metrics'
  """
  override = os.environ.get(CODON_METRICS_DIR_ENV, "").strip()
  return override or CODON_METRICS_DIR


def codon_metrics_required() -> bool:
  """
  Whether a missing kernel must refuse daemon start.

  ``HPCPERFSTATS_CODON_METRICS_REQUIRED`` decides when set; otherwise the
  kernel is required exactly on the baked pipeline interpreter.

  Returns:
    bool: True when the kernel is mandatory.

  Examples:
    >>> import os
    >>> os.environ["HPCPERFSTATS_CODON_METRICS_REQUIRED"] = "0"
    >>> codon_metrics_required()
    False
    >>> del os.environ["HPCPERFSTATS_CODON_METRICS_REQUIRED"]
  """
  raw = os.environ.get(CODON_METRICS_REQUIRED_ENV, "").strip()
  if raw:
    return _truthy(raw)
  return sys.executable.startswith(PIPELINE_INTERPRETER_PREFIX)


class _GcStackBase(ctypes.Structure):
  """Boehm ``GC_stack_base`` (x86_64: one stack pointer)."""

  _fields_ = [("mem_base", ctypes.c_void_p)]


def _enable_gc_thread_registration(directory: str) -> Any:
  """
  Load the bundled ``libcodonrt`` and allow per-thread Boehm registration.

  Threads that enter Codon without registering crash inside the collector
  once enough of them allocate; the decoder's name dictionary allocates.

  Args:
    directory (str): Pyext directory holding ``libcodonrt.so``.

  Returns:
    Any: ``ctypes.CDLL`` handle, or None when the runtime is absent.

  Examples:
    >>> _enable_gc_thread_registration("/nonexistent") is None
    True
  """
  path = os.path.join(directory, "libcodonrt.so")
  if not os.path.isfile(path):
    return None
  lib = ctypes.CDLL(path, mode=os.RTLD_NOW | os.RTLD_GLOBAL)
  lib.GC_allow_register_threads.restype = None
  lib.GC_get_stack_base.argtypes = [ctypes.POINTER(_GcStackBase)]
  lib.GC_get_stack_base.restype = ctypes.c_int
  lib.GC_register_my_thread.argtypes = [ctypes.POINTER(_GcStackBase)]
  lib.GC_register_my_thread.restype = ctypes.c_int
  lib.GC_unregister_my_thread.argtypes = []
  lib.GC_unregister_my_thread.restype = ctypes.c_int
  lib.GC_allow_register_threads()
  return lib


def _deepbind_libomp(directory: str) -> None:
  """
  Load the bundled ``libomp.so`` with ``RTLD_DEEPBIND`` before the import.

  NumPy/MKL brings ``libiomp5``; without deep binding Codon's OpenMP locks
  resolve into that runtime and spin when a Codon-calling thread exits.

  Args:
    directory (str): Pyext directory.

  Returns:
    None

  Examples:
    >>> _deepbind_libomp("/nonexistent")
  """
  lib = os.path.join(directory, "libomp.so")
  if os.path.isfile(lib):
    ctypes.CDLL(lib, mode=os.RTLD_NOW | os.RTLD_DEEPBIND | os.RTLD_GLOBAL)


def _gc_enter() -> None:
  """
  Register the calling thread with Boehm on its outermost kernel call.

  Returns:
    None

  Examples:
    >>> _gc_enter()
  """
  lib = _state.gc_lib
  if lib is None:
    return
  depth = int(getattr(_gc_tls, "depth", 0))
  if depth == 0:
    base = _GcStackBase()
    lib.GC_get_stack_base(ctypes.byref(base))
    _gc_tls.ours = int(lib.GC_register_my_thread(ctypes.byref(base))) == 0
  _gc_tls.depth = depth + 1


def _gc_leave() -> None:
  """
  Unregister the thread when its outermost kernel call returns.

  Returns:
    None

  Examples:
    >>> _gc_leave()
  """
  lib = _state.gc_lib
  if lib is None:
    return
  depth = int(getattr(_gc_tls, "depth", 0))
  if depth <= 0:
    return
  depth -= 1
  _gc_tls.depth = depth
  if depth == 0 and getattr(_gc_tls, "ours", False):
    lib.GC_unregister_my_thread()
    _gc_tls.ours = False


def _probe_locked() -> None:
  """
  Import the pyext once (caller holds ``_load_lock``).

  Returns:
    None

  Examples:
    >>> _probe_locked()
  """
  if _state.module is not None:
    return
  if _truthy(os.environ.get(CODON_METRICS_DISABLE_ENV)):
    _state.module = False
    _state.reason = CODON_METRICS_DISABLE_ENV
    return
  directory = codon_metrics_dir()
  if not os.path.isfile(os.path.join(directory, PYEXT_SO)):
    _state.module = False
    _state.reason = f"missing_so dir={directory}"
    return
  if directory not in sys.path:
    sys.path.insert(0, directory)
  os.environ.setdefault("OMP_NUM_THREADS", "1")
  try:
    _deepbind_libomp(directory)
    mod = importlib.import_module(MODULE_NAME)
  except Exception as exc:  # fail closed with the reason
    _state.module = False
    _state.reason = f"import_failed {type(exc).__name__}: {exc}"
    return
  missing = [m for m in _KERNEL_METHODS if not callable(getattr(mod, m, None))]
  if missing:
    _state.module = False
    _state.reason = f"missing_methods {','.join(missing)}"
    return
  gil_check = getattr(sys, "_is_gil_enabled", None)
  _state.gil_off = bool(gil_check is not None and gil_check() is False)
  _state.gc_lib = _enable_gc_thread_registration(directory)
  _state.module = mod
  _state.reason = "enabled"


def kernels() -> Any | None:
  """
  The loaded pyext, or None when unavailable (fail closed).

  Returns:
    Any | None: Module with the kernel methods.

  Examples:
    >>> kernels() is None or hasattr(kernels(), "group_reduce")
    True
  """
  if _state.module is None:
    with _load_lock:
      _probe_locked()
  return _state.module or None


def codon_metrics_available() -> bool:
  """
  Whether the Codon kernels are loaded.

  Returns:
    bool: True when :func:`kernels` returns a module.

  Examples:
    >>> isinstance(codon_metrics_available(), bool)
    True
  """
  return kernels() is not None


def reset_for_tests() -> None:
  """
  Forget the probe result so tests can re-point the directory.

  Returns:
    None

  Examples:
    >>> reset_for_tests()
  """
  with _load_lock:
    _state.module = None
    _state.reason = "unprobed"
    _state.gil_off = False
    _state.gc_lib = None


def format_startup_line() -> str:
  """
  Greppable ``codon_metrics status=…`` line for daemon startup logs.

  Returns:
    str: ``codon_metrics status=enabled module=… gil=off|on dir=…`` or
    ``codon_metrics status=disabled reason=… required=0|1``.

  Examples:
    >>> format_startup_line().startswith("codon_metrics status=")
    True
  """
  mod = kernels()
  req = "1" if codon_metrics_required() else "0"
  if mod is None:
    return (
      f"codon_metrics status=disabled reason={_state.reason} required={req}"
    )
  gil = "off" if _state.gil_off else "on"
  return (
    f"codon_metrics status=enabled module={mod.__name__} gil={gil} "
    f"dir={codon_metrics_dir()} required={req}"
  )


def require_codon_metrics(log: Callable[[str], Any]) -> None:
  """
  Log the startup line and refuse to continue without a required kernel.

  Args:
    log (Callable[[str], Any]): Logger for the status line.

  Returns:
    None

  Raises:
    SystemExit: The kernel is required (pipeline interpreter or
      ``HPCPERFSTATS_CODON_METRICS_REQUIRED=1``) and unavailable.

  Examples:
    >>> import os
    >>> os.environ["HPCPERFSTATS_CODON_METRICS_REQUIRED"] = "0"
    >>> require_codon_metrics(lambda line: None)
    >>> del os.environ["HPCPERFSTATS_CODON_METRICS_REQUIRED"]
  """
  line = format_startup_line()
  log(line)
  if codon_metrics_required() and kernels() is None:
    raise SystemExit(
      "update_metrics refusing to start: Codon metrics kernel required but "
      f"unavailable ({_state.reason}); rebuild the image (codon-build stage)"
    )


def _c(arr: Any, dtype: Any) -> np.ndarray:
  """
  C-contiguous view/copy with the kernel dtype.

  Args:
    arr (Any): Array-like.
    dtype (Any): Target dtype.

  Returns:
    np.ndarray: Contiguous array.

  Examples:
    >>> _c([1, 2], np.int64).dtype
    dtype('int64')
  """
  return np.ascontiguousarray(arr, dtype=dtype)


def call(name: str, *args: Any) -> Any:
  """
  Invoke one kernel method, wrapping failures in :class:`CodonKernelError`.

  Args:
    name (str): Kernel method name.
    *args (Any): Positional arguments (already dtype-normalised).

  Returns:
    Any: The kernel's return value.

  Raises:
    CodonNameTableOverflow: The decoder's name table was too small
      (``OverflowError`` from the pyext); the caller grows it and retries.
    CodonKernelError: The kernel is not loaded or raised anything else.

  Examples:
    >>> call("group_reduce")  # doctest: +SKIP
  """
  mod = kernels()
  if mod is None:
    raise CodonKernelError("codon metrics kernel not loaded")
  _gc_enter()
  try:
    return getattr(mod, name)(*args)
  except OverflowError as exc:
    raise CodonNameTableOverflow(f"{name}: {exc}") from exc
  except Exception as exc:  # hard-fail the job
    raise CodonKernelError(f"{name}: {exc}") from exc
  finally:
    _gc_leave()


RADIX_BUCKETS = 1 << 16
BANK_RETAIN_ROWS = 4_000_000
_bank = threading.local()


class _ScratchBank:
  """
  Per-thread reusable buffers for ``group_reduce`` (grow-only).

  Fresh 8 MB-class arrays on every call made eight metrics threads meet in
  the kernel's page-fault path; reusing one bank per thread keeps the
  reductions allocation-free after the first call.

  Attributes:
    rows: Row capacity of the bank.
    scratch: int64 ``4 * rows + RADIX_BUCKETS`` kernel scratch.
    out_i: int64 ``(2, rows)`` output rows (keys, counts).
    out_f: float64 ``(2, rows)`` output rows (sums, maxima).
    empty_u8: Zero-length mask placeholder.
  """

  def __init__(self, rows: int) -> None:
    """
    Allocate a bank for ``rows`` rows.

    Args:
      rows (int): Row capacity.

    Returns:
      None

    Examples:
      >>> _ScratchBank(4).out_i.shape
      (2, 4)
    """
    self.rows = rows
    self.scratch = np.empty(4 * rows + RADIX_BUCKETS, dtype=np.int64)
    self.out_i = np.empty((2, rows), dtype=np.int64)
    self.out_f = np.empty((2, rows), dtype=np.float64)
    self.empty_u8 = np.empty(0, dtype=np.uint8)


def _thread_bank(rows: int) -> _ScratchBank:
  """
  Bank for the calling thread with capacity for ``rows``.

  Banks above :data:`BANK_RETAIN_ROWS` rows are not kept between calls so
  one huge job does not pin tens of MB per pool thread.

  Args:
    rows (int): Rows needed.

  Returns:
    _ScratchBank: Bank with ``bank.rows >= rows``.

  Examples:
    >>> _thread_bank(8).rows >= 8
    True
  """
  bank = getattr(_bank, "bank", None)
  if bank is not None and bank.rows >= rows:
    return bank
  fresh = _ScratchBank(max(rows, 1024))
  if rows <= BANK_RETAIN_ROWS:
    _bank.bank = fresh
  return fresh


def group_reduce(
  keys: np.ndarray, values: np.ndarray, mask: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
  """
  Codon ``group_reduce``: ``(unique_keys, counts, sums, maxima)`` by key.

  Args:
    keys (np.ndarray): int64 keys.
    values (np.ndarray): float64 values.
    mask (np.ndarray | None): Row mask; None keeps every row.

  Returns:
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]: Sorted group
    keys and their count / sum / max (fresh arrays of the group count).

  Examples:
    >>> group_reduce(np.array([1]), np.array([2.0]), None)  # doctest: +SKIP
  """
  n = int(keys.shape[0])
  k = _c(keys, np.int64)
  v = _c(values, np.float64)
  bank = _thread_bank(n)
  if mask is None:
    has_mask, m = False, bank.empty_u8
  else:
    m_bool = np.ascontiguousarray(mask)
    m = (
      m_bool.view(np.uint8)
      if m_bool.dtype == np.bool_
      else _c(m_bool, np.uint8)
    )
    has_mask = True
  g = int(
    call(
      "group_reduce",
      n,
      k,
      v,
      has_mask,
      m,
      bank.scratch,
      bank.out_i[0],
      bank.out_i[1],
      bank.out_f[0],
      bank.out_f[1],
    )
  )
  return (
    bank.out_i[0, :g].copy(),
    bank.out_i[1, :g].copy(),
    bank.out_f[0, :g].copy(),
    bank.out_f[1, :g].copy(),
  )


def grid_mean(cells: np.ndarray, values: np.ndarray, size: int) -> np.ndarray:
  """
  Codon ``grid_mean``: mean of finite values per dense cell (NaN when none).

  Args:
    cells (np.ndarray): Flat cell index per row.
    values (np.ndarray): float64 values.
    size (int): Cell count.

  Returns:
    np.ndarray: float64 means of length ``size``.

  Examples:
    >>> grid_mean(np.array([0]), np.array([1.0]), 2)  # doctest: +SKIP
  """
  n = int(cells.shape[0])
  cnt = np.empty(size, dtype=np.int64)
  grid = np.empty(size, dtype=np.float64)
  call(
    "grid_mean", n, _c(cells, np.int64), _c(values, np.float64), size, cnt, grid
  )
  return grid


def join_sorted(
  left: np.ndarray, right: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
  """
  Codon ``join_sorted``: positions of common keys in two sorted arrays.

  Args:
    left (np.ndarray): Sorted unique int64 keys.
    right (np.ndarray): Sorted unique int64 keys.

  Returns:
    tuple[np.ndarray, np.ndarray]: ``(left_idx, right_idx)``.

  Examples:
    >>> join_sorted(np.array([1]), np.array([1]))  # doctest: +SKIP
  """
  nl, nr = int(left.shape[0]), int(right.shape[0])
  cap = min(nl, nr)
  li = np.empty(cap, dtype=np.int64)
  ri = np.empty(cap, dtype=np.int64)
  h = int(
    call("join_sorted", nl, _c(left, np.int64), nr, _c(right, np.int64), li, ri)
  )
  return li[:h], ri[:h]


def copy_decode(
  stream: bytes | bytearray | memoryview, *, has_owned: bool
) -> tuple[dict[str, np.ndarray], np.ndarray, list[str]]:
  """
  Codon COPY decode: numeric columns, chunk-local text ids and name table.

  The kernel keeps one distinct-name table for the four text columns; every
  text id in ``host``/``type``/``event``/``dev`` indexes that table.

  Args:
    stream (bytes | bytearray | memoryview): Complete binary COPY stream.
    has_owned (bool): Ninth ``owned`` int4 column present.

  Returns:
    tuple[dict[str, np.ndarray], np.ndarray, list[str]]: Columns keyed
    ``time_us``/``host``/``type``/``event``/``dev``/``owned``/``value``/
    ``arc``/``delta`` (text columns hold table ids), the text column index
    (0 host, 1 type, 2 event, 3 dev) of each table entry, and the entries.

  Raises:
    CodonKernelError: Malformed stream or kernel failure.

  Examples:
    >>> copy_decode(b"", has_owned=False)  # doctest: +SKIP
  """
  buf = stream if isinstance(stream, (bytes, bytearray)) else bytes(stream)
  nrows = int(call("copy_count_rows", buf, bool(has_owned)))
  cols = {
    "time_us": np.empty(nrows, dtype=np.int64),
    "host": np.empty(nrows, dtype=np.int32),
    "type": np.empty(nrows, dtype=np.int32),
    "event": np.empty(nrows, dtype=np.int32),
    "dev": np.empty(nrows, dtype=np.int32),
    "owned": np.empty(nrows, dtype=np.uint8),
    "value": np.empty(nrows, dtype=np.float64),
    "arc": np.empty(nrows, dtype=np.float64),
    "delta": np.empty(nrows, dtype=np.float64),
  }
  name_cap = 4096
  while True:
    n_col = np.empty(name_cap, dtype=np.int64)
    n_off = np.empty(name_cap, dtype=np.int64)
    n_len = np.empty(name_cap, dtype=np.int64)
    try:
      n_names = int(
        call(
          "copy_decode",
          buf,
          bool(has_owned),
          nrows,
          cols["time_us"],
          cols["host"],
          cols["type"],
          cols["event"],
          cols["dev"],
          cols["owned"],
          cols["value"],
          cols["arc"],
          cols["delta"],
          name_cap,
          n_col,
          n_off,
          n_len,
        )
      )
      break
    except CodonNameTableOverflow as exc:
      if name_cap >= 4 * max(nrows, 1):
        raise CodonKernelError(
          "copy_decode: name table larger than rows"
        ) from exc
      name_cap *= 4
  names = [
    buf[int(n_off[i]) : int(n_off[i]) + int(n_len[i])].decode("utf-8")
    for i in range(n_names)
  ]
  return cols, n_col[:n_names].copy(), names
