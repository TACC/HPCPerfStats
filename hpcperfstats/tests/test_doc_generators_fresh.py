"""Committed generated docs match a re-run of their generator's ``--check``."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "docs" / "measurements" / "overhead_profile"

GENERATORS = {
  "metrics_reference": [
    "monitor/scripts/gen_metrics_reference.py",
    "--check",
  ],
  "monitor_naming_tables": [
    "scripts/docs/gen_monitor_naming_tables.py",
    "--check",
  ],
  "monitor_variables_consumer_map": [
    "scripts/docs/gen_monitor_variables_consumer_map.py",
    "--check",
  ],
  "readme_measurement_summary": [
    "scripts/docs/gen_readme_measurement_summary.py",
    "--check",
  ],
  "overhead_profiles": [
    "monitor/measurement/bin/aggregate_profiles.py",
    "--check",
    "--out",
    str(PROFILES),
    *sorted(str(p) for p in PROFILES.glob("*/profile.json")),
  ],
}


@pytest.mark.parametrize("name", sorted(GENERATORS))
def test_generated_doc_is_fresh(name: str) -> None:
  script, *args = GENERATORS[name]
  res = subprocess.run(
    [sys.executable, str(ROOT / script), *args],
    capture_output=True,
    text=True,
    cwd=ROOT,
    timeout=120,
  )
  assert res.returncode == 0, res.stdout + res.stderr


def _module_literal(script: str, name: str) -> object:
  tree = ast.parse((ROOT / script).read_text(encoding="utf-8"))
  for node in tree.body:
    if isinstance(node, ast.Assign) and any(
      isinstance(t, ast.Name) and t.id == name for t in node.targets
    ):
      return ast.literal_eval(node.value)
  raise AssertionError(f"{name} not found in {script}")


def _consumer_map_inputs() -> list[str]:
  script = "scripts/docs/gen_monitor_variables_consumer_map.py"
  roots = _module_literal(script, "SCAN_ROOTS")
  suffixes = _module_literal(script, "SCAN_SUFFIXES")
  scanned = subprocess.run(
    ["git", "ls-files", "--", *roots],
    capture_output=True,
    text=True,
    check=True,
    cwd=ROOT,
  ).stdout.split()
  monitor_src = sorted(
    str(p.relative_to(ROOT)) for p in (ROOT / "monitor" / "src").glob("*.[ch]")
  )
  assert monitor_src, "monitor/src/*.[ch] not found"
  return [
    script,
    "docs/reference/monitor-variables-consumer-map.md",
    "monitor/scripts/lib/keys_catalog.py",
    "hpcperfstats/lib/monitor_sentinel.py",
    "hpcperfstats/dbload/lib/sync_timedb_parsing.py",
    "hpcperfstats/dbload/lib/monitor_naming/load_map.py",
    _module_literal("scripts/docs/gen_monitor_naming_tables.py", "MAP_REL"),
    *monitor_src,
    *(p for p in scanned if p.endswith(suffixes) and (ROOT / p).is_file()),
  ]


def _naming_tables_inputs() -> list[str]:
  script = "scripts/docs/gen_monitor_naming_tables.py"
  return [
    script,
    "docs/reference/monitor-naming-tables.md",
    "hpcperfstats/dbload/lib/monitor_naming/load_map.py",
    _module_literal(script, "MAP_REL"),
  ]


def _readme_summary_inputs() -> list[str]:
  script = "scripts/docs/gen_readme_measurement_summary.py"
  return [script, "README.md", _module_literal(script, "CSV_REL")]


REGEN_HOOKS = {
  "monitor-variables-consumer-map": (
    "scripts/docs/gen_monitor_variables_consumer_map.py",
    _consumer_map_inputs,
  ),
  "monitor-naming-tables": (
    "scripts/docs/gen_monitor_naming_tables.py",
    _naming_tables_inputs,
  ),
  "readme-measurement-summary": (
    "scripts/docs/gen_readme_measurement_summary.py",
    _readme_summary_inputs,
  ),
}


@pytest.mark.parametrize("hook_id", sorted(REGEN_HOOKS))
def test_regen_hook_covers_generator_inputs(hook_id: str) -> None:
  """The pre-commit regen hook runs --fix on a change to any input."""
  config = yaml.safe_load(
    (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
  )
  hooks = {
    h["id"]: h
    for repo in config["repos"]
    if repo["repo"] == "local"
    for h in repo["hooks"]
  }
  hook = hooks[hook_id]
  script, inputs = REGEN_HOOKS[hook_id]
  assert hook["entry"] == f"../.venv/bin/python3 {script} --fix"
  assert hook["language"] == "system"
  assert hook["pass_filenames"] is False
  assert "stages" not in hook
  files = re.compile(hook["files"])
  paths = inputs()
  missing = [p for p in paths if not (ROOT / p).is_file()]
  assert not missing, f"generator inputs moved: {missing}"
  unmatched = [p for p in paths if not files.search(p)]
  assert not unmatched, f"{hook_id} files: misses {unmatched}"
  assert not files.search("docs/reference/monitor-naming-scheme.md")
