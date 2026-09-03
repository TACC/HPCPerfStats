"""job store hardening tests retired with the sync_timedb Redis cutover."""
from __future__ import annotations


def test_archive_members_client_retired_in_favor_of_store():
    """
    The archive-members job store is gone; the process store is required.

    Returns:
      None

    Examples:
      >>> test_archive_members_client_retired_in_favor_of_store()
    """
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
        SyncTimedbArchiveMembersStore,
        require_process_archive_members_store,
        set_process_archive_members_store,
    )

    store = SyncTimedbArchiveMembersStore("/tmp/empty")
    set_process_archive_members_store(store)
    try:
        assert require_process_archive_members_store() is store
    finally:
        set_process_archive_members_store(None)
