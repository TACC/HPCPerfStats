"""Unit tests for metrics idle-slot sample-count supplement helpers."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest

from hpcperfstats.analysis.metrics.lib.metrics_idle_slot_supplement import (
  estimated_sample_count_for_job,
  pop_supplement_refs_from_ready_queue,
  resolve_nhosts_for_ref,
  sample_count_for_ref,
)


@pytest.mark.machine_unit_mock
def test_estimated_sample_count_for_ref_basic():
  assert estimated_sample_count_for_job(2, 120.0) == 4
  assert estimated_sample_count_for_job(1, 59.0) == 1
  assert estimated_sample_count_for_job(3, 61.0) == 6
  assert estimated_sample_count_for_job(0, None, unknown_runtime_s=60.0) == 1


@pytest.mark.machine_unit_mock
def test_resolve_nhosts_prefers_nhosts_then_host_list():
  assert resolve_nhosts_for_ref(4, ["a", "b"]) == 4
  assert resolve_nhosts_for_ref(0, ["a", "b"]) == 2
  assert resolve_nhosts_for_ref(None, None) == 1


@pytest.mark.machine_unit_mock
def test_iter_metrics_supplement_prefers_under_soft_max():
  q = deque([
      SimpleNamespace(jid="big", estimated_sample_count=90000),
      SimpleNamespace(jid="mid", estimated_sample_count=20000),
      SimpleNamespace(jid="small", estimated_sample_count=10),
  ])
  taken = pop_supplement_refs_from_ready_queue(
      q,
      max_n=2,
      soft_max=10000,
      hard_max=80000,
      original_batch_still_inflight=True,
  )
  assert [r.jid for r in taken] == ["small", "mid"]
  assert [r.jid for r in q] == ["big"]


@pytest.mark.machine_unit_mock
def test_metrics_supplement_stops_when_only_supplements_in_flight():
  q = deque([SimpleNamespace(jid="s", estimated_sample_count=5)])
  assert pop_supplement_refs_from_ready_queue(
      q,
      max_n=1,
      soft_max=10000,
      hard_max=80000,
      original_batch_still_inflight=False,
  ) == []
  assert len(q) == 1


@pytest.mark.machine_unit_mock
def test_sample_count_for_ref_uses_attached_estimate():
  ref = SimpleNamespace(estimated_sample_count=42, nhosts=1, runtime_s=999)
  assert sample_count_for_ref(ref) == 42
