"""
Uniform script-prefixed print for all HPCPerfStats scripts.

Use log_print() instead of print() so every message is prefixed with
[script_name] of the original calling tool (the script that was run). Library
code that is only imported uses the same label as the script that invoked it
(e.g. [sync_timedb]).

When a daemon role is set via set_log_role() (wired from process_title hooks),
the prefix becomes [script_name:role], e.g. [sync_timedb:thread:archive-
janitor]. When the role is unset, format_log_prefix defaults to ``main`` so
lines are always [script_name:main] (never a bare [script_name] at runtime).

Body facets (outside brackets):
- janitorial_logging() / janitorial=True → add or strip leading ``janitor:``
  (strip when role already contains ``janitor``).
- ingest_logging() / ingest=True → add leading ``ingest:`` on MainThread only
  (role ``main`` or unset). Janitorial scope wins over ingest when both active.

Canonical implementation; hpcperfstats-tools may keep a copy for standalone use.

Attributes:
  _INGEST_BODY_PREFIX: Attribute.
  _JANITOR_BODY_PREFIX: Attribute.
  _ingest_depth: Attribute.
  _janitorial_depth: Attribute.
  _log_print_lock: Attribute.
  _log_role: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

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
  """
  Set the log prefix role for the current context (thread or process).
  
  Args:
    role (str | None): One of ``str``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> set_log_role(None)  # doctest: +SKIP
  """
  _log_role.set(role)


def get_log_role() -> str | None:
  """
  Return the current log role, or None when unset.
  
  Returns:
    str | None: One of ``str``, ``None`` depending on inputs/branch.
  
  Examples:
    >>> get_log_role()  # doctest: +SKIP
  """
  return _log_role.get()


@contextmanager
def janitorial_logging() -> Iterator[Any]:
  """
  Mark nested log_print calls as janitorial (body ``janitor:`` rules).
  
  Yields:
    Iterator[Any]: Open return polymorphism from ``janitorial_logging``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> janitorial_logging()  # doctest: +SKIP
  """
  token = _janitorial_depth.set(_janitorial_depth.get() + 1)
  try:
    yield
  finally:
    _janitorial_depth.reset(token)


@contextmanager
def ingest_logging() -> Iterator[Any]:
  """
  Mark nested log_print calls as MainThread ingest/pre-work (body ``ingest:``).
  
  Yields:
    Iterator[Any]: Open return polymorphism from ``ingest_logging``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> ingest_logging()  # doctest: +SKIP
  """
  token = _ingest_depth.set(_ingest_depth.get() + 1)
  try:
    yield
  finally:
    _ingest_depth.reset(token)


def _script_prefix() -> Any:
  """
  Return [scriptname] for the original entry point (__main__), not the.
  
    immediate.
  
    caller.
  
  Returns:
    Any: Open return polymorphism from ``_script_prefix``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> _script_prefix()  # doctest: +SKIP
  """
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
  """
  Return ``[script:role]``, defaulting unset role to ``main``.

  Always includes a role segment so operators never see a bare ``[script]``
  prefix from ``log_print`` (``process-title-and-pool-labels.mdc``).

  Returns:
    str: Bracketed ``[script_name:role]`` prefix for the current context.

  Examples:
    >>> format_log_prefix()  # doctest: +SKIP
  """
  base = _script_prefix()
  role = get_log_role() or "main"
  script_name = base[1:-1]
  return f"[{script_name}:{role}]"


def _script_name_from_bracket_prefix(prefix: str) -> str:
  """
  Extract script basename from ``[script]`` or ``[script:role]``.
  
  Args:
    prefix (str): String for prefix.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _script_name_from_bracket_prefix("x")  # doctest: +SKIP
  """
  inner = prefix[1:-1] if prefix.startswith("[") and prefix.endswith("]") else prefix
  return inner.split(":", 1)[0]


def _role_has_janitor(role: str | None) -> bool:
  """
  Internal helper to handle role has janitor.
  
  Args:
    role (str | None): One of ``str``, ``None``.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _role_has_janitor(None)  # doctest: +SKIP
  """
  return bool(role) and "janitor" in role


def _role_is_main_thread(role: str | None) -> bool:
  """
  Supervisor MainThread: explicit ``main`` or unset (pre-title / tests).
  
  Args:
    role (str | None): One of ``str``, ``None``.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _role_is_main_thread(None)  # doctest: +SKIP
  """
  return not role or role == "main"


def _strip_leading_token(text: str, token: str) -> str:
  """
  Strip ``token`` or ``token `` from the start of ``text`` (case-sensitive).
  
  Args:
    text (str): String for text.
    token (str): String for token.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _strip_leading_token("x", "x")  # doctest: +SKIP
  """
  if text.startswith(token + " "):
    return text[len(token) + 1 :]
  if text.startswith(token):
    rest = text[len(token) :]
    return rest.lstrip(" ")
  return text


def _body_has_leading_token(text: str, token: str) -> bool:
  """
  Internal helper to handle body has leading token.
  
  Args:
    text (str): String for text.
    token (str): String for token.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _body_has_leading_token("x", "x")  # doctest: +SKIP
  """
  return text.startswith(token + " ") or text == token or text.startswith(token)


def _normalize_log_body_args(
  args: tuple,
  *,
  script_name: str,
  role: str | None,
  janitorial: bool,
  ingest: bool,
) -> tuple:
  """
  Internal helper to normalize the log body args.
  
  Args:
    args (tuple): Sequence for args.
    script_name (str): String for script name.
    role (str | None): One of ``str``, ``None``.
    janitorial (bool): Boolean flag for janitorial.
    ingest (bool): Boolean flag for ingest.
  
  Returns:
    tuple: tuple produced by this call.
  
  Examples:
    >>> _normalize_log_body_args([], "x", None, True, True)  # doctest: +SKIP
  """
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


def log_print(*args: Any, **kwargs: Any) -> None:
  """
  Print with script prefix using one ``write()`` of the full line.

  Accepts the same keyword surface as ``print`` (``sep``, ``end``, ``file``,
  ``flush``). Podman ``json-file``/``k8s-file`` treats each ``write()`` as a
  separate log event and prefixes it for ``compose logs``; CPython ``print``
  writes prefix, separator, body, and newline separately, which mashs lines.
  This helper joins once and writes the complete record (including ``end``).

  Optional oneshot kwargs (not forwarded to the stream):
  - ``janitorial=True`` — apply janitorial body rules for this call
  - ``ingest=True`` — apply MainThread ingest body rules for this call

  Args:
    *args (Any): Message fragments joined with ``sep`` after the script
      prefix (same positional contract as ``print``).
    **kwargs (Any): ``sep`` (str), ``end`` (str), ``file`` (text stream),
      ``flush`` (bool), plus oneshot ``janitorial`` / ``ingest`` (bool).

  Returns:
    None

  Raises:
    TypeError: When an unsupported keyword is passed.

  Examples:
    >>> log_print("ready")  # doctest: +SKIP
  """
  oneshot_janitorial = bool(kwargs.pop("janitorial", False))
  oneshot_ingest = bool(kwargs.pop("ingest", False))
  sep = kwargs.pop("sep", " ")
  end = kwargs.pop("end", "\n")
  file = kwargs.pop("file", None)
  flush = bool(kwargs.pop("flush", False))
  if kwargs:
    unexpected = ", ".join(sorted(kwargs))
    raise TypeError(f"log_print() got unexpected keyword arguments: {unexpected}")
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
  stream = sys.stdout if file is None else file
  line = sep.join(str(part) for part in (prefix, *args)) + end
  with _log_print_lock:
    stream.write(line)
    if flush:
      stream.flush()
