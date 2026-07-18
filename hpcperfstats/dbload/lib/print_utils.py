"""Uniform script-prefixed print for all HPCPerfStats scripts.

Use log_print() instead of print() so every message is prefixed with [script_name]
of the original calling tool (the script that was run). Library code that is only
imported uses the same label as the script that invoked it (e.g. [sync_timedb]).

When a daemon role is set via set_log_role() (wired from process_title hooks),
the prefix becomes [script_name:role], e.g. [sync_timedb:thread:archive-janitor].
Canonical implementation; hpcperfstats-tools may keep a copy for standalone use.
"""
from __future__ import annotations

import contextvars
import inspect
import sys
import threading

_log_role: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hpc_log_role",
    default=None,
)
_log_print_lock = threading.Lock()


def set_log_role(role: str | None) -> None:
  """Set the log prefix role for the current context (thread or process)."""
  _log_role.set(role)


def get_log_role() -> str | None:
  """Return the current log role, or None when unset."""
  return _log_role.get()


def _script_prefix():
  """Return [scriptname] for the original entry point (__main__), not the immediate caller."""
  main = sys.modules.get("__main__")
  if main is not None and getattr(main, "__file__", None):
    path = main.__file__
  else:
    # Fallback: use immediate caller (e.g. interactive interpreter)
    frame = inspect.stack()[2]
    path = frame.filename
  name = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
  return f"[{name}]"


def format_log_prefix() -> str:
  """Return [script] or [script:role] when a daemon role is active."""
  base = _script_prefix()
  role = get_log_role()
  if not role:
    return base
  script_name = base[1:-1]
  return f"[{script_name}:{role}]"


def log_print(*args, **kwargs):
  """Print with script prefix. Same signature as print(); forwards all kwargs (e.g. file=, flush=)."""
  prefix = format_log_prefix()
  with _log_print_lock:
    return print(prefix, *args, **kwargs)
