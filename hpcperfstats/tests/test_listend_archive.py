"""Tests for monitor payload append helper (archive layout)."""

import os

import pytest

import hpcperfstats.listend as ld


def test_append_monitor_payload_to_archive_plain_sample(tmp_path, monkeypatch):
  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  host_fqdn = "n001.demo.cluster.local"
  jid = "12345"
  body = "1700000000.0 %s %s\ncpu 0 1 2 3 4 5 6 7\n" % (jid, host_fqdn)
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


def test_append_monitor_payload_to_archive_preserves_tier_markers(tmp_path, monkeypatch):
  """Sparse @fast/@full rows must pass through unchanged (listend does not parse tiers)."""
  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  host_fqdn = "n001.demo.cluster.local"
  jid = "12345"
  body = (
      "1700000000.0 %s %s\n"
      "!host_tt a,E b,E,R=S c,E d,E,R=S\n"
      "host_tt dev0 @fast 100 300\n"
      "1700000600.0 %s %s\n"
      "host_tt dev0 @full 200 250 400 450\n"
  ) % (jid, host_fqdn, jid, host_fqdn)
  result = ld.append_monitor_payload_to_archive(body)
  assert result.host == host_fqdn
  current = tmp_path / host_fqdn / "current"
  assert current.read_text() == body


def test_append_second_sample_offset_after_first(tmp_path, monkeypatch):
  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  host = "n002.demo.cluster.local"
  first = "1700000000.0 1 %s\ncpu 0\n" % host
  second = "1700000001.0 1 %s\ncpu 1\n" % host
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
    tmp_path, monkeypatch,
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
    tmp_path, monkeypatch,
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
      body = "17000000%02d.0 1 %s\ncpu %d\n" % (i, host, i)
      ld.append_monitor_payload_to_archive(body)
    assert len(removed_locks) <= 1
  finally:
    _release_sticky_archive_writer()


def test_current_hardlink_cache_skips_scandir_on_same_inode(
    tmp_path, monkeypatch,
):
  """Cached digit inode must skip host_dir scandir on the next $ check."""
  host_dir = tmp_path / "h.example.edu"
  host_dir.mkdir()
  current = host_dir / "current"
  current.write_text("x")
  epoch = host_dir / "1700000000"
  os.link(current, epoch)
  assert ld._current_is_hardlinked_to_digit_epoch(
      str(host_dir), str(current),
  ) is True

  def boom(*_a, **_k):
    raise AssertionError("scandir should be skipped on cache hit")

  monkeypatch.setattr(os, "scandir", boom)
  assert ld._current_is_hardlinked_to_digit_epoch(
      str(host_dir), str(current),
  ) is True
