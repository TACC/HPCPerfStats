"""Isolated edge-case units for sync_timedb helpers (empty, malformed, bounds).

Locks current behavior, including bugs. No pipeline source changes. ORM/DB/APIs
are mocked or unused.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import sync_timedb_append_day_lists as day_lists
from hpcperfstats.dbload.lib import (
  sync_timedb_ingest_progress as ingest_progress,
)
from hpcperfstats.dbload.lib import sync_timedb_ingest_readiness as readiness
from hpcperfstats.dbload.lib import sync_timedb_ingest_sigalrm as ingest_sigalrm
from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ingest_timeout
from hpcperfstats.dbload.lib import sync_timedb_jid_scope as jid_scope
from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as reconstruct
from hpcperfstats.dbload.lib import sync_timedb_manifest_contract as manifest
from hpcperfstats.dbload.lib import sync_timedb_parsing as parsing
from hpcperfstats.dbload.lib import sync_timedb_parsing_legacy as legacy
from hpcperfstats.dbload.lib import sync_timedb_persistence as persist
from hpcperfstats.dbload.lib import sync_timedb_progress_io as progress_io

# --- parsing: empty / malformed / numerical bounds ---


def test_digit_line_identity_empty_and_malformed():
  assert parsing._digit_line_identity(None) is None
  assert parsing._digit_line_identity("") is None
  assert parsing._digit_line_identity("only-two tokens") is None
  assert parsing._digit_line_identity("1 job") is None
  parsed = parsing._digit_line_identity("1709123456 job1 cn001 extra extra")
  assert parsed == ("1709123456", "job1", "cn001")
  parsed_tab = parsing._digit_line_identity("1709123456\tjob1\tcn001")
  assert parsed_tab == ("1709123456", "job1", "cn001")


def test_digit_line_unix_second_malformed_and_boundary():
  assert parsing._digit_line_unix_second(None) is None
  assert parsing._digit_line_unix_second("1abc job host") is None
  assert (
    parsing._digit_line_unix_second("1709123456.9 job host") == 1709123456
  )
  assert parsing._digit_line_unix_second("0 job host") == 0
  assert parsing._digit_line_unix_second("1e9 job host") == 1_000_000_000


def test_parse_first_and_last_timestamp_empty_malformed():
  assert parsing.parse_first_timestamp_line([]) == (None, None, None)
  assert parsing.parse_first_timestamp_line(["", "  ", "1 job\n"]) == (
    None,
    None,
    None,
  )
  assert parsing.parse_last_timestamp_line([]) == (None, None, None)
  assert parsing.parse_last_timestamp_line(None) == (None, None, None)
  lines = ["1709123456 job1 cn001\n", "1709123460 job2 cn002 extra\n"]
  assert parsing.parse_last_timestamp_line(lines) == (
    "1709123460",
    "job2",
    "cn002",
  )


def test_parse_last_timestamp_streaming_empty_and_missing(tmp_path):
  missing = tmp_path / "gone"
  assert parsing.parse_last_timestamp_line_streaming(str(missing)) == (
    None,
    None,
    None,
  )
  empty = tmp_path / "empty"
  empty.write_bytes(b"")
  assert parsing.parse_last_timestamp_line_streaming(str(empty)) == (
    None,
    None,
    None,
  )
  only_schema = tmp_path / "schema"
  only_schema.write_text("!cpu user\ncpu 0 1\n")
  assert parsing.parse_last_timestamp_line_streaming(str(only_schema)) == (
    None,
    None,
    None,
  )


def test_find_processing_start_index_empty_and_malformed():
  start, need = parsing.find_processing_start_index([], set())
  assert start == -1
  assert need is True
  start, need = parsing.find_processing_start_index(
    ["", "1 job\n", "1709123456 job1 cn001\n"],
    set(),
  )
  assert start == 0
  assert need is True
  start, need = parsing.find_processing_start_index(
    ["1709123456 job1 cn001\n"],
    {1709123456},
  )
  assert start == -1


def test_parse_stats_lines_empty_payload():
  stats, procs = parsing.parse_stats_lines([], 0)
  assert stats == []
  assert procs == []
  stats, procs = parsing.parse_stats_lines(["", "  \n"], 0)
  assert stats == []
  assert procs == []


def test_parse_stats_lines_none_raises():
  with pytest.raises(TypeError):
    parsing.parse_stats_lines(None, 0)


def test_parse_stats_lines_bang_without_events_raises():
  with pytest.raises(ValueError):
    parsing.parse_stats_lines(["!cpu\n"], 0)
  with pytest.raises(ValueError):
    parsing.parse_stats_lines(["!\n"], 0)


def test_parse_stats_lines_malformed_time_raises():
  with pytest.raises(ValueError):
    parsing.parse_stats_lines(["1.2.3 job host\n"], 0)


def test_parse_stats_lines_skips_unknown_type_and_excluded():
  lines = [
    "1709123456 job1 cn001\n",
    "!cpu user sys\n",
    "unknown 0 1 2\n",
    "ib 0 1 2\n",
    "cpu 0 10 20\n",
  ]
  stats, procs = parsing.parse_stats_lines(
    lines,
    0,
    exclude_types_list=list(parsing.exclude_types),
  )
  assert procs == []
  assert [r["event"] for r in stats] == ["user", "sys"]
  assert stats[0]["value"] == 10.0


def test_parse_stats_lines_type_before_timestamp_skipped():
  lines = [
    "!cpu user sys\n",
    "cpu 0 10 20\n",
    "1709123456 job1 cn001\n",
    "cpu 0 30 40\n",
  ]
  stats, _procs = parsing.parse_stats_lines(lines, 0)
  assert len(stats) == 2
  assert stats[0]["value"] == 30.0


def test_parse_stats_lines_start_idx_skips_early_samples():
  lines = [
    "1709123456 job1 cn001\n",
    "!cpu user\n",
    "cpu 0 1\n",
    "1709123460 job1 cn001\n",
    "cpu 0 2\n",
  ]
  stats, _ = parsing.parse_stats_lines(lines, start_idx=3)
  assert len(stats) == 1
  assert stats[0]["value"] == 2.0
  assert stats[0]["time"] == 1709123460.0


def test_parse_stats_lines_boundary_numeric_values():
  lines = [
    "0 - cn001\n",
    "!cpu user sys other\n",
    "cpu 0 0 -1 9223372036854775807\n",
  ]
  stats, _ = parsing.parse_stats_lines(lines, 0)
  by_event = {r["event"]: r["value"] for r in stats}
  assert by_event["user"] == 0.0
  assert by_event["sys"] == -1.0
  assert by_event["other"] == 9223372036854775807.0
  assert all(r["jid"] == "-" for r in stats)


def test_parse_stats_lines_scientific_header_current_behavior():
  lines = [
    "1e9 job1 cn001\n",
    "!cpu user\n",
    "cpu 0 5\n",
  ]
  stats, _ = parsing.parse_stats_lines(lines, 0)
  assert stats[0]["time"] == 1_000_000_000.0


def test_parse_stats_lines_proc_malformed_values_become_none():
  lines = [
    "1709123456 job1 cn001\n",
    "proc foo garbage x 3 4 5 6 7 8 9 10 11 12\n",
  ]
  _stats, procs = parsing.parse_stats_lines(lines, 0)
  assert len(procs) == 1
  assert procs[0]["uid"] is None
  assert procs[0]["proc"] == "foo"


def test_incremental_parser_feed_none_and_empty():
  parser = parsing.IncrementalStatsParser()
  parser.feed_line(None)
  parser.feed_line("")
  parser.feed_line("   \n")
  stats, procs = parser.finish()
  assert stats == []
  assert procs == []


def test_build_stats_dataframes_empty_payloads():
  stats_df, proc_df = parsing.build_stats_dataframes([], [])
  assert stats_df.empty
  assert proc_df.empty


def test_load_stats_file_lines_empty_contents_list():
  lines, err = parsing.load_stats_file_lines("/any", stats_file_contents=[])
  assert err is None
  assert lines == []


# --- jid scope ---


def test_normalize_job_host_list_empty_malformed_nested():
  assert jid_scope.normalize_job_host_list(None) == []
  assert (
    jid_scope.normalize_job_host_list(
      datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    == []
  )
  assert jid_scope.normalize_job_host_list("") == []
  assert jid_scope.normalize_job_host_list(b"a, b  c") == ["a", "b", "c"]
  assert jid_scope.normalize_job_host_list(["", "  ", ["n1", {"x"}]]) == [
    "n1",
    "x",
  ]
  assert jid_scope.normalize_job_host_list(12345) == []


def test_as_host_data_fqdn_empty_and_suffix(monkeypatch):
  monkeypatch.setattr(jid_scope.cfg, "get_host_name_ext", lambda: "")
  assert jid_scope.as_host_data_fqdn(None) == ""
  assert jid_scope.as_host_data_fqdn("  ") == ""
  assert jid_scope.as_host_data_fqdn("c001") == "c001"
  monkeypatch.setattr(jid_scope.cfg, "get_host_name_ext", lambda: ".EX")
  assert jid_scope.as_host_data_fqdn("c001.ex") == "c001.ex"
  assert jid_scope.as_host_data_fqdn("c001") == "c001.EX"


def test_parse_jid_cli_malformed_and_empty():
  jid, err = jid_scope.parse_sync_timedb_jid_cli_arg(
    ["sync_timedb.py", "--jid"]
  )
  assert jid is None
  assert "usage" in err
  jid, err = jid_scope.parse_sync_timedb_jid_cli_arg(
    ["sync_timedb.py", "--jid="]
  )
  assert jid is None
  assert "empty" in err
  # Current behavior: a following token that starts with '-' is treated as
  # missing JID, not as the job id.
  jid, err = jid_scope.parse_sync_timedb_jid_cli_arg(
    ["sync_timedb.py", "--jid", "-1"]
  )
  assert jid is None
  assert "usage" in err
  jid, err = jid_scope.parse_sync_timedb_jid_cli_arg(
    ["sync_timedb.py", "--jid=-1"]
  )
  assert err is None
  assert jid == "-1"


def test_padded_job_window_none_start_and_end_before_start():
  with pytest.raises(jid_scope.JobIngestScopeError):
    jid_scope.padded_job_window(None, None)
  start = datetime(2026, 7, 1, 12, 0, 0)
  end = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
  w0, w1 = jid_scope.padded_job_window(start, end)
  assert w0.tzinfo is timezone.utc
  assert (w1 - w0) == timedelta(hours=2)


def test_resolve_job_ingest_scope_empty_jid(monkeypatch):
  with pytest.raises(jid_scope.JobIngestScopeError, match="empty jid"):
    jid_scope.resolve_job_ingest_scope("  ")


# --- manifest ---


def test_day_phase_name_empty_and_malformed():
  assert manifest.day_phase_name_from_hints(None, "/t.tar") is None
  assert manifest.day_phase_name_from_hints({}, "") is None
  assert (
    manifest.day_phase_name_from_hints({"/t.tar": "sealed"}, "/t.tar")
    == "sealed"
  )
  assert (
    manifest.day_phase_name_from_hints(
      {"/t.tar": {"phase": "raw_removed"}},
      "/t.tar",
    )
    == "raw_removed"
  )


def test_day_phase_at_least_invalid_and_boundary():
  phases = {"/t.tar": "sealed"}
  assert manifest.day_phase_at_least(phases, "/t.tar", "sealed") is True
  assert manifest.day_phase_at_least(phases, "/t.tar", "raw_removed") is False
  assert (
    manifest.day_phase_at_least(phases, "/missing.tar", "sealed") is False
  )
  assert manifest.day_phase_at_least(phases, "/t.tar", "not-a-phase") is False
  phases2 = {"/t.tar": "tar_dropped"}
  assert manifest.day_phase_at_least(phases2, "/t.tar", "sealed") is True


def test_validate_manifest_payload_empty_malformed():
  assert manifest.validate_manifest_payload("day_raw_removal", None) is False
  assert manifest.validate_manifest_payload("day_raw_removal", []) is False
  assert manifest.validate_manifest_payload("day_raw_removal", {}) is False
  assert manifest.validate_manifest_payload("unknown_kind", {"x": 1}) is True
  assert (
    manifest.validate_manifest_payload(
      "day_close_manifest",
      {"entries": {}},
    )
    is True
  )
  assert manifest.manifest_phase_is_valid("other", "anything") is True


# --- ingest timeout ---


def test_timeout_helpers_always_zero_on_empty_and_max():
  assert ingest_timeout.resolve_ingest_per_file_timeout_s("") == 0.0
  assert (
    ingest_timeout.resolve_ingest_per_file_timeout_for_size_bytes(0) == 0.0
  )
  assert (
    ingest_timeout.resolve_ingest_per_file_timeout_for_size_bytes(2**63)
    == 0.0
  )
  assert ingest_timeout.max_ingest_per_file_timeout_for_paths([]) == 0.0
  assert ingest_timeout.max_ingest_per_file_timeout_for_paths(None) == 0.0


def test_calendar_day_from_sealed_empty_malformed_and_valid():
  assert ingest_timeout.calendar_day_from_sealed_archive_path("") == ""
  assert ingest_timeout.calendar_day_from_sealed_archive_path(None) == ""
  assert (
    ingest_timeout.calendar_day_from_sealed_archive_path(
      "/a/2026-13-40.tar.zst",
    )
    == ""
  )
  assert (
    ingest_timeout.calendar_day_from_sealed_archive_path(
      "/a/2026-01-02.tar.zst",
    )
    == "2026-01-02"
  )


def test_sealed_member_count_hint_garbage_and_missing(tmp_path):
  missing = str(tmp_path / "nope.tar.zst")
  assert (
    ingest_timeout.sealed_archive_member_count_hint(
      missing,
      member_count="nope",
    )
    == 1
  )
  assert (
    ingest_timeout.sealed_archive_member_count_hint(
      missing,
      member_count=0,
    )
    == 1
  )
  assert (
    ingest_timeout.sealed_archive_member_count_hint(
      missing,
      member_count=7,
    )
    == 7
  )
  sealed = tmp_path / "2026-01-01.tar.zst"
  sealed.write_bytes(b"x" * (64 * 1024 * 1024))
  hint = ingest_timeout.sealed_archive_member_count_hint(
    str(sealed), member_count=None
  )
  assert hint >= 1


def test_estimate_sealed_budget_zero_floor(monkeypatch, tmp_path):
  monkeypatch.setattr(
    ingest_timeout.cfg,
    "get_sync_ingest_per_file_timeout_s",
    lambda: 0.0,
  )
  assert ingest_timeout.estimate_sealed_archive_ingest_budget_s("/x") == 0.0
  assert (
    ingest_timeout.max_sealed_archive_ingest_budget_for_paths(None) == 0.0
  )
  assert (
    ingest_timeout.max_sealed_archive_ingest_budget_for_paths(["", None])
    == 0.0
  )


def test_is_giant_ingest_budget_current_wall_deleted():
  assert ingest_timeout.is_giant_ingest_budget("/x", trigger_s=0.0) is False
  assert ingest_timeout.is_giant_ingest_budget("/x", trigger_s=-1) is False
  # resolved timeout is always 0, so default trigger 6600 never matches
  assert ingest_timeout.is_giant_ingest_budget("/x") is False


def test_default_giant_supplement_trigger_budget_s(monkeypatch):
  monkeypatch.setattr(
    ingest_timeout.cfg,
    "get_sync_ingest_per_file_timeout_s_per_mib",
    lambda: 2.0,
  )
  assert (
    ingest_timeout.default_giant_supplement_trigger_budget_s()
    == 900.0 + 2048.0 * 2.0
  )


# --- reconstruct ---


def test_reconstruct_laws_and_select_ingest_band_boundary():
  assert reconstruct.empty_job_queues_mean_caught_up() is False
  assert (
    reconstruct.checkpoint_sidecar_is_reconstruct_source_of_truth() is False
  )
  today = date(2026, 8, 24)
  assert (
    reconstruct.select_ingest_band(date(2026, 8, 24), today=today) == "hot"
  )
  assert (
    reconstruct.select_ingest_band(
      date(2026, 8, 17), today=today, hot_days=8
    )
    == "hot"
  )
  assert (
    reconstruct.select_ingest_band(
      date(2026, 8, 16), today=today, hot_days=8
    )
    == "catchup"
  )
  assert (
    reconstruct.select_ingest_band(date(2026, 8, 25), today=today) == "hot"
  )
  assert (
    reconstruct.select_ingest_band(today, today=today, hot_days=0) == "hot"
  )


def test_ingest_is_complete_empty_and_live_on_ignores_head_tail():
  assert reconstruct.ingest_is_complete("") is False
  assert reconstruct.ingest_is_complete("   ") is False
  assert (
    reconstruct.ingest_is_complete(
      "/x",
      listend_enabled=True,
      has_file_complete_fn=lambda _p: False,
      has_zero_host_fn=lambda _p: False,
      head_tail_ready_fn=lambda _p: True,
    )
    is False
  )
  assert (
    reconstruct.ingest_is_complete(
      "/x",
      listend_enabled=False,
      has_file_complete_fn=lambda _p: False,
      has_zero_host_fn=lambda _p: False,
      head_tail_ready_fn=lambda _p: True,
    )
    is True
  )


def test_closed_path_plan_kinds_empty_and_both():
  empty = reconstruct.ClosedPathReconstructPlan(
    path="/a",
    identity="/a|1|2",
    needs_ingest=False,
    needs_append=False,
    calendar_day=None,
    tar_path=None,
  )
  assert empty.kinds_to_enqueue() == ()
  both = reconstruct.ClosedPathReconstructPlan(
    path="/a",
    identity="/a|1|2",
    needs_ingest=True,
    needs_append=True,
    calendar_day=None,
    tar_path=None,
  )
  assert both.kinds_to_enqueue() == ("ingest", "append")


# --- persistence envelope ---


def test_persistence_empty_archive_dir_and_unknown_kind(tmp_path):
  assert persist.ensure_persistence_contract("") is False
  with pytest.raises(KeyError):
    persist.artifact_path(str(tmp_path), "not-a-kind")
  assert persist._read_json_file(str(tmp_path / "missing.json")) is None
  garbage = tmp_path / "bad.json"
  garbage.write_text("{not json")
  assert persist._read_json_file(str(garbage)) is None


def test_validate_envelope_empty_malformed_and_kinds():
  assert persist._validate_envelope(None, kind="ingest_checkpoint") is False
  assert persist._validate_envelope([], kind="ingest_checkpoint") is True
  assert persist._validate_envelope("x", kind="ingest_checkpoint") is False
  assert (
    persist._validate_envelope(
      {"schema_version": "nope", "entries": []},
      kind="ingest_checkpoint",
    )
    is False
  )
  assert (
    persist._validate_envelope(
      {"schema_version": 1, "entries": []},
      kind="zero_host_ingest_mark",
    )
    is False
  )
  assert (
    persist._validate_envelope(
      {"schema_version": 1, "entries": {}},
      kind="zero_host_ingest_mark",
    )
    is True
  )
  assert (
    persist._validate_envelope(
      {"schema_version": 1},
      kind="zero_host_ingest_mark",
    )
    is True
  )
  assert (
    persist._validate_envelope(
      {"schema_version": 1, "ingest": []},
      kind="job_store_snapshot",
    )
    is False
  )
  assert persist._validate_envelope({"x": 1}, kind="unknown") is True


def test_unwrap_envelope_malformed_returns_none():
  assert persist._unwrap_envelope({}, kind="ingest_checkpoint") is None
  assert persist._unwrap_envelope(["a"], kind="ingest_checkpoint") == ["a"]
  assert persist._unwrap_envelope("x", kind="archive_maint_hints") is None
  out = persist._unwrap_envelope({}, kind="job_store_snapshot")
  assert out == {"ingest": {}, "lists": {}, "pending": {}, "payloads": {}}


def test_load_missing_and_reject_returns_default(tmp_path):
  path = str(tmp_path / "state.json")
  assert persist.load_persistence_document(path, "ingest_checkpoint") == []
  with open(path, "w", encoding="utf-8") as handle:
    json.dump({"schema_version": 999, "entries": [{"path": "/x"}]}, handle)
  assert persist.load_persistence_document(path, "ingest_checkpoint") == []


def test_ensure_contract_allow_reset_false_missing_and_mismatch(tmp_path):
  archive = str(tmp_path / "archive")
  os.makedirs(archive)
  assert (
    persist.ensure_persistence_contract(archive, allow_reset=False) is False
  )
  with open(
    persist.persistence_contract_path(archive), "w", encoding="utf-8"
  ) as handle:
    json.dump({"contract_version": 1, "written_at": 0}, handle)
  with pytest.raises(persist.PersistenceContractMismatchError):
    persist.ensure_persistence_contract(archive, allow_reset=False)


def test_read_contract_version_malformed(tmp_path):
  archive = str(tmp_path / "archive")
  os.makedirs(archive)
  path = persist.persistence_contract_path(archive)
  with open(path, "w", encoding="utf-8") as handle:
    json.dump(["not-a-dict"], handle)
  assert persist._read_contract_version(archive) is None
  with open(path, "w", encoding="utf-8") as handle:
    json.dump({"contract_version": "nope"}, handle)
  assert persist._read_contract_version(archive) is None


def test_save_unknown_kind_writes_payload(tmp_path):
  path = str(tmp_path / "raw.json")
  persist.save_persistence_document(path, "not-registered", {"a": 1})
  with open(path, encoding="utf-8") as handle:
    assert json.load(handle) == {"a": 1}


# --- readiness ---


def test_path_fingerprint_missing_and_gate_identities_empty():
  assert readiness.path_ingest_ready_fingerprint("/no/such") is None
  assert readiness.head_tail_identity_as_gate_identities(None, None) == {}
  assert (
    readiness.head_tail_identity_as_gate_identities(
      {"/p": None},
      {"/p": ("h", 1)},
    )
    == {}
  )
  assert (
    readiness.head_tail_identity_as_gate_identities(
      {"/p": ("h", None)},
      {"/p": ("h", 1)},
    )
    == {}
  )
  assert readiness.gate_identities_ready_in_db(None) is False
  assert readiness.gate_identities_ready_in_db({}) is False


def test_filter_paths_empty_payload():
  ready, skipped = readiness.filter_paths_head_ingested([])
  assert ready == []
  assert skipped == []


def test_build_head_ingest_ready_set_live_on_ignores_head_tail(
  monkeypatch,
  tmp_path,
):
  monkeypatch.setattr(
    readiness.cfg, "get_sync_archive_require_db_ingest", lambda: True
  )
  monkeypatch.setattr(
    readiness.cfg, "get_listend_db_ingest_enabled", lambda: True
  )
  monkeypatch.setattr(
    readiness, "stats_file_is_active_segment", lambda _p: False
  )
  monkeypatch.setattr(
    readiness, "_path_ready_via_file_complete_mark", lambda _p: False
  )
  monkeypatch.setattr(
    readiness, "_path_ready_via_zero_host_mark", lambda _p: False
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.sync_timedb._sync_worker_db_task",
    lambda: _NullCtx(),
  )
  seg = tmp_path / "seg"
  seg.write_text("x")
  gate = {str(seg): {"cn001": {1}}}
  ready = readiness.build_head_ingest_ready_set([str(seg)], gate, log_fn=None)
  assert str(seg) not in ready
  monkeypatch.setattr(
    readiness, "_path_ready_via_file_complete_mark", lambda _p: True
  )
  ready = readiness.build_head_ingest_ready_set([str(seg)], gate, log_fn=None)
  assert str(seg) in ready


def test_stats_file_head_ingested_missing_path_false(monkeypatch, tmp_path):
  monkeypatch.setattr(
    readiness.cfg, "get_sync_archive_require_db_ingest", lambda: True
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.sync_timedb._sync_worker_db_task",
    lambda: _NullCtx(),
  )
  assert (
    readiness.stats_file_head_ingested_in_db(str(tmp_path / "gone"))
    is False
  )


class _NullCtx:
  def __enter__(self):
    return self

  def __exit__(self, *_a):
    return False


# --- append day lists peek_first ---


def test_append_day_lists_peek_first_empty_and_present():
  lists = day_lists.AppendDayClaimLists()
  assert lists.peek_first("missing") is None
  assert lists.peek_first("") is None
  lists.add("", "should-not-store")
  assert lists.day_keys() == ()
  lists.add("2026-08-01", "a")
  lists.add("2026-08-01", "b")
  assert lists.peek_first("2026-08-01") == "a"
  assert lists.peek_len("2026-08-01") == 2


# --- ingest progress / sigalrm ---


def test_ingest_progress_idle_zero_and_none_tokens():
  ingest_progress.end_ingest_progress(None)
  toks = ingest_progress.begin_ingest_progress("/x", idle_s=0.0)
  assert ingest_progress.get_ingest_idle_stall_s() is None
  ingest_progress.raise_if_ingest_idle_stalled("/x")
  ingest_progress.end_ingest_progress(toks)
  assert ingest_progress.get_ingest_idle_stall_s() is None


def test_ingest_progress_boundary_stall(monkeypatch):
  clock = {"t": 10.0}
  toks = ingest_progress.begin_ingest_progress(
    "/raw/a",
    idle_s=5.0,
    clock=lambda: clock["t"],
  )
  try:
    ingest_progress.raise_if_ingest_idle_stalled(
      "/raw/a",
      clock=lambda: 14.9,
    )
    with pytest.raises(st.IngestPerFileTimeoutError):
      ingest_progress.raise_if_ingest_idle_stalled(
        "/raw/a",
        clock=lambda: 15.0,
      )
  finally:
    ingest_progress.end_ingest_progress(toks)


def test_populate_wait_sigalrm_guard_respect_and_ignore():
  seen = {"n": 0}

  def _touch(*, clock=None):
    del clock
    seen["n"] += 1

  import hpcperfstats.dbload.lib.sync_timedb_ingest_progress as prog

  orig = prog.touch_ingest_progress
  prog.touch_ingest_progress = _touch
  try:
    with ingest_sigalrm.populate_wait_ingest_sigalrm_guard(
      respect_ingest_deadline=True,
    ):
      pass
    assert seen["n"] == 0
    with ingest_sigalrm.populate_wait_ingest_sigalrm_guard(
      respect_ingest_deadline=False,
    ):
      pass
    assert seen["n"] == 1
  finally:
    prog.touch_ingest_progress = orig


# --- progress_io ---


def test_kill_process_group_already_exited():
  proc = SimpleNamespace(
    pid=1, poll=lambda: 0, terminate=lambda: None, kill=lambda: None
  )
  progress_io._kill_process_group(proc)


def test_log_progress_sop_rate_limit(monkeypatch):
  logs = []
  monkeypatch.setattr(
    progress_io, "log_print", lambda msg, **_k: logs.append(msg)
  )
  clock = {"t": 100.0}
  progress_io.log_progress_sop(
    stage="tar_append",
    path="/x",
    advancing=True,
    idle_s=0.0,
    last_progress=None,
    metric="bytes",
    force=True,
    clock=lambda: clock["t"],
  )
  n1 = len(logs)
  progress_io.log_progress_sop(
    stage="tar_append",
    path="/x",
    advancing=False,
    idle_s=1.0,
    last_progress=1.0,
    metric="bytes",
    force=False,
    clock=lambda: clock["t"],
  )
  assert len(logs) == n1


# --- legacy decode ---


def test_decode_counter_line_unknown_and_ctl_other():
  assert legacy.decode_counter_line("not-a-type", {}, []) is None
  assert legacy.decode_counter_line("amd64_pmc", {}, [1]) is None
  schema = {"amd64_pmc": ["CTL0", "CTR0"]}
  vals = ["not-an-int", 5]
  out = legacy.decode_counter_line("amd64_pmc", schema, vals)
  assert out is not None
  assert "OTHER" in out
  assert legacy.legacy_output_type("intel_knl_mc") == "intel_knl_mc_dclk"
  assert legacy.legacy_output_type("cpu") == "cpu"
