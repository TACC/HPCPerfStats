"""Unit tests for the shared Django bootstrap helper (thread safety)."""
from __future__ import annotations

import threading
import time


def _reset_bootstrap_state(monkeypatch):
  """Force ensure_django() to run a fresh (faked) setup for one test."""
  from hpcperfstats.dbload.lib import django_bootstrap

  monkeypatch.setattr(django_bootstrap, "_SETUP_COMPLETE", False)
  monkeypatch.setattr(django_bootstrap, "_SETUP_OWNER_IDENT", None)
  return django_bootstrap


def test_ensure_django_runs_setup_once_under_thread_concurrency(monkeypatch):
  """Concurrent worker threads must not run django.setup() in parallel.

  listend starts up to `listend_db_ingest_pool_processes` DB threads that each
  call ensure_django(). Django's LazySettings.__setattr__ clears __dict__
  before storing `_wrapped`, so a second thread reading settings during that
  window falls back to the LazyObject class attribute `_wrapped = None` and
  raises `AttributeError: 'NoneType' object has no attribute 'LOGGING'`.
  """
  import django

  django_bootstrap = _reset_bootstrap_state(monkeypatch)

  state_lock = threading.Lock()
  concurrent = {"active": 0, "overlaps": 0, "calls": 0}

  def fake_setup(**kwargs) -> None:
    del kwargs
    with state_lock:
      concurrent["active"] += 1
      concurrent["calls"] += 1
      if concurrent["active"] > 1:
        concurrent["overlaps"] += 1
    time.sleep(0.02)
    with state_lock:
      concurrent["active"] -= 1

  monkeypatch.setattr(django, "setup", fake_setup)

  errors: list = []
  start = threading.Event()

  def worker() -> None:
    start.wait(5.0)
    try:
      django_bootstrap.ensure_django()
    except BaseException as exc:  # noqa: BLE001 - recorded for assertion
      errors.append(exc)

  threads = [
      threading.Thread(target=worker, name="bootstrap-%d" % i, daemon=True)
      for i in range(16)
  ]
  for thread in threads:
    thread.start()
  start.set()
  for thread in threads:
    thread.join(30.0)
    assert not thread.is_alive()

  assert errors == []
  assert concurrent["overlaps"] == 0
  assert concurrent["calls"] == 1


def test_ensure_django_retries_after_failed_setup(monkeypatch):
  """A failed django.setup() must not latch the module as bootstrapped."""
  import django

  django_bootstrap = _reset_bootstrap_state(monkeypatch)

  calls = {"n": 0}

  def flaky_setup(**kwargs) -> None:
    del kwargs
    calls["n"] += 1
    if calls["n"] == 1:
      raise RuntimeError("settings module missing")

  monkeypatch.setattr(django, "setup", flaky_setup)

  try:
    django_bootstrap.ensure_django()
    raise AssertionError("expected first ensure_django() to raise")
  except RuntimeError:
    pass

  django_bootstrap.ensure_django()
  django_bootstrap.ensure_django()
  assert calls["n"] == 2


def test_ensure_django_reentrant_call_does_not_recurse(monkeypatch):
  """A nested ensure_django() during django.setup() must be a no-op."""
  import django

  django_bootstrap = _reset_bootstrap_state(monkeypatch)

  calls = {"n": 0}

  def reentrant_setup(**kwargs) -> None:
    del kwargs
    calls["n"] += 1
    if calls["n"] < 5:
      # Simulates an app module that calls ensure_django() at import time.
      django_bootstrap.ensure_django()

  monkeypatch.setattr(django, "setup", reentrant_setup)

  django_bootstrap.ensure_django()
  assert calls["n"] == 1
