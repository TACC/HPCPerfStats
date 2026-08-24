"""Tests for daemon process title resolution and setproctitle wiring."""

import importlib
import sys
from types import SimpleNamespace


from hpcperfstats.dbload.lib.process_title import (
    apply_pool_worker_process_title,
    enable_parent_death_signal,
    format_daemon_process_title,
    format_daemon_thread_title,
    resolve_script_process_title_name,
    running_under_gunicorn,
    set_daemon_process_title,
    set_script_process_title,
)


def test_resolve_script_path():
  assert (
      resolve_script_process_title_name(
          argv=["/home/hpcperfstats/hpcperfstats/dbload/sync_timedb.py"]
      )
      == "sync_timedb.py"
  )


def test_resolve_python_m_module():
  assert (
      resolve_script_process_title_name(
          argv=["/usr/local/bin/python3", "-m", "hpcperfstats.seal_syslog_daily"]
      )
      == "seal_syslog_daily.py"
  )


def test_resolve_explicit_name_adds_py_suffix():
  assert resolve_script_process_title_name(explicit="listend") == "listend.py"


def test_resolve_interpreter_argv_returns_none():
  assert resolve_script_process_title_name(argv=["/usr/bin/python3.12"]) is None


def test_format_daemon_process_title_main():
  assert (
      format_daemon_process_title("sync_timedb.py", role="main")
      == "sync_timedb.py [main]"
  )


def test_format_daemon_process_title_worker():
  assert (
      format_daemon_process_title(
          "sync_timedb.py",
          role="worker",
          pool_kind="ingest-pool",
      )
      == "sync_timedb.py [worker:ingest-pool]"
  )


def test_format_daemon_thread_title():
  assert (
      format_daemon_thread_title("listend.py", role="idle-monitor")
      == "listend.py [thread:idle-monitor]"
  )


def test_running_under_gunicorn_server_software(monkeypatch):
  monkeypatch.setenv("SERVER_SOFTWARE", "gunicorn/26.0.0")
  assert running_under_gunicorn() is True


def test_running_under_gunicorn_getproctitle(monkeypatch):
  monkeypatch.delenv("SERVER_SOFTWARE", raising=False)
  monkeypatch.setitem(
      sys.modules,
      "setproctitle",
      SimpleNamespace(
          getproctitle=lambda: "gunicorn: worker [hpcperfstats]",
          setproctitle=lambda _title: None,
      ),
  )
  assert running_under_gunicorn() is True


def test_set_daemon_process_title_skips_under_gunicorn(monkeypatch):
  calls = []

  def fake_setproctitle(title):
    calls.append(title)

  monkeypatch.setenv("SERVER_SOFTWARE", "gunicorn/26.0.0")
  monkeypatch.setitem(
      sys.modules,
      "setproctitle",
      SimpleNamespace(
          getproctitle=lambda: "gunicorn: worker [hpcperfstats]",
          setproctitle=fake_setproctitle,
      ),
  )
  assert set_daemon_process_title(name="sync_timedb.py", role="main") is None
  assert calls == []


def test_set_script_process_title_calls_setproctitle(monkeypatch):
  calls = []

  def fake_setproctitle(title):
    calls.append(title)

  monkeypatch.delenv("SERVER_SOFTWARE", raising=False)
  monkeypatch.setitem(
      sys.modules,
      "setproctitle",
      SimpleNamespace(
          getproctitle=lambda: "",
          setproctitle=fake_setproctitle,
      ),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.running_under_gunicorn",
      lambda: False,
  )
  result = set_script_process_title(name="sync_timedb.py")
  assert result == "sync_timedb.py [main]"
  assert calls == ["sync_timedb.py [main]"]


def test_set_script_process_title_without_setproctitle(monkeypatch):
  monkeypatch.delitem(sys.modules, "setproctitle", raising=False)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.running_under_gunicorn",
      lambda: False,
  )
  assert set_script_process_title(name="listend.py") == "listend.py [main]"


def test_apply_pool_worker_process_title(monkeypatch):
  calls = []
  pdeath_calls = []

  def fake_setproctitle(title):
    calls.append(title)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.running_under_gunicorn",
      lambda: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.enable_parent_death_signal",
      lambda sig=None: pdeath_calls.append(sig),
  )
  monkeypatch.setitem(
      sys.modules,
      "setproctitle",
      SimpleNamespace(
          getproctitle=lambda: "",
          setproctitle=fake_setproctitle,
      ),
  )
  # Pool calls initializer(*initargs), not initializer(initargs).
  apply_pool_worker_process_title("sync_timedb.py", "ingest-pool")
  assert calls == ["sync_timedb.py [worker:ingest-pool]"]
  assert len(pdeath_calls) == 1


def test_apply_pool_worker_process_title_restores_default_signal_handlers(monkeypatch):
  """Workers must not inherit a parent flag-only SIGTERM handler (hs04 hang)."""
  import signal

  restored = []

  def fake_signal(sig, handler):
    restored.append((sig, handler))
    return signal.SIG_IGN

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.signal.signal",
      fake_signal,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.enable_parent_death_signal",
      lambda sig=None: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.set_daemon_process_title",
      lambda **kwargs: None,
  )
  apply_pool_worker_process_title("update_metrics.py", "metrics-pool")
  assert (signal.SIGTERM, signal.SIG_DFL) in restored
  assert (signal.SIGINT, signal.SIG_DFL) in restored


def test_enable_parent_death_signal_noop_off_linux(monkeypatch):
  monkeypatch.setattr("hpcperfstats.dbload.lib.process_title.sys.platform", "darwin")
  assert enable_parent_death_signal() is False


def test_import_sync_acct_does_not_set_process_title(monkeypatch):
  calls = []

  def fake_setproctitle(title):
    calls.append(title)

  monkeypatch.setitem(
      sys.modules,
      "setproctitle",
      SimpleNamespace(
          getproctitle=lambda: "",
          setproctitle=fake_setproctitle,
      ),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.running_under_gunicorn",
      lambda: False,
  )
  if "hpcperfstats.dbload.sync_acct" in sys.modules:
    importlib.reload(sys.modules["hpcperfstats.dbload.sync_acct"])
  else:
    importlib.import_module("hpcperfstats.dbload.sync_acct")
  assert calls == []
