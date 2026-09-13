"""N7b: stats-file read-lock contextmanager + RC-0 resume still feeds prefix."""

from __future__ import annotations

import os

from hpcperfstats.dbload.lib import sync_timedb_parsing as parsing
from hpcperfstats.dbload.lib.file_locking import LOCK_SUFFIX
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    parse_stats_file_streaming,
    parse_stats_lines,
)
from hpcperfstats.tests.test_sync_timedb import _resume_schema_fixture_lines


def test_stats_file_read_lock_unlinks_sidecar(tmp_path):
    stats = tmp_path / "host" / "1"
    stats.parent.mkdir(parents=True)
    stats.write_text("1709123456 job1 cn001\n")
    with parsing._stats_file_read_lock(str(stats)):
        assert os.path.isfile(str(stats))
    assert not os.path.exists(str(stats) + LOCK_SUFFIX)


def test_stats_file_read_lock_missing_file():
    import pytest

    with pytest.raises(FileNotFoundError):
        with parsing._stats_file_read_lock("/no/such/stats/file"):
            pass


def test_stats_file_read_lock_unlink_oserror(tmp_path, monkeypatch):
    stats = tmp_path / "host" / "2"
    stats.parent.mkdir(parents=True)
    stats.write_text("x\n")
    real_remove = os.remove

    def _remove(path: str) -> None:
        if str(path).endswith(LOCK_SUFFIX):
            raise OSError("busy")
        real_remove(path)

    monkeypatch.setattr(parsing.os, "remove", _remove)
    with parsing._stats_file_read_lock(str(stats)):
        pass


def test_load_stats_file_lines_contents_bypass_skips_lock():
    lines, err = parsing.load_stats_file_lines(
        "/any", stats_file_contents=["a\n"]
    )
    assert err is None
    assert lines == ["a\n"]


def test_streaming_resume_feeds_schema_prefix(tmp_path):
    lines = _resume_schema_fixture_lines()
    stats_file = tmp_path / "host.example.com" / "1709123456"
    stats_file.parent.mkdir(parents=True)
    stats_file.write_text("".join(lines), encoding="utf-8")
    start_idx = 3
    expected_stats, expected_proc = parse_stats_lines(lines, start_idx)
    stream_stats, stream_proc = parse_stats_file_streaming(
        str(stats_file),
        start_line_idx=start_idx,
    )
    assert stream_stats == expected_stats
    assert stream_proc == expected_proc
    assert len(stream_stats) > 0


def _tracking_stats_lock(held):
    from contextlib import contextmanager

    @contextmanager
    def tracking_lock(_path):
        held["n"] += 1
        try:
            yield
        finally:
            held["n"] -= 1

    return tracking_lock


def test_iter_stats_lock_hold_yields_after_shared_lock(tmp_path, monkeypatch):
    stats = tmp_path / "host" / "1"
    stats.parent.mkdir(parents=True)
    stats.write_text("1709123456 job1 cn001\n1709123457 job1 cn001\n")
    held = {"n": 0}
    monkeypatch.setattr(
        parsing, "_stats_file_read_lock", _tracking_stats_lock(held),
    )
    lines = []
    for line in parsing.iter_stats_file_lines(str(stats)):
        assert held["n"] == 0
        lines.append(line)
    assert lines == ["1709123456 job1 cn001\n", "1709123457 job1 cn001\n"]


def test_feed_lines_lock_hold_outside_shared_lock(tmp_path, monkeypatch):
    from hpcperfstats.dbload.lib.sync_timedb_parsing import (
        IncrementalStatsParser,
    )
    from hpcperfstats.tests.test_sync_timedb import _resume_schema_fixture_lines

    stats = tmp_path / "host.example.com" / "1709123456"
    stats.parent.mkdir(parents=True)
    stats.write_text("".join(_resume_schema_fixture_lines()), encoding="utf-8")
    held = {"n": 0}
    monkeypatch.setattr(
        parsing, "_stats_file_read_lock", _tracking_stats_lock(held),
    )
    real_feed = IncrementalStatsParser.feed_lines

    def wrapped(self, batch):
        assert held["n"] == 0
        return real_feed(self, batch)

    monkeypatch.setattr(IncrementalStatsParser, "feed_lines", wrapped)
    stats_list, proc_list = parse_stats_file_streaming(str(stats))
    assert len(stats_list) > 0


def test_tail_parse_lock_hold_after_byte_snapshot(tmp_path, monkeypatch):
    stats = tmp_path / "host" / "1"
    stats.parent.mkdir(parents=True)
    stats.write_text("1709123456 job1 cn001\n1709123457 job1 cn002\n")
    held = {"n": 0}
    monkeypatch.setattr(
        parsing, "_stats_file_read_lock", _tracking_stats_lock(held),
    )
    real_ident = parsing._digit_line_identity

    def wrapped(s):
        assert held["n"] == 0
        return real_ident(s)

    monkeypatch.setattr(parsing, "_digit_line_identity", wrapped)
    parsed = parsing.parse_last_timestamp_line_streaming(str(stats))
    assert parsed == ("1709123457", "job1", "cn002")
    collected = parsing._collect_tail_timestamp_lines(str(stats), max_lines=2)
    assert collected
    assert held["n"] == 0


def test_on_chunk_lock_hold_outside_shared_lock(tmp_path, monkeypatch):
    from hpcperfstats.dbload.lib.sync_timedb_parsing import (
        parse_stats_file_streaming_incremental,
    )
    from hpcperfstats.tests.test_sync_timedb import _resume_schema_fixture_lines

    stats = tmp_path / "host.example.com" / "1709123456"
    stats.parent.mkdir(parents=True)
    stats.write_text("".join(_resume_schema_fixture_lines()), encoding="utf-8")
    held = {"n": 0}
    monkeypatch.setattr(
        parsing, "_stats_file_read_lock", _tracking_stats_lock(held),
    )
    chunks = []

    def on_chunk(stats_rows, proc_rows):
        assert held["n"] == 0
        chunks.append((list(stats_rows), list(proc_rows)))

    parse_stats_file_streaming_incremental(
        str(stats),
        flush_rows=1,
        on_chunk=on_chunk,
        line_batch_size=1,
    )
    assert chunks


def test_sealed_on_member_lock_hold_after_shared_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

    path = tmp_path / "2026-01-01.tar.zst"
    path.write_bytes(b"not-a-real-archive")
    held = {"n": 0}

    @contextmanager
    def tracking_lock(_p):
        held["n"] += 1
        try:
            yield
        finally:
            held["n"] -= 1

    class FakeMember:
        def __init__(self, name, size):
            self.name = name
            self.size = size

        def isfile(self):
            return True

    @contextmanager
    def fake_open(*_a, **_k):
        yield object()

    monkeypatch.setattr(helpers, "_archive_file_read_lock_wait", tracking_lock)
    monkeypatch.setattr(helpers, "_open_tarfile_for_read", fake_open)
    monkeypatch.setattr(
        helpers, "_iter_tar_members", lambda _tf: [FakeMember("h/1", 4)],
    )
    monkeypatch.setattr(helpers, "detect_compressed_format", lambda _p: "zst")
    seen = []

    def on_member(name, size):
        assert held["n"] == 0
        seen.append((name, size))

    readable, members, _dups, err = helpers._stream_compressed_archive_members(
        str(path), on_member, defer_on_member=True,
    )
    assert err is None
    assert readable is True
    assert seen == [("h/1", 4)]
    assert members == {"h/1": 4}


def test_sealed_on_member_lock_hold_in_stream_early_exit(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

    path = tmp_path / "2026-01-01.tar.zst"
    path.write_bytes(b"not-a-real-archive")
    held = {"n": 0}

    @contextmanager
    def tracking_lock(_p):
        held["n"] += 1
        try:
            yield
        finally:
            held["n"] -= 1

    class FakeMember:
        def __init__(self, name, size):
            self.name = name
            self.size = size

        def isfile(self):
            return True

    @contextmanager
    def fake_open(*_a, **_k):
        yield object()

    monkeypatch.setattr(helpers, "_archive_file_read_lock_wait", tracking_lock)
    monkeypatch.setattr(helpers, "_open_tarfile_for_read", fake_open)
    monkeypatch.setattr(
        helpers, "_iter_tar_members", lambda _tf: [FakeMember("h/1", 4)],
    )
    monkeypatch.setattr(helpers, "detect_compressed_format", lambda _p: "zst")

    def on_member(_name, _size):
        assert held["n"] == 1
        raise helpers._MemberStreamEarlyExit()

    try:
        helpers._stream_compressed_archive_members(str(path), on_member)
        raise AssertionError("expected _MemberStreamEarlyExit")
    except helpers._MemberStreamEarlyExit:
        pass
