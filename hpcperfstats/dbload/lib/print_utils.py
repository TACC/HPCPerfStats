"""Uniform script-prefixed print for all HPCPerfStats scripts.

Use log_print() instead of print() so every message is prefixed with [script_name]
of the original calling tool (the script that was run). Library code that is only
imported uses the same label as the script that invoked it (e.g. [sync_timedb]).

When a daemon role is set via set_log_role() (wired from process_title hooks),
the prefix becomes [script_name:role], e.g. [sync_timedb:thread:archive-janitor].

Body facets (outside brackets):
- janitorial_logging() / janitorial=True → add or strip leading ``janitor:``
  (strip when role already contains ``janitor``).
- ingest_logging() / ingest=True → add leading ``ingest:`` on MainThread only
  (role ``main`` or unset). Janitorial scope wins over ingest when both active.

Canonical implementation; hpcperfstats-tools may keep a copy for standalone use.
"""
from __future__ import annotations

import contextvars
import inspect
import sys
import threading
from contextlib import contextmanager

_log_role: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "hpc_log_role",
    default=None,
)
_janitorial_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "hpc_janitorial_logging",
    default=0,
)
_ingest_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "hpc_ingest_logging",
    default=0,
)
_log_print_lock = threading.Lock()

_JANITOR_BODY_PREFIX = "janitor:"
_INGEST_BODY_PREFIX = "ingest:"


def set_log_role(role: str | None) -> None:
  """Set the log prefix role for the current context (thread or process)."""
  _log_role.set(role)


def get_log_role() -> str | None:
  """Return the current log role, or None when unset."""
  return _log_role.get()


@contextmanager
def janitorial_logging():
  """Mark nested log_print calls as janitorial (body ``janitor:`` rules)."""
  token = _janitorial_depth.set(_janitorial_depth.get() + 1)
  try:
    yield
  finally:
    _janitorial_depth.reset(token)


@contextmanager
def ingest_logging():
  """Mark nested log_print calls as MainThread ingest/pre-work (body ``ingest:``)."""
  token = _ingest_depth.set(_ingest_depth.get() + 1)
  try:
    yield
  finally:
    _ingest_depth.reset(token)


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


def _script_name_from_bracket_prefix(prefix: str) -> str:
  """Extract script basename from ``[script]`` or ``[script:role]``."""
  inner = prefix[1:-1] if prefix.startswith("[") and prefix.endswith("]") else prefix
  return inner.split(":", 1)[0]


def _role_has_janitor(role: str | None) -> bool:
  return bool(role) and "janitor" in role


def _role_is_main_thread(role: str | None) -> bool:
  """Supervisor MainThread: explicit ``main`` or unset (pre-title / tests)."""
  return not role or role == "main"


def _strip_leading_token(text: str, token: str) -> str:
  """Strip ``token`` or ``token `` from the start of ``text`` (case-sensitive)."""
  if text.startswith(token + " "):
    return text[len(token) + 1 :]
  if text.startswith(token):
    rest = text[len(token) :]
    return rest.lstrip(" ")
  return text


def _body_has_leading_token(text: str, token: str) -> bool:
  return text.startswith(token + " ") or text == token or text.startswith(token)


def _normalize_log_body_args(
    args: tuple,
    *,
    script_name: str,
    role: str | None,
    janitorial: bool,
    ingest: bool,
) -> tuple:
  if not args:
    return args
  first = args[0]
  if not isinstance(first, str):
    return args

  if first.startswith(f"{script_name}:"):
    first = first[len(script_name) + 1 :].lstrip(" ")

  if janitorial:
    if _role_has_janitor(role):
      if _body_has_leading_token(first, _JANITOR_BODY_PREFIX):
        first = _strip_leading_token(first, _JANITOR_BODY_PREFIX)
    elif not _body_has_leading_token(first, _JANITOR_BODY_PREFIX):
      first = f"{_JANITOR_BODY_PREFIX} {first}" if first else _JANITOR_BODY_PREFIX
  elif ingest and _role_is_main_thread(role):
    if not _body_has_leading_token(first, _INGEST_BODY_PREFIX):
      first = f"{_INGEST_BODY_PREFIX} {first}" if first else _INGEST_BODY_PREFIX

  return (first,) + args[1:]


def log_print(*args, **kwargs):
  """Print with script prefix. Same signature as print(); forwards kwargs (e.g. file=, flush=).

  Optional oneshot kwargs (not forwarded to print):
  - ``janitorial=True`` — apply janitorial body rules for this call
  - ``ingest=True`` — apply MainThread ingest body rules for this call
  """
  oneshot_janitorial = bool(kwargs.pop("janitorial", False))
  oneshot_ingest = bool(kwargs.pop("ingest", False))
  prefix = format_log_prefix()
  script_name = _script_name_from_bracket_prefix(prefix)
  role = get_log_role()
  janitorial = oneshot_janitorial or _janitorial_depth.get() > 0
  ingest = oneshot_ingest or _ingest_depth.get() > 0
  args = _normalize_log_body_args(
      args,
      script_name=script_name,
      role=role,
      janitorial=janitorial,
      ingest=ingest,
  )
  with _log_print_lock:
    return print(prefix, *args, **kwargs)
