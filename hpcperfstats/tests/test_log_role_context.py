"""Tests for log role context wired from process_title hooks."""

import builtins

import pytest

from hpcperfstats.dbload.lib.print_utils import (
    format_log_prefix,
    get_log_role,
    log_print,
    set_log_role,
)
from hpcperfstats.dbload.lib.process_title import (
    apply_pool_worker_process_title,
    set_daemon_process_title,
    set_daemon_thread_title,
)


@pytest.fixture
def sync_timedb_main(monkeypatch):
  class DummyMain:
    __file__ = "/path/to/sync_timedb.py"

  import sys

  monkeypatch.setitem(sys.modules, "__main__", DummyMain)
  set_log_role(None)
  yield
  set_log_role(None)


def test_format_log_prefix_without_role(sync_timedb_main):
  assert format_log_prefix() == "[sync_timedb]"


def test_format_log_prefix_with_role(sync_timedb_main):
  set_log_role("main")
  assert format_log_prefix() == "[sync_timedb:main]"
  set_log_role("thread:archive-janitor")
  assert format_log_prefix() == "[sync_timedb:thread:archive-janitor]"


def test_log_print_includes_role_prefix(sync_timedb_main, monkeypatch):
  calls = []

  def fake_print(*args, **kwargs):
    calls.append((args, kwargs))

  monkeypatch.setattr(builtins, "print", fake_print)
  set_log_role("worker:archive-pool")
  log_print("seal done", flush=True)
  assert calls[0][0][0] == "[sync_timedb:worker:archive-pool]"
  assert calls[0][0][1:] == ("seal done",)


def test_set_daemon_process_title_main_sets_log_role(sync_timedb_main, monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.running_under_gunicorn",
      lambda: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title._apply_setproctitle",
      lambda title: title,
  )
  set_daemon_process_title(name="sync_timedb.py", role="main")
  assert get_log_role() == "main"


def test_apply_pool_worker_process_title_sets_log_role(sync_timedb_main, monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.running_under_gunicorn",
      lambda: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title._apply_setproctitle",
      lambda title: title,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.enable_parent_death_signal",
      lambda *a, **k: False,
  )
  apply_pool_worker_process_title("sync_timedb.py", "archive-pool")
  assert get_log_role() == "worker:archive-pool"


def test_set_daemon_thread_title_sets_log_role(sync_timedb_main, monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.setthreadtitle",
      lambda title: None,
      raising=False,
  )
  set_daemon_thread_title("", script_name="sync_timedb.py", role="archive-janitor")
  assert get_log_role() == "thread:archive-janitor"


def test_gunicorn_skips_log_role_on_process_title(sync_timedb_main, monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.running_under_gunicorn",
      lambda: True,
  )
  set_log_role(None)
  set_daemon_process_title(name="sync_timedb.py", role="main")
  assert get_log_role() is None
