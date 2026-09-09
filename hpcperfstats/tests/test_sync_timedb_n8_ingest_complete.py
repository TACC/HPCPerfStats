"""N8: 100% branch coverage of ingest_is_complete."""

from __future__ import annotations

from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as reconstruct


def test_empty_and_whitespace_false():
    assert reconstruct.ingest_is_complete("") is False
    assert reconstruct.ingest_is_complete("   ") is False
    assert reconstruct.ingest_is_complete(None) is False


def test_injectable_file_complete_or_zero_host_true():
    assert (
        reconstruct.ingest_is_complete(
            "/x",
            listend_enabled=True,
            has_file_complete_fn=lambda _p: True,
            has_zero_host_fn=lambda _p: False,
            head_tail_ready_fn=lambda _p: False,
        )
        is True
    )
    assert (
        reconstruct.ingest_is_complete(
            "/x",
            listend_enabled=True,
            has_file_complete_fn=lambda _p: False,
            has_zero_host_fn=lambda _p: True,
            head_tail_ready_fn=lambda _p: False,
        )
        is True
    )


def test_live_on_ignores_head_tail_without_marks():
    assert (
        reconstruct.ingest_is_complete(
            "/x",
            listend_enabled=True,
            has_file_complete_fn=lambda _p: False,
            has_zero_host_fn=lambda _p: False,
            head_tail_ready_fn=lambda _p: True,
        )
        is False
    )


def test_live_off_uses_head_tail_injectable():
    assert (
        reconstruct.ingest_is_complete(
            "/x",
            listend_enabled=False,
            has_file_complete_fn=lambda _p: False,
            has_zero_host_fn=lambda _p: False,
            head_tail_ready_fn=lambda _p: True,
        )
        is True
    )
    assert (
        reconstruct.ingest_is_complete(
            "/x",
            listend_enabled=False,
            has_file_complete_fn=lambda _p: False,
            has_zero_host_fn=lambda _p: False,
            head_tail_ready_fn=lambda _p: False,
        )
        is False
    )


def test_default_mark_imports(monkeypatch):
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_file_complete_ingest_mark.has_file_complete_ingest_mark",
        lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_zero_host_ingest_mark.has_zero_host_ingest_mark",
        lambda *_a, **_k: False,
    )
    assert reconstruct.ingest_is_complete("/x", listend_enabled=True) is True

    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_file_complete_ingest_mark.has_file_complete_ingest_mark",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_zero_host_ingest_mark.has_zero_host_ingest_mark",
        lambda *_a, **_k: True,
    )
    assert reconstruct.ingest_is_complete("/x", listend_enabled=True) is True


def test_default_head_tail_import_when_live_off(monkeypatch):
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_file_complete_ingest_mark.has_file_complete_ingest_mark",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_zero_host_ingest_mark.has_zero_host_ingest_mark",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_ingest_readiness.stats_file_head_ingested_in_db",
        lambda _p: True,
    )
    assert reconstruct.ingest_is_complete("/x", listend_enabled=False) is True
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_ingest_readiness.stats_file_head_ingested_in_db",
        lambda _p: False,
    )
    assert reconstruct.ingest_is_complete("/x", listend_enabled=False) is False


def test_default_listend_enabled_none(monkeypatch):
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_file_complete_ingest_mark.has_file_complete_ingest_mark",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_zero_host_ingest_mark.has_zero_host_ingest_mark",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        reconstruct,
        "_default_listend_enabled",
        lambda: True,
    )
    assert reconstruct.ingest_is_complete("/x") is False
    monkeypatch.setattr(
        reconstruct,
        "_default_listend_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_ingest_readiness.stats_file_head_ingested_in_db",
        lambda _p: True,
    )
    assert reconstruct.ingest_is_complete("/x") is True
