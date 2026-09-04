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
  for service in ["web", "pipeline", "redis", "proxy", "db", "db_pg18", "rabbitmq"]:
    assert f"{service}:" in content
  assert content.count("logging: *hpc-logging") == 7


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
  assert "      - --maxmemory-policy\n      - volatile-lru\n" in content
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


def test_docker_compose_redis_maxmemory_policy_is_volatile_lru():
  """job:v1 keys are TTL-free; allkeys-* would evict them (Q9)."""
  repo_root = Path(__file__).resolve().parents[2]
  content = (repo_root / "docker-compose.yaml").read_text()
  assert "      - --maxmemory-policy\n      - volatile-lru\n" in content
  assert "allkeys-lru" not in content
  assert "allkeys-lfu" not in content
  assert "allkeys-random" not in content


def test_readme_and_design_doc_redis_policy_is_volatile_lru():
  """Operator install docs must match the compose Redis eviction policy."""
  repo_root = Path(__file__).resolve().parents[2]
  readme = (repo_root / "README.md").read_text()
  design = (repo_root / "docs" / "design-document.md").read_text()
  assert "volatile-lru" in readme
  assert "allkeys-lru" not in readme
  assert "volatile-lru" in design
  assert "allkeys-lru" not in design


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
  assert "max_message_size = 134217728" not in conf_text


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


def test_docker_compose_rabbitmq_vm_memory_cap_is_96gib():
  """Unbounded RMQ RSS OOM'd the host; 96g cgroup + 80GiB watermark headroom."""
  repo_root = Path(__file__).resolve().parents[2]
  compose_path = repo_root / "docker-compose.yaml"
  content = compose_path.read_text()
  conf_path = repo_root / "services-conf" / "rabbitmq_vm_memory.conf"
  rabbitmq_block = content.split("  rabbitmq:\n", 1)[1].split("\nvolumes:", 1)[0]

  assert (
      "rabbitmq_vm_memory.conf:/etc/rabbitmq/conf.d/35-vm_memory.conf"
      in content
  )
  assert "mem_limit: 96g" in rabbitmq_block
  assert "memswap_limit: 96g" in rabbitmq_block
  conf_text = conf_path.read_text()
  assert "vm_memory_high_watermark.absolute = 80GiB" in conf_text
  assert "vm_memory_high_watermark.absolute = 96GiB" not in conf_text
  assert "vm_memory_high_watermark.relative" not in conf_text
  readme = (repo_root / "README.md").read_text()
  design = (repo_root / "docs" / "design-document.md").read_text()
  deploy = (repo_root / "docs" / "DEPLOY_CONCURRENCY_AND_NUMA.md").read_text()
  assert "80GiB" in readme
  assert "mem_limit: 96g" in readme or "mem_limit` / `memswap_limit` 96g" in readme
  assert "80GiB" in design
  assert "80GiB" in deploy


def test_docker_compose_proxy_runtime_tls_mount_and_entrypoint_materialize():
  repo_root = Path(__file__).resolve().parents[2]
  compose_path = repo_root / "docker-compose.yaml"
  content = compose_path.read_text()

  assert "./services-conf/nginx.conf:/etc/nginx/http.d/default.conf:ro" in content
  assert "proxy_ssl_source:/mnt/ssl-source:ro" in content
  assert "ssl_certs:/etc/ssl/hpcperfstats:ro" not in content
  assert "additional_contexts:" not in content
  assert "ssl_certs: ./.hpcperfstats_ssl_certs" not in content
  assert "${HPCPERFSTATS_SSL_CERTS_DIR" not in content
  assert ":-./tests/fixtures/proxy-ssl}" not in content
  assert ".hpcperfstats_ssl_certs" not in (repo_root / ".gitignore").read_text()
  assert "/etc/letsencrypt/:/etc/letsencrypt/:ro" not in content
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
  assert (repo_root / "services-conf" / "resolve_proxy_ssl_certs_dir.py").exists()
  assert (repo_root / "services-conf" / "proxy_entrypoint.sh").exists()
  assert (repo_root / "services-conf" / "nginx.conf").exists()
  fixture = repo_root / "tests" / "fixtures" / "proxy-ssl"
  assert (fixture / "fullchain.pem").is_file()
  assert (fixture / "privkey.pem").is_file()
  assert not (repo_root / "services-conf" / "nginx.conf.example").exists()
  nginx = (repo_root / "services-conf" / "nginx.conf").read_text()
  assert "ssl_certificate /etc/ssl/hpcperfstats/fullchain.pem;" in nginx
  assert "ssl_certificate_key /etc/ssl/hpcperfstats/privkey.pem;" in nginx
  dockerfile = (repo_root / "services-conf" / "proxy.Dockerfile").read_text()
  entrypoint = (repo_root / "services-conf" / "proxy_entrypoint.sh").read_text()
  # Mount-only snippets must not also be COPY'd (single source of truth = compose volumes).
  assert "COPY services-conf/nginx-static-files.conf" not in dockerfile
  assert "COPY services-conf/nginx-edge-security-headers.inc" not in dockerfile
  assert "COPY services-conf/nginx-csp-no-active.inc" not in dockerfile
  assert "COPY services-conf/nginx-csp-django-html.inc" not in dockerfile
  assert "COPY services-conf/nginx-django-proxy-common.inc" not in dockerfile
  assert "nginx.conf.example" not in dockerfile
  assert "COPY services-conf/nginx.conf /build/nginx.conf" in dockerfile
  assert "COPY --from=ssl_certs" not in dockerfile
  assert "resolve_proxy_ssl_certs_dir.py" in dockerfile
  assert "--mount=type=bind,source=/,target=/host" not in dockerfile
  assert "--host-prefix /host" not in dockerfile
  assert "--dest-dir /etc/ssl/hpcperfstats" not in dockerfile
  assert "test -f /etc/ssl/hpcperfstats/fullchain.pem" not in dockerfile
  assert "test -f /etc/ssl/hpcperfstats/privkey.pem" not in dockerfile
  assert "resolve_proxy_ssl_certs_dir.py" in entrypoint
  assert "--ssl-source-mount /mnt/ssl-source" in entrypoint
  assert "--dest-dir /etc/ssl/hpcperfstats" in entrypoint
  assert "test -f /etc/ssl/hpcperfstats/fullchain.pem" in entrypoint
  assert "test -f /etc/ssl/hpcperfstats/privkey.pem" in entrypoint
  assert "HPCPERFSTATS_SSL_CERTS_REL" in entrypoint
  # Host archive modes preserved via resolve copy; do not rewrite in Dockerfile.
  assert "chown -R root:root /etc/ssl/hpcperfstats" not in dockerfile
  assert "chmod 400 /etc/ssl/hpcperfstats/privkey.pem" not in dockerfile
  assert "chmod 600 /etc/ssl/hpcperfstats/privkey.pem" not in dockerfile
  assert "chown nginx:" not in dockerfile
  assert "services-conf/proxy-ssl" not in dockerfile
  assert not (repo_root / "services-conf" / "proxy-ssl").exists()
  assert not (repo_root / "services-conf" / "proxy-ssl.fixture").exists()


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


def test_docker_compose_includes_settings_not_app_or_pinning():
  repo_root = Path(__file__).resolve().parents[2]
  content = (repo_root / "docker-compose.yaml").read_text()
  assert "docker-compose.settings.yaml" in content
  assert "docker-compose.app.yaml" not in content
  assert "cpu-pinning" not in content
  assert not (repo_root / "docker-compose.app.yaml.example").exists()
  assert not (repo_root / "scripts" / "apply_compose_cpu_pinning.py").exists()
  assert not (
      repo_root / "hpcperfstats" / "dbload" / "lib" / "compose_cpu_layout.py"
  ).exists()
  assert not (
      repo_root / "hpcperfstats" / "dbload" / "lib" / "numa_topology.py"
  ).exists()


def test_docker_compose_base_omits_null_volume_stubs_for_podman_compose():
  """Regression: podman-compose fails merging null volume stubs with settings dicts.

  Signature (hpcperfstats04): ValueError: can't merge value of [hpcperfstatsdata]
  of type <class 'NoneType'> and <class 'dict'> during include merge / down.
  """
  repo_root = Path(__file__).resolve().parents[2]
  base = (repo_root / "docker-compose.yaml").read_text()
  settings = (repo_root / "docker-compose.settings.yaml.example").read_text()
  # Top-level volumes live only in settings (include). Bare `name:` under volumes
  # parses as null and breaks podman-compose rec_merge_one.
  assert re.search(r"(?m)^volumes:\s*$", base) is None
  assert re.search(r"(?m)^volumes:\s*$", settings) is not None
  for name in (
      "hpcperfstatsdata",
      "staticfiles_data",
      "media_data",
      "postgres_data",
      "postgres_data_pg18",
      "rabbitmq_messages",
      "ssh_keys",
      "proxy_ssl_source",
  ):
    assert f"{name}:" in settings
    assert "driver: local" in settings
    # Service mounts in base still reference the volume names.
    assert name in base
  assert "ssl_certs:" not in settings
  assert "ssl_certs:/etc/ssl/hpcperfstats" not in base
  assert "proxy_ssl_source:/mnt/ssl-source:ro" in base


def test_docker_compose_db_pg18_dual_run_beside_hub_pg15():
  """Hub PG15 keeps alias db; homemade PG18 is profile-gated with db18 + io_uring."""
  repo_root = Path(__file__).resolve().parents[2]
  content = (repo_root / "docker-compose.yaml").read_text()
  db_m = re.search(r"(?ms)^  db:\n(.*?)(?=^  [a-z].*:|\Z)", content)
  assert db_m, "db service not found"
  db_block = db_m.group(0)
  assert "timescale/timescaledb:2.28.3-pg15" in db_block
  assert "io_method=io_uring" not in db_block
  assert 'shm_size: "16gb"' in db_block

  pg18_m = re.search(r"(?ms)^  db_pg18:\n(.*?)(?=^  [a-z].*:|\Z)", content)
  assert pg18_m, "db_pg18 service not found"
  pg18 = pg18_m.group(0)
  assert "services-conf/db.Dockerfile" in pg18 or "dockerfile: db.Dockerfile" in pg18
  assert "image: hpcperfstats-db" in pg18
  assert "pg18-migrate" in pg18
  assert "io_method=io_uring" in pg18
  assert "seccomp=unconfined" in pg18
  assert 'shm_size: "16gb"' in pg18
  assert "postgres_data_pg18:/var/lib/postgresql" in pg18
  assert "- db18" in pg18
  # Cutover must not steal alias db while Hub PG15 is still the live writer.
  assert re.search(r"(?m)^\s+-\s+db\s*$", pg18) is None


def test_docker_compose_pipeline_ssh_uses_ssh_keys_volume():
  repo_root = Path(__file__).resolve().parents[2]
  content = (repo_root / "docker-compose.yaml").read_text()
  assert "ssh_keys:/hpcperfstats/.ssh/:ro" in content
  assert "HPCPERFSTATS_PIPELINE_SSH_DIR" not in content
  assert "HPCPERFSTATS_INI=/home/hpcperfstats/hpcperfstats.ini" in content
  assert "./hpcperfstats.ini:/home/hpcperfstats/hpcperfstats.ini:ro" not in content
  assert "target: hpcperfstats-full" in content


def test_docker_compose_web_build_uses_hpcperfstats_full_target():
  """Compose must request full image; rebuild_pipeline.sh uses pipeline-refresh explicitly."""
  repo_root = Path(__file__).resolve().parents[2]
  content = (repo_root / "docker-compose.yaml").read_text()
  assert "dockerfile: Dockerfile" in content
  assert "target: hpcperfstats-full" in content
  assert content.index("target: hpcperfstats-full") < content.index("image: hpcperfstats")


# Operator-facing bind devices that must stay in docker-compose.settings.yaml.example
# (see docker-compose-settings-example-sync.mdc).
_OPERATOR_SETTINGS_SHARED_BIND_DEVICES = (
    "device: /data/hpcperfstats_data/site_data",
    "device: /data/hpcperfstats_site/staticfiles",
    "device: /data/hpcperfstats_site/media",
    "device: /data/hpcperfstats_db/pg15",
    "device: /data/hpcperfstats_db/pg18",
    "device: /data/hpcperfstats_data/rabbitmq",
)

_OPERATOR_SETTINGS_EXAMPLE_SSH_DEVICE = "device: /keys_directory/.ssh"
_OPERATOR_SETTINGS_EXAMPLE_SSL_DEVICE = "device: /opt/certs"

_OPERATOR_SETTINGS_VOLUME_NAMES = (
    "hpcperfstatsdata:",
    "staticfiles_data:",
    "media_data:",
    "postgres_data:",
    "postgres_data_pg18:",
    "rabbitmq_messages:",
    "ssh_keys:",
    "proxy_ssl_source:",
)


def test_docker_compose_settings_example_operator_markers():
  repo_root = Path(__file__).resolve().parents[2]
  example_content = (repo_root / "docker-compose.settings.yaml.example").read_text()
  for marker in _OPERATOR_SETTINGS_VOLUME_NAMES:
    assert marker in example_content, "example missing volume: %s" % marker
  for marker in _OPERATOR_SETTINGS_SHARED_BIND_DEVICES:
    assert marker in example_content, "example missing bind device: %s" % marker
  assert _OPERATOR_SETTINGS_EXAMPLE_SSH_DEVICE in example_content
  assert _OPERATOR_SETTINGS_EXAMPLE_SSL_DEVICE in example_content
  assert "HPCPERFSTATS_SSL_CERTS_REL" in example_content
  assert "ssl_certs:" not in example_content
  assert "SYS_PTRACE" in example_content
  assert "15672:15672" in example_content
  assert "5432:5432" in example_content


def test_docker_compose_settings_example_operator_parity():
  """Shared /data binds must match .example; ssh device may differ per site."""
  repo_root = Path(__file__).resolve().parents[2]
  settings_path = repo_root / "docker-compose.settings.yaml"
  example_path = repo_root / "docker-compose.settings.yaml.example"
  if not settings_path.is_file():
    return
  settings_content = settings_path.read_text()
  example_content = example_path.read_text()
  for marker in _OPERATOR_SETTINGS_SHARED_BIND_DEVICES:
    assert marker in settings_content, "settings yaml missing bind: %s" % marker
    assert marker in example_content, "example missing bind: %s" % marker
  for name in _OPERATOR_SETTINGS_VOLUME_NAMES:
    assert name in settings_content
    assert name in example_content
  assert "ssl_certs:" not in settings_content
  assert re.search(
      r"(?ms)^  ssh_keys:.*?^\s+device:\s+\S+",
      settings_content,
  ), "settings must set ssh_keys device"
  assert _OPERATOR_SETTINGS_EXAMPLE_SSH_DEVICE in example_content


def test_docker_compose_test_overlay_clears_host_binds():
  repo_root = Path(__file__).resolve().parents[2]
  example = repo_root / "tests" / "docker-compose.test-overlay.yaml.example"
  overlay_path = repo_root / "tests" / "docker-compose.test-overlay.yaml"
  assert example.is_file(), "committed test overlay template missing"
  overlay = example.read_text()
  if overlay_path.is_file():
    # Local (gitignored) copy must keep the same bind-clearing contract.
    for name in (
        "test_hpcperfstatsdata",
        "test_staticfiles_data",
        "test_media_data",
        "test_postgres_data",
        "test_postgres_data_pg18",
        "test_rabbitmq_messages",
        "test_ssh_keys",
        "test_proxy_ssl_source",
    ):
      assert name in overlay_path.read_text()
    assert "test_ssl_certs" not in overlay_path.read_text()
  for name in (
      "test_hpcperfstatsdata",
      "test_staticfiles_data",
      "test_media_data",
      "test_postgres_data",
      "test_postgres_data_pg18",
      "test_rabbitmq_messages",
      "test_ssh_keys",
      "test_proxy_ssl_source",
  ):
    assert name in overlay
  assert "test_postgres_data_pg18:/var/lib/postgresql" in overlay
  assert "test_proxy_ssl_source:/mnt/ssl-source:ro" in overlay
  assert "device: ./tests/fixtures/proxy-ssl" in overlay
  assert "test_ssl_certs" not in overlay
  assert "/data/hpcperfstats" not in overlay
  assert "/opt/hpcperfstats" not in overlay
  assert "/etc/ssl/hpcperfstats" not in overlay
  assert "additional_contexts:" not in overlay
  assert "ssl_certs: ./tests/fixtures/proxy-ssl" not in overlay


def test_docker_compose_pipeline_has_no_process_pool_shared_memory_override():
  """Thread-only pipeline workers do not require a service /dev/shm override."""
  import re

  repo_root = Path(__file__).resolve().parents[2]
  content = (repo_root / "docker-compose.yaml").read_text()
  # Extract the pipeline service block until the next top-level service key.
  m = re.search(
      r"(?ms)^  pipeline:\n(.*?)(?=^  [a-z].*:|\Z)",
      content,
  )
  assert m, "pipeline service not found"
  block = m.group(0)
  assert "POSIX SharedMemory for listend live-DB enqueue" not in block
  assert "shm_size:" not in block
  db_m = re.search(
      r"(?ms)^  db:\n(.*?)(?=^  [a-z].*:|\Z)",
      content,
  )
  assert db_m, "db service not found"
  assert 'shm_size: "16gb"' in db_m.group(0)
