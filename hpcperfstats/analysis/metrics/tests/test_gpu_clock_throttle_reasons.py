"""Unit tests for DCGM clock-throttle reason display decode."""

from __future__ import annotations

from hpcperfstats.analysis.metrics.lib.gpu_clock_throttle_reasons import (
    format_gpu_clock_throttle_reasons,
)


def test_format_decodes_seven_as_idle_clocks_sw_power():
    assert format_gpu_clock_throttle_reasons(7) == (
        "GPU idle, Application clocks setting, SW power cap"
    )


def test_format_zero_and_none_empty():
    assert format_gpu_clock_throttle_reasons(0) == ""
    assert format_gpu_clock_throttle_reasons(None) == ""


def test_format_float_seven():
    assert format_gpu_clock_throttle_reasons(7.0) == (
        "GPU idle, Application clocks setting, SW power cap"
    )


def test_format_unknown_residual_hex():
    assert format_gpu_clock_throttle_reasons(0x200) == "unknown (0x200)"
    assert format_gpu_clock_throttle_reasons(0x201) == (
        "GPU idle, unknown (0x200)"
    )


def test_format_display_clocks_alone():
    assert format_gpu_clock_throttle_reasons(0x100) == "Display clocks setting"
