"""Tests for monitor payload append helper (archive layout)."""

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
