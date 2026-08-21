from __future__ import annotations

import re
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


def test_docker_compose_json_file_logging_rotated():
  """Stdout logging is file-backed for compose logs (Podman-safe, not journald)."""
  repo_root = Path(__file__).resolve().parents[2]
  content = (repo_root / "docker-compose.yaml").read_text()
  assert "driver: json-file" in content
  assert 'max-size: "100m"' in content
  assert 'max-file: "3"' in content
  for service in ["web", "pipeline", "redis", "proxy", "db", "rabbitmq"]:
    assert f"{service}:" in content
  assert content.count("logging: *hpc-logging") == 6


def test_docker_compose_commands_and_healthchecks_use_yaml_list_form():
  """Regression: keep argv/healthcheck tests as YAML block lists, not flow [...]."""
  repo_root = Path(__file__).resolve().parents[2]
  compose_path = repo_root / "docker-compose.yaml"
  content = compose_path.read_text()

  assert "command: [" not in content
  assert "test:\n        [" not in content
  assert 'test: ["' not in content
  assert "command:\n      - redis-server\n" in content
  assert "      - --maxmemory\n      - 16gb\n" in content
  assert "      - --io-threads\n      - \"4\"\n" in content
  assert "test:\n        - CMD\n        - redis-cli\n        - ping\n" in content
  assert "test:\n        - CMD-SHELL\n        - nc -z 127.0.0.1 80 || exit 1\n" in content
  assert (
      "test:\n        - CMD-SHELL\n"
      "        - rabbitmq-diagnostics -q ping || exit 1\n"
  ) in content
  assert (
      "test:\n        - CMD-SHELL\n"
      "        - pg_isready -U hpcperfstats -h 127.0.0.1 -p 5432\n"
  ) in content
  assert "command:\n      - -c\n      - max_connections=500\n" in content


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
  assert "max_message_size = 134217728" in conf_text


def test_docker_compose_rabbitmq_defaults_to_quorum_queue_type():
  """Classic queues OOM under many monitor connections; keep quorum default."""
  repo_root = Path(__file__).resolve().parents[2]
  compose_path = repo_root / "docker-compose.yaml"
  content = compose_path.read_text()
  conf_path = repo_root / "services-conf" / "rabbitmq_default_queue_type.conf"

  assert (
      "rabbitmq_default_queue_type.conf:/etc/rabbitmq/conf.d/25-default_queue_type.conf"
      in content
  )
  conf_text = conf_path.read_text()
  assert "default_queue_type = quorum" in conf_text
  assert "default_queue_type = classic" not in conf_text


def test_docker_compose_proxy_bakes_default_conf_and_mounts_shared_includes():
  repo_root = Path(__file__).resolve().parents[2]
  compose_path = repo_root / "docker-compose.yaml"
  content = compose_path.read_text()

  assert "./services-conf/nginx.conf:/etc/nginx/http.d/default.conf:ro" in content
  assert "services-conf/proxy.Dockerfile" in content
  assert "NGINX_SSL_CERT" not in content
  assert "PROXY_NGINX_TLS" not in content
  assert "./services-conf/nginx-django-proxy-common.inc:/etc/nginx/nginx-django-proxy-common.inc:ro" in content
  assert "./services-conf/nginx-edge-security-headers.inc:/etc/nginx/nginx-edge-security-headers.inc:ro" in content
  assert "./services-conf/nginx-csp-no-active.inc:/etc/nginx/nginx-csp-no-active.inc:ro" in content
  assert "./services-conf/nginx-csp-django-html.inc:/etc/nginx/nginx-csp-django-html.inc:ro" in content
  assert (repo_root / "services-conf" / "nginx-static-files.conf").exists()
  assert (repo_root / "services-conf" / "nginx-django-proxy-common.inc").exists()
  assert (repo_root / "services-conf" / "nginx-csp-no-active.inc").exists()
  assert (repo_root / "services-conf" / "nginx-csp-django-html.inc").exists()
  assert (repo_root / "services-conf" / "parse_hpcperfstats_proxy_hosts.py").exists()
  assert (repo_root / "services-conf" / "write_nginx_proxy_allowed_hosts_include.py").exists()
  assert (repo_root / "services-conf" / "write_nginx_resolver_include.py").exists()
  assert (repo_root / "services-conf" / "proxy_entrypoint.sh").exists()
  assert (repo_root / "services-conf" / "nginx.conf.example").exists()
  dockerfile = (repo_root / "services-conf" / "proxy.Dockerfile").read_text()
  # Mount-only snippets must not also be COPY'd (single source of truth = compose volumes).
  assert "COPY services-conf/nginx-static-files.conf" not in dockerfile
  assert "COPY services-conf/nginx-edge-security-headers.inc" not in dockerfile
  assert "COPY services-conf/nginx-csp-no-active.inc" not in dockerfile
  assert "COPY services-conf/nginx-csp-django-html.inc" not in dockerfile
  assert "COPY services-conf/nginx-django-proxy-common.inc" not in dockerfile


def test_proxy_dockerfile_pins_nginx_and_brotli_to_same_edge_version():
  """Regression: apk world[nginx=X] breaks when Alpine edge advances; bump ARG intentionally."""
  repo_root = Path(__file__).resolve().parents[2]
  dockerfile = (repo_root / "services-conf" / "proxy.Dockerfile").read_text()
  match = re.search(
    r"^ARG NGINX_EDGE_VERSION=([0-9]+\.[0-9]+\.[0-9]+-r[0-9]+)\s*$",
    dockerfile,
    flags=re.MULTILINE,
  )
  assert match is not None, "proxy.Dockerfile must pin NGINX_EDGE_VERSION"
  pinned = match.group(1)
  # Current Alpine edge main (x86_64) nginx + nginx-mod-http-brotli; bump both ARG and this assert.
  assert pinned == "1.30.4-r3"
  assert "nginx=${NGINX_EDGE_VERSION}" in dockerfile
  assert "nginx-mod-http-brotli=${NGINX_EDGE_VERSION}" in dockerfile
  assert "ALPINE_EDGE_MAIN=" in dockerfile
  assert "--repository=${ALPINE_EDGE_MAIN}" in dockerfile
  assert "ca-certificates" in dockerfile
  assert 'CMD ["/usr/local/bin/proxy_entrypoint.sh"]' in dockerfile


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
    assert "./hpcperfstats.ini:/home/hpcperfstats/hpcperfstats.ini:ro" not in content
    assert "target: hpcperfstats-full" in content


def test_docker_compose_app_web_build_uses_hpcperfstats_full_target():
  """Compose must request full image; rebuild_pipeline.sh uses pipeline-refresh explicitly."""
  repo_root = Path(__file__).resolve().parents[2]
  app_compose_path = repo_root / "docker-compose.app.yaml"
  content = app_compose_path.read_text()

  assert "dockerfile: Dockerfile" in content
  assert "target: hpcperfstats-full" in content
  assert content.index("target: hpcperfstats-full") < content.index("image: hpcperfstats")


# Operator-facing keys that must stay aligned between the gitignored app overlay
# and docker-compose.app.yaml.example (see docker-compose-app-example-sync.mdc).
_OPERATOR_APP_COMPOSE_MARKERS = (
    "CPU pinning fragments: see docker-compose.yaml",
    "${HPCPERFSTATS_WEB_PORT:-8000}:8000",
    "mem_limit: 128g",
    "memswap_limit: 128g",
    "${HPCPERFSTATS_PIPELINE_STOP_GRACE:-2m}",
    "${HPCPERFSTATS_PIPELINE_SSH_DIR:-/tmp/hpcperfstats-pipeline-ssh}:/hpcperfstats/.ssh/:ro",
    "device: /opt/hpcperfstats_data/",
)


def test_docker_compose_app_example_operator_markers():
  repo_root = Path(__file__).resolve().parents[2]
  example_content = (repo_root / "docker-compose.app.yaml.example").read_text()
  for marker in _OPERATOR_APP_COMPOSE_MARKERS:
    assert marker in example_content, "example missing operator marker: %s" % marker


def test_docker_compose_app_example_operator_parity():
  """When local docker-compose.app.yaml exists, operator fields must match .example."""
  repo_root = Path(__file__).resolve().parents[2]
  app_path = repo_root / "docker-compose.app.yaml"
  example_path = repo_root / "docker-compose.app.yaml.example"
  if not app_path.is_file():
    return
  app_content = app_path.read_text()
  example_content = example_path.read_text()
  for marker in _OPERATOR_APP_COMPOSE_MARKERS:
    assert marker in app_content, "app yaml missing operator marker: %s" % marker
    assert marker in example_content, "example missing operator marker: %s" % marker

