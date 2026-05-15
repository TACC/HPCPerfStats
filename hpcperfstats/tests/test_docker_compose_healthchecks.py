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


def test_docker_compose_rabbitmq_defaults_to_guest_credentials():
  repo_root = Path(__file__).resolve().parents[2]
  compose_path = repo_root / "docker-compose.yaml"
  content = compose_path.read_text()

  assert "RABBITMQ_DEFAULT_USER=guest" in content
  assert "RABBITMQ_DEFAULT_PASS=guest" in content


def test_docker_compose_rabbitmq_allows_large_monitor_messages():
  """Regression: default 16 MiB max_message_size rejects ~41 MiB hpcperfstatsd publishes."""
  repo_root = Path(__file__).resolve().parents[2]
  compose_path = repo_root / "docker-compose.yaml"
  content = compose_path.read_text()
  conf_path = repo_root / "services-conf" / "rabbitmq_max_message_size.conf"

  assert "rabbitmq_max_message_size.conf:/etc/rabbitmq/conf.d/20-max_message_size.conf" in content
  conf_text = conf_path.read_text()
  assert "max_message_size = 67108864" in conf_text


def test_docker_compose_proxy_bakes_default_conf_and_mounts_shared_includes():
  repo_root = Path(__file__).resolve().parents[2]
  compose_path = repo_root / "docker-compose.yaml"
  content = compose_path.read_text()

  assert "./services-conf/nginx.conf:/etc/nginx/http.d/default.conf:ro" in content
  assert "services-conf/proxy.Dockerfile" in content
  assert "NGINX_SSL_CERT" not in content
  assert "PROXY_NGINX_TLS" not in content
  assert "./services-conf/nginx-django-proxy-common.inc:/etc/nginx/nginx-django-proxy-common.inc:ro" in content
  assert (repo_root / "services-conf" / "nginx-static-files.conf").exists()
  assert (repo_root / "services-conf" / "nginx-django-proxy-common.inc").exists()
  assert (repo_root / "services-conf" / "parse_hpcperfstats_proxy_hosts.py").exists()
  assert (repo_root / "services-conf" / "write_nginx_proxy_allowed_hosts_include.py").exists()
  assert (repo_root / "services-conf" / "nginx.conf.example").exists()


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

