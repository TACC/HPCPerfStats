"""Host-side unit tests for multiprecision mix pie builders (no Postgres)."""

import json

import pytest

from hpcperfstats.site.lib.machine import job_detail_artifacts as jda

pytestmark = pytest.mark.machine_unit_mock


def test_multiprecision_mix_payload_staff_reasons_align_with_plot_tabs():
  payload = jda._multiprecision_mix_payload({})
  cpu_r = payload["cpu_unavailable_reason"] or ""
  assert "Missing CPU busy-ops mix metrics in job metrics" in cpu_r
  assert "avg_fp64_instr" in cpu_r
  assert "avg_fp16_instr" in cpu_r
  assert "avg_arm_int8_ops" in cpu_r
  gpu_r = payload["gpu_unavailable_reason"] or ""
  assert "Missing GPU precision-width mix metrics in job metrics" in gpu_r
  assert "avg_*_active" in gpu_r
  assert payload["cpu_plot_item"] is None
  assert payload["gpu_plot_item"] is None


def test_multiprecision_cpu_pie_uses_fp_instr_not_vecpercent():
  payload = jda._multiprecision_mix_payload(
    {"vecpercent_64b": 40.0, "vecpercent_32b": 60.0}
  )
  assert payload["cpu_plot_item"] is None
  payload_ok = jda._multiprecision_mix_payload(
    {"avg_fp64_instr": 40.0, "avg_fp32_instr": 60.0}
  )
  assert payload_ok["cpu_plot_item"] is not None


def test_multiprecision_gpu_prefers_tensor_splits_over_lumped():
  mix = jda._gpu_precision_mix_from_metric_values(
    {
      "avg_tensor_active": 90.0,
      "avg_tensor_imma_active": 10.0,
      "avg_tensor_hmma_active": 20.0,
      "avg_tensor_dmma_active": 30.0,
      "avg_fp16_active": 5.0,
    }
  )
  assert "Tensor" not in mix
  assert mix.get("Tensor IMMA (INT8/INT4)") == 10.0
  assert mix.get("Tensor HMMA (FP16/BF16/TF32/FP8)") == 20.0
  assert mix.get("Tensor DMMA (FP64)") == 30.0
  assert mix.get("Tensor (unattributed)") == pytest.approx(30.0)


def test_gpu_pie_has_no_dfma_wedge_it_duplicated_fp64():
  """DFMA is the FP64 FMA pipe (equal to fp64_active on Hopper/Blackwell),
  so it is never a wedge and never stands in for a tensor split."""
  values = {
    "avg_tensor_dfma_active": 40.0,
    "avg_fp64_active": 40.0,
    "avg_tensor_active": 12.0,
  }
  mix = jda._gpu_precision_mix_from_metric_values(values)
  assert mix == {"Tensor": 12.0, "FP64": 40.0}
  assert "avg_tensor_dfma_active" not in jda._GPU_PRECISION_METRIC_TO_LABEL
  assert "avg_tensor_dfma_active" not in jda._GPU_TENSOR_SPLIT_METRICS
  assert "Tensor DFMA (FP64)" not in jda._GPU_PRECISION_LABEL_ORDER


def test_gpu_pie_labels_and_order():
  """Wedge names and their legend order."""
  assert jda._GPU_PRECISION_LABEL_ORDER == (
    "Tensor IMMA (INT8/INT4)",
    "Tensor HMMA (FP16/BF16/TF32/FP8)",
    "Tensor DMMA (FP64)",
    "Tensor (unattributed)",
    "Tensor",
    "FP16",
    "FP32",
    "FP64",
    "Integer pipe",
  )
  assert jda._GPU_PRECISION_METRIC_TO_LABEL["avg_integer_active"] == (
    "Integer pipe"
  )
  assert jda._GPU_PRECISION_METRIC_TO_LABEL["avg_tensor_dmma_active"] == (
    "Tensor DMMA (FP64)"
  )
  assert jda._GPU_TENSOR_SPLIT_METRICS == (
    "avg_tensor_imma_active",
    "avg_tensor_hmma_active",
    "avg_tensor_dmma_active",
  )


def test_gpu_integer_and_dmma_wedges_only_when_positive():
  """Zero, None or absent integer / DMMA values add no wedge."""
  for integer, dmma in ((0.0, 0.0), (None, None)):
    mix = jda._gpu_precision_mix_from_metric_values(
      {
        "avg_tensor_active": 20.0,
        "avg_tensor_hmma_active": 20.0,
        "avg_integer_active": integer,
        "avg_tensor_dmma_active": dmma,
        "avg_fp32_active": 10.0,
      }
    )
    assert mix == {"Tensor HMMA (FP16/BF16/TF32/FP8)": 20.0, "FP32": 10.0}
  mix = jda._gpu_precision_mix_from_metric_values(
    {
      "avg_tensor_active": 25.0,
      "avg_tensor_hmma_active": 20.0,
      "avg_tensor_dmma_active": 5.0,
      "avg_integer_active": 50.0,
      "avg_fp64_active": 30.0,
    }
  )
  assert mix == {
    "Tensor HMMA (FP16/BF16/TF32/FP8)": 20.0,
    "Tensor DMMA (FP64)": 5.0,
    "FP64": 30.0,
    "Integer pipe": 50.0,
  }


def test_gpu_unattributed_tensor_only_with_splits_and_above_epsilon():
  """Remainder = tensor - HMMA - IMMA - DMMA, shown only when a split is
  positive and the remainder exceeds the epsilon; never negative."""
  eps = jda._GPU_TENSOR_UNATTRIBUTED_EPSILON
  base = {"avg_tensor_hmma_active": 20.0, "avg_tensor_imma_active": 5.0}
  over = jda._gpu_precision_mix_from_metric_values(
    {**base, "avg_tensor_active": 25.0 + 2 * eps}
  )
  assert over["Tensor (unattributed)"] == pytest.approx(2 * eps)
  for tensor in (25.0, 25.0 + eps / 2, 10.0):
    mix = jda._gpu_precision_mix_from_metric_values(
      {**base, "avg_tensor_active": tensor}
    )
    assert "Tensor (unattributed)" not in mix
    assert "Tensor" not in mix
  # No positive split: the lumped wedge, no remainder.
  mix = jda._gpu_precision_mix_from_metric_values(
    {
      "avg_tensor_active": 30.0,
      "avg_tensor_hmma_active": 0.0,
      "avg_tensor_imma_active": None,
    }
  )
  assert mix == {"Tensor": 30.0}
  # Splits without a tensor_active value: no remainder.
  mix = jda._gpu_precision_mix_from_metric_values(base)
  assert "Tensor (unattributed)" not in mix


def test_gpu_pie_a100_like_job_keeps_hmma_imma_fp_and_remainder():
  """A100 (DCGM path): no integer key, DMMA NOT_PRESENT (stored NULL);
  HMMA / IMMA / FP wedges plus the positive tensor remainder."""
  values = {
    "avg_tensor_active": 50.0,
    "avg_tensor_hmma_active": 30.0,
    "avg_tensor_imma_active": 5.0,
    "avg_tensor_dmma_active": None,
    "avg_tensor_dfma_active": 10.0,
    "avg_fp32_active": 20.0,
    "avg_fp64_active": 10.0,
  }
  mix = jda._gpu_precision_mix_from_metric_values(values)
  assert mix == {
    "Tensor IMMA (INT8/INT4)": 5.0,
    "Tensor HMMA (FP16/BF16/TF32/FP8)": 30.0,
    "Tensor (unattributed)": pytest.approx(15.0),
    "FP32": 20.0,
    "FP64": 10.0,
  }
  payload = jda._multiprecision_mix_payload(values)
  assert payload["gpu_unavailable_reason"] is None
  doc = json.dumps(payload["gpu_plot_item"])
  assert '"Tensor (unattributed)"' in doc
  assert '"Tensor DFMA (FP64)"' not in doc


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
    "avg_fp64_instr": 30.0,
    "avg_fp32_instr": 70.0,
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
      "avg_fp64_instr": 10.0,
      "avg_fp32_instr": 20.0,
      "avg_arm_int16_ops": 30.0,
      "avg_arm_int8_ops": 40.0,
    }
  )
  assert payload["cpu_unavailable_reason"] is None
  assert payload["cpu_plot_item"] is not None
  mix = jda._precision_mix_from_metric_values(
    {
      "avg_fp64_instr": 10.0,
      "avg_fp32_instr": 20.0,
      "avg_arm_int16_ops": 30.0,
      "avg_arm_int8_ops": 40.0,
    },
    jda._CPU_PRECISION_METRIC_TO_LABEL,
  )
  assert mix == {"FP64": 10.0, "FP32": 20.0, "INT16": 30.0, "INT8": 40.0}
  assert jda._CPU_PRECISION_LABEL_ORDER == (
    "FP64",
    "FP32",
    "FP16",
    "INT16",
    "INT8",
  )
  assert jda.APP_DETAIL_ARTIFACT_SCHEMA_VERSION == 18


def test_multiprecision_mix_payload_appends_shared_node_footer_to_both_pies():
  metric_values = {
    "avg_fp64_instr": 30.0,
    "avg_fp32_instr": 70.0,
    "avg_tensor_active": 12.0,
  }
  payload = jda._multiprecision_mix_payload(metric_values, is_shared_node=True)
  cpu_doc = json.dumps(payload["cpu_plot_item"])
  gpu_doc = json.dumps(payload["gpu_plot_item"])
  assert "shared node" in cpu_doc
  assert "shared node" in gpu_doc

  payload_exclusive = jda._multiprecision_mix_payload(
    metric_values, is_shared_node=False
  )
  assert "shared node" not in json.dumps(payload_exclusive["cpu_plot_item"])
  assert "shared node" not in json.dumps(payload_exclusive["gpu_plot_item"])


def test_multiprecision_pie_legend_below_frame_with_long_labels():
  """Legend is below the figure so long GPU labels do not clip wedges."""
  item, reason = jda._pie_item_from_precision_mix(
    precision_mix={
      "Tensor IMMA (INT8/INT4)": 10.0,
      "Tensor HMMA (FP16/BF16/TF32/FP8)": 20.0,
      "Tensor DMMA (FP64)": 30.0,
      "Integer pipe": 25.0,
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
  assert (
    "stretch_width" not in doc
    or '"sizing_mode": "fixed"' in doc
    or "fixed" in doc
  )


def test_multiprecision_cpu_pie_omits_zero_int_wedges():
  mix = jda._precision_mix_from_metric_values(
    {"avg_fp64_instr": 40.0, "avg_fp32_instr": 60.0, "avg_arm_int8_ops": 0.0},
    jda._CPU_PRECISION_METRIC_TO_LABEL,
  )
  assert mix == {"FP64": 40.0, "FP32": 60.0}
  assert "INT8" not in mix
