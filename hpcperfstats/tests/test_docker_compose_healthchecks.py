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


def test_docker_compose_proxy_mounts_existing_nginx_config():
  repo_root = Path(__file__).resolve().parents[2]
  compose_path = repo_root / "docker-compose.yaml"
  content = compose_path.read_text()

  assert "./services-conf/nginx.conf" in content
  assert "./services-conf/nginx-withssl.conf" not in content
  assert (repo_root / "services-conf" / "nginx-static-files.conf").exists()


def test_docker_compose_app_uses_configurable_pipeline_ssh_mount():
  repo_root = Path(__file__).resolve().parents[2]
  app_compose_path = repo_root / "docker-compose.app.yaml"
  app_example_path = repo_root / "docker-compose.app.yaml.example"

  app_content = app_compose_path.read_text()
  app_example_content = app_example_path.read_text()

  expected_mount = (
    "${HPCPERFSTATS_PIPELINE_SSH_DIR:-/tmp/hpcperfstats-pipeline-ssh}:/hpcperfstats/.ssh/:ro"
  )
  assert expected_mount in app_content
  assert expected_mount in app_example_content

  for content in (app_content, app_example_content):
    assert "HPCPERFSTATS_INI=/home/hpcperfstats/hpcperfstats.ini" in content
    assert "./hpcperfstats.ini:/home/hpcperfstats/hpcperfstats.ini:ro" in content

