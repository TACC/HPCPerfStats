import builtins

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


def test_log_print_prefixes_output(monkeypatch, capsys):
  # Force a deterministic prefix
  class DummyMain:
    __file__ = "/tmp/tool.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)

  calls = []

  def fake_print(*args, **kwargs):
    calls.append((args, kwargs))

  monkeypatch.setattr(builtins, "print", fake_print)

  log_print("hello", 123, end="!")

  assert calls, "log_print should call print()"
  (args, kwargs) = calls[0]
  # First arg is the prefix, remaining are passthrough
  assert args[0] == "[tool]"
  assert args[1:] == ("hello", 123)
  assert kwargs.get("end") == "!"


def _capture_log_print(monkeypatch):
  calls = []

  def fake_print(*args, **kwargs):
    calls.append((args, kwargs))

  monkeypatch.setattr(builtins, "print", fake_print)
  return calls


def test_log_print_strips_redundant_script_body_prefix(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  calls = _capture_log_print(monkeypatch)
  log_print("sync_timedb: pending reconcile cap begin")
  assert calls[0][0][0] == "[sync_timedb:main]"
  assert calls[0][0][1] == "pending reconcile cap begin"
  set_log_role(None)


def test_log_print_adds_janitor_body_prefix_for_main(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  calls = _capture_log_print(monkeypatch)
  with janitorial_logging():
    log_print("day-scoped closed_raw tar=2026-06-05.tar")
  assert calls[0][0][0] == "[sync_timedb:main]"
  assert calls[0][0][1] == "janitor: day-scoped closed_raw tar=2026-06-05.tar"
  set_log_role(None)


def test_log_print_adds_janitor_body_prefix_for_day_close_role(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("thread:day-close-0")
  calls = _capture_log_print(monkeypatch)
  with janitorial_logging():
    log_print("seal begin day=2026-06-05")
  assert calls[0][0][1] == "janitor: seal begin day=2026-06-05"
  set_log_role(None)


def test_log_print_strips_body_janitor_when_role_has_janitor(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("thread:archive-janitor")
  calls = _capture_log_print(monkeypatch)
  with janitorial_logging():
    log_print("janitor: discover_ready_day_close reason=tick")
  assert calls[0][0][0] == "[sync_timedb:thread:archive-janitor]"
  assert calls[0][0][1] == "discover_ready_day_close reason=tick"
  set_log_role(None)


def test_log_print_keeps_single_janitor_when_already_present_for_day_close(
    monkeypatch,
):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("thread:day-close-1")
  calls = _capture_log_print(monkeypatch)
  with janitorial_logging():
    log_print("janitor: day_close defer tar=x")
  assert calls[0][0][1] == "janitor: day_close defer tar=x"
  set_log_role(None)


def test_log_print_adds_ingest_body_prefix_for_main(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  calls = _capture_log_print(monkeypatch)
  with ingest_logging():
    log_print("post_finalize_reconcile oldest_tar=x")
  assert calls[0][0][0] == "[sync_timedb:main]"
  assert calls[0][0][1] == "ingest: post_finalize_reconcile oldest_tar=x"
  set_log_role(None)


def test_log_print_does_not_double_ingest_prefix(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  calls = _capture_log_print(monkeypatch)
  with ingest_logging():
    log_print("ingest: pending reconcile")
  assert calls[0][0][1] == "ingest: pending reconcile"
  set_log_role(None)


def test_log_print_janitorial_wins_over_ingest_on_main(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  calls = _capture_log_print(monkeypatch)
  with ingest_logging():
    with janitorial_logging():
      log_print("day-scoped closed_raw")
  assert calls[0][0][1] == "janitor: day-scoped closed_raw"
  assert not calls[0][0][1].startswith("ingest:")
  set_log_role(None)


def test_log_print_ingest_scope_skips_pool_worker_role(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("worker:ingest-pool")
  calls = _capture_log_print(monkeypatch)
  with ingest_logging():
    log_print("File successfully added to DB")
  assert calls[0][0][1] == "File successfully added to DB"
  set_log_role(None)


def test_log_print_oneshot_kwargs(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role("main")
  calls = _capture_log_print(monkeypatch)
  log_print("day close note", janitorial=True)
  log_print("chunk note", ingest=True)
  assert calls[0][0][1] == "janitor: day close note"
  assert calls[1][0][1] == "ingest: chunk note"
  # oneshot kwargs must not leak to print()
  assert "janitorial" not in calls[0][1]
  assert "ingest" not in calls[1][1]
  set_log_role(None)


def test_log_print_unset_role_treated_as_main_for_ingest(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role(None)
  calls = _capture_log_print(monkeypatch)
  with ingest_logging():
    log_print("pending reconcile")
  assert calls[0][0][0] == "[sync_timedb]"
  assert calls[0][0][1] == "ingest: pending reconcile"
