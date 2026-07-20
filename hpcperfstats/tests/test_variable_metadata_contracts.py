from __future__ import annotations

import re
from pathlib import Path

from hpcperfstats.analysis.metrics.lib.metrics import job_metrics_catalog_entries
from hpcperfstats.analysis.metrics.lib.plot.summary_metric_descriptions import (
    SUMMARY_METRIC_DESCRIPTIONS,
    SUMMARY_METRIC_RESEARCHER_USE,
)


def _read_text(path: Path) -> str:
  return path.read_text(encoding="utf-8", errors="replace")


def _extract_js_object_keys(js_text: str, object_name: str) -> set[str]:
  pattern = re.compile(
      rf"const\s+{re.escape(object_name)}\s*=\s*\{{(.*?)\n\}};",
      re.DOTALL,
  )
  match = pattern.search(js_text)
  assert match, f"Unable to locate JS object: {object_name}"
  block = match.group(1)
  keys = set(re.findall(r"^\s*([A-Za-z0-9_]+)\s*:", block, re.MULTILINE))
  keys.discard("description")
  keys.discard("researcherUse")
  return keys


def test_metric_catalog_entries_have_variable_metadata_definitions():
  repo_root = Path(__file__).resolve().parents[2]
  meta_path = (
      repo_root / "hpcperfstats" / "site" / "frontend" / "src" / "utils"
      / "variableMetadata.ts"
  )
  meta_text = _read_text(meta_path)
  metadata_keys = _extract_js_object_keys(meta_text, "JOB_ACCOUNTING_AND_DERIVED_METADATA")
  catalog_metrics = {row["metric"] for row in job_metrics_catalog_entries()}
  missing = sorted(catalog_metrics - metadata_keys)
  assert not missing, f"Missing metadata definitions for catalog metrics: {missing}"


def test_summary_plot_metadata_keys_match_python_descriptions():
  repo_root = Path(__file__).resolve().parents[2]
  meta_path = (
      repo_root / "hpcperfstats" / "site" / "frontend" / "src" / "utils"
      / "variableMetadata.ts"
  )
  meta_text = _read_text(meta_path)
  summary_keys = _extract_js_object_keys(meta_text, "SUMMARY_PLOT_METRIC_METADATA")
  py_keys = set(SUMMARY_METRIC_DESCRIPTIONS.keys())
  assert summary_keys == py_keys


def test_summary_plot_researcher_use_keys_match_description_keys():
  assert set(SUMMARY_METRIC_RESEARCHER_USE.keys()) == set(SUMMARY_METRIC_DESCRIPTIONS.keys())


def test_generated_monitor_metadata_header_references_authoritative_rule_path():
  repo_root = Path(__file__).resolve().parents[2]
  gen_path = repo_root / "hpcperfstats" / "site" / "frontend" / "src" / "utils" / "generate-variable-metadata-monitor-events.py"
  out_path = repo_root / "hpcperfstats" / "site" / "frontend" / "src" / "utils" / "variableMetadataMonitorEvents.ts"
  gen_text = _read_text(gen_path)
  out_text = _read_text(out_path)
  expected = "HPCPerfStats/hpcperfstats/cursor-rules/variable-metadata-monitor-contract.mdc"
  assert expected in gen_text
  assert expected in out_text


def test_grace_fail_soft_cycle_event_descriptions_in_generator_and_generated_ts():
  """Lock Grace DCGM fail-soft meanings for mperf/aperf/cpu_clock_est_cycles."""
  repo_root = Path(__file__).resolve().parents[2]
  utils = repo_root / "hpcperfstats" / "site" / "frontend" / "src" / "utils"
  gen_text = _read_text(utils / "generate-variable-metadata-monitor-events.py")
  out_text = _read_text(utils / "variableMetadataMonitorEvents.ts")
  for key, needles in (
      ("mperf", ("Reference cycles", "clock_khz")),
      ("aperf", ("Active cycles", "util_total")),
      ("cpu_clock_est_cycles", ("Active cycles", "aperf")),
  ):
    assert f'"{key}"' in gen_text or f"{key}:" in out_text
    for needle in needles:
      assert needle in gen_text, f"DESC for {key} missing {needle!r} in generator"
      assert needle in out_text, f"generated metadata for {key} missing {needle!r}"


def test_monitor_keys_extractor_keeps_all_x_entries_on_one_line():
  """KEYS macros pack multiple X(name,...) on one line; do not keep only the first."""
  import importlib.util

  repo_root = Path(__file__).resolve().parents[2]
  gen_path = (
      repo_root
      / "hpcperfstats"
      / "site"
      / "frontend"
      / "src"
      / "utils"
      / "generate-variable-metadata-monitor-events.py"
  )
  spec = importlib.util.spec_from_file_location("gen_var_meta", gen_path)
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  names = mod.extract_x_keys(
      [
          '      X(instr_retired, "E,W=48", ""), X(aperf, "E,W=48", ""), X(mperf, "E,W=48", ""),',
          '      X(cpu_util_nice_accum_us, "E,W=64", ""), X(cpu_clock_est_cycles, "E,W=64", ""),',
      ]
  )
  assert names == {
      "instr_retired",
      "aperf",
      "mperf",
      "cpu_util_nice_accum_us",
      "cpu_clock_est_cycles",
  }
  collected = mod.collect_all_names()
  for key in ("aperf", "mperf", "cpu_clock_est_cycles", "arm_int8_ops"):
    assert key in collected, f"collect_all_names missed {key} (multi-X KEYS parse bug)"
