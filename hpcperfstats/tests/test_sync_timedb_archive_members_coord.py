"""Store-backed archive member coordination."""
from __future__ import annotations

from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
    ArchiveMembersKeys,
    populate_archive_members,
    lookup_full_members,
    members_cache_is_fully_warm,
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
    suffix = "%s:none:none:none:none" % day
    return ArchiveMembersKeys(
        day_token=day,
        identity=suffix,
        hash_key="archive_members:%s" % suffix,
        complete_key="archive_members_complete:%s" % suffix,
        lock_key="archive_members_lock:%s" % suffix,
        dedupe_hint_key="archive_dedupe_hint:%s" % day,
        invalidate_pending_key="archive_members_invalidate:%s" % suffix,
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
