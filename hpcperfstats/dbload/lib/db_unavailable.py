"""
Detect PostgreSQL reachability failures and terminate pipeline processes.

Long-running ingest and metrics jobs should not retry or degrade when the server
is down, still starting, or otherwise unreachable: supervisor restarts after the
database is healthy.

Attributes:
  _DATABASE_UNAVAILABLE_MARKERS: Attribute.
  _QUERY_BOUNDED_FAILURE_MARKERS: Attribute.
"""

from __future__ import annotations

from typing import Any

from django.db.utils import DatabaseError, OperationalError

from hpcperfstats.dbload.lib.print_utils import log_print

# Lowercased fragments matched against the full exception chain text.
_DATABASE_UNAVAILABLE_MARKERS = (
    "connection failed",
    "could not connect to server",
    "connection refused",
    "connection timed out",
    "could not translate host name",
    "temporary failure in name resolution",
    "network is unreachable",
    "no route to host",
    "the database system is not yet accepting connections",
    "the database system is shutting down",
    "server closed the connection unexpectedly",
    "connection to server at",
    "terminating connection due to administrator command",
    "ssl syscall error",
    "broken pipe",
    "connection reset by peer",
)

# Do not treat query timeouts / lock waits as "database unavailable".
_QUERY_BOUNDED_FAILURE_MARKERS = (
    "statement timeout",
    "lock timeout",
    "canceling statement due to statement timeout",
    "canceling statement due to lock timeout",
)


class DatabaseUnavailableExit(BaseException):
  """
  Raised when the database cannot be used; exit the process (supervisor.
  
  Attributes:
    cause: Attribute.
  """

  exit_code = 2

  def __init__(self, cause: Any) -> None:
    """
    Initialize a new instance.
    
    Args:
      cause (Any): Cause passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> DatabaseUnavailableExit(None)  # doctest: +SKIP
    """
    self.cause = cause
    super().__init__(str(cause))


def _chain_text(exc: BaseException | None) -> str:
  """
  Internal helper to handle chain text.
  
  Args:
    exc (BaseException | None): One of ``BaseException``, ``None``.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _chain_text(None)  # doctest: +SKIP
  """
  parts: list[str] = []
  seen: set[int] = set()
  cur: BaseException | None = exc
  while cur is not None and id(cur) not in seen:
    seen.add(id(cur))
    parts.append(str(cur).lower())
    nxt = getattr(cur, "__cause__", None)
    if nxt is None:
      nxt = getattr(cur, "__context__", None)
    cur = nxt
  return "\n".join(parts)


def is_query_bounded_failure_error(exc: BaseException | None) -> bool:
  """
  True when ``exc`` is a statement/lock timeout (bounded query failure, not DB.
  
    down).
  
  Args:
    exc (BaseException | None): One of ``BaseException``, ``None``.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> is_query_bounded_failure_error(None)  # doctest: +SKIP
  """
  if exc is None:
    return False
  text = _chain_text(exc)
  return any(m in text for m in _QUERY_BOUNDED_FAILURE_MARKERS)


def is_database_unavailable_error(exc: BaseException | None) -> bool:
  """
  True when ``exc`` indicates the server is down or not accepting sessions.
  
  Args:
    exc (BaseException | None): One of ``BaseException``, ``None``.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> is_database_unavailable_error(None)  # doctest: +SKIP
  """
  if exc is None:
    return False
  text = _chain_text(exc)
  if any(marker in text for marker in _DATABASE_UNAVAILABLE_MARKERS):
    return True
  if is_query_bounded_failure_error(exc):
    return False
  return False


def log_and_raise_database_unavailable(
  exc: BaseException,
  *,
  context: str,
) -> None:
  """
  Log once and raise :class:`DatabaseUnavailableExit` (non-``Exception``.
  
    subtree).
  
  Args:
    exc (BaseException): Exc.
    context (str): String for context.
  
  Returns:
    None
  
  Raises:
    DatabaseUnavailableExit: Raised when
    ``log_and_raise_database_unavailable`` hits a ``DatabaseUnavailableExit``
    failure path.
  
  Examples:
    >>> log_and_raise_database_unavailable(None, "x")  # doctest: +SKIP
  """
  log_print(
      "%s: database unavailable, exiting: %s" % (context, exc),
      flush=True,
  )
  raise DatabaseUnavailableExit(exc) from exc


def reraise_database_unavailable_chain(
  exc: BaseException,
  *,
  context: str,
) -> None:
  """
  If ``exc`` or its causes/contexts indicate DB unavailability, terminate the.
  
    process.
  
  Args:
    exc (BaseException): Exc.
    context (str): String for context.
  
  Returns:
    None
  
  Raises:
    cur: Raised when ``reraise_database_unavailable_chain`` hits a ``cur``
    failure path.
  
  Examples:
    >>> reraise_database_unavailable_chain(None, "x")  # doctest: +SKIP
  """
  seen: set[int] = set()
  cur: BaseException | None = exc
  while cur is not None and id(cur) not in seen:
    seen.add(id(cur))
    if isinstance(cur, DatabaseUnavailableExit):
      raise cur
    if isinstance(cur, (OperationalError, DatabaseError)) and is_database_unavailable_error(cur):
      log_and_raise_database_unavailable(cur, context=context)
    cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
