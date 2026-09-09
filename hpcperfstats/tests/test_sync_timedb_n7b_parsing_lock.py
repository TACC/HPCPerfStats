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
