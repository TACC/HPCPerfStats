"""
Shared DB retry helper for metrics workers.
"""
from __future__ import annotations

from typing import Any

from django.db import close_old_connections
from django.db.utils import DatabaseError, OperationalError

from hpcperfstats.dbload.lib.db_unavailable import (
    is_database_unavailable_error,
    log_and_raise_database_unavailable,
)


def run_with_db_retry(
  func: Any,
  *,
  attempts: int = 2,
  on_retry: Any | None = None,
) -> Any:
  """
  Run ``func`` and retry on DB connection errors.
  
  Args:
    func (Any): Callable invoked by this helper.
    attempts (int): Integer value for attempts.
    on_retry (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``run_with_db_retry`` hits a ``Exception`` failure
    path.
  
  Examples:
    >>> run_with_db_retry(None, 0, None)  # doctest: +SKIP
  """
  for attempt in range(max(1, int(attempts))):
    try:
      close_old_connections()
      return func()
    except (OperationalError, DatabaseError) as exc:
      close_old_connections()
      if is_database_unavailable_error(exc):
        log_and_raise_database_unavailable(
            exc, context="run_with_db_retry"
        )
      if attempt + 1 >= attempts:
        raise
      if on_retry is not None:
        on_retry(exc, attempt + 1)
  return None
