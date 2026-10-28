"""Q5: retry/dead-letter helper and no-bump ingest fill-skip requeue."""

from __future__ import annotations

from hpcperfstats.dbload.lib import (
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
)


def test_retry_or_dead_letter_claim_none_empty_dir_and_score(monkeypatch):
  assert (
    qo._retry_or_dead_letter(
      None,
      kind="ingest",
      claim=None,
      archive_data_dir="/a",
      reason="x",
    )
    == "dropped_no_claim"
  )
  client = jq.SyncTimedbJobStore("")
  claim = jq.ClaimedJob(
    kind=jq.JOB_KIND_INGEST,
    identity="/p",
    owner_token="n:h:b:1",
    deadline=1060.0,
    score=5.0,
  )
  requeues: list[dict] = []
  monkeypatch.setattr(jq, "bump_job_attempt", lambda *_a, **_k: 1)
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 5)
  monkeypatch.setattr(
    jq,
    "requeue_job",
    lambda *_a, **k: requeues.append(dict(k)) or True,
  )
  assert (
    qo._retry_or_dead_letter(
      client,
      kind=jq.JOB_KIND_INGEST,
      claim=claim,
      archive_data_dir="/a",
      reason="x",
    )
    == "requeued"
  )
  assert requeues[0]["score"] == 5.0
  letters: list[int] = []
  monkeypatch.setattr(jq, "bump_job_attempt", lambda *_a, **_k: 99)
  monkeypatch.setattr(
    jq,
    "append_queue_dead_letter",
    lambda *_a, **_k: letters.append(1),
  )
  monkeypatch.setattr(jq, "ack_job", lambda *_a, **_k: True)
  assert (
    qo._retry_or_dead_letter(
      client,
      kind=jq.JOB_KIND_INGEST,
      claim=claim,
      archive_data_dir="",
      reason="x",
    )
    == "dead_letter"
  )
  assert letters == []
  claim_dc = jq.ClaimedJob(
    kind=jq.JOB_KIND_DAY_CLOSE,
    identity="2026-01-01.tar",
    owner_token="n:h:b:1",
    deadline=1060.0,
    score=None,
  )
  monkeypatch.setattr(jq, "bump_job_attempt", lambda *_a, **_k: 1)
  requeues.clear()
  assert (
    qo._retry_or_dead_letter(
      client,
      kind=jq.JOB_KIND_DAY_CLOSE,
      claim=claim_dc,
      archive_data_dir="/a",
      reason="x",
      score=99.0,
    )
    == "requeued"
  )
  assert requeues[0]["score"] == 99.0
  assert (
    qo._requeue_ingest_fill_skip(
      None,
      claim=None,
      archive_data_dir="/a",
      reason="skip_missing",
    )
    == "dropped_no_claim"
  )


def test_requeue_ingest_fill_skip_no_attempt_burn(monkeypatch):
  """Backpressure fill skips must not bump or dead-letter."""
  client = jq.SyncTimedbJobStore("")
  claim = jq.ClaimedJob(
    kind=jq.JOB_KIND_INGEST,
    identity="/bp",
    owner_token="n:h:b:1",
    deadline=1060.0,
    score=5.0,
  )
  bumps: list[int] = []
  requeues: list[dict] = []
  letters: list[str] = []

  def _bump(*_a, **_k):
    bumps.append(1)
    return 99

  monkeypatch.setattr(jq, "bump_job_attempt", _bump)
  monkeypatch.setattr(jq, "job_max_attempts", lambda: 5)
  monkeypatch.setattr(
    jq,
    "requeue_job",
    lambda *_a, **k: requeues.append(dict(k)) or True,
  )
  monkeypatch.setattr(
    jq,
    "append_queue_dead_letter",
    lambda *_a, **k: letters.append(str(k.get("reason"))),
  )
  for reason in (
    "skip_cgroup_headroom",
    "skip_budget_bytes",
    "skip_file_cache_pressure",
  ):
    bumps.clear()
    requeues.clear()
    letters.clear()
    assert (
      qo._requeue_ingest_fill_skip(
        client,
        claim=claim,
        archive_data_dir="/a",
        reason=reason,
        score=5.0,
      )
      == "requeued"
    )
    assert bumps == []
    assert letters == []
    assert len(requeues) == 1
    assert requeues[0]["identity"] == "/bp"
    assert requeues[0]["score"] == jq.penalize_ingest_score(5.0)
