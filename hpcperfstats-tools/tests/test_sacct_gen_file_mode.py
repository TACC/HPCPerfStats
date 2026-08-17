"""Tests for sacct_gen -f directory file-write mode."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hpcperfstats_tools.sacct_gen import main, write_accounting_daily_file


def test_write_accounting_daily_file_writes_yyyy_mm_dd_txt(tmp_path):
    path = write_accounting_daily_file(str(tmp_path), "2024-06-15", "JobID|User\n1|alice\n")
    assert path == str(tmp_path / "2024-06-15.txt")
    assert (tmp_path / "2024-06-15.txt").read_text(encoding="utf-8") == "JobID|User\n1|alice\n"


def test_main_file_mode_rejects_non_directory(tmp_path, capsys):
    missing = tmp_path / "nope"
    with pytest.raises(SystemExit) as exc_info:
        main(["-f", str(missing), "2024-01-01", "2024-01-01"])
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "not a directory" in err
    assert str(missing) in err


def test_main_file_mode_rejects_file_path(tmp_path, capsys):
    not_dir = tmp_path / "file.txt"
    not_dir.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as exc_info:
        main(["-f", str(not_dir), "2024-01-01", "2024-01-01"])
    assert exc_info.value.code == 1
    assert "not a directory" in capsys.readouterr().err


def test_main_file_mode_mutex_with_api_key(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["-f", str(tmp_path), "--api-key", "secret", "2024-01-01", "2024-01-01"])
    assert exc_info.value.code == 2  # argparse error
    err = capsys.readouterr().err
    assert "not allowed with" in err or "mutually exclusive" in err.lower()


def test_main_file_mode_writes_daily_files_without_api(tmp_path, capsys):
    payload = b"JobID|User\n42|bob\n"
    with patch(
        "hpcperfstats_tools.sacct_gen.run_sacct_for_date",
        return_value=("2024-01-01", payload),
    ) as mock_sacct, patch(
        "hpcperfstats_tools.sacct_gen.get_api_base_url"
    ) as mock_base, patch(
        "hpcperfstats_tools.sacct_gen.send_to_api"
    ) as mock_send:
        main(["-f", str(tmp_path), "2024-01-01", "2024-01-01"])

    mock_sacct.assert_called_once()
    mock_base.assert_not_called()
    mock_send.assert_not_called()
    out_file = tmp_path / "2024-01-01.txt"
    assert out_file.is_file()
    assert out_file.read_text(encoding="utf-8") == "JobID|User\n42|bob\n"
    assert "2024-01-01: wrote" in capsys.readouterr().out


def test_main_file_mode_skips_failed_sacct(tmp_path, capsys):
    with patch(
        "hpcperfstats_tools.sacct_gen.run_sacct_for_date",
        return_value=("2024-01-01", None),
    ):
        main(["-f", str(tmp_path), "2024-01-01", "2024-01-01"])
    assert not list(tmp_path.glob("*.txt"))
    assert "sacct failed for 2024-01-01" in capsys.readouterr().err


def test_main_file_mode_skips_header_only_no_job_rows(tmp_path, capsys):
    """sacct -P still prints a header when there are no jobs; do not write the day."""
    with patch(
        "hpcperfstats_tools.sacct_gen.run_sacct_for_date",
        return_value=("2024-01-01", b"JobID|User\n"),
    ):
        main(["-f", str(tmp_path), "2024-01-01", "2024-01-01"])
    assert not list(tmp_path.glob("*.txt"))
    out = capsys.readouterr().out
    assert "no jobs" in out.lower() or "skipped" in out.lower()
    assert "wrote" not in out


def test_main_file_mode_skips_empty_sacct_output(tmp_path, capsys):
    with patch(
        "hpcperfstats_tools.sacct_gen.run_sacct_for_date",
        return_value=("2024-01-01", b""),
    ):
        main(["-f", str(tmp_path), "2024-01-01", "2024-01-01"])
    assert not list(tmp_path.glob("*.txt"))


def test_sacct_body_has_job_rows():
    from hpcperfstats_tools.sacct_gen import sacct_body_has_job_rows

    assert sacct_body_has_job_rows("") is False
    assert sacct_body_has_job_rows("JobID|User\n") is False
    assert sacct_body_has_job_rows("JobID|User\n\n") is False
    assert sacct_body_has_job_rows("JobID|User\n1|alice\n") is True


def test_daterange_cli_inclusive_end():
    from datetime import datetime

    from hpcperfstats_tools.sacct_gen import _daterange

    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 4)
    assert [d.strftime("%Y-%m-%d") for d in _daterange(start, end)] == [
        "2024-01-01",
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
    ]
    assert [d.strftime("%Y-%m-%d") for d in _daterange(start, start)] == [
        "2024-01-01",
    ]
    assert [
        d.strftime("%Y-%m-%d")
        for d in _daterange(start, end, inclusive_end=False)
    ] == [
        "2024-01-01",
        "2024-01-02",
        "2024-01-03",
    ]


def test_main_file_mode_default_end_is_start_day(tmp_path, capsys):
    """Omitting end_date processes only start_date (inclusive default)."""
    payload = b"JobID|User\n7|carol\n"
    with patch(
        "hpcperfstats_tools.sacct_gen.run_sacct_for_date",
        return_value=("2024-01-01", payload),
    ) as mock_sacct:
        main(["-f", str(tmp_path), "2024-01-01"])

    mock_sacct.assert_called_once()
    assert (tmp_path / "2024-01-01.txt").is_file()
    assert not (tmp_path / "2024-01-02.txt").exists()


def test_main_file_mode_respects_date_range(tmp_path):
    calls = []

    def fake_sacct(single_date):
        date_str = single_date.strftime("%Y-%m-%d")
        calls.append(date_str)
        return date_str, b"JobID|User\n1|alice\n"

    with patch("hpcperfstats_tools.sacct_gen.run_sacct_for_date", side_effect=fake_sacct):
        main(["-f", str(tmp_path), "2024-01-01", "2024-01-04"])

    assert calls == ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
    assert sorted(p.name for p in tmp_path.glob("*.txt")) == [
        "2024-01-01.txt",
        "2024-01-02.txt",
        "2024-01-03.txt",
        "2024-01-04.txt",
    ]
