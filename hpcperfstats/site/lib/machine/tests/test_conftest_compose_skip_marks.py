"""Regression: compose-network ``skipif`` modules must not trigger DB setup.

``test_metrics_telemetry_bounds.py`` skips itself with a module-level
``skipif`` off the compose network while still carrying ``django_db``. The
machine-tests conftest used to recognise only ``skip`` marks, so collecting
that module made pytest-django create the default test database and every
later host test errored with ``failed to resolve host 'db'``.
"""

from types import SimpleNamespace

import pytest

from hpcperfstats.site.lib.machine.tests.conftest import (
  skipped_off_compose_network,
)

_REASON = "Requires Docker Compose network (PostgreSQL at host 'db')."


def _item(*marks):
  def iter_markers(name):
    return [m for m in marks if m.name == name]

  return SimpleNamespace(iter_markers=iter_markers)


@pytest.mark.machine_unit_mock
def test_skipif_with_compose_reason_counts_as_skipped():
  item = _item(pytest.mark.skipif(True, reason=_REASON).mark)
  assert skipped_off_compose_network(item) is True


@pytest.mark.machine_unit_mock
def test_skip_with_compose_reason_counts_as_skipped():
  item = _item(pytest.mark.skip(reason=_REASON).mark)
  assert skipped_off_compose_network(item) is True


@pytest.mark.machine_unit_mock
def test_unrelated_skip_reason_is_not_compose():
  item = _item(pytest.mark.skipif(True, reason="needs GPU").mark)
  assert skipped_off_compose_network(item) is False
