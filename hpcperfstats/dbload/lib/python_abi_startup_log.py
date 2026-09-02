"""
One-line Python ABI fingerprint for pipeline daemon startup logs.

Used by listend, sync_timedb, and update_metrics so T0 / operator greps can
prove the process is on baked ``/opt/python3.14t`` (free-threaded) versus the
image GIL ``python3``.
"""

from __future__ import annotations

from typing import Any, Callable

import sys
import sysconfig

from hpcperfstats.dbload.lib.print_utils import log_print


def gil_disabled_config_flag() -> int | None:
  """
  Return CPython ``Py_GIL_DISABLED`` from ``sysconfig``, or ``None``.

  Returns:
    int | None: ``1`` when this build was configured ``--disable-gil``,
    ``0`` when GIL is enabled at build time, or ``None`` when the key is
    absent (older interpreters).

  Examples:
    >>> isinstance(gil_disabled_config_flag(), (int, type(None)))
    True
  """
  value = sysconfig.get_config_var("Py_GIL_DISABLED")
  if value is None:
    return None
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def gil_enabled_runtime() -> bool | None:
  """
  Return whether the GIL is currently enabled, when the runtime exposes it.

  Returns:
    bool | None: ``True``/``False`` from ``sys._is_gil_enabled()`` on 3.13+,
    else ``None`` when that helper is missing.

  Examples:
    >>> value = gil_enabled_runtime()
    >>> value is None or isinstance(value, bool)
    True
  """
  checker = getattr(sys, "_is_gil_enabled", None)
  if checker is None:
    return None
  try:
    return bool(checker())
  except Exception:
    return None


def format_python_abi_startup_line() -> str:
  """
  Build a single greppable ABI fingerprint line for daemon startup.

  Returns:
    str: Line including ``sys.executable``, ``Py_GIL_DISABLED``, and
    ``sys._is_gil_enabled()`` (or ``n/a`` when unavailable).

  Examples:
    >>> line = format_python_abi_startup_line()
    >>> "python_abi executable=" in line
    True
    >>> "Py_GIL_DISABLED=" in line
    True
  """
  disabled = gil_disabled_config_flag()
  disabled_s = "n/a" if disabled is None else str(disabled)
  enabled = gil_enabled_runtime()
  enabled_s = "n/a" if enabled is None else str(enabled).lower()
  return (
      "python_abi executable={0} Py_GIL_DISABLED={1} "
      "sys._is_gil_enabled={2}".format(sys.executable, disabled_s, enabled_s)
  )


def log_python_abi_startup(
  *,
  printer: Callable[..., Any] | None = None,
) -> str:
  """
  Log the ABI fingerprint once via ``log_print`` (or a test double).

  Args:
    printer (Callable[..., Any] | None): Optional print-like callable with
      ``flush`` support. Defaults to ``log_print``.

  Returns:
    str: The line that was logged (for tests and callers).

  Examples:
    >>> logged: list[str] = []
    >>> def _capture(msg: str, flush: bool = False) -> None:
    ...   logged.append(msg)
    >>> out = log_python_abi_startup(printer=_capture)
    >>> out.startswith("python_abi executable=")
    True
    >>> logged == [out]
    True
  """
  line = format_python_abi_startup_line()
  emit = log_print if printer is None else printer
  emit(line, flush=True)
  return line
