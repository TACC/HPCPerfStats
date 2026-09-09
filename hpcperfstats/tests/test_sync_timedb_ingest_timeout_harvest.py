"""N9: 100% branch coverage of ingest-timeout shells and helpers."""

from __future__ import annotations

from datetime import date

from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ingest_timeout


def test_always_zero_public_shells():
    assert ingest_timeout.resolve_ingest_per_file_timeout_s("/x") == 0.0
    assert (
        ingest_timeout.resolve_ingest_per_file_timeout_for_size_bytes(1 << 30)
        == 0.0
    )
    assert (
        ingest_timeout.resolve_ingest_per_file_timeout_for_size_bytes(0, base=9)
        == 0.0
    )
    assert (
        ingest_timeout.max_ingest_per_file_timeout_for_paths(["/a", "/b"])
        == 0.0
    )
    assert ingest_timeout.stall_abort_polls_for_paths(["/a"]) == 0
    assert (
        ingest_timeout.stall_abort_polls_for_sealed_archives(["/a.tar.zst"])
        == 0
    )
    assert (
        ingest_timeout.stall_abort_polls_for_sealed_archives(
            ["/a"],
            member_counts={"/a": 3},
        )
        == 0
    )
    assert ingest_timeout.is_giant_ingest_budget("/x", trigger_s=0.0) is False
    assert ingest_timeout.is_giant_ingest_budget("/x", trigger_s=-1) is False
    assert ingest_timeout.is_giant_ingest_budget("/x") is False


def test_default_giant_supplement_trigger_budget_s(monkeypatch):
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_sync_ingest_per_file_timeout_s_per_mib",
        lambda: 2.0,
    )
    assert (
        ingest_timeout.default_giant_supplement_trigger_budget_s()
        == 900.0 + 2048.0 * 2.0
    )


def test_calendar_day_iso_and_fallbacks(monkeypatch):
    assert ingest_timeout.calendar_day_from_sealed_archive_path("") == ""
    assert ingest_timeout.calendar_day_from_sealed_archive_path(
        "/a/2026-01-02.tar.zst"
    ) == ("2026-01-02")
    assert (
        ingest_timeout.calendar_day_from_sealed_archive_path(
            "/a/2026-13-40.tar.zst"
        )
        == ""
    )
    assert (
        ingest_timeout.calendar_day_from_sealed_archive_path(
            "/a/notiso.tar.zst"
        )
        == ""
    )

    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.parse_archive_date_from_daily_gz_path",
        lambda _p: date(2026, 3, 4),
    )
    assert ingest_timeout.calendar_day_from_sealed_archive_path(
        "/a/notiso.tar.zst"
    ) == ("2026-03-04")


def test_store_member_count_empty_and_no_dir(monkeypatch):
    assert ingest_timeout._store_member_count_for_sealed_day("") == 0
    assert ingest_timeout._store_member_count_for_sealed_day(None) == 0
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_daily_archive_dir_path",
        lambda: "",
    )
    assert ingest_timeout._store_member_count_for_sealed_day("2026-01-01") == 0


def test_store_member_count_success_none_and_errors(monkeypatch):
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_daily_archive_dir_path",
        lambda: "/daily",
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.archive_compress.daily_compressed_path_for_date",
        lambda *_a, **_k: "/daily/2026-01-01.tar.zst",
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_helpers._daily_archive_members_cache_key",
        lambda *_a, **_k: "k",
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.normalize_daily_compressed_path",
        lambda p: p,
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.build_archive_members_keys",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.lookup_full_members",
        lambda *_a, **_k: {"a": 1, "b": 2},
    )
    assert ingest_timeout._store_member_count_for_sealed_day("2026-01-01") == 2

    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.lookup_full_members",
        lambda *_a, **_k: None,
    )
    assert ingest_timeout._store_member_count_for_sealed_day("2026-01-01") == 0

    def _boom(*_a, **_k):
        raise OSError("store down")

    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.lookup_full_members",
        _boom,
    )
    assert ingest_timeout._store_member_count_for_sealed_day("2026-01-01") == 0
    assert ingest_timeout._store_member_count_for_sealed_day("not-a-date") == 0
    assert ingest_timeout._store_member_count_for_sealed_day(123) == 0


def test_sealed_member_count_hint_branches(tmp_path, monkeypatch):
    missing = str(tmp_path / "nope.tar.zst")
    assert (
        ingest_timeout.sealed_archive_member_count_hint(
            missing, member_count="nope"
        )
        == 1
    )
    assert (
        ingest_timeout.sealed_archive_member_count_hint(missing, member_count=0)
        == 1
    )
    assert (
        ingest_timeout.sealed_archive_member_count_hint(missing, member_count=7)
        == 7
    )

    monkeypatch.setattr(
        ingest_timeout,
        "_store_member_count_for_sealed_day",
        lambda _d: 4,
    )
    assert (
        ingest_timeout.sealed_archive_member_count_hint(
            missing, member_count=None
        )
        == 4
    )

    monkeypatch.setattr(
        ingest_timeout,
        "_store_member_count_for_sealed_day",
        lambda _d: 0,
    )
    sealed = tmp_path / "2026-01-01.tar.zst"
    sealed.write_bytes(b"x" * (64 * 1024 * 1024))
    hint = ingest_timeout.sealed_archive_member_count_hint(
        str(sealed), member_count=None
    )
    assert (
        hint
        == (64 * 1024 * 1024) // ingest_timeout._TYPICAL_SEALED_MEMBER_BYTES
    )

    monkeypatch.setattr(
        ingest_timeout.os.path,
        "getsize",
        lambda _p: (_ for _ in ()).throw(OSError("gone")),
    )
    assert (
        ingest_timeout.sealed_archive_member_count_hint(
            str(sealed), member_count=None
        )
        == 1
    )


def test_estimate_and_max_sealed_budget_when_floor_positive(
    tmp_path, monkeypatch
):
    sealed = tmp_path / "2026-01-01.tar.zst"
    sealed.write_bytes(b"x" * 100)
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_sync_ingest_per_file_timeout_s",
        lambda: 10.0,
    )
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_sync_ingest_per_file_timeout_max_s",
        lambda: 100.0,
    )
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_sync_pool_poll_timeout_s",
        lambda: 1.0,
    )
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_sync_pool_stall_abort_after_timeouts",
        lambda: 5,
    )
    assert (
        ingest_timeout.estimate_sealed_archive_ingest_budget_s(str(sealed))
        == 10.0
    )
    assert (
        ingest_timeout.estimate_sealed_archive_ingest_budget_s(
            str(sealed),
            member_count=2,
        )
        == 10.0
    )

    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_sync_ingest_per_file_timeout_max_s",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_sync_pool_poll_timeout_s",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_sync_pool_stall_abort_after_timeouts",
        lambda: 0,
    )
    monkeypatch.setattr(
        ingest_timeout.os.path,
        "getsize",
        lambda _p: (_ for _ in ()).throw(OSError("gone")),
    )
    assert (
        ingest_timeout.estimate_sealed_archive_ingest_budget_s(str(sealed))
        == 10.0
    )

    monkeypatch.setattr(ingest_timeout.os.path, "getsize", lambda _p: 100)
    assert (
        ingest_timeout.max_sealed_archive_ingest_budget_for_paths(None) == 10.0
    )
    assert (
        ingest_timeout.max_sealed_archive_ingest_budget_for_paths(["", None])
        == 10.0
    )
    assert (
        ingest_timeout.max_sealed_archive_ingest_budget_for_paths(
            [str(sealed)],
            member_counts={str(sealed): 3},
        )
        == 10.0
    )


def test_max_sealed_updates_best_when_estimate_exceeds_floor(monkeypatch):
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_sync_ingest_per_file_timeout_s",
        lambda: 10.0,
    )

    def _fake_estimate(path: str, *, member_count=None):
        del member_count
        return 50.0 if "big" in path else 10.0

    monkeypatch.setattr(
        ingest_timeout,
        "estimate_sealed_archive_ingest_budget_s",
        _fake_estimate,
    )
    assert (
        ingest_timeout.max_sealed_archive_ingest_budget_for_paths(
            ["/small", "/big"],
        )
        == 50.0
    )


def test_estimate_zero_floor(monkeypatch):
    monkeypatch.setattr(
        ingest_timeout.cfg,
        "get_sync_ingest_per_file_timeout_s",
        lambda: 0.0,
    )
    assert ingest_timeout.estimate_sealed_archive_ingest_budget_s("/x") == 0.0
    assert (
        ingest_timeout.max_sealed_archive_ingest_budget_for_paths(["/x"]) == 0.0
    )
