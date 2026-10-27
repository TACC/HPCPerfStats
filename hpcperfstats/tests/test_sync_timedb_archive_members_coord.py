"""Store-backed archive member coordination."""

from __future__ import annotations

from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
  ArchiveMembersKeys,
  ArchiveMembersStoreUnavailableError,
  is_transient_ingest_archive_coordination_error,
  lookup_full_members,
  members_cache_is_fully_warm,
  populate_archive_members,
  set_archive_day_ingest_skip,
  wait_for_member_match,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
  SyncTimedbArchiveMembersStore,
  set_process_archive_members_store,
)


def _keys(day: str = "2026-08-01") -> ArchiveMembersKeys:
  """
  Build a day/identity handle for unit tests.

  Args:
    day (str): ISO calendar day.

  Returns:
    ArchiveMembersKeys: Store handle.

  Examples:
    >>> _keys().day_token
    '2026-08-01'
  """
  suffix = f"{day}:none:none:none:none"
  return ArchiveMembersKeys(
    day_token=day,
    identity=suffix,
    hash_key=f"archive_members:{suffix}",
    complete_key=f"archive_members_complete:{suffix}",
    lock_key=f"archive_members_lock:{suffix}",
    dedupe_hint_key=f"archive_dedupe_hint:{day}",
    invalidate_pending_key=f"archive_members_invalidate:{suffix}",
  )


def test_populate_store_single_flight_and_warm_lookup(tmp_path):
  store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
  set_process_archive_members_store(store)
  keys = _keys()

  def _scan(on_member):
    on_member("host/1", 10)
    on_member("host/2", 20)
    return True, False

  members = populate_archive_members(keys, _scan)
  assert members == {"host/1": 10, "host/2": 20}
  assert members_cache_is_fully_warm(keys)
  assert lookup_full_members(keys) == members
  assert wait_for_member_match(keys, "host/1", 10) is True
  assert wait_for_member_match(keys, "missing", 1) is False


def test_members_cache_is_fully_warm_without_full_map_copy(
  tmp_path, monkeypatch
):
  """Warm gate must not call lookup_full_members (full dict copy) for a bool."""
  store = SyncTimedbArchiveMembersStore(str(tmp_path / "archive"))
  set_process_archive_members_store(store)
  keys = _keys("2026-09-01")
  store.store_complete(keys.day_token, keys.identity, {"host/1": 1})

  def _forbid_full_lookup(*_a, **_k):
    raise AssertionError("lookup_full_members must not run for warm bool")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.lookup_full_members",
    _forbid_full_lookup,
  )
  assert members_cache_is_fully_warm(keys) is True
  store.store_complete(keys.day_token, keys.identity, {})
  assert members_cache_is_fully_warm(keys) is False


def test_sticky_skip_survives_reload(tmp_path):
  archive = str(tmp_path / "archive")
  store = SyncTimedbArchiveMembersStore(archive)
  set_process_archive_members_store(store)
  keys = _keys("2026-08-02")
  set_archive_day_ingest_skip(keys, kind="read_error", detail="eof")
  revived = SyncTimedbArchiveMembersStore(archive)
  payload = revived.get_day_skip("2026-08-02")
  assert payload is not None
  assert payload["kind"] == "read_error"


def test_transient_ingest_archive_coordination_classifier():
  """Members-store unavailability is coordination, never a file defect."""
  assert (
    is_transient_ingest_archive_coordination_error(RuntimeError("x")) is False
  )
  assert (
    is_transient_ingest_archive_coordination_error(
      ArchiveMembersStoreUnavailableError(
        "transient defer open-tar repair during append inflight day=2026-01-01",
      ),
    )
    is True
  )
  # A11/DE-17: type-based; any members-store unavailability is transient.
  assert (
    is_transient_ingest_archive_coordination_error(
      ArchiveMembersStoreUnavailableError("store down after prewarm"),
    )
    is True
  )


# --- A4 (DE-6), A11 (DE-17/DE-20/DE-6b), G3 stale-flag re-validation ---

import errno  # noqa: E402
import tarfile  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def _members_store(tmp_path):
  """Install a process members store rooted in tmp_path."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    SyncTimedbArchiveMembersStore,
    set_process_archive_members_store,
  )

  store = SyncTimedbArchiveMembersStore(str(tmp_path / "members"))
  set_process_archive_members_store(store)
  yield store
  set_process_archive_members_store(None)


def _day_keys(sealed_or_tar):
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
    build_archive_members_keys,
  )

  canonical = helpers.normalize_daily_compressed_path(sealed_or_tar)
  return build_archive_members_keys(
    helpers._daily_archive_members_cache_key(canonical)
  )


def _sealed_only_day(tmp_path, day="2023-05-08", corrupt=False):
  src = tmp_path / "m.txt"
  src.write_text("x")
  gz = tmp_path / f"{day}.tar.gz"
  with tarfile.open(gz, "w:gz") as tf:
    tf.add(str(src), arcname="h/1")
  if corrupt:
    data = gz.read_bytes()
    gz.write_bytes(data[: len(data) // 2])
  return str(gz)


def _age_skip(store, day, seconds):
  shard = store._shard(day)
  with shard.lock:
    shard.day_skip = dict(shard.day_skip, set_at=time.time() - seconds)


def test_day_skip_clears_on_sealed_only_readable_day(tmp_path, _members_store):
  """DE-6: a stale skip on a readable sealed-only day clears (02 2023-05-08)."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz = _sealed_only_day(tmp_path)
  keys = _day_keys(gz)
  _members_store.set_day_skip(
    keys.day_token,
    kind="tar_truncated_or_unreadable",
    detail="unexpected end of data",
  )
  _members_store.set_degraded(keys.day_token)
  _age_skip(_members_store, keys.day_token, 2 * 86400)
  tar = helpers.daily_tar_path_from_compressed(gz)
  helpers._clear_stale_day_ingest_skip_if_tar_repaired(
    keys, tar, tar + ".zst", gz, gz
  )
  assert _members_store.get_day_skip(keys.day_token) is None


def test_degraded_and_skip_does_not_short_circuit_clear(
  tmp_path, _members_store
):
  """DE-6: degraded+skip no longer returns {} before the clear runs."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
    populate_degraded_is_set,
  )

  src = tmp_path / "m.txt"
  src.write_text("x")
  tar = tmp_path / "2026-09-30.tar"
  with tarfile.open(tar, "w") as tf:
    tf.add(str(src), arcname="h/1")
  keys = _day_keys(str(tar) + ".zst")
  _members_store.set_day_skip(
    keys.day_token, kind="open_tar_gnu_tvf_failed", detail="stale"
  )
  _members_store.set_degraded(keys.day_token)
  members = helpers.execute_archive_members_populate_for_canonical(
    str(tar) + ".zst"
  )
  assert _members_store.get_day_skip(keys.day_token) is None
  assert not populate_degraded_is_set(keys)
  assert members == {"h/1": 1}


def test_members_unavailable_any_message_is_transient():
  """DE-17: classification is by type, never by message text."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
    ArchiveMembersStoreUnavailableError,
  )
  from hpcperfstats.dbload.lib.sync_timedb_negative_states import (
    is_environment_error,
  )

  for msg in ("archive members lookup did not return members", "", "x"):
    exc = ArchiveMembersStoreUnavailableError(msg)
    assert is_transient_ingest_archive_coordination_error(exc)
    assert is_environment_error(exc)


def test_coord_defer_reaches_cooldown_not_quarantine(tmp_path):
  """A11: repeated coordination defers end in an env cooldown, file untouched."""
  from types import SimpleNamespace

  from hpcperfstats.dbload.lib import (
    sync_timedb_job_store as jq,
    sync_timedb_queue_orchestrator as qo,
  )

  raw = tmp_path / "h" / "1729700000"
  raw.parent.mkdir()
  raw.write_text("x")
  store = jq.SyncTimedbJobStore("")
  claim = SimpleNamespace(
    kind="ingest", identity=str(raw), owner_token="t", score=1.0
  )
  out = ""
  for _ in range(3):
    out = qo._requeue_bounded_without_attempt_bump(
      store,
      kind="ingest",
      claim=claim,
      counter="env_defer",
      cap=3,
      reason="env:ArchiveMembersStoreUnavailableError",
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
    )
  assert out == "dead_letter"
  entry = jq._dead_letter_index(str(tmp_path))[("ingest", str(raw))]
  assert entry["env"] and not entry["terminal"]
  assert raw.exists(), "a coordination miss never quarantines the file"


def test_permission_error_is_environment_not_file_fault():
  """DE-20: PermissionError / EAGAIN / ENOSPC / DB errors are environment."""
  from django.db.utils import OperationalError

  from hpcperfstats.dbload.lib.sync_timedb_negative_states import (
    environment_error_tag,
    is_environment_error,
  )

  assert is_environment_error(PermissionError(13, "denied"))
  assert is_environment_error(OSError(errno.ENOSPC, "full"))
  assert is_environment_error(BlockingIOError(errno.EAGAIN, "again"))
  assert is_environment_error(OperationalError("too many clients"))
  assert not is_environment_error(ValueError("bad line"))
  assert not is_environment_error(OSError(errno.ENOENT, "gone"))
  assert environment_error_tag(OSError(errno.ENOSPC, "full")) == "env:ENOSPC"


def test_eagain_zstd_spawn_does_not_set_day_skip(tmp_path, _members_store):
  """DE-6b: EAGAIN spawning zstd is transient, never a sticky day_skip."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz = _sealed_only_day(tmp_path, day="2026-05-05")
  keys = _day_keys(gz)
  err = BlockingIOError(
    errno.EAGAIN, "Resource temporarily unavailable: '/usr/local/bin/zstd'"
  )
  with pytest.raises(ArchiveMembersStoreUnavailableError):
    helpers.mark_archive_day_ingest_skip_and_raise(gz, keys, None, err)
  assert _members_store.get_day_skip(keys.day_token) is None


def test_stale_day_skip_revalidated_and_cleared(tmp_path, _members_store):
  """G3: only a flag older than the revalidate age triggers the (T2) check."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz = _sealed_only_day(tmp_path, day="2026-09-29")
  keys = _day_keys(gz)
  tar = helpers.daily_tar_path_from_compressed(gz)
  _members_store.set_day_skip(keys.day_token, kind="read_error", detail="x")
  helpers._clear_stale_day_ingest_skip_if_tar_repaired(
    keys, tar, tar + ".zst", gz, gz
  )
  assert _members_store.get_day_skip(keys.day_token) is not None, "fresh flag"
  _age_skip(_members_store, keys.day_token, 2 * 86400)
  helpers._clear_stale_day_ingest_skip_if_tar_repaired(
    keys, tar, tar + ".zst", gz, gz
  )
  assert _members_store.get_day_skip(keys.day_token) is None


def test_stale_flag_kept_when_gate_still_fails(tmp_path, _members_store):
  """G3: a still-unreadable archive keeps its flag and restarts the clock."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  gz = _sealed_only_day(tmp_path, day="2026-10-14", corrupt=True)
  keys = _day_keys(gz)
  tar = helpers.daily_tar_path_from_compressed(gz)
  _members_store.set_day_skip(keys.day_token, kind="read_error", detail="x")
  _age_skip(_members_store, keys.day_token, 2 * 86400)
  helpers._clear_stale_day_ingest_skip_if_tar_repaired(
    keys, tar, tar + ".zst", gz, gz
  )
  skip = _members_store.get_day_skip(keys.day_token)
  assert skip is not None
  assert time.time() - float(skip["set_at"]) < 60, "clock restarted"
