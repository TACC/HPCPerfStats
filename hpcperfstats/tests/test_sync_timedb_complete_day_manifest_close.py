"""A complete day must not leave its day-removal manifest open.

The daily-dir scan (DE-24) enqueues a sealed-only day while its manifest
phase is not ``done``; the day_close worker returns ``complete`` for a day
that is already filesystem-complete. Unless the worker closes the manifest,
the two predicates disagree and the day is claimed and vacated forever.
"""

import json

from hpcperfstats.dbload.lib import (
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


def _zst_only_day_with_open_manifest(tmp_path, day, phase):
  daily = tmp_path / "daily"
  daily.mkdir(exist_ok=True)
  manifests = tmp_path / ".sync_timedb_day_raw_removal"
  manifests.mkdir(exist_ok=True)
  (daily / f"{day}.tar.zst").write_bytes(b"zst")
  manifest = manifests / f"{day}.json"
  manifest.write_text(
    json.dumps(
      {
        "phase": phase,
        "entries": {},
        "tar_path": str(daily / f"{day}.tar"),
      }
    )
  )
  return daily, manifest


def _phase(manifest_path):
  with open(manifest_path, encoding="utf-8") as fh:
    return json.load(fh).get("phase")


def _run(daily, tmp_path, day, lines=None):
  sink = lines if lines is not None else []
  return qo._run_day_close_job(
    str(daily / f"{day}.tar"),
    tgz_archive_dir=str(daily),
    archive_data_dir=str(tmp_path),
    log_fn=lambda msg, **k: sink.append(str(msg)),
  )


def _scan(client, daily, tmp_path):
  return qo._enqueue_day_closes_for_daily_dir(
    client, tgz_archive_dir=str(daily), archive_data_dir=str(tmp_path)
  )


def test_complete_day_close_closes_open_manifest_and_stops_requeue(
  tmp_path, monkeypatch
):
  """Already-complete day: worker closes the manifest; the scan stops."""
  day = "2020-02-01"
  daily, manifest = _zst_only_day_with_open_manifest(
    tmp_path, day, "verification_complete"
  )
  client = SyncTimedbJobStore("")
  assert _scan(client, daily, tmp_path) == 1
  lines = []
  with monkeypatch.context() as m:
    m.setattr(qo, "_day_close_live_ingest_or_populate", lambda *a, **k: False)
    m.setattr(qo.jr, "day_close_is_complete", lambda *a, **k: True)
    assert _run(daily, tmp_path, day, lines) == "complete"
  assert _phase(manifest) == "done"
  assert any("manifest closed" in ln and f"day={day}" in ln for ln in lines)
  claim = jq.claim_list_job(
    client,
    kind=jq.JOB_KIND_DAY_CLOSE,
    owner_token=jq.make_lease_owner_token(),
  )
  jq.ack_job(
    client,
    kind=jq.JOB_KIND_DAY_CLOSE,
    identity=claim.identity,
    owner_token=claim.owner_token,
  )
  assert _scan(client, daily, tmp_path) == 0


def test_complete_day_close_leaves_done_manifest_untouched(
  tmp_path, monkeypatch
):
  """A manifest already at done is not rewritten."""
  day = "2020-02-02"
  daily, manifest = _zst_only_day_with_open_manifest(tmp_path, day, "done")
  before = manifest.stat().st_mtime_ns
  monkeypatch.setattr(
    qo, "_day_close_live_ingest_or_populate", lambda *a, **k: False
  )
  monkeypatch.setattr(qo.jr, "day_close_is_complete", lambda *a, **k: True)
  lines = []
  assert _run(daily, tmp_path, day, lines) == "complete"
  assert manifest.stat().st_mtime_ns == before
  assert not any("manifest closed" in ln for ln in lines)


def test_incomplete_day_close_leaves_manifest_open(tmp_path, monkeypatch):
  """Not complete (age gate): the manifest must stay open."""
  day = "2020-02-03"
  daily, manifest = _zst_only_day_with_open_manifest(
    tmp_path, day, "verification_complete"
  )
  monkeypatch.setattr(
    qo, "_day_close_live_ingest_or_populate", lambda *a, **k: False
  )
  monkeypatch.setattr(qo.jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(qo.jr, "day_close_min_age_elapsed", lambda *a, **k: False)
  assert _run(daily, tmp_path, day) == "deferred_age"
  assert _phase(manifest) == "verification_complete"
