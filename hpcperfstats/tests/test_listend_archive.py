"""Tests for monitor payload append helper (archive layout)."""

import os

import pytest

import hpcperfstats.listend as ld


def test_append_monitor_payload_to_archive_plain_sample(tmp_path, monkeypatch):
  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  host_fqdn = "n001.demo.cluster.local"
  jid = "12345"
  body = f"1700000000.0 {jid} {host_fqdn}\ncpu 0 1 2 3 4 5 6 7\n"
  result = ld.append_monitor_payload_to_archive(body)
  assert result.host == host_fqdn
  assert result.path.endswith("/current")
  assert result.offset == 0
  assert result.length == len(body.encode("utf-8"))
  current = tmp_path / host_fqdn / "current"
  assert current.is_file()
  text = current.read_text()
  assert jid in text
  assert host_fqdn in text


def test_append_monitor_payload_to_archive_rejects_empty():
  with pytest.raises(ValueError, match="Empty"):
    ld.append_monitor_payload_to_archive("")


def test_append_monitor_payload_to_archive_preserves_tier_markers(
  tmp_path, monkeypatch
):
  """Sparse @fast/@full rows must pass through unchanged (listend does not parse tiers)."""
  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  host_fqdn = "n001.demo.cluster.local"
  jid = "12345"
  body = (
    f"1700000000.0 {jid} {host_fqdn}\n"
    "!host_tt a,E b,E,R=S c,E d,E,R=S\n"
    "host_tt dev0 @fast 100 300\n"
    f"1700000600.0 {jid} {host_fqdn}\n"
    "host_tt dev0 @full 200 250 400 450\n"
  )
  result = ld.append_monitor_payload_to_archive(body)
  assert result.host == host_fqdn
  current = tmp_path / host_fqdn / "current"
  assert current.read_text() == body


def test_append_second_sample_offset_after_first(tmp_path, monkeypatch):
  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  host = "n002.demo.cluster.local"
  first = f"1700000000.0 1 {host}\ncpu 0\n"
  second = f"1700000001.0 1 {host}\ncpu 1\n"
  r1 = ld.append_monitor_payload_to_archive(first)
  r2 = ld.append_monitor_payload_to_archive(second)
  assert r1.offset == 0
  assert r2.offset == r1.length
  assert r2.length == len(second.encode("utf-8"))


def _release_sticky_archive_writer():
  release = getattr(ld, "_release_sticky_archive_writer", None)
  if callable(release):
    release()


def test_append_writes_raw_amqp_bytes_without_utf8_roundtrip(
  tmp_path,
  monkeypatch,
):
  """Archive ``current`` must be the AMQP bytes, not decode/encode."""
  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  host = "n001.demo.cluster.local"
  body = b"1700000000.0 12345 %s \xff\xfe\ncpu 0 1 2\n" % host.encode("ascii")
  try:
    result = ld.append_monitor_payload_to_archive(body)
    assert result.host == host
    current = tmp_path / host / "current"
    assert current.read_bytes() == body
  finally:
    _release_sticky_archive_writer()


def test_n_appends_do_not_unlink_flock_sidecar_each_sample(
  tmp_path,
  monkeypatch,
):
  """Sticky flock keeps ``current.fnctl.lock`` across samples (Approach B)."""
  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  host = "n003.demo.cluster.local"
  removed_locks = []
  real_remove = os.remove

  def spy_remove(path, *args, **kwargs):
    if str(path).endswith(".fnctl.lock"):
      removed_locks.append(str(path))
    return real_remove(path, *args, **kwargs)

  monkeypatch.setattr(os, "remove", spy_remove)
  try:
    for i in range(20):
      body = f"17000000{i:02}.0 1 {host}\ncpu {i}\n"
      ld.append_monitor_payload_to_archive(body)
    assert len(removed_locks) <= 1
  finally:
    _release_sticky_archive_writer()


def test_current_hardlink_cache_skips_scandir_on_same_inode(
  tmp_path,
  monkeypatch,
):
  """Cached digit inode must skip host_dir scandir on the next $ check."""
  host_dir = tmp_path / "h.example.edu"
  host_dir.mkdir()
  current = host_dir / "current"
  current.write_text("x")
  epoch = host_dir / "1700000000"
  os.link(current, epoch)
  assert (
    ld._current_is_hardlinked_to_digit_epoch(
      str(host_dir),
      str(current),
    )
    is True
  )

  def boom(*_a, **_k):
    raise AssertionError("scandir should be skipped on cache hit")

  monkeypatch.setattr(os, "scandir", boom)
  assert (
    ld._current_is_hardlinked_to_digit_epoch(
      str(host_dir),
      str(current),
    )
    is True
  )


class _RotPipe:
  def __init__(self, sink, fail=False):
    self.sink = sink
    self.fail = fail

  def lpush(self, key, value):
    self.sink.append(("lpush", key, value))

  def ltrim(self, key, start, end):
    self.sink.append(("ltrim", key, start, end))

  def expire(self, key, ttl):
    self.sink.append(("expire", key, ttl))

  def execute(self):
    if self.fail:
      raise ConnectionError("valkey down")
    return []


class _RotClient:
  def __init__(self, fail=False):
    self.ops = []
    self.fail = fail

  def pipeline(self, transaction=False):
    return _RotPipe(self.ops, self.fail)


def _dollar(host, ts):
  return (
    f"$\n1 {host}\n$hpcperfstats 3.0\n!host_cpu user,E\n"
    f"{ts}.0 1 {host}\nhost_cpu 0 5\n"
  )


def test_listend_dollar_records_rotation(tmp_path, monkeypatch):
  """A $ that closes a segment queues one rotation; samples queue nothing."""
  import json

  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  queued = []
  monkeypatch.setattr(ld, "_enqueue_rotation_record", queued.append)
  monkeypatch.setattr(ld, "_enqueue_monitor_identity_update", lambda i: None)
  monkeypatch.setattr(ld, "_enqueue_recent_host_update", lambda h: None)
  real_tail = helpers.read_stats_file_tail_identity

  def _no_tail_on_consume(path):
    raise AssertionError("tail read must run in the writer thread")

  monkeypatch.setattr(
    helpers, "read_stats_file_tail_identity", _no_tail_on_consume
  )
  host = "n010.demo.cluster.local"
  _release_sticky_archive_writer()
  ld.append_monitor_payload_to_archive(_dollar(host, 1700000000))
  assert queued == [], "the first $ closes nothing"
  ld.append_monitor_payload_to_archive(f"1700000060.0 1 {host}\nhost_cpu 0 6\n")
  assert queued == [], "sample messages never record a rotation"
  closed_size = (tmp_path / host / "current").stat().st_size
  ld.append_monitor_payload_to_archive(_dollar(host, 1700000120))
  _release_sticky_archive_writer()
  assert len(queued) == 1
  rec = queued[0]
  assert rec.fqdn == host
  assert rec.closed_bytes == closed_size
  assert os.path.basename(rec.closed_path) == str(rec.closed_epoch)
  assert os.path.isfile(rec.closed_path)
  assert rec.new_epoch == 1700000120
  assert rec.new_epoch != rec.closed_epoch

  monkeypatch.setattr(helpers, "read_stats_file_tail_identity", real_tail)
  client = _RotClient()
  ld._write_rotation_record(client, rec)
  ops = [op[0] for op in client.ops]
  assert ops == ["lpush", "ltrim", "expire"]
  assert client.ops[0][1] == f"ingest_rotation:v1:{host}"
  assert client.ops[1][2:] == (0, 7)
  assert client.ops[2][2] == ld.RECENT_HOST_TTL_SECONDS
  doc = json.loads(client.ops[0][2])
  _h, tail_ts = real_tail(rec.closed_path)
  assert doc["closed_last_sample_at"] == int(tail_ts.timestamp()) == 1700000060
  assert doc["closed_bytes"] == closed_size
  assert doc["new_epoch"] == 1700000120
  assert doc["rotated_at"] == rec.rotated_at


def test_listend_rotation_tail_failure_stores_null(monkeypatch):
  import json

  client = _RotClient()
  rec = ld._RotationRecord("a.example.com", "/no/such/1700", 5, 1700, 10, 1800)
  ld._write_rotation_record(client, rec)
  assert json.loads(client.ops[0][2])["closed_last_sample_at"] is None


def test_listend_rotation_valkey_error_is_swallowed(monkeypatch):
  """The writer thread logs and keeps going when Valkey fails."""
  client = _RotClient(fail=True)
  monkeypatch.setattr(ld, "_get_recent_host_valkey_client", lambda: client)
  items = [ld._RotationRecord("a.example.com", "/no/1", 5, 1, 0, 2)]
  done = []

  def _get(timeout=1.0):
    if items:
      return items.pop(0)
    ld._recent_host_worker_stop_event.set()
    raise ld.queue.Empty

  monkeypatch.setattr(ld._recent_host_queue, "get", _get)
  monkeypatch.setattr(
    ld._recent_host_queue, "task_done", lambda: done.append(1)
  )
  ld._recent_host_worker_stop_event.clear()
  ld._recent_host_worker()
  ld._recent_host_worker_stop_event.clear()
  assert done == [1]
  assert client.ops[0][0] == "lpush"
