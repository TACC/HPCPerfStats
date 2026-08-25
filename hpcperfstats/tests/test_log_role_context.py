"""Tests for log role context wired from process_title hooks."""

import io
import sys

import pytest

from hpcperfstats.dbload.lib.print_utils import (
    format_log_prefix,
    get_log_role,
    ingest_logging,
    janitorial_logging,
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


@pytest.mark.usefixtures("sync_timedb_main")
def test_format_log_prefix_without_role():
  assert format_log_prefix() == "[sync_timedb:main]"


@pytest.mark.usefixtures("sync_timedb_main")
def test_format_log_prefix_with_role():
  set_log_role("main")
  assert format_log_prefix() == "[sync_timedb:main]"
  set_log_role("thread:archive-janitor")
  assert format_log_prefix() == "[sync_timedb:thread:archive-janitor]"


@pytest.mark.usefixtures("sync_timedb_main")
def test_log_print_includes_role_prefix(monkeypatch):
  writes: list[str] = []
  buf = io.StringIO()
  real_write = io.StringIO.write

  def tracking_write(data):
    writes.append(data)
    return real_write(buf, data)

  buf.write = tracking_write  # type: ignore[method-assign]
  monkeypatch.setattr(sys, "stdout", buf)
  set_log_role("worker:archive-pool")
  log_print("seal done", flush=True)
  assert writes == ["[sync_timedb:worker:archive-pool] seal done\n"]


@pytest.mark.usefixtures("sync_timedb_main")
def test_set_daemon_process_title_main_sets_log_role(monkeypatch):
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


@pytest.mark.usefixtures("sync_timedb_main")
def test_apply_pool_worker_process_title_sets_log_role(monkeypatch):
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
      lambda *_args, **_kwargs: False,
  )
  apply_pool_worker_process_title("sync_timedb.py", "archive-pool")
  assert get_log_role() == "worker:archive-pool"


@pytest.mark.usefixtures("sync_timedb_main")
def test_set_daemon_thread_title_sets_log_role(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.setthreadtitle",
      lambda title: None,
      raising=False,
  )
  set_daemon_thread_title("", script_name="sync_timedb.py", role="archive-janitor")
  assert get_log_role() == "thread:archive-janitor"


@pytest.mark.usefixtures("sync_timedb_main")
def test_gunicorn_skips_log_role_on_process_title(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_title.running_under_gunicorn",
      lambda: True,
  )
  set_log_role(None)
  set_daemon_process_title(name="sync_timedb.py", role="main")
  assert get_log_role() is None


@pytest.mark.usefixtures("sync_timedb_main")
def test_janitor_body_prefix_respects_role(monkeypatch):
  writes: list[str] = []
  buf = io.StringIO()
  real_write = io.StringIO.write

  def tracking_write(data):
    writes.append(data)
    return real_write(buf, data)

  buf.write = tracking_write  # type: ignore[method-assign]
  monkeypatch.setattr(sys, "stdout", buf)
  set_log_role("main")
  with janitorial_logging():
    log_print("day-scoped closed_raw")
  assert writes[-1] == "[sync_timedb:main] janitor: day-scoped closed_raw\n"
  writes.clear()
  set_log_role("thread:archive-janitor")
  with janitorial_logging():
    log_print("janitor: discover_ready_day_close")
  assert writes[-1] == (
      "[sync_timedb:thread:archive-janitor] discover_ready_day_close\n"
  )


@pytest.mark.usefixtures("sync_timedb_main")
def test_ingest_body_prefix_main_only(monkeypatch):
  writes: list[str] = []
  buf = io.StringIO()
  real_write = io.StringIO.write

  def tracking_write(data):
    writes.append(data)
    return real_write(buf, data)

  buf.write = tracking_write  # type: ignore[method-assign]
  monkeypatch.setattr(sys, "stdout", buf)
  set_log_role("main")
  with ingest_logging():
    log_print("post_finalize_reconcile")
  assert writes[-1] == (
      "[sync_timedb:main] ingest: post_finalize_reconcile\n"
  )
  writes.clear()
  set_log_role("worker:ingest-pool")
  with ingest_logging():
    log_print("File successfully added to DB")
  assert writes[-1] == (
      "[sync_timedb:worker:ingest-pool] File successfully added to DB\n"
  )
