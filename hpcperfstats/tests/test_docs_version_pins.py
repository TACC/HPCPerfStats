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
DB_DOCKERFILE = "services-conf/db.Dockerfile"
PYPROJECT = "pyproject.toml"

VALKEY_UPGRADE_DOC = "docs/how-to/operate/upgrade-the-stack.md"
TOOLS_README = "hpcperfstats-tools/README.md"
METRICS_STATE_DOC = "docs/UPDATE_METRICS_STATE_MACHINE.md"
RABBITMQ_RECOVERY_DOC = "docs/how-to/operate/recover-rabbitmq.md"
PG18_MIGRATION_DOC = "docs/how-to/operate/migrate-to-postgresql-18.md"
RELEASE_NOTES_DOC = "docs/reference/release-notes.md"


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


def test_timescale_version_in_pg18_doc_matches_db_dockerfile_arg():
  """Regression (E12): the PG18 doc named TimescaleDB 2.31.x after the
  image moved to 2.32.1."""
  match = re.search(
    r"^ARG TIMESCALEDB_VERSION=(\S+)$", _read(DB_DOCKERFILE), re.M
  )
  assert match, DB_DOCKERFILE
  doc = _read(PG18_MIGRATION_DOC)
  stated = re.findall(r"\bTimescale(?:DB)? (\d+\.\d+\.\w+)", doc)
  assert stated, f"{PG18_MIGRATION_DOC} names no TimescaleDB version"
  assert set(stated) == {match.group(1)}, (stated, match.group(1))


def test_pg18_doc_names_only_phases_it_defines():
  """Regression (E12): the timeline said "site down until E3"; there is no
  phase E3, so every phase ID the doc cites must have its own heading."""
  doc = _read(PG18_MIGRATION_DOC)
  prose = re.sub(r"```.*?```", "", doc, flags=re.S)
  prose = re.sub(r"`[^`]*`", "", prose)
  headings = "\n".join(re.findall(r"^#{2,3} .*$", doc, re.M))
  headed = set(re.findall(r"\b([A-E]\d)\b", headings))
  cited = set(re.findall(r"\b([A-E]\d)\b", prose))
  assert "E1" in headed and "E3" not in cited
  assert cited <= headed, sorted(cited - headed)


def test_django_valkey_version_in_release_notes_matches_pyproject():
  """Regression (E2): the Valkey cutover note said django-valkey 0.4.1
  while pyproject.toml pins 0.5.0."""
  match = re.search(r'"django-valkey==([^"]+)"', _read(PYPROJECT))
  assert match, PYPROJECT
  stated = re.findall(
    r"django-valkey(?:==| )(\d+\.\d+\.\d+)", _read(RELEASE_NOTES_DOC)
  )
  assert stated, f"{RELEASE_NOTES_DOC} names no django-valkey version"
  assert set(stated) == {match.group(1)}, (stated, match.group(1))
