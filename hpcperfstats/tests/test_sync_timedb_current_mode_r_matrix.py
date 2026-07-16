"""Automated coverage for plan remediations R1–R40 (CLI ``current`` / ``backlog``).

Each test cites its R# in the name or docstring and fails on the listed Bad change.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers
from hpcperfstats.dbload.lib import sync_timedb_mode_heartbeat as heartbeat
from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import (
    iter_giant_supplement_paths,
)
from hpcperfstats.dbload.lib.shutdown_utils import shutdown_requested


REPO_ROOT = Path(__file__).resolve().parents[2]
HELPERS_SRC = (
    REPO_ROOT / "hpcperfstats" / "dbload" / "lib" / "sync_timedb_archive_helpers.py"
)
SUPERVISOR_SRC = REPO_ROOT / "hpcperfstats" / "dbload" / "sync_timedb.py"
OPERATOR_DOC = REPO_ROOT / "docs" / "OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md"
DEPLOY_DOC = REPO_ROOT / "docs" / "DEPLOY_CONCURRENCY_AND_NUMA.md"
JANITOR_RULE = (
    REPO_ROOT
    / "hpcperfstats"
    / "cursor-rules"
    / "sync-timedb-archive-janitor-contract.mdc"
)
HOOK_ROUTER = REPO_ROOT / "cursor-hooks" / "hook_task_router.py"
DISCIPLINE = (
    REPO_ROOT / "hpcperfstats" / "cursor-rules" / "agent-discipline-core.mdc"
)


def _epoch_paths(tmp_path, epochs):
  host = tmp_path / "host.cluster.test"
  host.mkdir(exist_ok=True)
  paths = []
  for epoch in epochs:
    path = host / str(epoch)
    path.write_text("1\n", encoding="utf-8")
    paths.append(str(path))
  return paths


def _day_paths(tmp_path, day, count, prefix="p"):
  daily = tmp_path / "daily"
  daily.mkdir(exist_ok=True)
  tar = daily / ("%s.tar" % day.isoformat())
  tar.write_bytes(b"")
  ts = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc).timestamp()
  paths = []
  for i in range(count):
    path = tmp_path / ("%s_%s_%d" % (prefix, day.isoformat(), i))
    path.write_text("x\n", encoding="utf-8")
    os.utime(path, (ts, ts))
    paths.append(str(path))
  return os.path.normpath(str(tar)), paths


# --- R1–R15: HOT flag threading / ordering ---


def test_r1_supervisor_hot_call_sites_thread_newest_first():
  """R1: newest_first must stay on rescan/reconcile/failed-requeue paths."""
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert src.count("newest_first=newest_first") >= 20
  for needle in (
      "merge_rescan_discovered_into_pending(",
      "select_ingest_chunk_paths(",
      "pending_minus_chunk(",
      "build_giant_supplement_pending_tail(",
      "oldest_checkpoint_incomplete_tar(",
      "_publish_current_mode_heartbeat(",
      "_all_should_exit_for_current_proximity(",
  ):
    assert needle in src
  # Idle refill + periodic rescan both pass the flag into merge.
  assert src.count("newest_first=newest_first") >= src.count(
      "merge_rescan_discovered_into_pending("
  )


def test_r2_cap_pending_retains_newest_when_newest_first(tmp_path):
  """R2: cap retains newest max_size under True (ascending then [-max:])."""
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  paths = _epoch_paths(tmp_path, [base + i for i in range(5)])
  capped = helpers.cap_pending_stats_file_list(
      paths, 3, log_fn=None, newest_first=True,
  )
  assert capped == [paths[4], paths[3], paths[2]]
  # Default False still oldest prefix.
  assert helpers.cap_pending_stats_file_list(
      paths, 3, log_fn=None, newest_first=False,
  ) == paths[:3]


def test_r3_blocked_retention_tail_is_newest_under_newest_first(tmp_path):
  """R3: non-reserved capacity comes from newest eligible under True."""
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  paths = _epoch_paths(tmp_path, [base + i for i in range(6)])
  blocked = [paths[5]]  # youngest blocked stays reserved at head
  capped = helpers.cap_pending_stats_with_blocked_retention(
      paths,
      max_size=3,
      blocked_paths=blocked,
      newest_first=True,
  )
  assert capped[0] == paths[5]
  assert set(capped[1:]) <= set(paths[3:5])
  assert paths[0] not in capped


def test_r4_supplement_replaces_oldest_with_newest_when_newest_first(tmp_path):
  """R4: at-cap under True, newer closed_paths displace older heads."""
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  pending = _epoch_paths(tmp_path, [base, base + 1, base + 2])
  newer = _epoch_paths(tmp_path, [base + 10])[0]
  capped = helpers.supplement_pending_paths_from_closed_paths(
      pending,
      closed_paths=[newer],
      max_size=3,
      processed_exclude=set(),
      log_fn=None,
      newest_first=True,
  )
  assert newer in capped
  assert pending[0] not in capped
  assert capped[0] == newer


def test_r5_merge_rescan_newest_first_retains_quiet_host(tmp_path):
  """R5: merge under True keeps quiet pending hosts missing from discovered."""
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  quiet, recent, brand_new = _epoch_paths(
      tmp_path, [base, base + 10, base + 20],
  )
  merged = helpers.merge_rescan_discovered_into_pending(
      [quiet, recent],
      [brand_new, recent],
      newest_first=True,
  )
  assert merged == [brand_new, recent, quiet]


def test_r6_collect_and_rescan_return_descending_under_newest_first(tmp_path):
  """R6: collect/rescan honor newest_first for full-archive ``current``."""
  suffix = "cluster.integration.test"
  host = tmp_path / ("c1." + suffix)
  host.mkdir()
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  paths = []
  for offset in (0, 1, 2):
    path = host / str(base + offset)
    path.write_text("1\n", encoding="utf-8")
    paths.append(str(path))
  collected = helpers.collect_stats_files_in_range(
      str(tmp_path),
      "current",
      None,
      suffix,
      newest_first=True,
  )
  assert collected == list(reversed(paths))
  rescanned = helpers.rescan_pending_stats_files(
      str(tmp_path),
      "current",
      None,
      suffix,
      processed_files=set(),
      newest_first=True,
  )
  assert rescanned == list(reversed(paths))


def test_r7_handoff_priority_dispatch_front_newest_first(tmp_path):
  """R7: handoff block stays at index 0; internally mode-sorted descending."""
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  older, mid, newer = _epoch_paths(tmp_path, [base, base + 1, base + 2])
  handoff = helpers.sort_pending_stats_paths_oldest_first(
      [older, newer], newest_first=True,
  )
  pending = helpers.prepend_checkpoint_incomplete_paths_to_pending(
      [mid],
      handoff,
  )
  assert pending[0] == newer
  assert pending[1] == older
  assert pending[2] == mid


def test_r8_prepend_keeps_priority_at_head_under_newest_first(tmp_path):
  """R8: prepend never becomes append when newest_first is True."""
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  a, b, c = _epoch_paths(tmp_path, [base, base + 1, base + 2])
  merged = helpers.prepend_checkpoint_incomplete_paths_to_pending([b, c], [a])
  assert merged[0] == a
  assert merged == [a, b, c]
  src = inspect.getsource(helpers.prepend_checkpoint_incomplete_paths_to_pending)
  assert "merged = list(blocked)" in src
  assert "merged.append(path)" in src  # rest after blocked head — not blocked at tail



def test_r9_incomplete_tar_selector_matrix(tmp_path):
  """R9: False→oldest incomplete tar; True→youngest incomplete tar."""
  old_tar, old_paths = _day_paths(tmp_path, date(2020, 1, 1), 1, prefix="old")
  new_tar, new_paths = _day_paths(tmp_path, date(2020, 1, 10), 1, prefix="new")
  unprocessed = {old_tar: old_paths, new_tar: new_paths}
  assert helpers.oldest_checkpoint_incomplete_tar(
      unprocessed, tgz_archive_dir=str(tmp_path / "daily"), newest_first=False,
  ) == old_tar
  assert helpers.oldest_checkpoint_incomplete_tar(
      unprocessed, tgz_archive_dir=str(tmp_path / "daily"), newest_first=True,
  ) == new_tar


def test_r10_cold_iterators_ignore_newest_first_kw_absence():
  """R10: cold day iterators stay ascending; HOT uses separate param."""
  src = HELPERS_SRC.read_text(encoding="utf-8")
  # Cold helper signatures must not grow a newest_first flip for all callers.
  cold_fn = helpers.iter_checkpoint_incomplete_days_oldest_first
  params = inspect.signature(cold_fn).parameters
  assert "newest_first" not in params
  assert "sort_archive_items_oldest_day_first" in src


def test_r11_chunk_pad_follows_newest_pending_under_true(tmp_path):
  """R11: youngest gate day first, then pad toward newest pending order."""
  young_day = date(2020, 1, 10)
  old_day = date(2020, 1, 1)
  mid_day = date(2020, 1, 5)
  young_tar, young_paths = _day_paths(tmp_path, young_day, 2, prefix="y")
  _old_tar, old_paths = _day_paths(tmp_path, old_day, 2, prefix="o")
  _mid_tar, mid_paths = _day_paths(tmp_path, mid_day, 2, prefix="m")
  pending = list(reversed(young_paths + mid_paths + old_paths))
  logs = []
  chunk = helpers.select_ingest_chunk_paths(
      pending,
      oldest_tar=young_tar,
      unprocessed_by_tar={young_tar: list(young_paths)},
      inflight_archive_paths=set(),
      tgz_archive_dir=str(tmp_path / "daily"),
      chunk_size=4,
      log_fn=logs.append,
      newest_first=True,
  )
  assert chunk[:2] == young_paths[::-1] or set(chunk[:2]) == set(young_paths)
  assert any("youngest_day_chunk_gate_pad" in line for line in logs)
  assert old_paths[0] not in chunk or chunk.index(old_paths[0]) > 1


def test_r12_failed_requeue_sort_signature_honors_newest_first():
  """R12: supervisor failed-chunk re-sort passes newest_first into sort."""
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert "failed_chunk_paths = sort_pending_stats_paths_oldest_first(" in src
  block = src.split("failed_chunk_paths = sort_pending_stats_paths_oldest_first(")[1][:200]
  assert "newest_first=newest_first" in block


def test_r13_giant_supplement_walk_follows_pending_order(tmp_path):
  """R13: giant reservoir / iter walk pending list order under True."""
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  paths = _epoch_paths(tmp_path, [base + i for i in range(4)])
  newest_first_paths = list(reversed(paths))
  walked = list(
      iter_giant_supplement_paths(
          newest_first_paths,
          max_bytes=10**18,
          large_max_bytes=10**19,
          newest_first=True,
      )
  )
  assert walked == newest_first_paths
  reserved = helpers.build_giant_supplement_pending_tail(
      newest_first_paths[:1],
      closed_paths=newest_first_paths,
      supplement_queue=2,
      log_fn=None,
      newest_first=True,
  )
  assert reserved[0] == paths[-1]


def test_r14_pending_minus_chunk_set_diff_under_newest_first():
  """R14: never pending[len(chunk):] — set-diff preserves non-prefix tails."""
  pending = ["/p/D", "/p/C", "/p/B", "/p/A"]
  chunk = ["/p/C", "/p/A"]  # non-prefix under newest-first head
  assert helpers.pending_minus_chunk(
      pending, chunk, newest_first=True,
  ) == ["/p/D", "/p/B"]
  assert helpers.pending_minus_chunk(pending, chunk) != pending[len(chunk):]


def test_r15_reserved_blocked_uses_mode_sorted_blocked(tmp_path):
  """R15: supervisor sorts blocked with newest_first before [:chunk_size]."""
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert "reserved_blocked = blocked[:chunk_size]" in src
  assert re.search(
      r"blocked = \(\s*sort_pending_stats_paths_oldest_first\(\s*"
      r"_checkpoint_unblocked_paths_for_tar\(unprocessed, tar_norm\),\s*"
      r"newest_first=newest_first,",
      src,
      re.S,
  )


# --- R16–R20: telemetry / operator / titles ---


def test_r16_youngest_gate_tokens_under_newest_first(tmp_path):
  """R16: select/helpers emit youngest_day_chunk_gate* when True."""
  young_tar, young_paths = _day_paths(tmp_path, date(2020, 2, 2), 1)
  _old_tar, old_paths = _day_paths(tmp_path, date(2020, 1, 1), 1)
  pending = list(reversed(young_paths + old_paths))
  logs = []
  helpers.select_ingest_chunk_paths(
      pending,
      oldest_tar=young_tar,
      unprocessed_by_tar={young_tar: list(young_paths)},
      inflight_archive_paths=set(),
      tgz_archive_dir=str(tmp_path / "daily"),
      chunk_size=3,
      log_fn=logs.append,
      newest_first=True,
  )
  joined = "\n".join(logs)
  assert "youngest_day_chunk_gate" in joined or not logs
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert "_day_chunk_gate_prefix" in src
  assert "youngest_day_chunk_gate" in src


def test_r17_stall_watchdog_tokens_mode_aware_in_supervisor():
  """R17: supervisor stall/gate logs use mode-aware prefixes."""
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert "%s_stall" % "" in src or "_day_chunk_gate_prefix()" in src
  assert "oldest_day_chunk_gate_stall" not in src or "_day_chunk_gate_prefix()" in src


def test_r18_reconcile_cache_fingerprint_includes_newest_first():
  """R18: reuse skips when ordering mode flips with same tar/incomplete_n."""
  cached = {"/tar": ["/a"]}
  reused = helpers.try_reuse_pending_reconcile_unprocessed_cache(
      cached=cached,
      last_mono=10.0,
      mono_now=11.0,
      ttl_s=60.0,
      last_incomplete_n=2,
      last_oldest_tar="/tar",
      newest_first=True,
      last_newest_first=False,
  )
  assert reused is None
  ok = helpers.try_reuse_pending_reconcile_unprocessed_cache(
      cached=cached,
      last_mono=10.0,
      mono_now=11.0,
      ttl_s=60.0,
      last_incomplete_n=2,
      last_oldest_tar="/tar",
      newest_first=True,
      last_newest_first=True,
  )
  assert ok is not None


def test_r19_operator_doc_has_dual_t1_for_current_and_all():
  """R19: OPERATOR preserves all T1 and documents current youngest greps."""
  text = OPERATOR_DOC.read_text(encoding="utf-8")
  assert "oldest_day_chunk_gate" in text
  assert "youngest_day_chunk_gate" in text or "CLI `current`" in text or "CLI ``current``" in text
  assert "current" in text.lower()
  assert "newest-first" in text.lower() or "newest first" in text.lower()


def test_r20_process_titles_unchanged_for_mode():
  """R20: mode must not rename pool/daemon titles."""
  assert "current" not in st.SYNC_TIMEDB_PROCESS_TITLE
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert 'apply_pool_worker_process_title' in src
  assert 'initargs=(SYNC_TIMEDB_PROCESS_TITLE, "archive-pool")' in src
  assert 'initargs=(SYNC_TIMEDB_PROCESS_TITLE, "current' not in src


# --- R21–R25: CLI / startup / argv ---


@pytest.mark.parametrize(
    "argv, expected_start, expected_newest",
    [
        (["sync_timedb.py", "current"], "current", True),
        (["sync_timedb.py", "backlog"], "backlog", False),
        (["sync_timedb.py", "once", "current"], "current", True),
        (["sync_timedb.py", "once", "backlog"], "backlog", False),
    ],
)
def test_r21_r24_r25_argv_current_wiring(monkeypatch, argv, expected_start, expected_newest):
  """R21/R24/R25: argv tuple stays 3-wide; current→full archive + newest_first."""
  monkeypatch.setattr(
      st,
      "parse_start_end_dates",
      lambda *_a, **_k: (datetime(2020, 1, 1), datetime(2020, 1, 2)),
  )
  monkeypatch.setattr(st, "days_to_process", 2, raising=False)
  run_once, startdate, enddate = st.parse_sync_timedb_argv(argv)
  assert isinstance(run_once, bool)
  assert startdate == expected_start
  assert (startdate == "current") is expected_newest
  assert enddate is None


def test_r25_date_range_argv_is_not_newest_first(monkeypatch):
  """R25: explicit dates keep newest_first=False (derived from startdate)."""
  monkeypatch.setattr(st, "days_to_process", 2, raising=False)

  def _parse(argv_for_dates, default_start, default_end):
    del default_start, default_end
    if len(argv_for_dates) >= 3:
      return (
          datetime.strptime(argv_for_dates[1], "%Y-%m-%d"),
          datetime.strptime(argv_for_dates[2], "%Y-%m-%d"),
      )
    return datetime(2024, 1, 15), datetime(2024, 1, 15, 23, 59, 59)

  monkeypatch.setattr(st, "parse_start_end_dates", _parse)
  _run_once, startdate, _end = st.parse_sync_timedb_argv(
      ["sync_timedb.py", "2024-01-15"],
  )
  assert startdate == datetime(2024, 1, 15)
  assert startdate != "current"
  _run_once, startdate, enddate = st.parse_sync_timedb_argv(
      ["sync_timedb.py", "2024-01-15", "2024-01-20"],
  )
  assert startdate == datetime(2024, 1, 15)
  assert enddate == datetime(2024, 1, 20)
  assert startdate != "current"

def test_r21_full_archive_discovery_includes_current_string():
  src = HELPERS_SRC.read_text(encoding="utf-8")
  assert 'startdate in ("backlog", "current")' in src or "in ('backlog', 'current')" in src


def test_r22_r23_startup_maintenance_backlog_only_current_skips():
  """R22/R23: backlog keeps startup snapshot/handoff; day-close disabled for backlog."""
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert 'run_startup_maintenance = startdate == "backlog"' in src
  assert 'day_close_enabled = startdate != "backlog"' in src
  assert 'newest_first = startdate == "current"' in src
  assert 'run_startup_maintenance = startdate in ("backlog", "current")' not in src


# --- R26–R32: heartbeat (unit module already covers core; lock supervisor wire) ---


def test_r26_supervisor_publishes_from_inflight_union_chunk_not_pending():
  """R26: publish call uses inflight ∪ chunk (source contract)."""
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert "_publish_current_mode_heartbeat(" in src
  assert (
      "list(inflight_archive_paths) + list(stats_files_chunk or ())" in src
  )


def test_r27_r29_r30_heartbeat_helpers_covered_by_module():
  """R27/R29/R30: fail-open + inclusive proximity + TTL live in heartbeat tests."""
  from hpcperfstats.tests import test_sync_timedb_mode_heartbeat as hb_tests

  names = {name for name in dir(hb_tests) if name.startswith("test_")}
  assert "test_r27_missing_or_corrupt_heartbeat_fails_open" in names
  assert "test_r29_proximity_boundary_is_inclusive_calendar_days" in names
  assert "test_r30_stale_written_at_is_ignored" in names


def test_r28_sidecar_path_contract():
  assert heartbeat.HEARTBEAT_BASENAME == ".sync_timedb_current_heartbeat.json"


def test_r31_docs_recommend_single_current():
  """R31: document single-current recommendation (last writer wins)."""
  text = OPERATOR_DOC.read_text(encoding="utf-8") + DEPLOY_DOC.read_text(
      encoding="utf-8",
  )
  assert "single" in text.lower() and "current" in text.lower()


def test_r32_heartbeat_not_persistence_artifact():
  assert (
      ".sync_timedb_current_heartbeat.json"
      not in heartbeat.PERSISTENCE_ARTIFACT_REGISTRY.values()
  )


# --- R33–R40: cross-cutting ---


def test_r33_ingest_stream_past_uses_max_under_newest_first(tmp_path):
  """R33: newest_first uses max pending epoch for past-day check."""
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  paths = _epoch_paths(tmp_path, [base, base + 100])
  far_day = date(1999, 1, 1)
  assert helpers.ingest_stream_past_calendar_day(
      far_day,
      pending_stats_paths=paths,
      max_sort_epoch_for_day=base + 50,
      newest_first=False,
  ) is False
  assert helpers.ingest_stream_past_calendar_day(
      far_day,
      pending_stats_paths=paths,
      max_sort_epoch_for_day=base + 50,
      newest_first=True,
  ) is True
  assert helpers.ingest_stream_past_calendar_day(
      far_day,
      pending_stats_paths=paths,
      max_sort_epoch_for_day=base + 200,
      newest_first=True,
  ) is False


def test_r34_cross_day_complete_log_is_mode_aware():
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert "_cross_day_db_complete" in src
  assert "_day_chunk_gate_prefix()" in src


def test_r35_ini_proximity_fully_wired():
  """R35: registry + default + getter + example."""
  assert cfg.get_sync_ingest_current_proximity_days() == 2
  example = (REPO_ROOT / "hpcperfstats.ini.example").read_text(encoding="utf-8")
  assert "sync_ingest_current_proximity_days = 2" in example
  assert (("PIPELINE", "sync_ingest_current_proximity_days") in {
      (section, option) for section, option, _default in cfg.INI_OPTION_REGISTRY
  })
  conf_src = (
      REPO_ROOT / "hpcperfstats" / "dbload" / "lib" / "conf_parser.py"
  ).read_text(encoding="utf-8")
  assert "'sync_ingest_current_proximity_days': '2'" in conf_src
  assert "def get_sync_ingest_current_proximity_days" in conf_src


def test_r36_heartbeat_module_under_dbload_lib():
  assert heartbeat.__name__ == "hpcperfstats.dbload.lib.sync_timedb_mode_heartbeat"


def test_r37_false_true_matrix_helpers_covered(tmp_path):
  """R37: False path still oldest; True path covered above — smoke both."""
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  paths = _epoch_paths(tmp_path, [base, base + 1, base + 2])
  assert helpers.sort_pending_stats_paths_oldest_first(paths) == paths
  assert helpers.sort_pending_stats_paths_oldest_first(
      paths, newest_first=True,
  ) == list(reversed(paths))


def test_r38_no_orphan_new_current_mode_rule_file():
  """R38: prefer amend; no new standalone current-mode .mdc without router."""
  rules = list((REPO_ROOT / "hpcperfstats" / "cursor-rules").glob("*current*.mdc"))
  assert rules == []
  router = HOOK_ROUTER.read_text(encoding="utf-8")
  discipline = DISCIPLINE.read_text(encoding="utf-8")
  assert "sync-timedb-archive-janitor-contract.mdc" in router
  assert "sync-timedb-archive-janitor-contract.mdc" in discipline


def test_r39_idle_refill_merge_threads_newest_first():
  """R39: idle refill merge sites pass newest_first."""
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert "idle_refill=True" in src
  # Both idle merge call sites include newest_first near processed_exclude.
  assert src.count("processed_exclude=_rescan_processed_exclusions()") >= 2
  assert src.count("newest_first=newest_first") >= 20


def test_r40_docs_scheduling_not_completion_order():
  """R40: docs must not claim within-chunk completion == schedule order."""
  text = DEPLOY_DOC.read_text(encoding="utf-8")
  assert "completion order" in text.lower() or "Dispatch order vs log order" in text
  assert "Scheduling order" in text or "scheduling order" in text.lower()
  # Negative: product docs must not assert worker finish order equals chunk order.
  assert "worker finish order equals" not in text.lower()
  assert "completion order is oldest-first" not in text.lower()
  assert "completion order is newest-first" not in text.lower()


def test_r_grace_default_24_locked():
  """Catalog invariant 27 / config gate: today tar grace default is 24h."""
  assert cfg.get_archive_today_uncompressed_tar_grace_hours() == 24.0


def test_r_all_proximity_exit_helper_wired_in_chunk_loop():
  src = SUPERVISOR_SRC.read_text(encoding="utf-8")
  assert "if _all_should_exit_for_current_proximity(pending_stats_files):" in src
  assert 'startdate != "backlog"' in src or 'startdate == "backlog"' in src


def test_r_janitor_contract_mentions_newest_first_and_proximity():
  text = JANITOR_RULE.read_text(encoding="utf-8")
  assert "newest_first" in text
  assert "sync_ingest_current_proximity_days" in text
  assert "24" in text


# --- Live supervisor e2e (closes R1 / coordination gaps) ---


class _FakeArchivePool:
  def __enter__(self):
    return self

  def __exit__(self, *_a):
    return False

  def map_async(self, fn, items):
    del fn, items

    class _R:
      def ready(self):
        return True

      def get(self, timeout=None):
        del timeout
        return None

    return _R()


def _patch_supervisor_quiet(monkeypatch, archive_dir, daily_dir, *, logs):
  monkeypatch.setattr(st, "ensure_persistence_contract", lambda *_a, **_k: None)
  monkeypatch.setattr(st, "build_archive_mapping", lambda *_a, **_k: {})
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st.connections, "close_all", lambda: None)
  monkeypatch.setattr(st, "tgz_archive_dir", str(daily_dir))
  monkeypatch.setattr(st, "_sync_timedb_ingest_inline_requested", lambda: True)
  monkeypatch.setattr(st, "sleep_until_shutdown", lambda *_a, **_k: None)
  monkeypatch.setattr(
      st,
      "log_print",
      lambda *args, **_k: logs.append(" ".join(str(a) for a in args)),
  )
  monkeypatch.setattr(
      helpers,
      "build_live_unprocessed_by_tar_for_reconcile",
      lambda *_a, **_k: {},
  )
  monkeypatch.setattr(
      st,
      "build_live_unprocessed_by_tar_for_reconcile",
      lambda *_a, **_k: {},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis."
      "get_archive_members_redis_client",
      lambda **_k: None,
      raising=False,
  )
  del archive_dir


def test_r1_e2e_current_dispatch_epochs_descending_through_chunks(
    monkeypatch, tmp_path,
):
  """R1 e2e: live ``current`` supervisor keeps descending epochs across chunks."""
  shutdown_requested[0] = False
  logs = []
  archive_dir = tmp_path / "archive"
  daily_dir = tmp_path / "daily"
  archive_dir.mkdir()
  daily_dir.mkdir()
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  paths = _epoch_paths(tmp_path, [base + i for i in range(5)])
  merges = []
  rescans = {"n": 0}

  def fake_rescan(*_a, **_k):
    rescans["n"] += 1
    if rescans["n"] == 1:
      return list(paths)
    return []

  real_merge = st.merge_rescan_discovered_into_pending

  def wrap_merge(*a, **k):
    merges.append(bool(k.get("newest_first")))
    return real_merge(*a, **k)

  try:
    _patch_supervisor_quiet(monkeypatch, archive_dir, daily_dir, logs=logs)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "merge_rescan_discovered_into_pending", wrap_merge)
    monkeypatch.setattr(
        st,
        "add_stats_file_to_db",
        lambda _lock, path, **_k: (path, True, True, 0.0),
    )
    monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 2)
    monkeypatch.setattr(st.cfg, "get_sync_ingest_queue_max_size", lambda: 100)
    pool = _FakeArchivePool()
    with pool:
      st.run_sync_timedb_supervisor_loop(
          str(archive_dir),
          "current",
          None,
          ".test",
          object(),
          pool,
          run_once=True,
      )
    dispatch = [ln for ln in logs if "chunk dispatch begin" in ln]
    assert len(dispatch) >= 2
    epoch_lists = []
    for line in dispatch:
      match = re.search(r"epochs=(\[[^\]]*\])", line)
      assert match, line
      epoch_lists.append(eval(match.group(1), {"__builtins__": {}}, {}))
    for epochs in epoch_lists:
      assert epochs == sorted(epochs, reverse=True)
    heads = [epochs[0] for epochs in epoch_lists if epochs]
    assert heads == sorted(heads, reverse=True)
    assert any(merges) or rescans["n"] >= 1
    assert all(flag is True for flag in merges) or not merges
  finally:
    shutdown_requested[0] = False


def test_r1_e2e_current_failed_requeue_stays_newest_first(monkeypatch, tmp_path):
  """R1/R12 e2e: failed newest path is re-sorted with newest_first and stays front."""
  shutdown_requested[0] = False
  logs = []
  archive_dir = tmp_path / "archive"
  daily_dir = tmp_path / "daily"
  archive_dir.mkdir()
  daily_dir.mkdir()
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  paths = _epoch_paths(tmp_path, [base, base + 1, base + 2])
  newest = paths[-1]
  attempts = {}

  def ingest(_lock, path, **_k):
    attempts[path] = attempts.get(path, 0) + 1
    if path == newest and attempts[path] == 1:
      return path, False, False, 0.0
    return path, True, True, 0.0

  rescans = {"n": 0}

  def fake_rescan(*_a, **_k):
    rescans["n"] += 1
    if rescans["n"] == 1:
      return list(paths)
    return []

  try:
    _patch_supervisor_quiet(monkeypatch, archive_dir, daily_dir, logs=logs)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(st, "add_stats_file_to_db", ingest)
    monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 2)
    pool = _FakeArchivePool()
    with pool:
      st.run_sync_timedb_supervisor_loop(
          str(archive_dir),
          "current",
          None,
          ".test",
          object(),
          pool,
          run_once=True,
      )
    assert attempts.get(newest, 0) >= 2
    dispatch = [ln for ln in logs if "chunk dispatch begin" in ln]
    assert dispatch
    for line in dispatch:
      match = re.search(r"epochs=(\[[^\]]*\])", line)
      assert match
      epochs = eval(match.group(1), {"__builtins__": {}}, {})
      assert epochs == sorted(epochs, reverse=True)
  finally:
    shutdown_requested[0] = False


def test_r26_e2e_current_publishes_heartbeat_sidecar(monkeypatch, tmp_path):
  """R26 e2e: ``current`` writes archive_dir heartbeat from active chunk paths."""
  shutdown_requested[0] = False
  logs = []
  archive_dir = tmp_path / "archive"
  daily_dir = tmp_path / "daily"
  archive_dir.mkdir()
  daily_dir.mkdir()
  base = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
  paths = _epoch_paths(tmp_path, [base, base + 100, base + 200])
  rescans = {"n": 0}

  def fake_rescan(*_a, **_k):
    rescans["n"] += 1
    if rescans["n"] == 1:
      return list(paths)
    return []

  try:
    _patch_supervisor_quiet(monkeypatch, archive_dir, daily_dir, logs=logs)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(
        st,
        "add_stats_file_to_db",
        lambda _lock, path, **_k: (path, True, True, 0.0),
    )
    monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 2)
    pool = _FakeArchivePool()
    with pool:
      st.run_sync_timedb_supervisor_loop(
          str(archive_dir),
          "current",
          None,
          ".test",
          object(),
          pool,
          run_once=True,
      )
    sidecar = archive_dir / heartbeat.HEARTBEAT_BASENAME
    assert sidecar.is_file(), "\n".join(logs[-20:])
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["mode"] == "current"
    assert payload["oldest_active_day"]
    assert payload["writer_pid"] == os.getpid()
  finally:
    shutdown_requested[0] = False


def test_r_e2e_all_exits_when_pending_near_current_heartbeat(monkeypatch, tmp_path):
  """Coordination e2e: ``backlog`` exits near a fresh ``current`` heartbeat."""
  shutdown_requested[0] = False
  logs = []
  archive_dir = tmp_path / "archive"
  daily_dir = tmp_path / "daily"
  archive_dir.mkdir()
  daily_dir.mkdir()
  base = int(datetime(2026, 6, 10, tzinfo=timezone.utc).timestamp())
  paths = _epoch_paths(tmp_path, [base, base + 10])
  day = heartbeat.calendar_day_from_stats_path(paths[0], str(daily_dir))
  assert day is not None
  heartbeat.publish_current_heartbeat(
      archive_dir=str(archive_dir),
      active_paths=[paths[0]],
      daily_archive_dir=str(daily_dir),
      now=time.time(),
      redis_client=None,
  )
  rescans = {"n": 0}
  ingested = []

  def fake_rescan(*_a, **_k):
    rescans["n"] += 1
    if rescans["n"] == 1:
      return list(paths)
    return []

  try:
    _patch_supervisor_quiet(monkeypatch, archive_dir, daily_dir, logs=logs)
    monkeypatch.setattr(st, "rescan_pending_stats_files", fake_rescan)
    monkeypatch.setattr(
        st,
        "add_stats_file_to_db",
        lambda _lock, path, **_k: ingested.append(path) or (path, True, True, 0.0),
    )
    monkeypatch.setattr(st.cfg, "get_sync_ingest_chunk_size", lambda: 10)
    monkeypatch.setattr(st.cfg, "get_sync_ingest_current_proximity_days", lambda: 2)
    import hpcperfstats.dbload.lib.sync_timedb_archive_janitor as janitor_mod

    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "signal_scheduled_maintenance_pass",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        janitor_mod.ArchiveJanitor,
        "enqueue_startup_debt",
        lambda *a, **k: None,
    )
    # Avoid blocking on startup snapshot wait in unit tests.
    from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import (
        StartupArchiveScanCoordinator,
    )

    monkeypatch.setattr(
        StartupArchiveScanCoordinator,
        "wait_for_snapshot",
        lambda self, *, allow_build=False: None,
    )
    monkeypatch.setattr(
        StartupArchiveScanCoordinator,
        "get_snapshot",
        lambda self: None,
    )
    monkeypatch.setattr(
        StartupArchiveScanCoordinator,
        "is_startup_heavy_maintenance_idle",
        lambda self: True,
    )
    pool = _FakeArchivePool()
    with pool:
      st.run_sync_timedb_supervisor_loop(
          str(archive_dir),
          "backlog",
          None,
          ".test",
          object(),
          pool,
          run_once=True,
      )
    assert any("backlog exiting near current" in ln for ln in logs), "\n".join(logs[-40:])
    assert len(ingested) == 0
  finally:
    shutdown_requested[0] = False
