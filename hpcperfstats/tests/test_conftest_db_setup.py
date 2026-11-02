"""Host runs (no compose DB) must not make pytest-django create a test DB.

pytest-django sizes the session ``django_db_setup`` from every collected
item's ``django_db`` marker, skipped or not. A skipped
``django_db(transaction=True)`` test in ``analysis/metrics/tests`` made the
host session try to create the default test database, and the connection
error landed on the mock-only ``databases=[]`` tests that ran after it
(``test_metrics_persist_provenance.py``: 7 setup errors in the full metrics
run, 0 when the file ran alone).
"""

from __future__ import annotations

import pytest
from pytest_django.fixtures import _get_databases_for_setup

from hpcperfstats import conftest as root_conftest


class _Item:
  """Minimal pytest item: own markers with closest-first lookup."""

  def __init__(self, path: str, *marks: pytest.MarkDecorator) -> None:
    self.fspath = path
    self.fixturenames = ()
    self.own_markers = [m.mark for m in marks]

  def iter_markers(self, name: str | None = None):
    return (m for m in self.own_markers if name is None or m.name == name)

  def get_closest_marker(self, name: str):
    return next(self.iter_markers(name), None)

  def add_marker(self, marker, append: bool = True) -> None:
    mark = getattr(marker, "mark", marker)
    if append:
      self.own_markers.append(mark)
    else:
      self.own_markers.insert(0, mark)


@pytest.mark.parametrize(
  "path",
  [
    "/x/hpcperfstats/analysis/metrics/tests/test_metrics_telemetry_bounds.py",
    "/x/hpcperfstats/site/lib/machine/tests/test_api.py",
  ],
)
def test_off_compose_default_db_items_are_skipped_without_db_setup(
  monkeypatch, path
):
  monkeypatch.delenv("HPCPERFSTATS_COMPOSE_NETWORK", raising=False)
  db_item = _Item(path, pytest.mark.django_db(transaction=True))
  mock_item = _Item(
    "/x/hpcperfstats/analysis/metrics/tests/test_mock.py",
    pytest.mark.django_db(databases=[]),
  )
  root_conftest.pytest_collection_modifyitems(None, [db_item, mock_item])
  assert db_item.get_closest_marker("skip") is not None
  assert mock_item.get_closest_marker("skip") is None
  aliases, _serialized = _get_databases_for_setup([db_item, mock_item])
  assert aliases == set()


def test_compose_keeps_default_db_items(monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_COMPOSE_NETWORK", "1")
  item = _Item(
    "/x/hpcperfstats/analysis/metrics/tests/test_metrics_telemetry_bounds.py",
    pytest.mark.django_db(transaction=True),
  )
  root_conftest.pytest_collection_modifyitems(None, [item])
  assert item.get_closest_marker("skip") is None
  aliases, _serialized = _get_databases_for_setup([item])
  assert aliases == {"default"}
