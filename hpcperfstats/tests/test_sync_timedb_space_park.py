"""Space-pressure deferrals park; the dead-letter list never evicts live entries.

hpcperfstats01 2026-11-13/14: under space pressure the open tars of 2026-10-18
(353 GB) and 2026-10-23 were unreadable and their repair was refused
(``env:ENOSPC``). Ingest on those days deferred 50 times in 12 s and was
dead-lettered (G1 ``env_defer`` cap); append returned a bare ``False`` that
``_fail_append_batch`` counted until 141 batches x 256 paths were
dead-lettered (40,118 entries in 9 h). The 5,000-entry dead-letter list then
kept only its newest entries, silently dropping live ingest cooldowns: 647
already-ingested files ended up with no mark, no queue entry and no dead
letter. The env schedule (6 h, 24 h, 72 h) would also have quarantined every
deferred raw file after about four days of pressure.
"""

from __future__ import annotations

from types import SimpleNamespace

import hpcperfstats.dbload.sync_timedb as st
from hpcperfstats.dbload.lib import (
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
  sync_timedb_space_pressure as sp,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
  OpenDailyTarEnsureResult,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
  ArchiveMembersStoreUnavailableError,
  ArchiveSpaceDeferredError,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


def _pressure(monkeypatch, on: bool) -> None:
  monkeypatch.setattr(sp, "under_pressure", lambda *_a, **_k: on)


def test_dead_letter_cap_never_evicts_live_entries(tmp_path):
  """Over the cap, live cooldown entries stay (the 647-orphan eviction)."""
  root = str(tmp_path)
  for i in range(5):
    jq.upsert_queue_dead_letter(
      root,
      kind="append",
      identity=f"/raw/h/{i}",
      attempt=5,
      reason="append_failed:false",
      max_entries=3,
      now=1000.0 + i,
    )
  kept = {e["identity"] for e in jq._load_dead_letter_entries(root)}
  assert kept == {f"/raw/h/{i}" for i in range(5)}


def test_dead_letter_cap_still_trims_terminal_entries_first(
  tmp_path, monkeypatch
):
  """Terminal entries are the only ones the cap removes."""
  from hpcperfstats.dbload.lib import conf_parser as cfg

  monkeypatch.setattr(
    cfg, "get_sync_dead_letter_readmit_backoff_s", lambda: [1]
  )
  root = str(tmp_path)
  for _ in range(2):  # second upsert passes the 1-cycle schedule: terminal
    jq.upsert_queue_dead_letter(
      root, kind="ingest", identity="/raw/t", attempt=5, reason="boom"
    )
  for i in range(3):
    jq.upsert_queue_dead_letter(
      root,
      kind="ingest",
      identity=f"/raw/live/{i}",
      attempt=5,
      reason="boom",
      max_entries=3,
    )
  kept = {e["identity"] for e in jq._load_dead_letter_entries(root)}
  assert kept == {f"/raw/live/{i}" for i in range(3)}


def test_space_entry_never_advances_cycle_or_turns_terminal(tmp_path):
  """A space park keeps the prior cycle and is never terminal."""
  root = str(tmp_path)
  first = jq.upsert_queue_dead_letter(
    root, kind="ingest", identity="/raw/s", attempt=50, reason="boom", now=1.0
  )
  assert first["cycle"] == 1
  for n in range(10):
    entry = jq.upsert_queue_dead_letter(
      root,
      kind="ingest",
      identity="/raw/s",
      attempt=0,
      reason=sp.SPACE_DEFER_REASON,
      space=True,
      now=10.0 + n,
    )
    assert entry["cycle"] == 1 and not entry["terminal"] and entry["space"]
  fresh = jq.upsert_queue_dead_letter(
    root,
    kind="append",
    identity="/raw/s2",
    attempt=0,
    reason=sp.SPACE_DEFER_REASON,
    space=True,
  )
  assert fresh["cycle"] == 0 and not fresh["terminal"]


def test_space_entry_blocked_and_held_until_pressure_clears(
  tmp_path, monkeypatch
):
  """Parked while under pressure; readmitted once pressure ends."""
  root = str(tmp_path)
  jq.upsert_queue_dead_letter(
    root,
    kind="ingest",
    identity="/raw/s",
    attempt=0,
    reason=sp.SPACE_DEFER_REASON,
    space=True,
    now=100.0,
  )
  _pressure(monkeypatch, True)
  assert jq.dead_letter_blocked(root, kind="ingest", identity="/raw/s")
  assert jq.due_dead_letter_readmissions(root) == []
  assert jq.space_parked_census_token(root) == " parked_space=1"
  _pressure(monkeypatch, False)
  assert not jq.dead_letter_blocked(root, kind="ingest", identity="/raw/s")
  due = jq.due_dead_letter_readmissions(root)
  assert [e["identity"] for e in due] == ["/raw/s"]
  jq.mark_dead_letters_readmitted(root, [("ingest", "/raw/s")])
  assert jq.due_dead_letter_readmissions(root) == []


def test_space_parked_census_token_empty_without_parks(tmp_path):
  """The census token is omitted when nothing is parked."""
  assert jq.space_parked_census_token(str(tmp_path)) == ""


def test_ingest_space_defer_parks_on_first_defer(monkeypatch, tmp_path):
  """Under pressure the first space defer parks: no requeue, no bump."""
  from hpcperfstats.tests.test_sync_timedb_queue_orchestrator import (
    _drain_one_ingest_failure,
  )

  _pressure(monkeypatch, True)
  identity, seen = _drain_one_ingest_failure(
    monkeypatch, tmp_path, ArchiveSpaceDeferredError(sp.SPACE_DEFER_REASON)
  )
  assert seen["requeues"] == [] and seen["bumps"] == []
  assert seen["acks"] == [identity]
  assert [k.get("space") for k in seen["letters"]] == [True]
  assert seen["letters"][0]["reason"] == sp.SPACE_DEFER_REASON


def test_ingest_space_defer_without_pressure_uses_bounded_requeue(
  monkeypatch, tmp_path
):
  """Below the hard reserve but not under pressure: the G1 path still runs."""
  from hpcperfstats.tests.test_sync_timedb_queue_orchestrator import (
    _drain_one_ingest_failure,
  )

  _pressure(monkeypatch, False)
  identity, seen = _drain_one_ingest_failure(
    monkeypatch, tmp_path, ArchiveSpaceDeferredError(sp.SPACE_DEFER_REASON)
  )
  assert seen["requeues"] == [identity]
  assert seen["letters"] == [] and seen["bumps"] == []


def test_ingest_open_tar_lookup_raises_typed_space_deferral(monkeypatch):
  """A space-refused repair raises the typed subclass; others do not."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as ah
  import hpcperfstats.dbload.lib.sync_timedb_archive_members_coord as coord

  monkeypatch.setattr(
    coord, "archive_append_inflight_for_day", lambda *_a: False
  )
  for reason, typed in ((sp.SPACE_DEFER_REASON, True), ("lease busy", False)):
    monkeypatch.setattr(
      ah,
      "ensure_open_daily_tar_readable",
      lambda *_a, _r=reason, **_k: OpenDailyTarEnsureResult(
        readable=False, transient=True, transient_reason=_r
      ),
    )
    try:
      ah._ensure_open_tar_readable_for_ingest_lookup(
        "/d/2026-10-18.tar",
        caller="ingest_membership",
        compressed_canonical="/d/2026-10-18.tar.zst",
      )
    except ArchiveMembersStoreUnavailableError as exc:
      assert isinstance(exc, ArchiveSpaceDeferredError) is typed, reason
    else:
      raise AssertionError("transient repair must raise")


def _unreadable_tar_append(tmp_path, monkeypatch, reason):
  raw = tmp_path / "1792316014"
  raw.write_text("1792316014 job1 cn001\n")
  archive_key = str(tmp_path / "2026-10-18.tar.zst")
  (tmp_path / "2026-10-18.tar").write_bytes(b"\0" * 20480)
  monkeypatch.setattr(st, "log_print", lambda *a, **k: None)
  monkeypatch.setattr(
    st, "filter_paths_head_ingested", lambda paths, **_: (list(paths), [])
  )

  def _scan_fails(*_a, **_k):
    raise RuntimeError("Unexpected EOF in archive")

  monkeypatch.setattr(
    st, "_lookup_existing_members_for_archive_append", _scan_fails
  )
  monkeypatch.setattr(
    st, "verify_tar_archive_readable", lambda *_a, **_k: False
  )
  monkeypatch.setattr(
    st,
    "ensure_open_daily_tar_readable",
    lambda *_a, **_k: OpenDailyTarEnsureResult(
      readable=False, transient=True, transient_reason=reason
    ),
  )
  return st._archive_stats_files_body((archive_key, [str(raw)]))


def test_append_unreadable_tar_space_deferral_is_soft_requeue(
  tmp_path, monkeypatch
):
  """A refused repair is a deferral, not a batch failure (bare False)."""
  outcome = _unreadable_tar_append(tmp_path, monkeypatch, sp.SPACE_DEFER_REASON)
  assert outcome is not False
  assert outcome.soft_requeue and outcome.space_deferred


def test_append_unreadable_tar_other_transient_is_plain_soft_requeue(
  tmp_path, monkeypatch
):
  """Other transient repair defers soft-requeue without the space flag."""
  outcome = _unreadable_tar_append(tmp_path, monkeypatch, "restore lease busy")
  assert outcome.soft_requeue and not outcome.space_deferred


def _drain_append(tmp_path, monkeypatch, outcome, n=3):
  parked, bounded = [], []
  monkeypatch.setattr(
    qo,
    "_dead_letter_claim",
    lambda *a, **k: parked.append((k["claim"].identity, k.get("space"))),
  )
  monkeypatch.setattr(
    qo,
    "_requeue_bounded_without_attempt_bump",
    lambda *a, **k: bounded.append(k["claim"].identity),
  )

  class _Ready:
    def ready(self):
      return True

    def get(self, timeout=0):
      return outcome

  client = SyncTimedbJobStore("")
  base = 1792316014
  for i in range(n):
    jq.enqueue_list_job(
      client, kind=jq.JOB_KIND_APPEND, identity=f"/raw/h/{base + i}"
    )
  claims = [
    jq.claim_list_job(
      client, kind=jq.JOB_KIND_APPEND, owner_token=jq.make_lease_owner_token()
    )
    for _ in range(n)
  ]
  qo._drain_append_ready(
    client,
    inflight={"/d/2026-10-18.tar": _Ready()},
    claims={"/d/2026-10-18.tar": claims},
    tgz_archive_dir="/d",
    archive_data_dir=str(tmp_path),
    log_fn=lambda *a, **k: None,
  )
  return parked, bounded


def test_append_space_deferral_parks_every_job_under_pressure(
  tmp_path, monkeypatch
):
  """Under pressure a space-deferred batch parks; no G1 counter is burned."""
  _pressure(monkeypatch, True)
  outcome = st.ArchiveAppendOutcome(
    ok=False, soft_requeue=True, space_deferred=True
  )
  parked, bounded = _drain_append(tmp_path, monkeypatch, outcome)
  assert bounded == []
  assert [s for _i, s in parked] == [True, True, True]


def test_append_space_deferral_without_pressure_stays_bounded(
  tmp_path, monkeypatch
):
  """Without pressure the existing bounded soft-requeue path runs."""
  _pressure(monkeypatch, False)
  outcome = st.ArchiveAppendOutcome(
    ok=False, soft_requeue=True, space_deferred=True
  )
  parked, bounded = _drain_append(tmp_path, monkeypatch, outcome)
  assert parked == [] and len(bounded) == 3


def test_space_park_resolves_no_terminal_quarantine(tmp_path, monkeypatch):
  """A parked claim is ACKed and never routed to terminal quarantine."""
  _pressure(monkeypatch, True)
  quarantined = []
  monkeypatch.setattr(
    qo,
    "_resolve_terminal_dead_letter",
    lambda *a, **k: quarantined.append(a),
  )
  store = SyncTimedbJobStore("")
  claim = SimpleNamespace(
    kind="ingest", identity="/raw/h/1792316014", owner_token="t", score=1.0
  )
  for _ in range(20):
    out = qo._dead_letter_claim(
      store,
      kind="ingest",
      claim=claim,
      archive_data_dir=str(tmp_path),
      reason=sp.SPACE_DEFER_REASON,
      attempt=0,
      space=True,
      log_fn=lambda *a, **k: None,
    )
    assert out == "dead_letter"
  assert quarantined == []
