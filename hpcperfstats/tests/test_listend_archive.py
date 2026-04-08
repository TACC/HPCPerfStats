"""Tests for monitor payload append helper (archive layout)."""

import pytest

import hpcperfstats.listend as ld


def test_append_monitor_payload_to_archive_plain_sample(tmp_path, monkeypatch):
  monkeypatch.setattr(ld.cfg, "get_archive_dir_path", lambda: str(tmp_path))
  host_fqdn = "n001.demo.cluster.local"
  jid = "12345"
  body = "1700000000.0 %s %s\ncpu 0 1 2 3 4 5 6 7\n" % (jid, host_fqdn)
  assert ld.append_monitor_payload_to_archive(body) == host_fqdn
  current = tmp_path / host_fqdn / "current"
  assert current.is_file()
  text = current.read_text()
  assert jid in text
  assert host_fqdn in text


def test_append_monitor_payload_to_archive_rejects_empty():
  with pytest.raises(ValueError, match="Empty"):
    ld.append_monitor_payload_to_archive("")
