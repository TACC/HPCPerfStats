"""Tests for scripts/analyze_ingest_stacks.py (wall-clock stack aggregation)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
  Path(__file__).resolve().parents[2] / "scripts" / "analyze_ingest_stacks.py"
)
_spec = importlib.util.spec_from_file_location("analyze_ingest_stacks", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(mod)

_DUMP = """Timeout (0:00:30)!
Thread 0x00007f0000000001 [sync_timedb.py ] (most recent call first):
  File "/opt/python3.14t/lib/python3.14t/threading.py", line 359 in wait
  File "/home/hpcperfstats/hpcperfstats/dbload/sync_timedb.py", line 1966 in _join_ingest_write_payload
  File "/home/hpcperfstats/hpcperfstats/dbload/sync_timedb.py", line 4828 in _on_chunk
  File "/home/hpcperfstats/hpcperfstats/dbload/lib/sync_timedb_session_executor.py", line 90 in _run_task

Thread 0x00007f0000000002 [sync_timedb.py ] (most recent call first):
  File "/opt/python3.14t/lib/python3.14t/site-packages/pandas/core/algorithms.py", line 476 in unique_with_mask
  File "/home/hpcperfstats/hpcperfstats/dbload/lib/sync_timedb_parsing.py", line 1513 in _groupby_sum_min_count
  File "/home/hpcperfstats/hpcperfstats/dbload/lib/sync_timedb_session_executor.py", line 90 in _run_task

Thread 0x00007f0000000003 [sync_timedb.py ] (most recent call first):
  File "/opt/python3.14t/lib/python3.14t/queue.py", line 171 in get
  File "/home/hpcperfstats/hpcperfstats/dbload/lib/sync_timedb_ingest_write_stage.py", line 440 in _write_worker_main

Thread 0x00007f0000000004 [sync_timedb.py ] (most recent call first):
  File "/home/hpcperfstats/hpcperfstats/dbload/lib/sync_timedb_parsing.py", line ??? in _add_parse_stage_s
  <invalid frame>

Thread 0x00007f0000000005 [sync_timedb.py ] (most recent call first):
  File "/opt/python3.14t/lib/python3.14t/concurrent/futures/thread.py", line 116 in _worker
"""


def test_parse_dump_splits_threads_and_tolerates_invalid_frames():
  threads = mod.parse_dump(_DUMP)
  assert len(threads) == 5
  assert threads[0][0].func == "wait"
  assert threads[3][0].func == "_add_parse_stage_s"
  assert threads[3][0].line == -1


def test_role_of_uses_distinctive_frames():
  threads = mod.parse_dump(_DUMP)
  assert mod.role_of(threads[0]) == "ingest"
  assert mod.role_of(threads[1]) == "ingest"
  assert mod.role_of(threads[2]) == "write"
  assert mod.role_of(threads[4]) == "idle_pool"


def test_wait_kind_classifies_innermost_frame():
  threads = mod.parse_dump(_DUMP)
  assert mod.wait_kind(threads[0]) == "lock_wait"
  assert mod.wait_kind(threads[1]) == "compute_pandas"
  assert mod.wait_kind(threads[2]) == "queue_wait"
  assert mod.wait_kind(threads[3]) == "compute_project"


def test_aggregate_counts_innermost_project_frame_per_role():
  agg = mod.aggregate([mod.parse_dump(_DUMP), mod.parse_dump(_DUMP)])
  ingest = agg["ingest"]
  assert ingest["threads"] == 4
  top = dict(ingest["top_project_frames"])
  assert top["sync_timedb.py:_join_ingest_write_payload"] == 2
  assert top["sync_timedb_parsing.py:_groupby_sum_min_count"] == 2
  assert ingest["kinds"]["lock_wait"] == 2


def test_render_markdown_lists_roles_and_shares():
  agg = mod.aggregate([mod.parse_dump(_DUMP)])
  text = mod.render_markdown(agg, snapshots=1)
  assert "## ingest" in text and "## write" in text
  assert "_join_ingest_write_payload" in text


def test_main_returns_nonzero_without_dump(tmp_path, capsys):
  p = tmp_path / "x.stacks"
  p.write_text("nothing\n")
  assert mod.main(["--stacks", str(p)]) == 1


def test_main_prints_report(tmp_path, capsys):
  p = tmp_path / "x.stacks"
  p.write_text(_DUMP)
  assert mod.main(["--stacks", str(p)]) == 0
  assert "## ingest" in capsys.readouterr().out


def test_snapshots_split_on_timeout_banner():
  snaps = mod.split_snapshots(_DUMP + _DUMP)
  assert len(snaps) == 2


@pytest.mark.parametrize("n", [0, 1])
def test_pct_handles_zero_total(n):
  assert mod.pct(n, 0) == 0.0
