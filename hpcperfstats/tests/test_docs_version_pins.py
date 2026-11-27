"""Drift guard: version and path facts in docs match the code that pins them.

Each doc path is a module constant so a doc move repoints one line.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DEFAULTS = "docker-compose.defaults.yaml"
METRICS_PY = "hpcperfstats/analysis/metrics/lib/metrics.py"
TOOLS_PYPROJECT = "hpcperfstats-tools/pyproject.toml"

VALKEY_UPGRADE_DOC = "docs/upgrade.md"
TOOLS_README = "hpcperfstats-tools/README.md"
METRICS_STATE_DOC = "docs/UPDATE_METRICS_STATE_MACHINE.md"
RABBITMQ_RECOVERY_DOC = "docs/OPERATOR_RABBITMQ_RECOVERY.md"


def _read(rel: str) -> str:
  return (ROOT / rel).read_text(encoding="utf-8")


def _compose_image_tag(image: str) -> str:
  match = re.search(
    rf"image:\s*docker\.io/\S*?{image}:(\S+)", _read(COMPOSE_DEFAULTS)
  )
  assert match, f"no {image} image in {COMPOSE_DEFAULTS}"
  return match.group(1)


def _major_minor(tag: str) -> str:
  return ".".join(tag.split(".")[:2])


def test_valkey_version_in_upgrade_doc_matches_compose_pin():
  tag = _compose_image_tag("valkey/valkey")
  doc = _read(VALKEY_UPGRADE_DOC)
  stated = re.findall(r"valkey_version:(\d+\.\d+)", doc)
  stated += re.findall(r"\bValkey (\d+\.\d+)\b", doc)
  assert stated, f"{VALKEY_UPGRADE_DOC} names no Valkey version"
  assert set(stated) == {_major_minor(tag)}, (stated, tag)
  for image in re.findall(r"valkey/valkey:(\S+?)`", doc):
    assert image == tag, (image, tag)


def test_tools_readme_python_floor_matches_requires_python():
  match = re.search(
    r'requires-python\s*=\s*">=(\d+\.\d+)"', _read(TOOLS_PYPROJECT)
  )
  assert match, TOOLS_PYPROJECT
  stated = re.findall(r"Python (\d+\.\d+)\+", _read(TOOLS_README))
  assert stated, f"{TOOLS_README} names no Python floor"
  assert set(stated) == {match.group(1)}, stated


def test_tools_readme_api_urls_paths_exist():
  paths = re.findall(r"hpcperfstats/[\w/]*api_urls\.py", _read(TOOLS_README))
  assert paths, f"{TOOLS_README} names no api_urls.py path"
  missing = [p for p in paths if not (ROOT / p).is_file()]
  assert not missing, missing


def test_metrics_contract_version_in_state_doc_matches_code():
  match = re.search(
    r"^METRICS_CONTRACT_VERSION\s*=\s*(\d+)\s*$", _read(METRICS_PY), re.M
  )
  assert match, METRICS_PY
  doc = _read(METRICS_STATE_DOC)
  stated = [
    found.group(1)
    for anchor in re.finditer(r"METRICS_CONTRACT_VERSION", doc)
    for found in [
      re.search(r"currently\s+\**(\d+)", doc[anchor.end() : anchor.end() + 400])
    ]
    if found
  ]
  assert stated, f"{METRICS_STATE_DOC} states no METRICS_CONTRACT_VERSION"
  assert set(stated) == {match.group(1)}, stated


def test_rabbitmq_version_family_in_recovery_doc_matches_compose_pin():
  family = _major_minor(_compose_image_tag("library/rabbitmq"))
  stated = re.findall(r"\b(\d+\.\d+)\.x\b", _read(RABBITMQ_RECOVERY_DOC))
  assert stated, f"{RABBITMQ_RECOVERY_DOC} names no RabbitMQ version family"
  assert set(stated) == {family}, (stated, family)
