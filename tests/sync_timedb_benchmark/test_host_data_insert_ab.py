"""Compose-backed write-only host_data bulk_create vs COPY A/B."""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.screening_runner import (
    build_host_insert_ab_manifest,
    host_insert_mode_enabled,
    host_insert_replicates,
    host_insert_retain_candidate,
    host_insert_row_count,
    summarize_write_s_replicates,
    write_screening_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.sync_timedb_bench,
]


def _build_host_objs(n: int, *, tag: str):
  from hpcperfstats.site.lib.machine.models import host_data

  base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
  host = "hostinsertab-%s.example.edu" % tag
  objs = []
  for i in range(n):
    objs.append(
        host_data(
            time=base + timedelta(microseconds=i),
            host=host,
            jid=str(i % 10_000),
            type="cpu",
            dev="",
            event="user",
            unit="%",
            value=float(i % 100),
            delta=1.0,
            arc=0.5,
        )
    )
  return objs


def _time_arm_insert(arm: str, objs, *, batch: int) -> float:
  from hpcperfstats.dbload.lib import sync_timedb_host_data_insert as hdi

  os.environ["HPCPERFSTATS_HOST_INSERT_ARM"] = arm
  t0 = time.perf_counter()
  for i in range(0, len(objs), batch):
    hdi.insert_host_data_batch(objs[i : i + batch])
  return time.perf_counter() - t0


def _cleanup_tag(tag: str) -> None:
  from hpcperfstats.site.lib.machine.models import host_data

  host_data.objects.filter(host__startswith="hostinsertab-%s" % tag).delete()


def test_host_data_insert_ab_write_only(monkeypatch):
  """
  Time baseline vs COPY insert on ≥100k host_data rows; write retain artifact.

  Requires compose db/redis and ``HPCPERFSTATS_SYNC_TIMEDB_HOST_INSERT=1``.
  """
  if not host_insert_mode_enabled():
    pytest.fail("HPCPERFSTATS_SYNC_TIMEDB_HOST_INSERT=1 required")

  row_count = host_insert_row_count()
  if row_count < 100_000:
    pytest.fail("row_count must be >= 100000 for large-data gate; got %s" % row_count)
  replicates = host_insert_replicates()
  batch = 10_000
  run_tag = uuid.uuid4().hex[:12]

  baseline_samples: list[float] = []
  candidate_samples: list[float] = []
  try:
    for rep in range(replicates):
      tag = "%s-b%d" % (run_tag, rep)
      objs = _build_host_objs(row_count, tag=tag)
      print("baseline replicate %s/%s …" % (rep + 1, replicates), flush=True)
      baseline_samples.append(_time_arm_insert("baseline", objs, batch=batch))
      _cleanup_tag(tag)

      tag = "%s-c%d" % (run_tag, rep)
      objs = _build_host_objs(row_count, tag=tag)
      print("candidate replicate %s/%s …" % (rep + 1, replicates), flush=True)
      candidate_samples.append(_time_arm_insert("candidate", objs, batch=batch))
      _cleanup_tag(tag)
  finally:
    monkeypatch.delenv("HPCPERFSTATS_HOST_INSERT_ARM", raising=False)
    _cleanup_tag(run_tag)

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
  path = write_screening_artifact(
      payload,
      repo_root=REPO_ROOT,
      prefix="host_data_insert_ab",
  )
  print(
      "host_data_insert_ab retain=%s baseline_mean=%.3f candidate_mean=%.3f path=%s"
      % (retain, baseline["mean_write_s"], candidate["mean_write_s"], path),
      flush=True,
  )
  # Always succeed as a measurement run; retain is in the artifact.
  assert path.is_file()
  assert int(payload["row_count"]) >= 100_000
