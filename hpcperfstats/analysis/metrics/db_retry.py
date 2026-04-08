"""Shared DB retry helper for metrics workers."""

from django.db import close_old_connections
from django.db.utils import DatabaseError, OperationalError


def run_with_db_retry(func, *, attempts=2, on_retry=None):
  """Run ``func`` and retry on DB connection errors."""
  for attempt in range(max(1, int(attempts))):
    try:
      close_old_connections()
      return func()
    except (OperationalError, DatabaseError) as exc:
      close_old_connections()
      if attempt + 1 >= attempts:
        raise
      if on_retry is not None:
        on_retry(exc, attempt + 1)
  return None
