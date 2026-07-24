"""Host-side unit tests for multiprecision mix pie builders (no Postgres)."""

import json

import pytest

from hpcperfstats.site.lib.machine import job_detail_artifacts as jda

pytestmark = pytest.mark.machine_unit_mock


def test_multiprecision_mix_payload_staff_reasons_align_with_plot_tabs():
  payload = jda._multiprecision_mix_payload({})
  cpu_r = payload["cpu_unavailable_reason"] or ""
  assert "Missing CPU busy-ops mix metrics in job metrics" in cpu_r
  assert "avg_flops64b" in cpu_r
  assert "avg_arm_int8_ops" in cpu_r
  gpu_r = payload["gpu_unavailable_reason"] or ""
  assert "Missing GPU precision-width mix metrics in job metrics" in gpu_r
  assert "avg_*_active" in gpu_r
  assert payload["cpu_plot_item"] is None
  assert payload["gpu_plot_item"] is None


def test_multiprecision_cpu_pie_uses_flops_not_vecpercent():
  payload = jda._multiprecision_mix_payload(
      {"vecpercent_64b": 40.0, "vecpercent_32b": 60.0}
  )
  assert payload["cpu_plot_item"] is None
  payload_ok = jda._multiprecision_mix_payload(
      {"avg_flops64b": 40.0, "avg_flops32b": 60.0}
  )
  assert payload_ok["cpu_plot_item"] is not None


def test_multiprecision_gpu_prefers_tensor_splits_over_lumped():
  mix = jda._gpu_precision_mix_from_metric_values(
      {
          "avg_tensor_active": 90.0,
          "avg_tensor_imma_active": 10.0,
          "avg_tensor_hmma_active": 20.0,
          "avg_tensor_dfma_active": 30.0,
          "avg_fp16_active": 5.0,
      }
  )
  assert "Tensor" not in mix
  assert mix.get("Tensor IMMA (INT8/INT4)") == 10.0
  assert mix.get("Tensor HMMA (FP16/BF16)") == 20.0
  assert mix.get("Tensor DFMA (FP64)") == 30.0


def test_multiprecision_pie_hover_uses_share_of_busy():
  item, reason = jda._pie_item_from_precision_mix(
      precision_mix={"FP32": 60.0, "FP64": 40.0},
      title="CPU Multiprecision Mix",
      empty_reason="empty",
      help_plot_key="jobDetailPlot_multiprecision_cpu",
      label_order=jda._CPU_PRECISION_LABEL_ORDER,
  )
  assert reason is None
  doc = json.dumps(item)
  assert "Share of busy" in doc
  assert "fixed" in doc


def test_multiprecision_mix_payload_from_metric_values():
  metric_values = {
      "avg_flops64b": 30.0,
      "avg_flops32b": 70.0,
      "avg_tensor_active": 12.0,
      "avg_fp16_active": 24.0,
      "avg_fp32_active": 36.0,
      "avg_fp64_active": 28.0,
  }
  payload = jda._multiprecision_mix_payload(metric_values)
  assert payload["cpu_unavailable_reason"] is None
  assert payload["gpu_unavailable_reason"] is None
  assert payload["cpu_plot_item"] is not None
  assert payload["gpu_plot_item"] is not None


def test_multiprecision_cpu_pie_includes_grace_int_wedges():
  payload = jda._multiprecision_mix_payload(
      {
          "avg_flops64b": 10.0,
          "avg_flops32b": 20.0,
          "avg_arm_int16_ops": 30.0,
          "avg_arm_int8_ops": 40.0,
      }
  )
  assert payload["cpu_unavailable_reason"] is None
  assert payload["cpu_plot_item"] is not None
  mix = jda._precision_mix_from_metric_values(
      {
          "avg_flops64b": 10.0,
          "avg_flops32b": 20.0,
          "avg_arm_int16_ops": 30.0,
          "avg_arm_int8_ops": 40.0,
      },
      jda._CPU_PRECISION_METRIC_TO_LABEL,
  )
  assert mix == {"FP64": 10.0, "FP32": 20.0, "INT16": 30.0, "INT8": 40.0}
  assert jda._CPU_PRECISION_LABEL_ORDER == ("FP64", "FP32", "INT16", "INT8")
  assert jda.APP_DETAIL_ARTIFACT_SCHEMA_VERSION == 10


def test_multiprecision_pie_legend_below_frame_with_long_labels():
  """Legend is below the figure so long GPU labels do not clip wedges."""
  item, reason = jda._pie_item_from_precision_mix(
      precision_mix={
          "Tensor IMMA (INT8/INT4)": 10.0,
          "Tensor HMMA (FP16/BF16)": 20.0,
          "Tensor DFMA (FP64)": 30.0,
          "FP16": 5.0,
      },
      title="GPU Multiprecision Mix",
      empty_reason="empty",
      help_plot_key="jobDetailPlot_multiprecision_gpu",
      label_order=jda._GPU_PRECISION_LABEL_ORDER,
  )
  assert reason is None
  doc = json.dumps(item)
  assert "fixed" in doc
  assert "below" in doc
  assert "Tensor IMMA (INT8/INT4)" in doc
  assert "stretch_width" not in doc or '"sizing_mode": "fixed"' in doc or "fixed" in doc


def test_multiprecision_cpu_pie_omits_zero_int_wedges():
  mix = jda._precision_mix_from_metric_values(
      {"avg_flops64b": 40.0, "avg_flops32b": 60.0, "avg_arm_int8_ops": 0.0},
      jda._CPU_PRECISION_METRIC_TO_LABEL,
  )
  assert mix == {"FP64": 40.0, "FP32": 60.0}
  assert "INT8" not in mix
