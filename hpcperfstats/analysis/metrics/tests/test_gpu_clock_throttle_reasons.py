"""Unit tests for DCGM clock-throttle reason display decode."""

from __future__ import annotations

from hpcperfstats.analysis.metrics.lib.gpu_clock_throttle_reasons import (
    format_gpu_clock_throttle_reasons,
)
from hpcperfstats.lib.dcgm_blank import DCGM_INT64_BLANK


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


def test_format_unknown_residual_alone_empty():
  """Garbage with no known bits (e.g. 0x6b48c000) must not print unknown hex."""
  assert format_gpu_clock_throttle_reasons(0x200) == ""
  assert format_gpu_clock_throttle_reasons(0x6B48C000) == ""


def test_format_known_plus_garbage_drops_unknown():
  assert format_gpu_clock_throttle_reasons(0x201) == "GPU idle"


def test_format_blank_int64_empty():
  assert format_gpu_clock_throttle_reasons(DCGM_INT64_BLANK) == ""
  assert format_gpu_clock_throttle_reasons(float(DCGM_INT64_BLANK)) == ""


def test_format_display_clocks_alone():
  assert format_gpu_clock_throttle_reasons(0x100) == "Display clocks setting"
