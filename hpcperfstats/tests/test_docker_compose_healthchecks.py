from __future__ import annotations

from pathlib import Path


def test_docker_compose_has_healthchecks_for_core_services():
  repo_root = Path(__file__).resolve().parents[2]
  compose_path = repo_root / "docker-compose.yaml"
  content = compose_path.read_text()

  # Core infrastructure services defined in docker-compose.yaml
  for service in ["redis", "proxy", "db", "rabbitmq"]:
    assert f"{service}:" in content
    assert "healthcheck:" in content

  assert "redis-cli" in content
  assert "nc -z 127.0.0.1 80" in content
  assert "pg_isready" in content
  assert "rabbitmq-diagnostics" in content

