"""Tests for daemon process title resolution and setproctitle wiring."""

import sys
from types import SimpleNamespace

import pytest

from hpcperfstats.process_title import (
    resolve_script_process_title_name,
    set_script_process_title,
)


def test_resolve_script_path():
  assert (
      resolve_script_process_title_name(
          argv=["/home/hpcperfstats/hpcperfstats/dbload/sync_timedb.py", "all"]
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


def test_set_script_process_title_calls_setproctitle(monkeypatch):
  calls = []

  def fake_setproctitle(title):
    calls.append(title)

  monkeypatch.setitem(
      sys.modules,
      "setproctitle",
      SimpleNamespace(setproctitle=fake_setproctitle),
  )
  result = set_script_process_title(name="sync_timedb.py")
  assert result == "sync_timedb.py"
  assert calls == ["sync_timedb.py"]


def test_set_script_process_title_without_setproctitle(monkeypatch):
  monkeypatch.delitem(sys.modules, "setproctitle", raising=False)
  assert set_script_process_title(name="listend.py") == "listend.py"
