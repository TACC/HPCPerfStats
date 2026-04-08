from __future__ import annotations

import re
from pathlib import Path

from hpcperfstats.analysis.metrics.metrics import job_metrics_catalog_entries
from hpcperfstats.analysis.plot.summary_metric_descriptions import (
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
  js_path = repo_root / "hpcperfstats" / "site" / "frontend" / "src" / "utils" / "variableMetadata.js"
  js_text = _read_text(js_path)
  metadata_keys = _extract_js_object_keys(js_text, "JOB_ACCOUNTING_AND_DERIVED_METADATA")
  catalog_metrics = {row["metric"] for row in job_metrics_catalog_entries()}
  missing = sorted(catalog_metrics - metadata_keys)
  assert not missing, f"Missing metadata definitions for catalog metrics: {missing}"


def test_summary_plot_metadata_keys_match_python_descriptions():
  repo_root = Path(__file__).resolve().parents[2]
  js_path = repo_root / "hpcperfstats" / "site" / "frontend" / "src" / "utils" / "variableMetadata.js"
  js_text = _read_text(js_path)
  summary_keys = _extract_js_object_keys(js_text, "SUMMARY_PLOT_METRIC_METADATA")
  py_keys = set(SUMMARY_METRIC_DESCRIPTIONS.keys())
  assert summary_keys == py_keys


def test_summary_plot_researcher_use_keys_match_description_keys():
  assert set(SUMMARY_METRIC_RESEARCHER_USE.keys()) == set(SUMMARY_METRIC_DESCRIPTIONS.keys())


def test_generated_monitor_metadata_header_references_authoritative_rule_path():
  repo_root = Path(__file__).resolve().parents[2]
  gen_path = repo_root / "hpcperfstats" / "site" / "frontend" / "src" / "utils" / "generate-variable-metadata-monitor-events.py"
  js_out_path = repo_root / "hpcperfstats" / "site" / "frontend" / "src" / "utils" / "variableMetadataMonitorEvents.js"
  gen_text = _read_text(gen_path)
  js_text = _read_text(js_out_path)
  expected = "HPCPerfStats/cursor-rules/variable-metadata-monitor-contract.mdc"
  assert expected in gen_text
  assert expected in js_text
