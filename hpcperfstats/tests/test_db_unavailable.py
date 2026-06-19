"""Unit tests for database-unavailability detection (pipeline exit semantics)."""

import pytest
from django.db.utils import OperationalError

from hpcperfstats.analysis.metrics.lib.db_retry import run_with_db_retry
from hpcperfstats.dbload.lib.db_unavailable import (
    DatabaseUnavailableExit,
    is_database_unavailable_error,
    is_query_bounded_failure_error,
    reraise_database_unavailable_chain,
)


@pytest.mark.parametrize(
    "message",
    [
        "connection failed: connection to server at \"10.89.0.4\", port 5432 failed: "
        "FATAL:  the database system is not yet accepting connections",
        "could not connect to server: Connection refused",
        "connection to server at \"db\" (172.18.0.2), port 5432 failed: Connection refused",
    ],
)
def test_is_database_unavailable_error_positive(message):
  assert is_database_unavailable_error(OperationalError(message))


def test_is_database_unavailable_error_statement_timeout_negative():
  assert not is_database_unavailable_error(
      OperationalError("canceling statement due to statement timeout")
  )


def test_is_query_bounded_failure_error_statement_timeout():
  assert is_query_bounded_failure_error(
      OperationalError("canceling statement due to statement timeout")
  )


def test_is_query_bounded_failure_error_connection_refused_negative():
  assert not is_query_bounded_failure_error(
      OperationalError("could not connect to server: Connection refused")
  )


def test_run_with_db_retry_exits_without_retry_on_unavailable():
  calls = {"n": 0}

  def _boom():
    calls["n"] += 1
    raise OperationalError(
        "connection failed: FATAL:  the database system is not yet accepting connections"
    )

  with pytest.raises(DatabaseUnavailableExit):
    run_with_db_retry(_boom, attempts=2)
  assert calls["n"] == 1


def test_reraise_database_unavailable_chain_nested():
  root = OperationalError("wrapper")
  root.__cause__ = OperationalError(
      "connection failed: FATAL:  the database system is shutting down"
  )
  with pytest.raises(DatabaseUnavailableExit):
    reraise_database_unavailable_chain(root, context="test")
