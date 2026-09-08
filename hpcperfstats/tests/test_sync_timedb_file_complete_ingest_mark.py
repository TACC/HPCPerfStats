"""Unit tests for durable file-complete ingest marks (no live DB)."""

from __future__ import annotations

import json
import os

from hpcperfstats.dbload.lib import sync_timedb_file_complete_ingest_mark as fcm
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
  FILE_COMPLETE_INGEST_MARK_SCHEMA_VERSION,
  load_persistence_document,
)


def _seg(tmp_path, name="host/1710000000", text="payload"):
  path = tmp_path / name
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text)
  return str(path)


def test_path_fingerprint_key_missing_returns_none(tmp_path):
  assert fcm.path_fingerprint_key(str(tmp_path / "missing")) is None


def test_path_fingerprint_key_empty_string_returns_none():
  assert fcm.path_fingerprint_key("") is None


def test_has_mark_false_for_missing_path(tmp_path):
  assert (
    fcm.has_file_complete_ingest_mark(
      str(tmp_path / "gone"),
      archive_data_dir=str(tmp_path),
    )
    is False
  )


def test_has_mark_false_when_archive_dir_empty(tmp_path):
  seg = _seg(tmp_path)
  assert fcm.has_file_complete_ingest_mark(seg, archive_data_dir="") is False


def test_has_mark_false_when_mark_file_missing(tmp_path):
  seg = _seg(tmp_path)
  assert (
    fcm.has_file_complete_ingest_mark(
      seg,
      archive_data_dir=str(tmp_path),
    )
    is False
  )


def test_record_and_has_roundtrip(tmp_path):
  seg = _seg(tmp_path)
  logs = []
  assert (
    fcm.record_file_complete_ingest_mark(
      seg,
      archive_data_dir=str(tmp_path),
      log_fn=lambda msg, **_k: logs.append(msg),
    )
    is True
  )
  assert fcm.has_file_complete_ingest_mark(
    seg, archive_data_dir=str(tmp_path)
  )
  assert any("file_complete_ingest_mark recorded" in line for line in logs)


def test_record_missing_path_returns_false(tmp_path):
  assert (
    fcm.record_file_complete_ingest_mark(
      str(tmp_path / "nope"),
      archive_data_dir=str(tmp_path),
    )
    is False
  )


def test_record_empty_archive_dir_returns_false(tmp_path):
  seg = _seg(tmp_path)
  assert (
    fcm.record_file_complete_ingest_mark(seg, archive_data_dir="") is False
  )


def test_record_stat_race_after_fingerprint_returns_false(
  tmp_path, monkeypatch
):
  seg = _seg(tmp_path)
  real_stat = os.stat
  calls = {"n": 0}

  def _stat(path, *args, **kwargs):
    if path == seg:
      calls["n"] += 1
      if calls["n"] >= 2:
        raise OSError("gone")
    return real_stat(path, *args, **kwargs)

  monkeypatch.setattr(os, "stat", _stat)
  assert (
    fcm.record_file_complete_ingest_mark(
      seg,
      archive_data_dir=str(tmp_path),
    )
    is False
  )


def test_load_entries_rejects_malformed_list_payload(tmp_path):
  mark = fcm.file_complete_ingest_mark_path(str(tmp_path))
  with open(mark, "w", encoding="utf-8") as handle:
    json.dump([{"not": "a dict envelope"}], handle)
  assert fcm._load_entries(mark) == {}


def test_load_entries_rejects_entries_list(tmp_path):
  mark = fcm.file_complete_ingest_mark_path(str(tmp_path))
  with open(mark, "w", encoding="utf-8") as handle:
    json.dump(
      {
        "schema_version": FILE_COMPLETE_INGEST_MARK_SCHEMA_VERSION,
        "entries": ["not-a-dict"],
      },
      handle,
    )
  loaded = load_persistence_document(
    mark,
    "file_complete_ingest_mark",
    default={"entries": {}},
  )
  assert loaded == {"entries": {}}
  assert fcm._load_entries(mark) == {}


def test_load_entries_none_entries_is_empty(tmp_path):
  mark = fcm.file_complete_ingest_mark_path(str(tmp_path))
  with open(mark, "w", encoding="utf-8") as handle:
    json.dump(
      {
        "schema_version": FILE_COMPLETE_INGEST_MARK_SCHEMA_VERSION,
      },
      handle,
    )
  assert fcm._load_entries(mark) == {}


def test_clear_empty_paths_returns_zero(tmp_path):
  assert (
    fcm.clear_file_complete_ingest_marks([], archive_data_dir=str(tmp_path))
    == 0
  )
  assert (
    fcm.clear_file_complete_ingest_marks(
      None, archive_data_dir=str(tmp_path)
    )
    == 0
  )
  assert (
    fcm.clear_file_complete_ingest_marks(
      [""], archive_data_dir=str(tmp_path)
    )
    == 0
  )


def test_clear_empty_archive_dir_returns_zero(tmp_path):
  seg = _seg(tmp_path)
  fcm.record_file_complete_ingest_mark(seg, archive_data_dir=str(tmp_path))
  assert fcm.clear_file_complete_ingest_marks([seg], archive_data_dir="") == 0


def test_clear_missing_mark_file_returns_zero(tmp_path):
  seg = _seg(tmp_path)
  assert (
    fcm.clear_file_complete_ingest_marks(
      [seg],
      archive_data_dir=str(tmp_path),
    )
    == 0
  )


def test_clear_by_meta_path_and_fingerprint(tmp_path):
  seg = _seg(tmp_path)
  other = _seg(tmp_path, name="host/1710000001")
  fcm.record_file_complete_ingest_mark(seg, archive_data_dir=str(tmp_path))
  fcm.record_file_complete_ingest_mark(other, archive_data_dir=str(tmp_path))
  logs = []
  removed = fcm.clear_file_complete_ingest_marks(
    [seg],
    archive_data_dir=str(tmp_path),
    log_fn=lambda msg, **_k: logs.append(msg),
  )
  assert removed == 1
  assert fcm.has_file_complete_ingest_mark(
    other, archive_data_dir=str(tmp_path)
  )
  assert not fcm.has_file_complete_ingest_mark(
    seg, archive_data_dir=str(tmp_path)
  )
  assert any("cleared n=1" in line for line in logs)


def test_clear_drops_non_dict_meta_via_fingerprint_prefix(tmp_path):
  seg = _seg(tmp_path)
  key = fcm.path_fingerprint_key(seg)
  mark = fcm.file_complete_ingest_mark_path(str(tmp_path))
  fcm._save_entries(mark, {key: "not-a-dict"})
  removed = fcm.clear_file_complete_ingest_marks(
    [seg],
    archive_data_dir=str(tmp_path),
  )
  assert removed == 1


def test_maybe_record_rejects_ingest_not_ok(tmp_path):
  seg = _seg(tmp_path)
  assert (
    fcm.maybe_record_file_complete_ingest_mark_from_outcome(
      seg,
      ingest_ok=False,
      outcome="ingested",
      archive_data_dir=str(tmp_path),
    )
    is False
  )


def test_maybe_record_ingested_records(tmp_path):
  seg = _seg(tmp_path)
  assert (
    fcm.maybe_record_file_complete_ingest_mark_from_outcome(
      seg,
      ingest_ok=True,
      outcome="ingested",
      archive_data_dir=str(tmp_path),
    )
    is True
  )
  assert fcm.has_file_complete_ingest_mark(
    seg, archive_data_dir=str(tmp_path)
  )


def test_maybe_record_db_skip_full_scan_and_alias(tmp_path):
  a = _seg(tmp_path, name="host/a")
  b = _seg(tmp_path, name="host/b")
  assert (
    fcm.maybe_record_file_complete_ingest_mark_from_outcome(
      a,
      ingest_ok=True,
      outcome="db_skip",
      db_skip="full_scan",
      archive_data_dir=str(tmp_path),
    )
    is True
  )
  assert (
    fcm.maybe_record_file_complete_ingest_mark_from_outcome(
      b,
      ingest_ok=True,
      outcome="db_skip",
      db_skip="db_complete_full_scan",
      archive_data_dir=str(tmp_path),
    )
    is True
  )


def test_maybe_record_rejects_head_tail_and_empty_outcome(tmp_path):
  seg = _seg(tmp_path)
  assert (
    fcm.maybe_record_file_complete_ingest_mark_from_outcome(
      seg,
      ingest_ok=True,
      outcome="db_skip",
      db_skip="head_tail",
      archive_data_dir=str(tmp_path),
    )
    is False
  )
  assert (
    fcm.maybe_record_file_complete_ingest_mark_from_outcome(
      seg,
      ingest_ok=True,
      outcome="",
      archive_data_dir=str(tmp_path),
    )
    is False
  )
  assert (
    fcm.maybe_record_file_complete_ingest_mark_from_outcome(
      seg,
      ingest_ok=True,
      outcome=None,
      archive_data_dir=str(tmp_path),
    )
    is False
  )


def test_default_archive_dir_uses_conf(monkeypatch):
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.conf_parser.get_archive_dir_path",
    lambda: "/archive/from/ini",
  )
  assert fcm._default_archive_dir() == "/archive/from/ini"
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.conf_parser.get_archive_dir_path",
    lambda: None,
  )
  assert fcm._default_archive_dir() == ""
