"""Compose-backed write-only proc_data bulk_create vs COPY A/B."""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.screening_runner import (
    build_host_insert_ab_manifest,
    host_insert_retain_candidate,
    summarize_write_s_replicates,
    write_screening_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROWS = 100_000
DEFAULT_REPS = 5

pytestmark = [pytest.mark.sync_timedb_bench]


def _proc_insert_enabled() -> bool:
  return os.environ.get(
      "HPCPERFSTATS_SYNC_TIMEDB_PROC_INSERT",
      "",
  ).strip().lower() in ("1", "yes", "true")


def _row_count() -> int:
  raw = os.environ.get("HPCPERFSTATS_PROC_INSERT_ROWS", "").strip()
  return max(1, int(raw)) if raw else DEFAULT_ROWS


def _replicates() -> int:
  raw = os.environ.get("HPCPERFSTATS_PROC_INSERT_REPLICATES", "").strip()
  return max(1, int(raw)) if raw else DEFAULT_REPS


def _build_proc_objs(n: int, *, tag: str):
  from hpcperfstats.site.lib.machine.models import proc_data

  host = "procinsertab-%s.example.edu" % tag
  objs = []
  for i in range(n):
    objs.append(
        proc_data(
            jid=str(i % 50_000),
            host=host,
            proc="proc-%s" % i,
            device="proc-%s/%s" % (i, i),
            uid=1000,
            vm_peak=i,
            vm_size=i,
            vm_lck=0,
            vm_hwm=i,
            vm_rss=i,
            vm_data=i,
            vm_stk=i,
            vm_exe=i,
            vm_lib=i,
            vm_pte=i,
            vm_swap=0,
            threads=1,
        )
    )
  return objs


def _time_arm(arm: str, objs, *, batch: int) -> float:
  from hpcperfstats.dbload.lib import sync_timedb_proc_data_insert as pdi

  os.environ["HPCPERFSTATS_PROC_INSERT_ARM"] = arm
  t0 = time.perf_counter()
  for i in range(0, len(objs), batch):
    pdi.insert_proc_data_batch(objs[i : i + batch])
  return time.perf_counter() - t0


def _cleanup(tag: str) -> None:
  from hpcperfstats.site.lib.machine.models import proc_data

  proc_data.objects.filter(host__startswith="procinsertab-%s" % tag).delete()


def test_proc_data_insert_ab_write_only(monkeypatch):
  """Time baseline vs COPY proc upsert on ≥100k rows; write retain artifact."""
  if not _proc_insert_enabled():
    pytest.fail("HPCPERFSTATS_SYNC_TIMEDB_PROC_INSERT=1 required")
  row_count = _row_count()
  if row_count < 100_000:
    pytest.fail("row_count must be >= 100000; got %s" % row_count)
  replicates = _replicates()
  batch = 10_000
  run_tag = uuid.uuid4().hex[:12]
  baseline_samples: list[float] = []
  candidate_samples: list[float] = []
  try:
    for rep in range(replicates):
      tag = "%s-b%d" % (run_tag, rep)
      objs = _build_proc_objs(row_count, tag=tag)
      print("baseline replicate %s/%s …" % (rep + 1, replicates), flush=True)
      baseline_samples.append(_time_arm("baseline", objs, batch=batch))
      _cleanup(tag)
      tag = "%s-c%d" % (run_tag, rep)
      objs = _build_proc_objs(row_count, tag=tag)
      print("candidate replicate %s/%s …" % (rep + 1, replicates), flush=True)
      candidate_samples.append(_time_arm("candidate", objs, batch=batch))
      _cleanup(tag)
  finally:
    monkeypatch.delenv("HPCPERFSTATS_PROC_INSERT_ARM", raising=False)
    _cleanup(run_tag)

  baseline = summarize_write_s_replicates(baseline_samples)
  candidate = summarize_write_s_replicates(candidate_samples)
  retain = host_insert_retain_candidate(baseline=baseline, candidate=candidate)
  payload = build_host_insert_ab_manifest(
      baseline=baseline,
      candidate=candidate,
      row_count=row_count,
      retain=retain,
      python_abi=sys.version.split()[0],
  )
  payload["kind"] = "proc_data_insert_ab"
  path = write_screening_artifact(
      payload,
      repo_root=REPO_ROOT,
      prefix="proc_data_insert_ab",
  )
  print(
      "proc_data_insert_ab retain=%s baseline_mean=%.3f candidate_mean=%.3f path=%s"
      % (retain, baseline["mean_write_s"], candidate["mean_write_s"], path),
      flush=True,
  )
  assert path.is_file()
  assert int(payload["row_count"]) >= 100_000
