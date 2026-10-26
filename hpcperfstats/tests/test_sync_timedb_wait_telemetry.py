"""Per-file wait/CPU attribution telemetry for sync_timedb ingest (default off)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from hpcperfstats.dbload.lib import sync_timedb_wait_telemetry as wt


@pytest.fixture(autouse=True)
def _telemetry_off_after():
  """Leave the process-wide switch off so other tests see the default."""
  yield
  wt.set_enabled(False)


def test_parse_schedstat_reads_cpu_and_runq_ns():
  assert wt.parse_schedstat("123 456 7\n") == (123, 456)


@pytest.mark.parametrize("text", ["", "abc", "1", "x y z", None])
def test_parse_schedstat_rejects_garbage(text):
  assert wt.parse_schedstat(text) is None


def test_decompose_wall_sums_to_wall():
  cpu, runq, sleep = wt.decompose_wall(10.0, 4.0, 3.0)
  assert (cpu, runq, sleep) == (4.0, 3.0, 3.0)


def test_decompose_wall_clamps_overshoot_without_negative_sleep():
  # schedstat and perf_counter are read at different instants, so cpu+runq
  # can exceed wall by a few microseconds; sleep must clamp to zero.
  cpu, runq, sleep = wt.decompose_wall(1.0, 0.8, 0.5)
  assert sleep == 0.0
  assert cpu + runq + sleep <= 1.0 + 1e-9
  assert min(cpu, runq, sleep) >= 0.0


def test_disabled_snapshot_is_empty_and_add_is_noop():
  wt.set_enabled(False)
  wt.begin_file()
  wt.add("wq_put_s", 1.0)
  assert wt.snapshot() == {}


def test_snapshot_keys_are_all_registered_log_keys():
  wt.set_enabled(True)
  wt.begin_file()
  snap = wt.snapshot()
  assert set(snap) == set(wt.WAIT_LOG_KEYS)


def test_snapshot_decomposes_thread_wall_from_schedstat(monkeypatch):
  wt.set_enabled(True)
  reads = iter([(1_000_000_000, 2_000_000_000), (4_000_000_000, 5_000_000_000)])
  monkeypatch.setattr(wt, "read_thread_schedstat", lambda: next(reads))
  clock = iter([100.0, 110.0])
  monkeypatch.setattr(wt.time, "perf_counter", lambda: next(clock))
  wt.begin_file()
  snap = wt.snapshot()
  assert snap["thr_cpu_s"] == pytest.approx(3.0)
  assert snap["thr_runq_s"] == pytest.approx(3.0)
  assert snap["thr_sleep_s"] == pytest.approx(4.0)


def test_snapshot_survives_missing_schedstat(monkeypatch):
  wt.set_enabled(True)
  monkeypatch.setattr(wt, "read_thread_schedstat", lambda: None)
  wt.begin_file()
  snap = wt.snapshot()
  assert snap["thr_cpu_s"] == 0.0
  assert snap["thr_sleep_s"] == 0.0


def test_add_accumulates_and_ignores_unknown_or_nonpositive():
  wt.set_enabled(True)
  wt.begin_file()
  wt.add("wq_put_s", 0.25)
  wt.add("wq_put_s", 0.25)
  wt.add("wq_put_s", -1.0)
  wt.add("not_a_key", 5.0)
  snap = wt.snapshot()
  assert snap["wq_put_s"] == pytest.approx(0.5)
  assert "not_a_key" not in snap


def test_stage_cpu_rolls_up_into_named_groups():
  wt.set_enabled(True)
  wt.begin_file()
  wt.add_stage_cpu("feed_s", 1.0)
  wt.add_stage_cpu("proc_merge_s", 0.5)
  wt.add_stage_cpu("hw_df_s", 0.25)
  wt.add_stage_cpu("delta_s", 2.0)
  wt.add_stage_cpu("collapse_sort_s", 3.0)
  wt.add_stage_cpu("collapse_host_sum_s", 1.0)
  wt.add_stage_cpu("arc_s", 4.0)
  wt.add_stage_cpu("lock_s", 0.5)
  snap = wt.snapshot()
  assert snap["feed_cpu_s"] == pytest.approx(1.0)
  assert snap["build_df_cpu_s"] == pytest.approx(0.75)
  assert snap["delta_cpu_s"] == pytest.approx(2.0)
  assert snap["collapse_cpu_s"] == pytest.approx(4.0)
  assert snap["arc_cpu_s"] == pytest.approx(4.0)
  assert snap["stages_cpu_s"] == pytest.approx(12.25)


def test_ledger_is_per_thread():
  wt.set_enabled(True)
  wt.begin_file()
  wt.add("wq_put_s", 1.0)
  seen = {}

  def other():
    wt.begin_file()
    wt.add("wq_put_s", 7.0)
    seen["other"] = wt.snapshot()["wq_put_s"]

  th = threading.Thread(target=other)
  th.start()
  th.join()
  assert seen["other"] == pytest.approx(7.0)
  assert wt.snapshot()["wq_put_s"] == pytest.approx(1.0)


def test_apply_payload_adds_queue_and_writer_seconds():
  wt.set_enabled(True)
  wt.begin_file()
  payload = SimpleNamespace(
    wait={
      "enq_mono": 10.0,
      "deq_mono": 12.5,
      "exec_s": 4.0,
      "cpu_s": 1.5,
      "runq_s": 0.5,
    }
  )
  wt.apply_payload(payload, join_wait_s=7.0)
  snap = wt.snapshot()
  assert snap["wq_resid_s"] == pytest.approx(2.5)
  assert snap["wq_join_s"] == pytest.approx(7.0)
  assert snap["wq_n"] == 1.0
  assert snap["w_exec_s"] == pytest.approx(4.0)
  assert snap["w_cpu_s"] == pytest.approx(1.5)
  assert snap["w_runq_s"] == pytest.approx(0.5)


def test_apply_payload_inline_write_has_no_residence():
  # Write stage not started: payload executes inline, nothing was enqueued.
  wt.set_enabled(True)
  wt.begin_file()
  wt.apply_payload(SimpleNamespace(wait={}), join_wait_s=0.0)
  snap = wt.snapshot()
  assert snap["wq_resid_s"] == 0.0
  assert snap["wq_n"] == 1.0


def test_stamp_enqueue_is_noop_when_disabled():
  wt.set_enabled(False)
  payload = SimpleNamespace(wait={})
  wt.stamp_enqueue(payload)
  assert payload.wait == {}


def test_writer_exec_scope_records_wall_cpu_runq(monkeypatch):
  wt.set_enabled(True)
  reads = iter([(0, 0), (2_000_000_000, 1_000_000_000)])
  monkeypatch.setattr(wt, "read_thread_schedstat", lambda: next(reads))
  clock = iter([53.0, 58.0])
  monkeypatch.setattr(wt.time, "monotonic", lambda: next(clock))
  payload = SimpleNamespace(wait={"enq_mono": 50.0})
  with wt.writer_exec_scope(payload):
    pass
  assert payload.wait["deq_mono"] == 53.0
  assert payload.wait["exec_s"] == pytest.approx(5.0)
  assert payload.wait["cpu_s"] == pytest.approx(2.0)
  assert payload.wait["runq_s"] == pytest.approx(1.0)


def test_writer_exec_scope_noop_when_disabled():
  wt.set_enabled(False)
  payload = SimpleNamespace(wait={})
  with wt.writer_exec_scope(payload):
    pass
  assert payload.wait == {}


def test_psi_token_parses_some_avg10():
  files = {
    "/proc/pressure/cpu": "some avg10=12.50 avg60=3.00 avg300=1.00 total=99\n",
    "/proc/pressure/memory": "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n",
    "/proc/pressure/io": "some avg10=4.25 avg60=1.00 avg300=0.50 total=7\n",
    "/proc/loadavg": "9.00 8.00 7.00 17/900 12345\n",
  }
  tok = wt.format_system_wait_census_token(read_text=files.get)
  assert "psi_cpu10=12.50" in tok
  assert "psi_mem10=0.00" in tok
  assert "psi_io10=4.25" in tok
  assert "sys_runnable=17" in tok


def test_psi_token_absent_files_yield_empty_string():
  assert wt.format_system_wait_census_token(read_text=lambda _p: None) == ""


def test_boehm_token_uses_available_counters():
  lib = SimpleNamespace(
    GC_get_gc_no=lambda: 42,
    GC_get_heap_size=lambda: 3 * 1024 * 1024 * 1024,
    GC_get_free_bytes=lambda: 1024 * 1024 * 1024,
    GC_get_full_gc_total_time=lambda: 1500,
  )
  tok = wt.format_boehm_census_token(lib)
  assert "boehm_gc_no=42" in tok
  assert "boehm_heap_mib=3072" in tok
  assert "boehm_free_mib=1024" in tok
  assert "boehm_gc_ms=1500" in tok


def test_boehm_token_missing_symbols_are_skipped_not_raised():
  lib = SimpleNamespace(GC_get_gc_no=lambda: 7)
  assert wt.format_boehm_census_token(lib) == " boehm_gc_no=7"
  assert wt.format_boehm_census_token(None) == ""


def test_write_stage_stamps_queue_residence_and_exec(monkeypatch):
  """A real write thread must stamp enqueue, dequeue and exec onto payload."""
  from hpcperfstats.dbload import sync_timedb as st
  from hpcperfstats.dbload.lib import sync_timedb_ingest_write_stage as ws

  def slow_write(stats_file, stats, proc, **_kw):
    time.sleep(0.05)
    return stats_file, False, True

  monkeypatch.setattr(st, "_write_stats_payload_to_db", slow_write)
  wt.set_enabled(True)
  wt.begin_file()
  ws.start_ingest_write_stage(write_workers=1, queue_max=4)
  try:
    payload = ws.IngestWritePayload(stats_file="x", stats=None, proc_stats=None)
    ws.submit_ingest_write_and_wait(payload)
  finally:
    ws.stop_ingest_write_stage()
  assert payload.wait["enq_mono"] <= payload.wait["deq_mono"]
  assert payload.wait["exec_s"] >= 0.04
  assert "cpu_s" in payload.wait and "runq_s" in payload.wait
  assert wt.snapshot()["wq_put_s"] >= 0.0


def test_outcome_log_line_carries_wait_keys():
  from hpcperfstats.dbload import sync_timedb as st

  meta = {
    "outcome": "ingested",
    "thr_cpu_s": 12.0,
    "thr_runq_s": 30.0,
    "thr_sleep_s": 58.0,
    "wq_n": 3.0,
  }
  outcome = st._ingest_file_outcome_from_worker("/x", True, True, 100.0, meta)
  assert outcome.parse_stage["thr_runq_s"] == 30.0
  lines: list[str] = []
  orig = st.log_print
  st.log_print = lambda msg, **_k: lines.append(msg)
  try:
    st._log_ingest_file_outcome(outcome)
  finally:
    st.log_print = orig
  line = lines[0]
  assert "thr_cpu_s=12.0" in line
  assert "thr_runq_s=30.0" in line
  assert "thr_sleep_s=58.0" in line
  assert "wq_n=3 " in line or line.endswith("wq_n=3")


def test_reset_parse_stage_timing_arms_and_disarms_wait_ledger():
  from hpcperfstats.dbload.lib import sync_timedb_parsing as parse_mod

  parse_mod.reset_parse_stage_timing(enabled=True)
  assert wt.enabled() is True
  parse_mod.reset_parse_stage_timing(enabled=False)
  assert wt.enabled() is False


def test_held_parse_stage_records_thread_cpu_into_ledger():
  from hpcperfstats.dbload.lib import sync_timedb_parsing as parse_mod

  parse_mod.reset_parse_stage_timing(enabled=True)
  wt.begin_file()
  with parse_mod._held_parse_stage("arc_s"):
    deadline = time.thread_time() + 0.05
    while time.thread_time() < deadline:
      pass
  snap = wt.snapshot()
  assert snap["arc_cpu_s"] >= 0.04
  assert snap["stages_cpu_s"] >= snap["arc_cpu_s"]


def test_held_parse_stage_nested_cpu_is_exclusive():
  from hpcperfstats.dbload.lib import sync_timedb_parsing as parse_mod

  parse_mod.reset_parse_stage_timing(enabled=True)
  wt.begin_file()
  with parse_mod._held_parse_stage("feed_s"):
    with parse_mod._held_parse_stage("hw_df_s"):
      deadline = time.thread_time() + 0.05
      while time.thread_time() < deadline:
        pass
  snap = wt.snapshot()
  assert snap["build_df_cpu_s"] >= 0.04
  # Inner burn must not also count under the outer stage.
  assert snap["feed_cpu_s"] < 0.03


def test_classify_thread_role_by_python_thread_name():
  assert wt.classify_thread_role("ingest-pool_17") == "ingest"
  assert wt.classify_thread_role("ingest-write-3") == "write"
  assert wt.classify_thread_role("ingest-coordinator") == "coord"
  assert wt.classify_thread_role("MainThread") == "coord"
  assert wt.classify_thread_role("archive-pool_2") == "archive"
  assert wt.classify_thread_role("populate-pool_0") == "archive"
  assert wt.classify_thread_role(None) == "native"
  assert wt.classify_thread_role("weird") == "other"


def test_thread_role_census_reports_core_equivalents_over_window():
  # Two samples 10 s apart: ingest tid 1 burned 5 s CPU and waited 10 s;
  # write tid 2 burned 1 s. native tid 3 (no Python thread) burned 2 s.
  snaps = iter(
    [
      {1: (0, 0), 2: (0, 0), 3: (0, 0)},
      {
        1: (5_000_000_000, 10_000_000_000),
        2: (1_000_000_000, 0),
        3: (2_000_000_000, 0),
      },
    ]
  )
  names = {1: "ingest-pool_0", 2: "ingest-write-0"}
  clock = iter([100.0, 110.0])
  census = wt.ThreadRoleCensus(
    read_tasks=lambda: next(snaps),
    thread_names=lambda: names,
    now=lambda: next(clock),
  )
  assert census.format_token() == ""  # first call only primes the baseline
  tok = census.format_token()
  assert "cpu_ingest=0.5" in tok
  assert "runq_ingest=1.0" in tok
  assert "cpu_write=0.1" in tok
  assert "cpu_native=0.2" in tok


def test_thread_role_census_survives_thread_exit_between_samples():
  snaps = iter([{1: (0, 0), 9: (1, 1)}, {1: (1_000_000_000, 0)}])
  clock = iter([0.0, 1.0])
  census = wt.ThreadRoleCensus(
    read_tasks=lambda: next(snaps),
    thread_names=lambda: {1: "ingest-pool_0"},
    now=lambda: next(clock),
  )
  census.format_token()
  tok = census.format_token()
  assert "cpu_ingest=1.0" in tok


def test_pygc_token_reports_window_deltas(monkeypatch):
  wt.set_enabled(True)
  wt.reset_pygc_for_tests()
  wt.record_pygc_pause(0.010, 2)
  wt.record_pygc_pause(0.030, 0)
  tok = wt.format_pygc_census_token()
  assert "pygc_n=2" in tok
  assert "pygc_s=0.040" in tok
  assert "pygc_max_ms=30" in tok
  # Next window with no collections reports zeros, not stale totals.
  assert "pygc_n=0" in wt.format_pygc_census_token()


def test_pygc_token_empty_when_disabled():
  wt.set_enabled(False)
  assert wt.format_pygc_census_token() == ""


def test_gc_callbacks_install_once_and_measure_a_collection():
  import gc

  wt.set_enabled(True)
  wt.reset_pygc_for_tests()
  wt.install_pygc_callbacks()
  wt.install_pygc_callbacks()
  assert sum(1 for cb in gc.callbacks if cb is wt._pygc_callback) == 1
  gc.collect()
  assert "pygc_n=0" not in wt.format_pygc_census_token()
  gc.callbacks.remove(wt._pygc_callback)
  wt._PYGC_INSTALLED.clear()


def test_write_stage_census_token_shows_depth_and_busy_writers():
  from hpcperfstats.dbload.lib import sync_timedb_ingest_write_stage as ws

  tok = ws.format_write_stage_census_token()
  assert tok == "" or tok.startswith(" wq_depth=")
  ws.start_ingest_write_stage(write_workers=2, queue_max=5)
  try:
    tok = ws.format_write_stage_census_token()
    assert tok == " wq_depth=0/5 w_busy=0/2"
  finally:
    ws.stop_ingest_write_stage()


def test_parse_path_keys_are_registered_integer_log_keys():
  for key in ("codon_primary", "emit_start"):
    assert key in wt.WAIT_LOG_KEYS
    assert key in wt.WAIT_COUNT_KEYS


def test_streaming_parser_records_codon_primary_and_emit_start():
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_parsing as parse_mod

  src = inspect.getsource(parse_mod.parse_stats_file_streaming_incremental)
  assert 'wait_telem.add("codon_primary"' in src
  assert 'wait_telem.add("emit_start"' in src


def test_streaming_parser_marks_cpython_path_when_resumed(
  tmp_path, monkeypatch
):
  from hpcperfstats.dbload.lib import sync_timedb_parsing as parse_mod

  stats = tmp_path / "1792900000"
  stats.write_text(
    "$tacc_stats 2.3.4\n!cpu user,E,U=cs\n1792900000.0 1 host1\ncpu 0 1\n"
  )
  wt.set_enabled(True)
  wt.begin_file()
  parse_mod.parse_stats_file_streaming_incremental(
    str(stats), flush_rows=10, on_chunk=lambda s, p: None, start_line_idx=2
  )
  snap = wt.snapshot()
  assert snap["codon_primary"] == 0.0
  assert snap["emit_start"] == 2.0


def test_copy_encode_is_a_registered_write_detail_key():
  from hpcperfstats.dbload import sync_timedb as st

  assert "copy_encode_s" in st.INGEST_WRITE_DETAIL_KEYS
  assert "copy_encode_s" in st.INGEST_OUTCOME_TELEMETRY_KEYS


@pytest.mark.parametrize("kind", ["host", "proc"])
def test_bulk_insert_times_python_copy_encoding(monkeypatch, kind):
  from hpcperfstats.dbload import sync_timedb as st

  if kind == "host":
    from hpcperfstats.dbload.lib import sync_timedb_host_data_insert as ins

    enc, sink, call = (
      "host_rows_to_copy_bytes",
      "_write_host_copy_payload",
      ins.bulk_insert_host_rows,
    )
  else:
    from hpcperfstats.dbload.lib import sync_timedb_proc_data_insert as ins

    enc, sink, call = (
      "proc_rows_to_copy_bytes",
      "_write_proc_copy_payload",
      ins.bulk_insert_proc_rows,
    )

  def slow(rows, copy_unique=False):
    time.sleep(0.05)
    return b"x"

  st._reset_ingest_write_timing(enabled=True)
  try:
    monkeypatch.setattr(ins, enc, slow)
    monkeypatch.setattr(ins, sink, lambda payload: None)
    call([object()])
    assert st._snapshot_ingest_write_timing()["copy_encode_s"] >= 0.04
  finally:
    st._reset_ingest_write_timing(enabled=False)
