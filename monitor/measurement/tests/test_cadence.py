"""Cadence conf parser tests."""

from __future__ import annotations

from pathlib import Path

from lib.cadence import parse_monitor_conf

ROOT = Path(__file__).resolve().parents[1]


def test_parses_upstream_default_conf() -> None:
    text = (ROOT / "fixtures" / "hpcperfstats.conf").read_text(encoding="utf-8")
    parsed = parse_monitor_conf(text)
    assert parsed["sample_freq"] == 30.0
    assert parsed["sample_freq_slow"] == 600.0
    assert parsed["send_freq"] == 300.0
    assert parsed["enable_slow_tier"] == 1
    assert parsed["missing_keys"] == []
    assert parsed["matches_upstream_defaults"] is True


def test_freq_alias_used_only_when_sample_freq_absent() -> None:
    parsed = parse_monitor_conf("freq 45\nsend_freq 120\n")
    assert parsed["sample_freq"] == 45.0
    assert parsed["raw"]["sample_freq_alias"] == "freq"
    assert parsed["send_freq"] == 120.0
    assert "sample_freq_slow" in parsed["missing_keys"]


def test_sample_freq_wins_over_deprecated_freq() -> None:
    parsed = parse_monitor_conf("freq 45\nsample_freq 30\n")
    assert parsed["sample_freq"] == 30.0
    assert "sample_freq_alias" not in parsed["raw"]
