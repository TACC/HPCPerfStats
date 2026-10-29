"""Day-close handoff asks the open-tar question once per batch, not per path.

hpcperfstats04 2026-10-28: each raw path of a handoff re-ran the open-tar
readability probe and the append-inflight check (``daily_tar_repair`` on a
27-46 GB tar, 132k ``handoff fail`` lines for 51k paths) although the answer
is the same for every path of that day.
"""

from __future__ import annotations

import os
from datetime import date

from hpcperfstats.dbload.lib import (
  sync_timedb_archive_helpers as ah,
  sync_timedb_job_reconstruct as jr,
  sync_timedb_queue_orchestrator as qo,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
  ArchiveMembersStoreUnavailableError,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


def _setup(tmp_path, n=40):
  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / "2020-01-01.tar"
  tar.write_bytes(b"tar")
  raws = []
  for i in range(n):
    p = tmp_path / f"node{i}.stats"
    p.write_bytes(b"payload")
    raws.append(str(p))
  return daily, tar, raws


def _run(tmp_path, daily, tar, raws, logs, done=()):
  qo._HANDOFF_DEFER_LOG_STATE.clear()
  client = SyncTimedbJobStore("")
  n = qo._handoff_retryable_paths_to_ingest(
    client,
    str(tar),
    raws,
    tgz_archive_dir=str(daily),
    archive_data_dir=str(tmp_path),
    today=date(2026, 8, 24),
    log_fn=lambda msg, **_k: logs.append(msg),
    ingest_is_complete_fn=lambda *, path, **_k: path in done,
  )
  return n, client


def test_handoff_defers_whole_batch_once_when_open_tar_unavailable(
  tmp_path, monkeypatch
):
  daily, tar, raws = _setup(tmp_path)
  probes, classified = [], []

  def _unavailable(*_a, **_k):
    probes.append(1)
    raise ArchiveMembersStoreUnavailableError("append inflight")

  monkeypatch.setattr(
    ah, "_ensure_open_tar_readable_for_ingest_lookup", _unavailable
  )
  real_classify = jr.classify_closed_raw_path
  monkeypatch.setattr(
    jr,
    "classify_closed_raw_path",
    lambda *a, **k: classified.append(1) or real_classify(*a, **k),
  )
  logs: list[str] = []
  n, _client = _run(tmp_path, daily, tar, raws, logs)
  assert n == 0
  assert len(probes) == 1
  assert not classified
  assert not [m for m in logs if "handoff fail" in m]
  deferred = [m for m in logs if "handoff deferred" in m]
  assert len(deferred) == 1
  assert f"paths={len(raws)}" in deferred[0]


def test_handoff_probes_and_reads_members_once_per_batch(tmp_path, monkeypatch):
  daily, tar, raws = _setup(tmp_path)
  probes, map_reads = [], []
  already = {ah.get_tar_member_name(p): os.path.getsize(p) for p in raws[:10]}
  monkeypatch.setattr(
    ah,
    "_ensure_open_tar_readable_for_ingest_lookup",
    lambda *_a, **_k: probes.append(1),
  )
  monkeypatch.setattr(
    ah,
    "get_mutable_tar_authority_member_map",
    lambda _tar: map_reads.append(1) or already,
  )
  logs: list[str] = []
  done = {os.path.normpath(p) for p in raws[:10]}
  n, client = _run(tmp_path, daily, tar, raws, logs, done)
  assert len(probes) == 1
  assert len(map_reads) == 1
  assert n == len(raws) - 10
  assert client.ingest_score(os.path.normpath(raws[20])) is not None
  assert client.ingest_score(os.path.normpath(raws[0])) is None
  assert not [m for m in logs if "handoff" in m and "fail" in m]


def test_handoff_keeps_injected_append_predicate_without_batch_probe(
  tmp_path, monkeypatch
):
  daily, tar, raws = _setup(tmp_path, n=3)
  monkeypatch.setattr(
    ah,
    "_ensure_open_tar_readable_for_ingest_lookup",
    lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("probed")),
  )
  client = SyncTimedbJobStore("")
  n = qo._handoff_retryable_paths_to_ingest(
    client,
    str(tar),
    raws,
    tgz_archive_dir=str(daily),
    archive_data_dir=str(tmp_path),
    today=date(2026, 8, 24),
    ingest_is_complete_fn=lambda **_k: False,
    append_is_complete_fn=lambda **_k: True,
  )
  assert n == 3
