"""
Canonical section placement contract for hpcperfstats.ini options.

Used by drift tests to ensure INI_OPTION_REGISTRY and hpcperfstats.ini.example
stay aligned with the section taxonomy documented in hpcperfstats-ini-
format.mdc.

Attributes:
  CACHE_OPTIONS: Attribute.
  DEFAULT_INSTALL_OPTIONS: Attribute.
  DEFAULT_POSTGRES_OPTIONS: Attribute.
  EXPLICIT_OPTION_SECTION: Attribute.
  OAUTH2_OPTIONS: Attribute.
  PORTAL_WEB_TUNING_OPTIONS: Attribute.
  RMQ_OPTIONS: Attribute.
  SECTION_OPTION_PREFIX_RULES: Attribute.
  SYSLOG_OPTIONS: Attribute.
  XALT_OPTIONS: Attribute.
"""

from __future__ import annotations

from typing import Any

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
    "ssl_certs_dir",
    "restricted_queue_keywords",
    "debug",
    "staff_email_domain",
    "timezone",
    "secret_key",
    "total_cores",
})

PORTAL_WEB_TUNING_OPTIONS = frozenset({
    "cors_origin_scheme",
    "gunicorn_workers",
    "summary_aggregate_prefetch_max_threads",
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
    ("PIPELINE", ("sync_", "metrics_", "ingest_", "listend_")),
    (
        "PORTAL",
        (
            "gunicorn_",
            "summary_aggregate_",
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
  """
  Return the canonical ini section for *option*.
  
  Args:
    option (str): String for option.
  
  Returns:
    str: str produced by this call.
  
  Raises:
    KeyError: Raised when ``expected_section`` hits a ``KeyError`` failure
    path.
  
  Examples:
    >>> expected_section("x")  # doctest: +SKIP
  """
  if option in EXPLICIT_OPTION_SECTION:
    return EXPLICIT_OPTION_SECTION[option]
  if option in DEFAULT_INSTALL_OPTIONS:
    return "DEFAULT"
  if option in DEFAULT_POSTGRES_OPTIONS:
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


def validate_registry_sections(registry: Any) -> list[str]:
  """
  Return human-readable violations when registry section != expected_section.
  
  Args:
    registry (Any): Registry passed to this helper.
  
  Returns:
    list[str]: list[str] produced by this call.
  
  Examples:
    >>> validate_registry_sections(None)  # doctest: +SKIP
  """
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
