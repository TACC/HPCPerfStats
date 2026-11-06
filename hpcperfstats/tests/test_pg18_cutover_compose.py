"""PG18 cutover contract: overlay include, db parity, rebuild + runbook wiring."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
OVERLAY = REPO / "docker-compose.pg18-primary.yaml"
RUNBOOK = REPO / "docs" / "OPERATOR_PG18_MIGRATION.md"
INCLUDE_LINE = "  - docker-compose.defaults.yaml"
OVERLAY_LINE = "  - docker-compose.pg18-primary.yaml"


class _ResetLoader(yaml.SafeLoader):
  """SafeLoader that accepts the compose ``!reset`` tag."""


_ResetLoader.add_constructor(
  "!reset", lambda loader, node: ("!reset", loader.construct_sequence(node))
)


def _service_command(service: str) -> list[str]:
  text = (REPO / "docker-compose.defaults.yaml").read_text()
  doc = yaml.load(text, Loader=_ResetLoader)  # noqa: S506 - SafeLoader subclass
  return [str(x) for x in doc["services"][service]["command"]]


def _guc(command: list[str], name: str) -> str | None:
  for i, item in enumerate(command[:-1]):
    if item == "-c" and command[i + 1].startswith(f"{name}="):
      return command[i + 1].split("=", 1)[1]
  return None


def test_overlay_parks_pg15_and_hands_db_alias_and_socket_to_pg18() -> None:
  doc = yaml.load(OVERLAY.read_text(), Loader=_ResetLoader)  # noqa: S506
  svc = doc["services"]
  assert svc["db"]["profiles"] == ["pg15-retired"]
  assert svc["db_pg18"]["profiles"] == ("!reset", [])
  assert "postgres_runtime:/run/postgresql" in svc["db_pg18"]["volumes"]
  aliases = svc["db_pg18"]["networks"]["hpcperfstats_net"]["aliases"]
  assert aliases == ["db"]
  # Overlay must not try command: overrides (podman-compose ignores them).
  assert "command" not in svc["db_pg18"]


@pytest.mark.parametrize(
  "guc", ["shared_buffers", "max_connections", "work_mem", "max_wal_size"]
)
def test_pg18_runtime_gucs_match_pg15_db(guc: str) -> None:
  """db_pg18 becomes production db at cutover; GUCs cannot drift (12GB bug)."""
  assert _guc(_service_command("db_pg18"), guc) == _guc(
    _service_command("db"), guc
  )


def test_pg18_command_keeps_io_uring_and_committed_hba() -> None:
  cmd = _service_command("db_pg18")
  assert _guc(cmd, "io_method") == "io_uring"
  assert _guc(cmd, "hba_file") == "/etc/postgresql/pg_hba.conf"


def _primary_db_service(services: str) -> subprocess.CompletedProcess[str]:
  script = (REPO / "scripts" / "rebuild_full_site.sh").read_text()
  start = script.index("compose_primary_db_service() {")
  end = script.index("\n}\n", start) + 3
  fake = f"fake_compose() {{ printf '%s\\n' {services}; }}"
  body = (
    f"{fake}\nPODMAN_COMPOSE=(fake_compose)\nPG18_SERVICE=db_pg18\n"
    f"{script[start:end]}\ncompose_primary_db_service"
  )
  return subprocess.run(
    ["bash", "-c", body], capture_output=True, text=True, check=False
  )


@pytest.mark.parametrize(
  ("services", "want"),
  [("web db rabbitmq", "db"), ("web db_pg18 rabbitmq", "db_pg18")],
)
def test_rebuild_full_site_requires_live_db_service(
  services: str, want: str
) -> None:
  """Post-cutover rebuild must not demand the parked PG15 `db` container."""
  res = _primary_db_service(services)
  assert res.returncode == 0 and res.stdout.strip() == want


def test_rebuild_full_site_fails_closed_without_any_db_service() -> None:
  res = _primary_db_service("web pipeline")
  assert res.returncode == 1
  assert "neither db nor db_pg18" in res.stderr


def test_runbook_uses_podman_compose_without_file_flag() -> None:
  """`-f docker-compose.yaml` / docker compose forms drift from prod hosts."""
  text = RUNBOOK.read_text()
  assert "docker compose" not in text
  assert not re.search(r"podman-compose[^\n]*-f docker-compose", text)
  assert "podman-compose -p hpcperfstats" in text


def test_runbook_references_existing_artifacts() -> None:
  text = RUNBOOK.read_text()
  assert "docs/snippets/" not in text  # snippet never existed
  # An override file is skipped under -f; it may only appear as a warning.
  blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.S)
  assert blocks and not any("override" in b for b in blocks)
  for rel in (
    "docker-compose.pg18-primary.yaml",
    "scripts/pg18_host_data_chunk_copy.py",
  ):
    assert rel in text and (REPO / rel).is_file(), rel
  # The cutover sed inserts after this exact example include line.
  example = (REPO / "docker-compose.yaml.example").read_text().splitlines()
  assert INCLUDE_LINE in example
  assert OVERLAY_LINE.strip() in text


@pytest.mark.skipif(
  shutil.which("podman-compose") is None, reason="podman-compose not installed"
)
@pytest.mark.parametrize("file_flag", [[], ["-f", "docker-compose.yaml"]])
def test_cutover_include_survives_explicit_file_flag(
  tmp_path: Path, file_flag: list[str]
) -> None:
  """Regression: an override file is dropped by -f and revives PG15 as db."""
  for name in ("docker-compose.defaults.yaml", OVERLAY.name):
    shutil.copy(REPO / name, tmp_path / name)
  site = (REPO / "docker-compose.yaml.example").read_text()
  site = site.replace(INCLUDE_LINE + "\n", f"{INCLUDE_LINE}\n{OVERLAY_LINE}\n")
  (tmp_path / "docker-compose.yaml").write_text(site)
  res = subprocess.run(
    ["podman-compose", *file_flag, "config"],
    cwd=tmp_path,
    capture_output=True,
    text=True,
    check=True,
  )
  services = yaml.safe_load(res.stdout)["services"]
  assert "db" not in services
  pg18 = services["db_pg18"]
  assert "db" in pg18["networks"]["hpcperfstats_net"]["aliases"]
  assert "postgres_runtime:/run/postgresql" in pg18["volumes"]


def test_runbook_up_commands_name_their_services() -> None:
  """Bare `up -d` after a compose edit recreates rabbitmq/valkey too."""
  for line in RUNBOOK.read_text().splitlines():
    for cmd in re.findall(r"podman-compose[^;&|\n]*? up -d[^;&|\n]*", line):
      words = [w for w in cmd.split(" up -d", 1)[1].split() if w]
      services = [w for w in words if not w.startswith("-")]
      assert services, f"bare up: {cmd}"


def test_runbook_python_blocks_avoid_parameterized_django_sql() -> None:
  """Regression (02, 2026-11-06): web cursor rejected execute(sql, params).

  Phase B checks failed with "the query has 1 placeholders but 2 parameters
  were passed"; verification SQL belongs in psql inside db_pg18.
  """
  text = RUNBOOK.read_text()
  for block in re.findall(r"```bash\n(.*?)```", text, flags=re.S):
    if "/usr/local/bin/python3" in block and "execute(" in block:
      assert not re.search(r'execute\("[^"]*",\s*[\[(]', block), block[:200]
  phase_b = text.split("## Phase B", 1)[1].split("## Phase C", 1)[0]
  assert "remove_compression_policy('host_data', if_exists => true)" in phase_b
  assert "compression_jobs=" in phase_b and "host_data_n=" in phase_b
