"""A1/A2: tar existence is decided inside the write lock; appends group per tar."""
from __future__ import annotations

import inspect
import os
import tarfile

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
from hpcperfstats.tests.fake_redis_queue import FakeRedis


def test_append_to_tar_uses_r_unconditionally_inside_lock():
  """A1: existence sampling and tar -c vs -r must happen under the write lock."""
  source = inspect.getsource(st._append_to_tar)
  exists_idx = source.find("os.path.exists(tar_path)")
  lock_idx = source.find("with file_write_lock(tar_path)")
  assert lock_idx != -1
  assert exists_idx == -1 or exists_idx > lock_idx
  assert '"-c"' not in source and "'-c'" not in source
  assert '"-r"' in source or "'-r'" in source


def test_concurrent_append_never_recreates_tar(tmp_path):
  """A1: two appends to a new daily tar must not truncate the first member."""
  tar_path = str(tmp_path / "2026-08-01.tar")
  first = tmp_path / "a.stats"
  second = tmp_path / "b.stats"
  first.write_text("one\n", encoding="utf-8")
  second.write_text("two\n", encoding="utf-8")
  st._append_to_tar(tar_path, [str(first)])
  st._append_to_tar(tar_path, [str(second)])
  with tarfile.open(tar_path, "r") as handle:
    names = [os.path.basename(member.name) for member in handle.getmembers()]
  assert "a.stats" in names
  assert "b.stats" in names


def test_append_jobs_group_per_daily_tar(monkeypatch):
  """A2: one archive-pool task per daily tar, batched up to the INI batch size."""
  submitted = []

  class _Pool:
    def apply_async(self, fn, args):
      submitted.append(args[0])
      class _Pending:
        def ready(self):
          return False
      return _Pending()

  monkeypatch.setattr(
      qo.cfg, "get_sync_timedb_tar_append_batch_size", lambda: 8,
  )
  monkeypatch.setattr(
      qo, "daily_tar_path_for_stats_path",
      lambda path, daily: "/daily/2026-08-01.tar",
  )
  qo.reset_append_day_lists_for_tests()
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  for name in ("/raw/a", "/raw/b", "/raw/c"):
    jq.enqueue_list_job(client, kind="append", identity=name, dedupe=True)

  def _isfile(path):
    return str(path).startswith("/raw/")

  monkeypatch.setattr(qo.os.path, "isfile", _isfile)
  inflight = {}
  claims = {}
  n = qo._fill_append_slots(
      client,
      cap=4,
      inflight=inflight,
      claims=claims,
      archive_pool=_Pool(),
      tgz_archive_dir="/daily",
  )
  assert n == 1
  assert len(submitted) == 1
  tar_path, paths = submitted[0]
  assert tar_path == "/daily/2026-08-01.tar"
  assert paths == ["/raw/a", "/raw/b", "/raw/c"]
  assert list(inflight) == ["/daily/2026-08-01.tar"]


def test_fill_append_slots_interleaved_days_batches_same_tar(monkeypatch):
  """Mixed FIFO still submits up to batch_size paths for one calendar day."""
  submitted = []

  class _Pool:
    def apply_async(self, fn, args):
      submitted.append(args[0])
      class _Pending:
        def ready(self):
          return False
      return _Pending()

  monkeypatch.setattr(
      qo.cfg, "get_sync_timedb_tar_append_batch_size", lambda: 4,
  )

  def _tar_for(path, daily):
    if "/d1/" in str(path):
      return "/daily/2026-08-01.tar"
    return "/daily/2026-08-02.tar"

  monkeypatch.setattr(qo, "daily_tar_path_for_stats_path", _tar_for)
  qo.reset_append_day_lists_for_tests()
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  identities = [
      "/raw/d1/a",
      "/raw/d2/a",
      "/raw/d1/b",
      "/raw/d2/b",
      "/raw/d1/c",
      "/raw/d2/c",
      "/raw/d1/d",
      "/raw/d2/d",
  ]
  for name in identities:
    jq.enqueue_list_job(client, kind="append", identity=name, dedupe=True)

  def _isfile(path):
    return str(path).startswith("/raw/")

  monkeypatch.setattr(qo.os.path, "isfile", _isfile)
  inflight = {}
  claims = {}
  n = qo._fill_append_slots(
      client,
      cap=2,
      inflight=inflight,
      claims=claims,
      archive_pool=_Pool(),
      tgz_archive_dir="/daily",
  )
  assert n == 2
  assert len(submitted) == 2
  by_tar = {item[0]: item[1] for item in submitted}
  assert by_tar["/daily/2026-08-01.tar"] == [
      "/raw/d1/a", "/raw/d1/b", "/raw/d1/c", "/raw/d1/d",
  ]
  assert by_tar["/daily/2026-08-02.tar"] == [
      "/raw/d2/a", "/raw/d2/b", "/raw/d2/c", "/raw/d2/d",
  ]


def test_fill_append_slots_holds_claims_when_tar_inflight(monkeypatch):
  """One writer per daily tar: inflight day stays in the in-memory list."""
  submitted = []

  class _Pool:
    def apply_async(self, fn, args):
      submitted.append(args[0])
      class _Pending:
        def ready(self):
          return False
      return _Pending()

  monkeypatch.setattr(
      qo.cfg, "get_sync_timedb_tar_append_batch_size", lambda: 2,
  )
  monkeypatch.setattr(
      qo, "daily_tar_path_for_stats_path",
      lambda path, daily: "/daily/2026-08-01.tar",
  )
  qo.reset_append_day_lists_for_tests()
  client = FakeRedis()
  jq.reset_job_queue_script_cache_for_tests()
  for name in ("/raw/a", "/raw/b"):
    jq.enqueue_list_job(client, kind="append", identity=name, dedupe=True)

  def _isfile(path):
    return str(path).startswith("/raw/")

  monkeypatch.setattr(qo.os.path, "isfile", _isfile)
  inflight = {"/daily/2026-08-01.tar": object()}
  claims = {}
  n = qo._fill_append_slots(
      client,
      cap=4,
      inflight=inflight,
      claims=claims,
      archive_pool=_Pool(),
      tgz_archive_dir="/daily",
  )
  assert n == 0
  assert submitted == []
  assert qo._APPEND_DAY_LISTS.peek_len("2026-08-01") == 2

