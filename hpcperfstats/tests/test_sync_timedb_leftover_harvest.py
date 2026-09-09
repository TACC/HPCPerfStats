"""Absence tests for deleted leftovers; keep _transition_file_state."""

from __future__ import annotations

import inspect

import hpcperfstats.dbload.sync_timedb as st
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo


def test_leftover_unused_defs_are_absent():
    """Deleted leftovers must not remain as product defs (cov-ex-6)."""
    st_src = inspect.getsource(st)
    qo_src = inspect.getsource(qo)
    for name in (
        "_handle_ingest_worker_memory_after_imap",
        "_archive_task_succeeded",
        "_ingest_remaining_count",
    ):
        assert f"def {name}" not in st_src
        assert not hasattr(st, name)
    assert "def _renew_active_claims" not in qo_src
    assert not hasattr(qo, "_renew_active_claims")


def test_transition_file_state_still_defined():
    assert hasattr(st, "_transition_file_state")
    assert "def _transition_file_state(" in inspect.getsource(st)


def test_transition_sets_state_when_path_unknown():
    states = {}
    assert (
        st._transition_file_state(
            states,
            "/raw/a",
            st.SyncFileState.DISCOVERED,
        )
        is True
    )
    assert states["/raw/a"] == st.SyncFileState.DISCOVERED


def test_transition_archive_queued_to_written_allowed():
    states = {"/raw/a": st.SyncFileState.ARCHIVE_QUEUED}
    assert (
        st._transition_file_state(
            states,
            "/raw/a",
            st.SyncFileState.WRITTEN,
        )
        is True
    )
    assert states["/raw/a"] == st.SyncFileState.WRITTEN


def test_transition_archived_to_written_allowed_for_reingest():
    states = {"/raw/a": st.SyncFileState.ARCHIVED}
    assert (
        st._transition_file_state(
            states,
            "/raw/a",
            st.SyncFileState.WRITTEN,
        )
        is True
    )
    assert states["/raw/a"] == st.SyncFileState.WRITTEN


def test_transition_archived_to_archive_queued_is_idempotent():
    states = {"/raw/a": st.SyncFileState.ARCHIVED}
    assert (
        st._transition_file_state(
            states,
            "/raw/a",
            st.SyncFileState.ARCHIVE_QUEUED,
        )
        is True
    )
    assert states["/raw/a"] == st.SyncFileState.ARCHIVED


def test_transition_same_state_is_allowed():
    states = {"/raw/a": st.SyncFileState.WRITTEN}
    assert (
        st._transition_file_state(
            states,
            "/raw/a",
            st.SyncFileState.WRITTEN,
        )
        is True
    )
    assert states["/raw/a"] == st.SyncFileState.WRITTEN


def test_transition_allowed_map_discovers_to_written():
    states = {"/raw/a": st.SyncFileState.DISCOVERED}
    assert (
        st._transition_file_state(
            states,
            "/raw/a",
            st.SyncFileState.WRITTEN,
        )
        is True
    )
    assert states["/raw/a"] == st.SyncFileState.WRITTEN


def test_transition_still_rejects_discovered_to_archived(capsys):
    states = {"/raw/a": st.SyncFileState.DISCOVERED}
    assert (
        st._transition_file_state(
            states,
            "/raw/a",
            st.SyncFileState.ARCHIVED,
        )
        is False
    )
    assert states["/raw/a"] == st.SyncFileState.DISCOVERED
    out = capsys.readouterr().out
    assert "Invalid sync_timedb file state transition" in out
    assert "path=/raw/a" in out


def test_transition_unknown_current_state_uses_empty_allowed(capsys):
    """PARSED is an enum member but is not in ``_SYNC_STATE_TRANSITIONS``."""
    states = {"/raw/a": st.SyncFileState.PARSED}
    assert (
        st._transition_file_state(
            states,
            "/raw/a",
            st.SyncFileState.WRITTEN,
        )
        is False
    )
    assert states["/raw/a"] == st.SyncFileState.PARSED
    assert (
        "Invalid sync_timedb file state transition" in capsys.readouterr().out
    )
