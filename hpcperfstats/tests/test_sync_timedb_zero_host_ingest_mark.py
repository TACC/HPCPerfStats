"""Unit tests for durable zero-host ingest marks (no live DB)."""

from __future__ import annotations

import json
import os

from hpcperfstats.dbload.lib import sync_timedb_zero_host_ingest_mark as zhm
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
  ZERO_HOST_INGEST_MARK_SCHEMA_VERSION,
)


def _seg(tmp_path, name="host/1710000100", text="proc-only"):
  path = tmp_path / name
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text)
  return str(path)


def test_path_fingerprint_key_missing_and_empty(tmp_path):
  assert zhm.path_fingerprint_key(str(tmp_path / "missing")) is None
  assert zhm.path_fingerprint_key("") is None


def test_has_mark_false_without_file_or_archive(tmp_path):
  seg = _seg(tmp_path)
  assert (
    zhm.has_zero_host_ingest_mark(seg, archive_data_dir=str(tmp_path))
    is False
  )
  assert zhm.has_zero_host_ingest_mark(seg, archive_data_dir="") is False
  assert (
    zhm.has_zero_host_ingest_mark(
      str(tmp_path / "gone"),
      archive_data_dir=str(tmp_path),
    )
    is False
  )


def test_record_roundtrip_and_log(tmp_path):
  seg = _seg(tmp_path)
  logs = []
  assert (
    zhm.record_zero_host_ingest_mark(
      seg,
      archive_data_dir=str(tmp_path),
      log_fn=lambda msg, **_k: logs.append(msg),
    )
    is True
  )
  assert zhm.has_zero_host_ingest_mark(seg, archive_data_dir=str(tmp_path))
  assert any("zero_host_ingest_mark recorded" in line for line in logs)


def test_record_missing_path_and_empty_archive(tmp_path):
  seg = _seg(tmp_path)
  assert (
    zhm.record_zero_host_ingest_mark(
      str(tmp_path / "gone"),
      archive_data_dir=str(tmp_path),
    )
    is False
  )
  assert zhm.record_zero_host_ingest_mark(seg, archive_data_dir="") is False


def test_record_stat_race_returns_false(tmp_path, monkeypatch):
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
    zhm.record_zero_host_ingest_mark(
      seg,
      archive_data_dir=str(tmp_path),
    )
    is False
  )


def test_load_entries_malformed_list_and_non_dict_entries(tmp_path):
  mark = zhm.zero_host_ingest_mark_path(str(tmp_path))
  with open(mark, "w", encoding="utf-8") as handle:
    json.dump(["list-payload"], handle)
  assert zhm._load_entries(mark) == {}
  with open(mark, "w", encoding="utf-8") as handle:
    json.dump(
      {
        "schema_version": ZERO_HOST_INGEST_MARK_SCHEMA_VERSION,
        "entries": ["x"],
      },
      handle,
    )
  assert zhm._load_entries(mark) == {}


def test_clear_empty_inputs_return_zero(tmp_path):
  assert (
    zhm.clear_zero_host_ingest_marks([], archive_data_dir=str(tmp_path))
    == 0
  )
  assert (
    zhm.clear_zero_host_ingest_marks(None, archive_data_dir=str(tmp_path))
    == 0
  )
  assert (
    zhm.clear_zero_host_ingest_marks([""], archive_data_dir=str(tmp_path))
    == 0
  )
  assert zhm.clear_zero_host_ingest_marks(["/x"], archive_data_dir="") == 0
  assert (
    zhm.clear_zero_host_ingest_marks(
      ["/x"],
      archive_data_dir=str(tmp_path),
    )
    == 0
  )


def test_clear_removes_matching_path(tmp_path):
  a = _seg(tmp_path, name="host/a")
  b = _seg(tmp_path, name="host/b")
  zhm.record_zero_host_ingest_mark(a, archive_data_dir=str(tmp_path))
  zhm.record_zero_host_ingest_mark(b, archive_data_dir=str(tmp_path))
  logs = []
  n = zhm.clear_zero_host_ingest_marks(
    [a],
    archive_data_dir=str(tmp_path),
    log_fn=lambda msg, **_k: logs.append(msg),
  )
  assert n == 1
  assert not zhm.has_zero_host_ingest_mark(a, archive_data_dir=str(tmp_path))
  assert zhm.has_zero_host_ingest_mark(b, archive_data_dir=str(tmp_path))
  assert any("cleared n=1" in line for line in logs)


def test_maybe_record_rejects_failed_ingest_and_nonzero_rows(tmp_path):
  seg = _seg(tmp_path)
  assert (
    zhm.maybe_record_zero_host_ingest_mark_from_outcome(
      seg,
      ingest_ok=False,
      outcome="ingested",
      stats_rows=0,
      archive_data_dir=str(tmp_path),
    )
    is False
  )
  assert (
    zhm.maybe_record_zero_host_ingest_mark_from_outcome(
      seg,
      ingest_ok=True,
      outcome="ingested",
      stats_rows=1,
      archive_data_dir=str(tmp_path),
    )
    is False
  )
  assert (
    zhm.maybe_record_zero_host_ingest_mark_from_outcome(
      seg,
      ingest_ok=True,
      outcome="ingested",
      stats_rows=None,
      archive_data_dir=str(tmp_path),
    )
    is False
  )
  assert (
    zhm.maybe_record_zero_host_ingest_mark_from_outcome(
      seg,
      ingest_ok=True,
      outcome="db_skip",
      stats_rows=0,
      archive_data_dir=str(tmp_path),
    )
    is False
  )


def test_maybe_record_empty_outcome_and_parsed_zero_wins(tmp_path):
  """RC-0: parsed count gates; empty outcome is treated as ingested."""
  a = _seg(tmp_path, name="host/a")
  b = _seg(tmp_path, name="host/b")
  assert (
    zhm.maybe_record_zero_host_ingest_mark_from_outcome(
      a,
      ingest_ok=True,
      outcome="",
      stats_rows=0,
      archive_data_dir=str(tmp_path),
    )
    is True
  )
  assert (
    zhm.maybe_record_zero_host_ingest_mark_from_outcome(
      b,
      ingest_ok=True,
      outcome="ingested",
      stats_rows=99,
      stats_rows_parsed=0,
      archive_data_dir=str(tmp_path),
    )
    is True
  )
  assert zhm.has_zero_host_ingest_mark(a, archive_data_dir=str(tmp_path))
  assert zhm.has_zero_host_ingest_mark(b, archive_data_dir=str(tmp_path))


def test_maybe_record_parsed_nonzero_blocks_even_if_stats_rows_zero(tmp_path):
  seg = _seg(tmp_path)
  assert (
    zhm.maybe_record_zero_host_ingest_mark_from_outcome(
      seg,
      ingest_ok=True,
      outcome="ingested",
      stats_rows=0,
      stats_rows_parsed=12,
      archive_data_dir=str(tmp_path),
    )
    is False
  )


def test_default_archive_dir_empty_when_conf_none(monkeypatch):
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.conf_parser.get_archive_dir_path",
    lambda: None,
  )
  assert zhm._default_archive_dir() == ""
