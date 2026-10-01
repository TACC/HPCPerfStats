"""Regression tests for ingest in-flight raw-byte admit budget."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

from hpcperfstats.dbload.lib import sync_timedb_job_store as jq
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
from hpcperfstats.dbload.lib import sync_timedb_worker_memory as wm
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


def test_peak_cgroup_constant_and_budget_from_roof(monkeypatch):
  assert wm.PEAK_CGROUP_PER_RAW_FILE_BYTE == 2.5
  max_128g = 128 * 1024 ** 3
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_process_tree_rss_limit_cgroup_pct",
      lambda: 40,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.read_cgroup_memory_max_bytes",
      lambda: max_128g,
  )
  budget = wm.compute_ingest_inflight_raw_bytes_budget()
  roof_mib = (max_128g * 40 // 100) // (1024 * 1024)
  expect = int((roof_mib * 1024 * 1024) / 2.5)
  assert budget == expect
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_process_tree_rss_limit_cgroup_pct",
      lambda: 0,
  )
  assert wm.compute_ingest_inflight_raw_bytes_budget() == 0


def test_can_admit_wait_and_alone_oversized():
  assert wm.can_admit_ingest_raw_bytes(0, 10, 100) is True
  assert wm.can_admit_ingest_raw_bytes(90, 20, 100) is False
  assert wm.can_admit_ingest_raw_bytes(0, 200, 100) is True
  assert wm.can_admit_ingest_raw_bytes(1, 200, 100) is False
  assert wm.can_admit_ingest_raw_bytes(0, 10, 0) is True


def test_fill_skips_when_inflight_raw_budget_tight(monkeypatch, tmp_path):
  """Multiple large files cannot all sit in flight over the byte budget."""
  paths = []
  for i in range(3):
    p = tmp_path / ("big%d" % i)
    p.write_bytes(b"x" * 40)
    paths.append(str(p))
  claims = [
      jq.ClaimedJob(
          kind=jq.JOB_KIND_INGEST,
          identity=p,
          owner_token="n:h:b:%d" % i,
          deadline=1e9,
          score=1.0,
          fingerprint=jq.ingest_fingerprint(
              os.stat(p).st_size, os.stat(p).st_mtime_ns,
          ),
      )
      for i, p in enumerate(paths)
  ]
  idx = {"n": 0}

  def _claim_jobs(*_a, **_k):
    if idx["n"] >= len(claims):
      return []
    c = claims[idx["n"]]
    idx["n"] += 1
    return [c]

  requeued = []
  monkeypatch.setattr(jq, "claim_ingest_jobs", _claim_jobs)
  monkeypatch.setattr(
      jq,
      "requeue_job",
      lambda *a, **k: requeued.append(k.get("identity")),
  )
  monkeypatch.setattr(
      jq, "bump_job_attempt", lambda *_a, **_k: 1,
  )
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 3)
  monkeypatch.setattr(
      wm, "compute_ingest_inflight_raw_bytes_budget", lambda: 50,
  )
  submitted = []

  class _Pool:
    def apply_async(self, fn, args):
      submitted.append(args[0])
      return SimpleNamespace(ready=lambda: False)

  inflight = {}
  sizes = {}
  stats = qo._empty_ingest_fill_stats()
  client = SyncTimedbJobStore("")
  n = qo._fill_ingest_band(
      client,
      band="catchup",
      cap=3,
      inflight=inflight,
      claims={},
      submitted={},
      ingest_pool=_Pool(),
      inflight_sizes=sizes,
      fill_stats=stats,
      tgz_archive_dir=str(tmp_path),
  )
  assert n == 1
  assert len(inflight) == 1
  assert sum(sizes.values()) == 40
  assert stats.get("skip_budget_bytes", 0) >= 1
  assert requeued
  assert qo.format_ingest_mem_block_census_suffix()


def test_fill_admits_alone_when_file_exceeds_budget(monkeypatch, tmp_path):
  p = tmp_path / "giant"
  p.write_bytes(b"y" * 200)
  identity = str(p)
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity=identity,
      owner_token="n:h:b:1",
      deadline=1e9,
      score=1.0,
      fingerprint=jq.ingest_fingerprint(
          os.stat(identity).st_size, os.stat(identity).st_mtime_ns,
      ),
  )
  monkeypatch.setattr(
      jq, "claim_ingest_jobs", lambda *_a, **_k: [claim],
  )
  monkeypatch.setattr(jq, "requeue_job", lambda *_a, **_k: None)
  monkeypatch.setattr(
      wm, "compute_ingest_inflight_raw_bytes_budget", lambda: 50,
  )

  class _Pool:
    def apply_async(self, fn, args):
      return SimpleNamespace(ready=lambda: False)

  inflight = {}
  sizes = {}
  n = qo._fill_ingest_band(
      SyncTimedbJobStore(""),
      band="hot",
      cap=1,
      inflight=inflight,
      claims={},
      submitted={},
      ingest_pool=_Pool(),
      inflight_sizes=sizes,
      tgz_archive_dir=str(tmp_path),
  )
  assert n == 1
  assert identity in inflight
  assert sizes[identity] == 200


def test_fill_block_keys_include_skip_budget_bytes():
  assert "skip_budget_bytes" in qo._FILL_BLOCK_KEYS
  assert "skip_cgroup_headroom" in qo._FILL_BLOCK_KEYS
  stats = qo._empty_ingest_fill_stats()
  assert stats["skip_budget_bytes"] == 0
  assert stats["skip_cgroup_headroom"] == 0
  assert qo._dominant_ingest_fill_block(
      {"skip_budget_bytes": 3, "claim_none": 1},
  ) == "skip_budget_bytes"


def test_fill_skips_cgroup_headroom(monkeypatch, tmp_path):
  p = tmp_path / "ok"
  p.write_bytes(b"z" * 10)
  identity = str(p)
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity=identity,
      owner_token="n:h:b:1",
      deadline=1e9,
      score=1.0,
      fingerprint=jq.ingest_fingerprint(
          os.stat(identity).st_size, os.stat(identity).st_mtime_ns,
      ),
  )
  monkeypatch.setattr(
      jq, "claim_ingest_jobs", lambda *_a, **_k: [claim],
  )
  requeued = []
  monkeypatch.setattr(
      jq,
      "requeue_job",
      lambda *a, **k: requeued.append(k.get("reason") or a),
  )
  monkeypatch.setattr(jq, "bump_job_attempt", lambda *_a, **_k: 1)
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 3)
  monkeypatch.setattr(
      wm, "compute_ingest_inflight_raw_bytes_budget", lambda: 10 ** 12,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_headroom_mib",
      lambda: 16384,
  )
  monkeypatch.setattr(
      qo, "cgroup_admit_headroom_ok", lambda *_a, **_k: False,
  )

  class _Pool:
    def apply_async(self, fn, args):
      raise AssertionError("must not submit under headroom block")

  stats = qo._empty_ingest_fill_stats()
  n = qo._fill_ingest_band(
      SyncTimedbJobStore(""),
      band="hot",
      cap=1,
      inflight={},
      claims={},
      submitted={},
      ingest_pool=_Pool(),
      inflight_sizes={},
      fill_stats=stats,
      tgz_archive_dir=str(tmp_path),
  )
  assert n == 0
  assert stats.get("skip_cgroup_headroom", 0) >= 1
  assert requeued


def test_fill_cgroup_headroom_skip_never_dead_letters(monkeypatch, tmp_path):
  """Sustained headroom skips must requeue without bumping attempts."""
  p = tmp_path / "ok"
  p.write_bytes(b"z" * 10)
  identity = str(p)
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity=identity,
      owner_token="n:h:b:1",
      deadline=1e9,
      score=1.0,
      fingerprint=jq.ingest_fingerprint(
          os.stat(identity).st_size, os.stat(identity).st_mtime_ns,
      ),
  )
  claim_calls = {"n": 0}

  def _claim(*_a, **_k):
    claim_calls["n"] += 1
    return [claim] if claim_calls["n"] <= 5 else []

  bumps: list[int] = []
  requeues: list[str] = []
  letters: list[str] = []
  monkeypatch.setattr(jq, "claim_ingest_jobs", _claim)
  monkeypatch.setattr(
      jq,
      "requeue_job",
      lambda *a, **k: requeues.append(k.get("identity") or "") or True,
  )
  monkeypatch.setattr(
      jq,
      "bump_job_attempt",
      lambda *_a, **_k: bumps.append(1) or 99,
  )
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 5)
  monkeypatch.setattr(
      jq,
      "append_queue_dead_letter",
      lambda *_a, **k: letters.append(str(k.get("reason"))),
  )
  monkeypatch.setattr(
      wm, "compute_ingest_inflight_raw_bytes_budget", lambda: 10 ** 12,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_headroom_mib",
      lambda: 16384,
  )
  monkeypatch.setattr(
      qo, "cgroup_admit_headroom_ok", lambda *_a, **_k: False,
  )

  class _Pool:
    def apply_async(self, fn, args):
      raise AssertionError("must not submit under headroom block")

  stats = qo._empty_ingest_fill_stats()
  for _ in range(5):
    qo._fill_ingest_band(
        SyncTimedbJobStore(""),
        band="hot",
        cap=1,
        inflight={},
        claims={},
        submitted={},
        ingest_pool=_Pool(),
        inflight_sizes={},
        fill_stats=stats,
        tgz_archive_dir=str(tmp_path),
    )
  assert stats.get("skip_cgroup_headroom", 0) >= 5
  assert len(requeues) >= 5
  assert bumps == []
  assert letters == []


def test_fill_budget_skip_never_dead_letters(monkeypatch, tmp_path):
  """skip_budget_bytes must requeue without bumping attempts."""
  p = tmp_path / "big"
  p.write_bytes(b"x" * 40)
  identity = str(p)
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity=identity,
      owner_token="n:h:b:1",
      deadline=1e9,
      score=1.0,
      fingerprint=jq.ingest_fingerprint(
          os.stat(identity).st_size, os.stat(identity).st_mtime_ns,
      ),
  )
  bumps: list[int] = []
  requeues: list[str] = []
  letters: list[str] = []
  monkeypatch.setattr(
      jq, "claim_ingest_jobs", lambda *_a, **_k: [claim],
  )
  monkeypatch.setattr(
      jq,
      "requeue_job",
      lambda *a, **k: requeues.append(k.get("identity") or "") or True,
  )
  monkeypatch.setattr(
      jq,
      "bump_job_attempt",
      lambda *_a, **_k: bumps.append(1) or 99,
  )
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 5)
  monkeypatch.setattr(
      jq,
      "append_queue_dead_letter",
      lambda *_a, **k: letters.append(str(k.get("reason"))),
  )
  monkeypatch.setattr(
      wm, "compute_ingest_inflight_raw_bytes_budget", lambda: 50,
  )

  class _Pool:
    def apply_async(self, fn, args):
      return SimpleNamespace(ready=lambda: False)

  inflight = {}
  sizes = {}
  # Occupy budget so the next claim is skip_budget_bytes.
  inflight["hold"] = SimpleNamespace(ready=lambda: False)
  sizes["hold"] = 40
  stats = qo._empty_ingest_fill_stats()
  qo._fill_ingest_band(
      SyncTimedbJobStore(""),
      band="catchup",
      cap=3,
      inflight=inflight,
      claims={},
      submitted={"hold": time.monotonic()},
      ingest_pool=_Pool(),
      inflight_sizes=sizes,
      fill_stats=stats,
      tgz_archive_dir=str(tmp_path),
  )
  assert stats.get("skip_budget_bytes", 0) >= 1
  assert requeues
  assert bumps == []
  assert letters == []


def test_fill_append_returns_zero_when_headroom_blocked(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_headroom_mib",
      lambda: 16384,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_timedb_tar_append_batch_size",
      lambda: 8,
  )
  monkeypatch.setattr(qo, "cgroup_admit_headroom_ok", lambda *_a, **_k: False)
  n = qo._fill_append_slots(
      SyncTimedbJobStore(""),
      cap=4,
      inflight={},
      claims={},
      archive_pool=None,
      tgz_archive_dir="/d",
  )
  assert n == 0


def test_alone_oversized_still_admits_when_headroom_blocked(monkeypatch, tmp_path):
  p = tmp_path / "giant"
  p.write_bytes(b"y" * 200)
  identity = str(p)
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity=identity,
      owner_token="n:h:b:1",
      deadline=1e9,
      score=1.0,
      fingerprint=jq.ingest_fingerprint(
          os.stat(identity).st_size, os.stat(identity).st_mtime_ns,
      ),
  )
  monkeypatch.setattr(
      jq, "claim_ingest_jobs", lambda *_a, **_k: [claim],
  )
  monkeypatch.setattr(jq, "requeue_job", lambda *_a, **_k: None)
  monkeypatch.setattr(
      wm, "compute_ingest_inflight_raw_bytes_budget", lambda: 50,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_headroom_mib",
      lambda: 16384,
  )
  monkeypatch.setattr(qo, "cgroup_admit_headroom_ok", lambda *_a, **_k: False)

  class _Pool:
    def apply_async(self, fn, args):
      return SimpleNamespace(ready=lambda: False)

  inflight = {}
  sizes = {}
  n = qo._fill_ingest_band(
      SyncTimedbJobStore(""),
      band="hot",
      cap=1,
      inflight=inflight,
      claims={},
      submitted={},
      ingest_pool=_Pool(),
      inflight_sizes=sizes,
      tgz_archive_dir=str(tmp_path),
  )
  assert n == 1
  assert identity in inflight


def test_fill_cgroup_file_cache_blocks_when_over_cap(monkeypatch, tmp_path):
  p = tmp_path / "ok"
  p.write_bytes(b"z" * 10)
  identity = str(p)
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity=identity,
      owner_token="n:h:b:1",
      deadline=1e9,
      score=1.0,
      fingerprint=jq.ingest_fingerprint(
          os.stat(identity).st_size, os.stat(identity).st_mtime_ns,
      ),
  )
  monkeypatch.setattr(
      jq, "claim_ingest_jobs", lambda *_a, **_k: [claim],
  )
  requeued = []
  monkeypatch.setattr(
      jq,
      "requeue_job",
      lambda *a, **k: requeued.append(k.get("reason") or a),
  )
  monkeypatch.setattr(jq, "bump_job_attempt", lambda *_a, **_k: 1)
  monkeypatch.setattr(
      wm, "compute_ingest_inflight_raw_bytes_budget", lambda: 10 ** 12,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_headroom_mib",
      lambda: 0,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_max_file_cache_mib",
      lambda: 81920,
  )
  monkeypatch.setattr(qo, "cgroup_admit_headroom_ok", lambda *_a, **_k: True)
  monkeypatch.setattr(qo, "cgroup_admit_file_cache_ok", lambda *_a, **_k: False)

  class _Pool:
    def apply_async(self, fn, args):
      raise AssertionError("must not submit under file-cache block")

  stats = qo._empty_ingest_fill_stats()
  n = qo._fill_ingest_band(
      SyncTimedbJobStore(""),
      band="hot",
      cap=1,
      inflight={},
      claims={},
      submitted={},
      ingest_pool=_Pool(),
      inflight_sizes={},
      fill_stats=stats,
      tgz_archive_dir=str(tmp_path),
  )
  assert n == 0
  assert stats.get("skip_file_cache_pressure", 0) >= 1
  assert requeued


def test_fill_append_returns_zero_when_file_cache_blocked(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_headroom_mib",
      lambda: 0,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_max_file_cache_mib",
      lambda: 81920,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_timedb_tar_append_batch_size",
      lambda: 8,
  )
  monkeypatch.setattr(qo, "cgroup_admit_headroom_ok", lambda *_a, **_k: True)
  monkeypatch.setattr(qo, "cgroup_admit_file_cache_ok", lambda *_a, **_k: False)
  n = qo._fill_append_slots(
      SyncTimedbJobStore(""),
      cap=4,
      inflight={},
      claims={},
      archive_pool=None,
      tgz_archive_dir="/d",
  )
  assert n == 0


def test_alone_oversized_still_admits_when_file_cache_blocked(monkeypatch, tmp_path):
  p = tmp_path / "giant"
  p.write_bytes(b"y" * 200)
  identity = str(p)
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity=identity,
      owner_token="n:h:b:1",
      deadline=1e9,
      score=1.0,
      fingerprint=jq.ingest_fingerprint(
          os.stat(identity).st_size, os.stat(identity).st_mtime_ns,
      ),
  )
  monkeypatch.setattr(
      jq, "claim_ingest_jobs", lambda *_a, **_k: [claim],
  )
  monkeypatch.setattr(jq, "requeue_job", lambda *_a, **_k: None)
  monkeypatch.setattr(
      wm, "compute_ingest_inflight_raw_bytes_budget", lambda: 50,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_headroom_mib",
      lambda: 16384,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_cgroup_admit_max_file_cache_mib",
      lambda: 81920,
  )
  monkeypatch.setattr(qo, "cgroup_admit_headroom_ok", lambda *_a, **_k: True)
  monkeypatch.setattr(qo, "cgroup_admit_file_cache_ok", lambda *_a, **_k: False)

  class _Pool:
    def apply_async(self, fn, args):
      return SimpleNamespace(ready=lambda: False)

  inflight = {}
  sizes = {}
  n = qo._fill_ingest_band(
      SyncTimedbJobStore(""),
      band="hot",
      cap=1,
      inflight=inflight,
      claims={},
      submitted={},
      ingest_pool=_Pool(),
      inflight_sizes=sizes,
      tgz_archive_dir=str(tmp_path),
  )
  assert n == 1
  assert identity in inflight


def test_fill_block_keys_include_skip_file_cache_pressure():
  assert "skip_file_cache_pressure" in qo._FILL_BLOCK_KEYS
