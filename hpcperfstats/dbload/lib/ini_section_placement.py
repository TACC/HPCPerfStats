"""Canonical section placement contract for hpcperfstats.ini options.

Used by drift tests to ensure INI_OPTION_REGISTRY and hpcperfstats.ini.example
stay aligned with the section taxonomy documented in hpcperfstats-ini-format.mdc.
"""

from __future__ import annotations

DEFAULT_PINNING_OPTIONS = frozenset({
    "cpuset_pin_min_total_cores",
    "cpuset_pin_min_cores_per_node",
    "numa_pin_max_nodes_auto",
    "pin_proxy_in_compose",
    "web_numa_node",
    "pipeline_numa_node",
})

DEFAULT_POSTGRES_OPTIONS = frozenset({
    "engine_name",
    "dbname",
    "username",
    "password",
    "host",
    "port",
})

DEFAULT_INSTALL_OPTIONS = frozenset({
    "machine",
    "host_name_ext",
    "data_dir",
    "server",
    "restricted_queue_keywords",
    "debug",
    "staff_email_domain",
    "timezone",
    "secret_key",
    "total_cores",
})

PORTAL_WEB_TUNING_OPTIONS = frozenset({
    "cors_origin_scheme",
    "max_gunicorn_workers",
    "parallel_db_prefetch_max",
    "api_small_executor_max_workers",
    "db_conn_max_age",
    "db_statement_timeout_ms",
    "db_idle_in_transaction_session_timeout_ms",
})

OAUTH2_OPTIONS = frozenset({
    "client_id",
    "client_key",
    "oauth_base_url",
    "authorize_url",
})

RMQ_OPTIONS = frozenset({"rmq_server", "rmq_queue"})

SYSLOG_OPTIONS = frozenset({"allow_from", "listen_tcp", "listen_udp"})

CACHE_OPTIONS = frozenset({"redis_location"})

XALT_OPTIONS = frozenset({
    "xalt_engine",
    "xalt_name",
    "xalt_user",
    "xalt_password",
    "xalt_host",
})

# Longest prefix match wins when iterating rules in order.
SECTION_OPTION_PREFIX_RULES = (
    ("PIPELINE", ("sync_", "metrics_", "pipeline_overlap")),
    (
        "PORTAL",
        (
            "max_gunicorn_",
            "parallel_db_",
            "api_small_",
            "db_conn_",
            "db_statement_",
            "db_idle_",
            "cors_",
        ),
    ),
    ("XALT", ("xalt_",)),
    ("RMQ", ("rmq_",)),
)

EXPLICIT_OPTION_SECTION = {
    "acct_path": "PIPELINE",
    "archive_dir": "PIPELINE",
    "daily_archive_dir": "PIPELINE",
}


def expected_section(option: str) -> str:
  """Return the canonical ini section for *option*."""
  if option in EXPLICIT_OPTION_SECTION:
    return EXPLICIT_OPTION_SECTION[option]
  if option in DEFAULT_INSTALL_OPTIONS:
    return "DEFAULT"
  if option in DEFAULT_POSTGRES_OPTIONS:
    return "DEFAULT"
  if option in DEFAULT_PINNING_OPTIONS:
    return "DEFAULT"
  if option in PORTAL_WEB_TUNING_OPTIONS:
    return "PORTAL"
  if option in OAUTH2_OPTIONS:
    return "OAUTH2"
  if option in RMQ_OPTIONS:
    return "RMQ"
  if option in SYSLOG_OPTIONS:
    return "SYSLOG"
  if option in CACHE_OPTIONS:
    return "CACHE"
  if option in XALT_OPTIONS:
    return "XALT"
  for section, prefixes in SECTION_OPTION_PREFIX_RULES:
    if any(option.startswith(prefix) for prefix in prefixes):
      return section
  if option.startswith("archive_"):
    return "PIPELINE"
  raise KeyError("no section placement rule for ini option %r" % option)


def validate_registry_sections(registry) -> list[str]:
  """Return human-readable violations when registry section != expected_section."""
  violations = []
  for entry in registry:
    section, option = entry[0], entry[1]
    try:
      expected = expected_section(option)
    except KeyError:
      violations.append(
          "option %r in registry section %r has no placement rule"
          % (option, section)
      )
      continue
    if section != expected:
      violations.append(
          "option %r registered under %r but expected %r"
          % (option, section, expected)
      )
  return violations
