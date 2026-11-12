"""Sealed archives are decompressed only when the answer needs it.

A full read of a multi-TB ``.tar.zst`` costs hours on a busy volume
(hpcperfstats01 2026-11-11). These pin the reads that were pure overhead:

- the seal-skip check ran ``zstd -t`` on the old archive before every reseal,
  even when the tar had changed and the new file is verified anyway;
- ``atomic_seal_tar_to_zst`` with no tar ran that check only to log a line;
- sealed-only populate streamed the whole day although the identity-keyed
  sealed member map (T0) already answers.
"""

from __future__ import annotations

import subprocess
import types

from hpcperfstats.dbload.lib import (
  sync_timedb_archive_helpers as ah,
  sync_timedb_archive_members_coord as coord,
)


def _files(tmp_path):
  tar = tmp_path / "2026-08-02.tar"
  zst = tmp_path / "2026-08-02.tar.zst"
  tar.write_bytes(b"t")
  zst.write_bytes(b"z")
  return str(tar), str(zst)


def _patch_seal(monkeypatch, zst_members, tar_members, zstd_ok=True):
  calls: list[str] = []

  def _zstd_test(path, _threads):
    calls.append(path)
    if not zstd_ok:
      raise subprocess.CalledProcessError(1, ["zstd", "-t", path])

  monkeypatch.setattr(ah, "zstd_test", _zstd_test)
  monkeypatch.setattr(
    ah,
    "_sealed_archive_members_via_store_or_scan",
    lambda *a, **k: (True, dict(zst_members)),
  )
  monkeypatch.setattr(
    ah, "get_existing_archive_members", lambda *_a, **_k: dict(tar_members)
  )
  return calls


def test_reseal_of_a_changed_tar_does_not_decompress_the_old_archive(
  monkeypatch, tmp_path
):
  tar, zst = _files(tmp_path)
  calls = _patch_seal(monkeypatch, {"h/1": 1}, {"h/1": 1, "h/2": 2})
  skipped, members = ah._seal_skip_existing_zst_equivalent(tar, zst, 4, None)
  assert (skipped, members) == (False, {"h/1": 1})
  assert calls == [], "old archive was decompressed for a reseal"


def test_skip_still_verifies_the_old_archive_it_keeps(monkeypatch, tmp_path):
  tar, zst = _files(tmp_path)
  calls = _patch_seal(monkeypatch, {"h/1": 1}, {"h/1": 1})
  assert ah._seal_skip_existing_zst_equivalent(tar, zst, 4, None) == (
    True,
    {"h/1": 1},
  )
  assert calls == [zst]


def test_skip_falls_back_to_reseal_when_kept_archive_fails(
  monkeypatch, tmp_path
):
  tar, zst = _files(tmp_path)
  _patch_seal(monkeypatch, {"h/1": 1}, {"h/1": 1}, zstd_ok=False)
  assert ah._seal_skip_existing_zst_equivalent(tar, zst, 4, None) == (
    False,
    None,
  )


def test_seal_without_a_tar_never_decompresses(monkeypatch, tmp_path):
  zst = tmp_path / "2026-08-02.tar.zst"
  zst.write_bytes(b"z")
  calls = _patch_seal(monkeypatch, {"h/1": 1}, {})
  tar = str(tmp_path / "2026-08-02.tar")
  assert ah.atomic_seal_tar_to_zst(tar, str(zst), 4, 9, False) is None
  assert ah._seal_skip_existing_zst_equivalent(tar, str(zst), 4, None) == (
    True,
    None,
  )
  assert calls == []


def _run_sealed_populate(monkeypatch, tmp_path, mapped):
  sealed = tmp_path / "2026-08-02.tar.zst"
  sealed.write_bytes(b"z")
  streams: list[str] = []
  written: list[dict] = []

  def _stream(path, on_member=None, **_k):
    streams.append(path)
    members = {"h/9": 9}
    for name, size in members.items():
      on_member(name, size)
    return True, members, False, None

  monkeypatch.setattr(ah, "_stream_compressed_archive_members", _stream)
  monkeypatch.setattr(ah, "sealed_members_lookup", lambda *a, **k: mapped)
  monkeypatch.setattr(
    ah,
    "write_sealed_member_map",
    lambda path, members, **k: written.append(dict(members)),
  )
  monkeypatch.setattr(ah, "_ensure_populate_scan_allowed", lambda: None)
  monkeypatch.setattr(ah, "_build_populate_source_decision", lambda *a: {})
  monkeypatch.setattr(
    coord,
    "build_archive_members_keys",
    lambda _k: types.SimpleNamespace(day_token="2026-08-02"),
  )
  seen: dict[str, int] = {}

  def _populate(keys, scan_fn, **_k):
    readable, _dups, _err = scan_fn(lambda n, s: seen.__setitem__(n, s))
    return dict(seen) if readable else None

  monkeypatch.setattr(coord, "populate_archive_members", _populate)
  members = ah._populate_members_from_sealed_scan(
    str(sealed), (str(sealed),), tar_path=str(tmp_path / "2026-08-02.tar")
  )
  return members, streams, written


def test_sealed_populate_answers_from_the_sealed_member_map(
  monkeypatch, tmp_path
):
  members, streams, written = _run_sealed_populate(
    monkeypatch, tmp_path, {"h/1": 1, "h/2": 2}
  )
  assert members == {"h/1": 1, "h/2": 2}
  assert streams == [], "populate decompressed a day its map already answers"
  assert written == []


def test_sealed_populate_streams_and_saves_the_map_on_a_miss(
  monkeypatch, tmp_path
):
  members, streams, written = _run_sealed_populate(monkeypatch, tmp_path, None)
  assert members == {"h/9": 9}
  assert len(streams) == 1
  assert written == [{"h/9": 9}]


def _mark_skip(monkeypatch, tmp_path, zstd_exc):
  """Drive mark_archive_day_ingest_skip_and_raise with a stubbed zstd -t."""
  import pytest

  sealed = tmp_path / "2026-08-02.tar.zst"
  sealed.write_bytes(b"\x28\xb5\x2f\xfd")
  probes: list[str] = []

  def _zstd_test(path, _threads):
    if zstd_exc is not None:
      raise zstd_exc

  from hpcperfstats.dbload.lib import zstd_cli

  monkeypatch.setattr(zstd_cli, "zstd_test", _zstd_test)
  monkeypatch.setattr(
    ah,
    "probe_tar_archive_readability",
    lambda path, **k: probes.append(path) or ah.CORRUPT,
  )
  monkeypatch.setattr(
    coord, "set_archive_day_ingest_skip", lambda *a, **k: None
  )
  keys = types.SimpleNamespace(day_token="2026-08-02")
  with pytest.raises(Exception) as info:
    ah.mark_archive_day_ingest_skip_and_raise(
      str(sealed), keys, None, RuntimeError("gnu tar tvf failed rc=1")
    )
  return probes, info.value


def test_bad_zstd_frames_are_not_decompressed_a_second_time(
  monkeypatch, tmp_path
):
  """zstd -t already read the file and failed; the re-probe only re-reads it."""
  bad = subprocess.CalledProcessError(
    1, ["zstd", "-t"], stderr="Restored data doesn't match checksum"
  )
  probes, exc = _mark_skip(monkeypatch, tmp_path, bad)
  assert probes == []
  assert type(exc).__name__ == "ArchiveDayIngestSkipError"


def test_signal_killed_zstd_test_still_reprobes(monkeypatch, tmp_path):
  """A killed zstd -t says nothing about the frames (DE-29: transient)."""
  killed = subprocess.CalledProcessError(-9, ["zstd", "-t"], stderr="")
  probes, _exc = _mark_skip(monkeypatch, tmp_path, killed)
  assert len(probes) == 1


def test_tar_stream_failure_with_good_frames_still_reprobes(
  monkeypatch, tmp_path
):
  """zstd -t passed: the stream failure may be a race, so re-probe (DE-31)."""
  probes, _exc = _mark_skip(monkeypatch, tmp_path, None)
  assert len(probes) == 1
