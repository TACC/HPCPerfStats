import types

import pytest

from hpcperfstats import shutdown_utils


def test_sleep_until_shutdown_returns_early(monkeypatch):
  calls = []

  def fake_sleep(duration):
    calls.append(duration)
    # Simulate external shutdown request after first sleep
    shutdown_utils.shutdown_requested[0] = True

  monkeypatch.setattr(shutdown_utils.time, "sleep", fake_sleep)
  shutdown_utils.shutdown_requested[0] = False

  shutdown_utils.sleep_until_shutdown(30, interval=5)

  # Only one sleep call should be needed because shutdown flag flips
  assert calls == [5]


def test_sleep_until_shutdown_full_duration(monkeypatch):
  calls = []

  def fake_sleep(duration):
    calls.append(duration)

  monkeypatch.setattr(shutdown_utils.time, "sleep", fake_sleep)
  shutdown_utils.shutdown_requested[0] = False

  shutdown_utils.sleep_until_shutdown(12, interval=5)

  # Should sleep in chunks that sum up to the requested duration
  assert sum(calls) == 12
  assert calls == [5, 5, 2]


def test_send_sigchld_to_parent_calls_os_kill(monkeypatch):
  calls = []

  monkeypatch.setattr(shutdown_utils.os, "getppid", lambda: 123)

  def _fake_kill(pid, sig):
    calls.append((pid, sig))

  monkeypatch.setattr(shutdown_utils.os, "kill", _fake_kill)

  shutdown_utils.send_sigchld_to_parent()

  import signal
  assert calls == [(123, signal.SIGCHLD)]


def test_make_sigterm_handler_sets_flag_and_exits():
  flag = [False]
  handler = shutdown_utils.make_sigterm_handler(flag, exit_code=143)

  import signal
  with pytest.raises(SystemExit) as excinfo:
    handler(signal.SIGTERM, None)

  assert flag[0] is True
  assert excinfo.value.code == 143

