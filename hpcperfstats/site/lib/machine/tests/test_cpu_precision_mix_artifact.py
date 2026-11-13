"""O8/O10/C5: CPU Multiprecision Mix pie from FP-instruction metrics.

The CPU pie is the share of FP instructions by precision (FP64/FP32/FP16)
plus SVE INT16/INT8 from ``avg_fp*_instr`` / ``avg_arm_int*_ops``. The
retired ``avg_flops64b`` / ``avg_flops32b`` no longer feed it. A blank pie
says whether the inputs were present but all 0 (idle) or absent (missing),
for both the CPU and the GPU pie.
"""

import json

import pytest

from hpcperfstats.site.lib.machine import job_detail_artifacts as jda

pytestmark = pytest.mark.machine_unit_mock

_CPU_INPUTS = (
  "avg_fp64_instr",
  "avg_fp32_instr",
  "avg_fp16_instr",
  "avg_arm_int16_ops",
  "avg_arm_int8_ops",
)
_GPU_INPUTS = (
  "avg_tensor_active",
  "avg_tensor_imma_active",
  "avg_tensor_hmma_active",
  "avg_tensor_dfma_active",
  "avg_fp16_active",
  "avg_fp32_active",
  "avg_fp64_active",
)


def _pie_labels(plot_item):
  """Wedge labels in render order from a serialized pie ``json_item``."""
  doc = plot_item["doc"]
  for root in doc["roots"]:
    stack = [root]
    while stack:
      node = stack.pop()
      if isinstance(node, dict):
        if node.get("name") == "ColumnDataSource":
          data = node["attributes"]["data"]
          entries = data.get("entries") if isinstance(data, dict) else None
          if entries:
            data = dict(entries)
          if "label" in data and "share" in data:
            return list(data["label"]), list(data["share"])
        stack.extend(node.values())
      elif isinstance(node, list):
        stack.extend(node)
  raise AssertionError("no pie ColumnDataSource in json_item")


def test_cpu_pie_from_fp_instr_metrics_includes_fp16_slice_in_order():
  """O8: FP16 is its own slice, ordered FP64, FP32, FP16, INT16, INT8."""
  values = {
    "avg_fp64_instr": 4.0,
    "avg_fp32_instr": 2.0,
    "avg_fp16_instr": 1.0,
    "avg_arm_int16_ops": 2.0,
    "avg_arm_int8_ops": 1.0,
  }
  payload = jda._multiprecision_mix_payload(values)

  assert payload["cpu_unavailable_reason"] is None
  labels, shares = _pie_labels(payload["cpu_plot_item"])
  assert labels == ["FP64", "FP32", "FP16", "INT16", "INT8"]
  assert shares == pytest.approx([40.0, 20.0, 10.0, 20.0, 10.0])


def test_cpu_pie_fp16_only_job_renders_single_fp16_slice():
  """An FP16-only (half-precision) job is not blank: one 100% FP16 slice."""
  payload = jda._multiprecision_mix_payload(
    {
      "avg_fp64_instr": 0.0,
      "avg_fp32_instr": 0.0,
      "avg_fp16_instr": 2.5,
      "avg_arm_int16_ops": 0.0,
      "avg_arm_int8_ops": 0.0,
    }
  )

  labels, shares = _pie_labels(payload["cpu_plot_item"])
  assert labels == ["FP16"]
  assert shares == pytest.approx([100.0])


def test_retired_avg_flops_precision_metrics_no_longer_feed_cpu_pie():
  """O10: positive avg_flops64b/32b alone leave the CPU pie blank."""
  payload = jda._multiprecision_mix_payload(
    {"avg_flops64b": 40.0, "avg_flops32b": 60.0}
  )

  assert payload["cpu_plot_item"] is None
  assert payload["cpu_unavailable_reason"] == (
    jda._CPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON
  )
  mix = jda._precision_mix_from_metric_values(
    {"avg_flops64b": 40.0, "avg_fp32_instr": 1.0},
    jda._CPU_PRECISION_METRIC_TO_LABEL,
  )
  assert mix == {"FP32": 1.0}


def test_cpu_pie_idle_reason_when_inputs_present_and_all_zero():
  """C5: measured-zero CPU inputs (idle job) give the idle reason."""
  payload = jda._multiprecision_mix_payload(dict.fromkeys(_CPU_INPUTS, 0.0))

  assert payload["cpu_plot_item"] is None
  reason = payload["cpu_unavailable_reason"]
  assert reason == jda._CPU_MULTIPRECISION_MIX_IDLE_REASON
  assert reason != jda._CPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON
  assert "idle" in reason.lower()


def test_cpu_pie_missing_reason_when_inputs_absent_or_null():
  """C5: no CPU input rows, or only NULL rows, give the missing reason."""
  for values in ({}, dict.fromkeys(_CPU_INPUTS)):
    payload = jda._multiprecision_mix_payload(values)
    reason = payload["cpu_unavailable_reason"]
    assert reason == jda._CPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON
    assert reason.startswith("Missing CPU")
    for key in _CPU_INPUTS:
      assert key in reason
    assert "avg_flops64b" not in reason


def test_cpu_pie_one_measured_zero_among_nulls_counts_as_idle():
  """C5: one sampled 0 is telemetry present (idle), not missing."""
  values = dict.fromkeys(_CPU_INPUTS)
  values["avg_fp64_instr"] = 0.0
  payload = jda._multiprecision_mix_payload(values)

  assert payload["cpu_unavailable_reason"] == (
    jda._CPU_MULTIPRECISION_MIX_IDLE_REASON
  )


def test_gpu_pie_idle_reason_when_inputs_present_and_all_zero():
  """C5: an idle GPU job (all *_active = 0, like jid 91955) says idle."""
  payload = jda._multiprecision_mix_payload(dict.fromkeys(_GPU_INPUTS, 0.0))

  assert payload["gpu_plot_item"] is None
  reason = payload["gpu_unavailable_reason"]
  assert reason == jda._GPU_MULTIPRECISION_MIX_IDLE_REASON
  assert reason != jda._GPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON
  assert "idle" in reason.lower()


def test_gpu_pie_missing_reason_when_inputs_absent_or_null():
  """C5: a CPU-only job (no GPU metric values) keeps the missing reason."""
  for values in ({}, dict.fromkeys(_GPU_INPUTS)):
    payload = jda._multiprecision_mix_payload(values)
    assert payload["gpu_unavailable_reason"] == (
      jda._GPU_MULTIPRECISION_MIX_UNAVAILABLE_REASON
    )


def test_idle_cpu_and_busy_gpu_reasons_are_independent():
  """Idle CPU text does not leak into a rendered GPU pie and vice versa."""
  values = dict.fromkeys(_CPU_INPUTS, 0.0)
  values.update({"avg_fp32_active": 30.0, "avg_fp64_active": 10.0})
  payload = jda._multiprecision_mix_payload(values)

  assert payload["cpu_unavailable_reason"] == (
    jda._CPU_MULTIPRECISION_MIX_IDLE_REASON
  )
  assert payload["gpu_unavailable_reason"] is None
  assert payload["gpu_plot_item"] is not None


def test_cpu_pie_help_text_describes_fp_instruction_share_and_fp16():
  """The ? hover explains FP-instruction share and the FP16 slice."""
  payload = jda._multiprecision_mix_payload({"avg_fp64_instr": 1.0})
  doc = json.dumps(payload["cpu_plot_item"])

  assert "FP instruction" in doc
  assert "FP16" in doc
  assert "avg_flops64b" not in doc


def test_detail_artifact_schema_bumped_for_precision_payload_change():
  """C5: pie inputs and reason text changed, so stored payloads rebuild."""
  assert jda.APP_DETAIL_ARTIFACT_SCHEMA_VERSION >= 15
