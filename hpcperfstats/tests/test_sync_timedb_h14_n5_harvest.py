"""H14/N5 classify wrappers and prune_day_phases_hints sealed-only days."""

from __future__ import annotations

import os

from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    _classify_removable_after_ok,
    classify_removable_raw_paths_for_daily_gz,
    classify_removable_raw_paths_for_open_tar,
    get_tar_member_name,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_maint import (
    prune_day_phases_hints,
)


def test_classify_empty_paths_returns_empty():
    assert classify_removable_raw_paths_for_daily_gz("/a.tar.zst", []) == []
    assert classify_removable_raw_paths_for_open_tar("/a.tar", []) == []
    assert _classify_removable_after_ok([], True, {}) == []
    assert _classify_removable_after_ok(["/p"], True, None)[0][1] == (
        "skipped_seal_invalid"
    )
    assert _classify_removable_after_ok(["/p"], False, {"x": 1})[0][1] == (
        "skipped_seal_invalid"
    )


def test_classify_skip_when_validate_fails(monkeypatch):
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.validate_sealed_daily_archive_for_raw_removal",
        lambda *_a, **_k: (False, None),
    )
    monkeypatch.setattr(
        "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.validate_open_tar_for_raw_removal",
        lambda *_a, **_k: (False, None),
    )
    paths = ["/raw/h/1"]
    gz = classify_removable_raw_paths_for_daily_gz("/a.tar.zst", paths)
    tar = classify_removable_raw_paths_for_open_tar("/a.tar", paths)
    assert gz == [
        ("/raw/h/1", "skipped_seal_invalid", "seal_validation_failed")
    ]
    assert tar == gz


def test_classify_with_provided_members_open_tar_vs_gz(tmp_path):
    raw = tmp_path / "host" / "1700000000"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"abcd")
    path = str(raw)
    member = get_tar_member_name(path)
    members = {member: 4}
    gz = classify_removable_raw_paths_for_daily_gz(
        str(tmp_path / "2026-01-01.tar.zst"),
        [path],
        sealed_members=members,
    )
    tar = classify_removable_raw_paths_for_open_tar(
        str(tmp_path / "2026-01-01.tar"),
        [path],
        open_tar_members=members,
    )
    assert gz == [(path, "verified", "")]
    assert tar == gz


def test_classify_not_in_archive_and_size_mismatch(tmp_path):
    raw = tmp_path / "host" / "1700000001"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"abcd")
    path = str(raw)
    member = get_tar_member_name(path)
    missing = classify_removable_raw_paths_for_open_tar(
        str(tmp_path / "x.tar"),
        [path],
        open_tar_members={"other": 4},
    )
    assert missing[0][1] == "skipped_not_in_archive"
    mismatch = classify_removable_raw_paths_for_daily_gz(
        str(tmp_path / "x.tar.zst"),
        [path],
        sealed_members={member: 99},
    )
    assert mismatch[0][1] == "skipped_size_mismatch"


def test_classify_ingest_ready_skip(tmp_path):
    raw = tmp_path / "host" / "1700000002"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"x")
    path = str(raw)
    member = get_tar_member_name(path)
    out = classify_removable_raw_paths_for_open_tar(
        str(tmp_path / "x.tar"),
        [path],
        open_tar_members={member: 1},
        ingest_ready_fn=lambda _p: False,
    )
    assert out[0][1] == "skipped_not_head_tail_ingested"


def test_prune_keeps_sealed_only_days(tmp_path):
    tar_path = str(tmp_path / "2026-06-04.tar")
    (tmp_path / "2026-06-04.tar.zst").write_bytes(b"z")
    pruned = prune_day_phases_hints({tar_path: "sealed"})
    assert pruned[tar_path] == "sealed"
    pruned_drop = prune_day_phases_hints({tar_path: "tar_dropped"})
    assert pruned_drop[tar_path] == "tar_dropped"
    pruned_raw = prune_day_phases_hints({tar_path: "raw_removed"})
    assert tar_path in pruned_raw


def test_prune_drops_when_no_sealed_sibling(tmp_path):
    tar_path = str(tmp_path / "2026-06-05.tar")
    pruned = prune_day_phases_hints({tar_path: "sealed"})
    assert tar_path not in pruned
    (tmp_path / "2026-06-05.tar.zst").write_bytes(b"z")
    pruned_unknown = prune_day_phases_hints({tar_path: "open"})
    assert tar_path not in pruned_unknown


def test_prune_keeps_string_phase_when_tar_fingerprint_present(tmp_path):
    tar_path = str(tmp_path / "2026-06-07.tar")
    (tmp_path / "2026-06-07.tar").write_bytes(b"tar")
    pruned = prune_day_phases_hints({tar_path: "sealed"})
    assert pruned[tar_path] == "sealed"
    tar_path = str(tmp_path / "2026-06-06.tar")
    (tmp_path / "2026-06-06.tar").write_bytes(b"tar")
    st = os.stat(tar_path)
    match = prune_day_phases_hints(
        {
            tar_path: {
                "phase": "sealed",
                "tar_mtime_ns": int(st.st_mtime_ns),
                "tar_size": int(st.st_size),
            },
        },
    )
    assert tar_path in match
    mismatch = prune_day_phases_hints(
        {
            tar_path: {
                "phase": "sealed",
                "tar_mtime_ns": 1,
                "tar_size": 1,
            },
        },
    )
    assert tar_path not in mismatch
    empty_phase = prune_day_phases_hints({tar_path: ""})
    assert tar_path not in empty_phase
    assert prune_day_phases_hints(None) == {}
    assert prune_day_phases_hints({}) == {}
