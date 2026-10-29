"""The census line reports how many closed raw stats files are on disk.

Operator request 2026-10-29 (hpcperfstats04: 732k raw files on disk found only
by a manual ``find``). ``raw_stats_files_on_disk=N`` counts what discover
would walk (fd walk of ``archive/<host>/<file>``, minus lock sidecars and
dot-prefixed sidecar directories), refreshed at most every
``RAW_STATS_COUNT_INTERVAL_S`` so the 60 s census does not re-walk the tree.
"""

from __future__ import annotations

import inspect

import pytest

from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo


def _rec(path: str) -> bytes:
  return f"{path}\0001\0001\0001\000".encode()


@pytest.fixture(autouse=True)
def _fresh():
  qo._RAW_STATS_COUNT.update(n=None, at=None)
  yield
  qo._RAW_STATS_COUNT.update(n=None, at=None)


def test_count_matches_what_discover_would_walk(monkeypatch):
  chunks = [
    _rec("/archive/h1/1787000000"),
    _rec("/archive/h1/1787000001"),
    _rec("/archive/h2/1787000002.fnctl.lock"),
    _rec("/archive/.sync_timedb_day_raw_removal/2026-08-25.json"),
  ]
  seen = {}

  def _walk(archive_dir, **kw):
    seen.update(kw, archive_dir=archive_dir)
    return iter(chunks)

  monkeypatch.setattr(qo, "iter_find_stats_stdout_chunks", _walk)
  assert qo._count_raw_stats_files_on_disk("/archive") == 2
  assert seen["archive_dir"] == "/archive"


def test_token_is_cached_between_refreshes():
  calls = []

  def _count(_d):
    calls.append(1)
    return 732100

  tok = qo._raw_stats_files_on_disk_token("/a", now=1000.0, count_fn=_count)
  assert tok == " raw_stats_files_on_disk=732100"
  later = 1000.0 + qo.RAW_STATS_COUNT_INTERVAL_S - 1
  assert qo._raw_stats_files_on_disk_token("/a", now=later, count_fn=_count)
  assert len(calls) == 1
  refresh = 1000.0 + qo.RAW_STATS_COUNT_INTERVAL_S
  qo._raw_stats_files_on_disk_token("/a", now=refresh, count_fn=_count)
  assert len(calls) == 2


def test_failed_count_keeps_the_last_value_and_never_raises():
  qo._raw_stats_files_on_disk_token("/a", now=0.0, count_fn=lambda _d: 5)

  def _boom(_d):
    raise OSError("walk failed")

  refresh = qo.RAW_STATS_COUNT_INTERVAL_S
  tok = qo._raw_stats_files_on_disk_token("/a", now=refresh, count_fn=_boom)
  assert tok == " raw_stats_files_on_disk=5"


def test_no_token_before_the_first_successful_count():
  def _boom(_d):
    raise OSError("walk failed")

  assert qo._raw_stats_files_on_disk_token("/a", now=0.0, count_fn=_boom) == ""


def test_census_line_carries_the_token():
  src = inspect.getsource(qo._reconstruct_coordinator_loop)
  assert "_raw_stats_files_on_disk_token(" in src
  assert "{raw_tok}" in src
