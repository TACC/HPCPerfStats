"""Tests for script-prefixed log_print atomic stdout writes."""

from __future__ import annotations

import io
import sys

from hpcperfstats.dbload.lib.print_utils import (
    _script_prefix,
    ingest_logging,
    janitorial_logging,
    log_print,
    set_log_role,
)


def test_script_prefix_uses_main_file(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(__import__("sys").modules, "__main__", DummyMain)
  assert _script_prefix() == "[sync_timedb]"


def _capture_log_writes(monkeypatch):
  """Capture each ``file.write`` call (Podman logs each write separately)."""
  writes: list[str] = []
  buf = io.StringIO()
  real_write = io.StringIO.write

  def tracking_write(data):
    writes.append(data)
    return real_write(buf, data)

  buf.write = tracking_write  # type: ignore[method-assign]
  monkeypatch.setattr(sys, "stdout", buf)
  return writes, buf


def test_log_print_single_atomic_write(monkeypatch):
  """Podman k8s-file prefixes each write(); multi-write print() mushs lines."""
  class DummyMain:
    __file__ = "/tmp/tool.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  writes, buf = _capture_log_writes(monkeypatch)

  log_print("hello", 123)

  assert writes == ["[tool] hello 123\n"], (
      "log_print must one write() the full line so compose/podman logs "
      "do not inject a container prefix between prefix and body"
  )
  assert buf.getvalue() == "[tool] hello 123\n"


def test_log_print_prefixes_output(monkeypatch):
  class DummyMain:
    __file__ = "/tmp/tool.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  writes, _buf = _capture_log_writes(monkeypatch)

  log_print("hello", 123, end="!")

  assert writes == ["[tool] hello 123!"]


def test_log_print_strips_redundant_script_body_prefix(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  writes, _buf = _capture_log_writes(monkeypatch)
  log_print("sync_timedb: pending reconcile cap begin")
  assert writes == ["[sync_timedb:main] pending reconcile cap begin\n"]
  set_log_role(None)


def test_log_print_adds_janitor_body_prefix_for_main(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  writes, _buf = _capture_log_writes(monkeypatch)
  with janitorial_logging():
    log_print("day-scoped closed_raw tar=2026-06-05.tar")
  assert writes == [
      "[sync_timedb:main] janitor: day-scoped closed_raw tar=2026-06-05.tar\n"
  ]
  set_log_role(None)


def test_log_print_adds_janitor_body_prefix_for_day_close_role(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("thread:day-close-0")
  writes, _buf = _capture_log_writes(monkeypatch)
  with janitorial_logging():
    log_print("seal begin day=2026-06-05")
  assert writes == [
      "[sync_timedb:thread:day-close-0] janitor: seal begin day=2026-06-05\n"
  ]
  set_log_role(None)


def test_log_print_strips_body_janitor_when_role_has_janitor(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("thread:archive-janitor")
  writes, _buf = _capture_log_writes(monkeypatch)
  with janitorial_logging():
    log_print("janitor: discover_ready_day_close reason=tick")
  assert writes == [
      "[sync_timedb:thread:archive-janitor] "
      "discover_ready_day_close reason=tick\n"
  ]
  set_log_role(None)


def test_log_print_keeps_single_janitor_when_already_present_for_day_close(
    monkeypatch,
):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("thread:day-close-1")
  writes, _buf = _capture_log_writes(monkeypatch)
  with janitorial_logging():
    log_print("janitor: day_close defer tar=x")
  assert writes == [
      "[sync_timedb:thread:day-close-1] janitor: day_close defer tar=x\n"
  ]
  set_log_role(None)


def test_log_print_adds_ingest_body_prefix_for_main(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  writes, _buf = _capture_log_writes(monkeypatch)
  with ingest_logging():
    log_print("post_finalize_reconcile oldest_tar=x")
  assert writes == [
      "[sync_timedb:main] ingest: post_finalize_reconcile oldest_tar=x\n"
  ]
  set_log_role(None)


def test_log_print_does_not_double_ingest_prefix(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  writes, _buf = _capture_log_writes(monkeypatch)
  with ingest_logging():
    log_print("ingest: pending reconcile")
  assert writes == ["[sync_timedb:main] ingest: pending reconcile\n"]
  set_log_role(None)


def test_log_print_janitorial_wins_over_ingest_on_main(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  writes, _buf = _capture_log_writes(monkeypatch)
  with ingest_logging():
    with janitorial_logging():
      log_print("day-scoped closed_raw")
  assert writes == ["[sync_timedb:main] janitor: day-scoped closed_raw\n"]
  set_log_role(None)


def test_log_print_ingest_scope_skips_pool_worker_role(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("worker:ingest-pool")
  writes, _buf = _capture_log_writes(monkeypatch)
  with ingest_logging():
    log_print("File successfully added to DB")
  assert writes == [
      "[sync_timedb:worker:ingest-pool] File successfully added to DB\n"
  ]
  set_log_role(None)


def test_log_print_oneshot_kwargs(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  writes, _buf = _capture_log_writes(monkeypatch)
  log_print("day close note", janitorial=True)
  log_print("chunk note", ingest=True)
  assert writes == [
      "[sync_timedb:main] janitor: day close note\n",
      "[sync_timedb:main] ingest: chunk note\n",
  ]
  set_log_role(None)


def test_log_print_unset_role_treated_as_main_for_ingest(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role(None)
  writes, _buf = _capture_log_writes(monkeypatch)
  with ingest_logging():
    log_print("pending reconcile")
  assert writes == ["[sync_timedb] ingest: pending reconcile\n"]
