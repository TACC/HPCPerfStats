"""S14: restore-fail log helper (append path; do not touch _append_to_tar)."""

from __future__ import annotations

from hpcperfstats.dbload import sync_timedb as st


def test_decompress_sealed_or_log_append_fail_false(monkeypatch):
    logs: list[str] = []
    monkeypatch.setattr(st, "_decompress_compressed_archive", lambda _p: False)
    monkeypatch.setattr(st, "log_print", lambda msg, **_k: logs.append(msg))
    assert (
        st._decompress_sealed_or_log_append_fail("/x.tar.zst", "sealed zst")
        is False
    )
    assert any(
        "could not restore daily tar from sealed zst" in line for line in logs
    )
    assert any("leaving raw stats files in place" in line for line in logs)


def test_decompress_sealed_or_log_append_fail_true(monkeypatch):
    logs: list[str] = []
    monkeypatch.setattr(st, "_decompress_compressed_archive", lambda _p: True)
    monkeypatch.setattr(st, "log_print", lambda msg, **_k: logs.append(msg))
    assert (
        st._decompress_sealed_or_log_append_fail("/x.tar.zst", "sealed zst")
        is True
    )
    assert logs == []
