"""Configuration parser for HPCPerfStats. Reads hpcperfstats.ini and exposes getters for portal, RMQ, XALT, and OAuth2 settings.

"""
import configparser
import ipaddress
import math
import os
import re
from zoneinfo import ZoneInfo

_DEFAULT_TOTAL_CORES = "40"

cfg = None
_ACTIVE_CONFIG_PATH = None

# Canonical (section, option) pairs read from hpcperfstats.ini. Used by
# test_hpcperfstats_ini_example drift guards; keep in sync when adding getters.
INI_OPTION_REGISTRY = (
    # [DEFAULT] — install, site-wide, PostgreSQL connection, cpuset pinning (last in example)
    ("DEFAULT", "machine"),
    ("DEFAULT", "host_name_ext"),
    ("DEFAULT", "data_dir"),
    ("DEFAULT", "server"),
    ("DEFAULT", "restricted_queue_keywords"),
    ("DEFAULT", "debug"),
    ("DEFAULT", "staff_email_domain"),
    ("DEFAULT", "timezone"),
    ("DEFAULT", "secret_key"),
    ("DEFAULT", "total_cores"),
    ("DEFAULT", "engine_name"),
    ("DEFAULT", "dbname"),
    ("DEFAULT", "username"),
    ("DEFAULT", "password"),
    ("DEFAULT", "host"),
    ("DEFAULT", "port"),
    ("DEFAULT", "cpuset_pin_min_total_cores"),
    ("DEFAULT", "cpuset_pin_min_cores_per_node"),
    ("DEFAULT", "numa_pin_max_nodes_auto"),
    ("DEFAULT", "pin_proxy_in_compose"),
    ("DEFAULT", "web_numa_node"),
    ("DEFAULT", "pipeline_numa_node"),
    # [PORTAL] — Gunicorn / Django web stack tuning
    ("PORTAL", "cors_origin_scheme"),
    ("PORTAL", "max_gunicorn_workers"),
    ("PORTAL", "parallel_db_prefetch_max"),
    ("PORTAL", "api_small_executor_max_workers"),
    ("PORTAL", "db_conn_max_age"),
    ("PORTAL", "db_statement_timeout_ms"),
    ("PORTAL", "db_idle_in_transaction_session_timeout_ms"),
    # [PIPELINE] — sync_timedb, update_metrics, sync_acct, archive paths/tuning
    ("PIPELINE", "pipeline_overlap_mode"),
    ("PIPELINE", "metrics_pool_process_cap"),
    ("PIPELINE", "metrics_ingest_priority_scale"),
    ("PIPELINE", "metrics_min_processes"),
    ("PIPELINE", "metrics_scheduler_mode"),
    ("PIPELINE", "metrics_scheduler_prefetch_chunks"),
    ("PIPELINE", "metrics_scheduler_ready_queue_target"),
    ("PIPELINE", "metrics_plot_prewarm_mode"),
    ("PIPELINE", "metrics_scheduler_skip_prewarm"),
    ("PIPELINE", "metrics_prewarm_workers"),
    ("PIPELINE", "metrics_prewarm_backlog_cap"),
    ("PIPELINE", "metrics_prewarm_backpressure_wait_s"),
    ("PIPELINE", "metrics_prewarm_drain_batch_budget_s"),
    ("PIPELINE", "metrics_prewarm_drain_batch_budget_max_s"),
    ("PIPELINE", "metrics_prewarm_drain_per_job_s"),
    ("PIPELINE", "metrics_prewarm_retry_attempts"),
    ("PIPELINE", "metrics_scheduler_compute_threads"),
    ("PIPELINE", "metrics_run_poll_timeout_s"),
    ("PIPELINE", "metrics_run_stall_timeout_s"),
    ("PIPELINE", "metrics_run_per_job_timeout_s"),
    ("PIPELINE", "metrics_persist_statement_timeout_ms"),
    ("PIPELINE", "metrics_persist_lock_timeout_ms"),
    ("PIPELINE", "metrics_proxy_reject_jid_batch_size"),
    ("PIPELINE", "metrics_compute_batch_max_window_s"),
    ("PIPELINE", "metrics_compute_batch_max_single_job_s"),
    ("PIPELINE", "metrics_compute_batch_unknown_runtime_s"),
    ("PIPELINE", "metrics_compute_watchdog_s"),
    ("PIPELINE", "metrics_compute_total_watchdog_s"),
    ("PIPELINE", "metrics_deferred_not_ready_retry_s"),
    ("PIPELINE", "metrics_deferred_not_ready_max_retries"),
    ("PIPELINE", "metrics_deferred_not_ready_max_age_s"),
    ("PIPELINE", "metrics_deferred_not_ready_quarantine_s"),
    ("PIPELINE", "metrics_readiness_require_window_coverage"),
    ("PIPELINE", "metrics_readiness_start_margin_seconds"),
    ("PIPELINE", "metrics_readiness_end_margin_seconds"),
    ("PIPELINE", "sync_pool_process_cap"),
    ("PIPELINE", "archive_pool_process_cap"),
    ("PIPELINE", "sync_enable_cpuset_priority_budget"),
    ("PIPELINE", "sync_budget_ingest_ratio"),
    ("PIPELINE", "sync_budget_archive_ratio"),
    ("PIPELINE", "sync_budget_metrics_ratio"),
    ("PIPELINE", "sync_budget_reserve_ratio"),
    ("PIPELINE", "sync_budget_min_metrics_percent"),
    ("PIPELINE", "sync_budget_min_archive_percent"),
    ("PIPELINE", "sync_enable_overprovision_mode"),
    ("PIPELINE", "sync_budget_overcommit_factor"),
    ("PIPELINE", "sync_overprovision_ingest_multiplier"),
    ("PIPELINE", "sync_overprovision_archive_multiplier"),
    ("PIPELINE", "sync_overprovision_metrics_multiplier"),
    ("PIPELINE", "sync_ingest_queue_max_size"),
    ("PIPELINE", "sync_archive_queue_max_size"),
    ("PIPELINE", "sync_archive_retry_max_attempts"),
    ("PIPELINE", "sync_archive_retry_backoff_base_seconds"),
    ("PIPELINE", "sync_archive_retry_backoff_max_seconds"),
    ("PIPELINE", "sync_checkpoint_flush_batch_size"),
    ("PIPELINE", "sync_host_itimes_cache_max_timestamps_per_entry"),
    ("PIPELINE", "sync_pool_poll_timeout_s"),
    ("PIPELINE", "sync_pool_stall_abort_after_timeouts"),
    ("PIPELINE", "sync_ingest_per_file_timeout_s"),
    ("PIPELINE", "sync_archive_members_cache_enabled"),
    ("PIPELINE", "sync_archive_members_cache_max_entries"),
    ("PIPELINE", "sync_write_lock_shards"),
    ("PIPELINE", "sync_enable_db_writer_pipeline"),
    ("PIPELINE", "sync_db_writer_combined_task"),
    ("PIPELINE", "sync_db_writer_stage_max_batch"),
    ("PIPELINE", "sync_ingest_chunk_size"),
    ("PIPELINE", "sync_supervisor_rss_limit_mb"),
    ("PIPELINE", "sync_supervisor_rss_check_every_n_chunks"),
    ("PIPELINE", "sync_db_writer_pool_multiplier"),
    ("PIPELINE", "sync_db_writer_pool_cap"),
    ("PIPELINE", "sync_adaptive_dispatch_enabled"),
    ("PIPELINE", "sync_dispatch_burst_factor"),
    ("PIPELINE", "sync_dispatch_archive_backoff_ratio"),
    ("PIPELINE", "sync_dispatch_step_size"),
    ("PIPELINE", "sync_enable_ingest_first_durability_mode"),
    ("PIPELINE", "sync_archive_require_db_head_ingest"),
    ("PIPELINE", "sync_archive_maint_hints"),
    ("PIPELINE", "sync_archive_discovery_workers"),
    ("PIPELINE", "acct_path"),
    ("PIPELINE", "archive_dir"),
    ("PIPELINE", "daily_archive_dir"),
    ("PIPELINE", "archive_keep_uncompressed_tar"),
    ("PIPELINE", "archive_today_uncompressed_tar_grace_hours"),
    ("PIPELINE", "archive_seal_idle_seconds"),
    ("PIPELINE", "archive_zstd_threads"),
    ("PIPELINE", "archive_zstd_level"),
    ("PIPELINE", "archive_zstd_nice"),
    ("PIPELINE", "archive_zstd_ionice_class"),
    ("PIPELINE", "archive_zstd_ionice_level"),
    ("PIPELINE", "archive_seal_parallel_workers"),
    ("PIPELINE", "archive_maintenance_interval_seconds"),
    ("PIPELINE", "archive_maintenance_max_defer_seconds"),
    ("PIPELINE", "archive_maintenance_idle_seconds"),
    ("PIPELINE", "archive_janitor_budget_seconds"),
    ("PIPELINE", "archive_janitor_days_per_tick"),
    ("PIPELINE", "archive_janitor_debt_high_watermark"),
    ("PIPELINE", "archive_janitor_debt_burst_factor"),
    ("PIPELINE", "archive_janitor_debt_max_entries"),
    ("PIPELINE", "archive_janitor_raw_paths_per_tick"),
    ("PIPELINE", "sync_unparsable_raw_quarantine_max_per_tick"),
    ("PIPELINE", "sync_startup_raw_removal_preflight"),
    ("PIPELINE", "sync_startup_raw_removal_verify_budget_seconds"),
    ("PIPELINE", "sync_startup_raw_removal_verify_days_per_slice"),
    ("PIPELINE", "sync_startup_raw_removal_max_deletes_per_pass"),
    ("PIPELINE", "sync_day_close_raw_removal_preflight"),
    ("PIPELINE", "sync_day_close_raw_removal_verify_budget_seconds"),
    ("PIPELINE", "sync_day_close_raw_removal_max_deletes_per_pass"),
    ("PIPELINE", "sync_archive_max_inflight_jobs"),
    ("PIPELINE", "sync_archive_worker_stall_seconds"),
    # [OAUTH2]
    ("OAUTH2", "client_id"),
    ("OAUTH2", "client_key"),
    ("OAUTH2", "oauth_base_url"),
    ("OAUTH2", "authorize_url"),
    # [RMQ]
    ("RMQ", "rmq_server"),
    ("RMQ", "rmq_queue"),
    # [SYSLOG] — section optional; keys documented when section is used
    ("SYSLOG", "allow_from"),
    ("SYSLOG", "listen_tcp"),
    ("SYSLOG", "listen_udp"),
    # [CACHE] — section optional
    ("CACHE", "redis_location"),
    # [XALT]
    ("XALT", "xalt_engine"),
    ("XALT", "xalt_name"),
    ("XALT", "xalt_user"),
    ("XALT", "xalt_password"),
    ("XALT", "xalt_host"),
)


def ini_option_registry_set():
  """Return the set of (section, option) tuples in INI_OPTION_REGISTRY."""
  return set(INI_OPTION_REGISTRY)


def _candidate_config_paths():
  """Return candidate config paths in lookup order.

  Search order is explicit env override first, then common runtime locations,
  then the bundled example as a development fallback.
  """
  module_dir = os.path.dirname(os.path.realpath(__file__))
  env_path = os.environ.get("HPCPERFSTATS_INI", "").strip()
  if env_path:
    # Explicit operator override should be authoritative.
    return [env_path]

  return [
      # Local development from repo root.
      os.path.abspath(os.path.join(os.getcwd(), "hpcperfstats.ini")),
      # Common container/runtime location.
      "/home/hpcperfstats/hpcperfstats.ini",
      # Source tree locations.
      os.path.abspath(os.path.join(module_dir, "..", "hpcperfstats.ini")),
      os.path.abspath(os.path.join(module_dir, "..", "hpcperfstats.ini.example")),
  ]


def _load_cfg():
  """Load config from first existing candidate path."""
  global cfg
  global _ACTIVE_CONFIG_PATH
  parser = configparser.ConfigParser()
  attempted_paths = _candidate_config_paths()
  for path in attempted_paths:
    if not os.path.isfile(path):
      continue
    if parser.read(path):
      cfg = parser
      _ACTIVE_CONFIG_PATH = path
      return
  raise FileNotFoundError(
      "Unable to locate HPCPerfStats config file. Set HPCPERFSTATS_INI or place "
      "hpcperfstats.ini at /home/hpcperfstats/hpcperfstats.ini. Attempted paths: "
      "%s" % ", ".join(attempted_paths)
  )


def _ensure_cfg_loaded():
  """Initialize config parser lazily."""
  if cfg is None:
    _load_cfg()


def _get(section, option):
  """Return config value for section/option. Single place for simple getters."""
  _ensure_cfg_loaded()
  if not cfg.has_section(section) and section != "DEFAULT":
    raise configparser.NoSectionError(
        "Missing section '%s' in %s" % (
            section,
            _ACTIVE_CONFIG_PATH,
        )
    )
  return cfg.get(section, option)


def _ini_has_option(section, option):
  """Return True when *option* is set under *section* (DEFAULT always exists)."""
  _ensure_cfg_loaded()
  if section == "DEFAULT":
    return cfg.has_option("DEFAULT", option)
  return cfg.has_section(section) and cfg.has_option(section, option)


def _ini_option(primary_section, option, legacy_sections=()):
  """Read *option* from *primary_section*, then legacy sections in order."""
  _ensure_cfg_loaded()
  if _ini_has_option(primary_section, option):
    return cfg.get(primary_section, option)
  for legacy in legacy_sections:
    if _ini_has_option(legacy, option):
      return cfg.get(legacy, option)
  if not cfg.has_section(primary_section) and primary_section != "DEFAULT":
    raise configparser.NoSectionError(
        "Missing section '%s' in %s" % (primary_section, _ACTIVE_CONFIG_PATH)
    )
  raise configparser.NoOptionError(option, primary_section)


def _ini_get(primary_section, option, *, fallback=None, legacy_sections=()):
  """Like ``_ini_option`` but returns *fallback* when absent everywhere."""
  _ensure_cfg_loaded()
  if _ini_has_option(primary_section, option):
    return cfg.get(primary_section, option)
  for legacy in legacy_sections:
    if _ini_has_option(legacy, option):
      return cfg.get(legacy, option)
  if fallback is not None:
    return str(fallback)
  return _ini_option(primary_section, option, legacy_sections=legacy_sections)


def _ini_getint(primary_section, option, *, legacy_sections=()):
  return int(_ini_option(primary_section, option, legacy_sections=legacy_sections))


def _ini_has_option_any(primary_section, option, legacy_sections=()):
  if _ini_has_option(primary_section, option):
    return True
  return any(_ini_has_option(legacy, option) for legacy in legacy_sections)


_PIPELINE_LEGACY = ("DEFAULT",)
_PIPELINE_ARCHIVE_LEGACY = ("PORTAL",)


def _pipeline_get(option, *, fallback):
  """Read a PIPELINE option with DEFAULT legacy fallback."""
  return _ini_get(
      "PIPELINE", option, fallback=str(fallback), legacy_sections=_PIPELINE_LEGACY,
  )


def _pipeline_has_option(option):
  return _ini_has_option_any("PIPELINE", option, _PIPELINE_LEGACY)


def _pipeline_getint(option, *, fallback):
  return int(_pipeline_get(option, fallback=fallback))


def _pipeline_getfloat(option, *, fallback):
  return float(_pipeline_get(option, fallback=fallback))


def _parse_bool(raw, *, default=False):
  if raw is None:
    return default
  value = str(raw).strip().lower()
  if value in ("1", "yes", "true", "on"):
    return True
  if value in ("0", "no", "false", "off"):
    return False
  return default


def _env_or_cfg_int(env_key, section, option, fallback, legacy_sections=()):
  env = os.environ.get(env_key, "").strip()
  if env:
    return int(env)
  _ensure_cfg_loaded()
  return int(_ini_get(
      section, option, fallback=str(fallback), legacy_sections=legacy_sections,
  ))


def _env_or_cfg_bounded_float(
    env_key, section, option, fallback, *, lower, upper, legacy_sections=(),
):
  env = os.environ.get(env_key, "").strip()
  if env:
    return max(lower, min(upper, float(env)))
  _ensure_cfg_loaded()
  return max(
      lower,
      min(upper, float(_ini_get(
          section, option, fallback=str(fallback), legacy_sections=legacy_sections,
      ))),
  )


def _optional_default_int_option(option_name, *, legacy_sections=()):
  _ensure_cfg_loaded()
  if not _ini_has_option_any("DEFAULT", option_name, legacy_sections):
    return None
  value = _ini_get(
      "DEFAULT",
      option_name,
      legacy_sections=legacy_sections,
  ).strip()
  if not value:
    return None
  return int(value)


def get_db_connection_string():
  """Return a PostgreSQL connection string from DEFAULT config (legacy: PORTAL)."""
  legacy = ("PORTAL",)
  return "dbname={0} user={1} password={2} port={3} host={4}".format(
      _ini_option("DEFAULT", "dbname", legacy_sections=legacy),
      _ini_option("DEFAULT", "username", legacy_sections=legacy),
      _ini_option("DEFAULT", "password", legacy_sections=legacy),
      _ini_option("DEFAULT", "port", legacy_sections=legacy),
      _ini_option("DEFAULT", "host", legacy_sections=legacy),
  )


def get_db_name():
  """Return the database name from DEFAULT config (legacy: PORTAL)."""
  return _ini_option("DEFAULT", "dbname", legacy_sections=("PORTAL",))


def get_debug():
  """Return True if DEFAULT.debug is yes/true/1, else False.

    Missing DEFAULT.debug is treated as False to keep startup resilient when
    older/minimal configs omit this optional setting.
    """
  _ensure_cfg_loaded()
  return cfg.get('DEFAULT', 'debug', fallback='no').lower() in ("yes", "true", "1")


def get_secret_key():
  """Return Django SECRET_KEY from DEFAULT.secret_key, or None if not set.

    Prefer environment variable SECRET_KEY over ini; settings.py should check
    os.environ first, then this, then fail or use dev default.
    """
  _ensure_cfg_loaded()
  if cfg.has_option('DEFAULT', 'secret_key'):
    return cfg.get('DEFAULT', 'secret_key').strip() or None
  return None


def get_archive_dir_path():
  """Return the archive directory path from PIPELINE config (legacy: PORTAL)."""
  raw = _ini_option("PIPELINE", "archive_dir", legacy_sections=("PORTAL",)).strip()
  return os.path.normpath(raw) if raw else raw


def get_host_name_ext():
  """Return the host name extension (domain) from DEFAULT config."""
  return _get('DEFAULT', 'host_name_ext')


def get_restricted_queue_keywords():
  """Return restricted queue keywords string from DEFAULT config."""
  return _get('DEFAULT', 'restricted_queue_keywords')


def get_accounting_path():
  """Return the accounting (sacct) file path from PIPELINE config (legacy: PORTAL)."""
  return _ini_option("PIPELINE", "acct_path", legacy_sections=("PORTAL",))


def get_daily_archive_dir_path():
  """Return the daily archive directory path from PIPELINE config (legacy: PORTAL)."""
  raw = _ini_option(
      "PIPELINE", "daily_archive_dir", legacy_sections=("PORTAL",),
  ).strip()
  return os.path.normpath(raw) if raw else raw


def get_archive_keep_uncompressed_tar():
  """Return True if daily ``.tar`` should always be kept after sealing.

  Default False; calendar-today grace is handled by
  ``effective_keep_uncompressed_tar`` unless this global override is yes.
  """
  _ensure_cfg_loaded()
  raw = _ini_get(
      "PIPELINE",
      "archive_keep_uncompressed_tar",
      fallback="no",
      legacy_sections=("PORTAL",),
  )
  return raw.lower() in ('yes', 'true', '1')


def get_archive_today_uncompressed_tar_grace_hours():
  """Hours after local midnight to retain today's uncompressed ``.tar`` when global keep is off."""
  _ensure_cfg_loaded()
  return max(
      0.0,
      _pipeline_getfloat("archive_today_uncompressed_tar_grace_hours", fallback=8.0),
  )


def get_archive_seal_idle_seconds():
  """Minimum seconds since last ``.tar`` mtime before sealing *today's* archive.

  Prior calendar days seal as soon as the tar/gz pair is dirty (no idle wait).
  """
  _ensure_cfg_loaded()
  return float(_ini_get(
      "PIPELINE",
      "archive_seal_idle_seconds",
      fallback="60",
      legacy_sections=("PORTAL",),
  ))


def _ini_bool(value, default=False):
  if value is None:
    return default
  return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def get_archive_zstd_level():
  """Native zstd compression level (1--19) for sealing ``.tar.zst``. Default 7."""
  _ensure_cfg_loaded()
  raw = _ini_get(
      "PIPELINE", "archive_zstd_level", fallback="7", legacy_sections=("PORTAL",),
  )
  level = int(raw)
  return max(1, min(19, level))


def get_archive_zstd_threads():
  """zstd ``-T`` thread count for archive compress/decompress. Default 0 (-T0)."""
  _ensure_cfg_loaded()
  raw = _ini_get(
      "PIPELINE", "archive_zstd_threads", fallback="0", legacy_sections=("PORTAL",),
  )
  return max(0, int(raw))


def get_archive_zstd_nice():
  """Added nice for archive zstd child processes (0 disables). Default 10."""
  _ensure_cfg_loaded()
  raw = _ini_get(
      "PIPELINE", "archive_zstd_nice", fallback="10", legacy_sections=("PORTAL",),
  )
  return max(0, int(raw))


def get_archive_zstd_ionice_class():
  """I/O scheduling class for archive zstd (0=none, 2=best-effort, 3=idle). Default 2."""
  _ensure_cfg_loaded()
  raw = _ini_get(
      "PIPELINE",
      "archive_zstd_ionice_class",
      fallback="2",
      legacy_sections=("PORTAL",),
  )
  return max(0, min(3, int(raw)))


def get_archive_zstd_ionice_level():
  """I/O priority level within class for archive zstd (0-7). Default 6."""
  _ensure_cfg_loaded()
  raw = _ini_get(
      "PIPELINE",
      "archive_zstd_ionice_level",
      fallback="6",
      legacy_sections=("PORTAL",),
  )
  return max(0, min(7, int(raw)))


def get_archive_seal_parallel_workers():
  """Max concurrent daily tar seals during maintenance. Default 4."""
  _ensure_cfg_loaded()
  raw = _ini_get(
      "PIPELINE",
      "archive_seal_parallel_workers",
      fallback="4",
      legacy_sections=("PORTAL",),
  )
  return max(1, int(raw))


def get_archive_maintenance_interval_seconds():
  """Deprecated: retained for INI compatibility; sync_timedb ignores this value."""
  _ensure_cfg_loaded()
  default_interval = float(8 * 3600)
  raw_value = _ini_get(
      "PIPELINE",
      "archive_maintenance_interval_seconds",
      fallback=str(default_interval),
      legacy_sections=("PORTAL",),
  )
  try:
    interval = float(raw_value)
  except (TypeError, ValueError):
    return default_interval
  if (not math.isfinite(interval)) or interval <= 0:
    return default_interval
  return interval


def get_archive_maintenance_max_defer_seconds():
  """Max seconds to defer scheduled maintenance while archive append is in flight (default 1h)."""
  _ensure_cfg_loaded()
  default_max_defer = float(3600)
  raw_value = _ini_get(
      "PIPELINE",
      "archive_maintenance_max_defer_seconds",
      fallback=str(default_max_defer),
      legacy_sections=("PORTAL",),
  )
  try:
    max_defer = float(raw_value)
  except (TypeError, ValueError):
    return default_max_defer
  if (not math.isfinite(max_defer)) or max_defer <= 0:
    return default_max_defer
  return max_defer


def get_rmq_server():
  """Return the RabbitMQ server host from RMQ config."""
  return _get('RMQ', 'rmq_server')


def get_rmq_queue():
  """Return the RabbitMQ queue name from RMQ config."""
  return _get('RMQ', 'rmq_queue')


def get_machine_name():
  """Return the machine name from DEFAULT config."""
  return _get('DEFAULT', 'machine')


def get_server_name():
  """Return the server name from DEFAULT config."""
  return _get('DEFAULT', 'server')


def get_cors_origin_scheme():
  """Return ``http`` or ``https`` when building CORS origins from ``[DEFAULT] server``.

  Optional ``[PORTAL] cors_origin_scheme`` may be set to ``http`` or ``https``
  (legacy: ``[DEFAULT]``). When omitted, defaults to ``https``.
  """
  _ensure_cfg_loaded()
  raw = _ini_get(
      "PORTAL",
      "cors_origin_scheme",
      fallback="",
      legacy_sections=("DEFAULT",),
  ).strip().lower()
  if raw in ('http', 'https'):
    return raw
  return 'https'


def format_cors_allowed_origins_csv_from_ini():
  """Build comma-separated browser ``Origin`` values from ``[DEFAULT] server``.

  Mirrors how Django ``ALLOWED_HOSTS`` is populated from the same ``server``
  key: each comma-separated hostname becomes ``{scheme}://{host}`` unless the
  token already contains a scheme.

  Returns an empty string when ``debug`` is enabled (Django applies Vite dev
  origins instead) or when ``server`` is blank.
  """
  if get_debug():
    return ''
  raw = (get_server_name() or '').strip()
  if not raw:
    return ''
  scheme = get_cors_origin_scheme()
  origins = []
  for part in raw.split(','):
    host = part.strip()
    if not host:
      continue
    if '://' in host:
      origins.append(host.rstrip('/'))
    else:
      origins.append('%s://%s' % (scheme, host))
  return ','.join(origins)


def get_data_dir_path():
  """Return the data directory path from DEFAULT config."""
  return _get('DEFAULT', 'data_dir')


def get_syslog_allow_from_ipv4_networks():
  """Return IPv4 CIDR strings for pipeline syslog-ng ``netmask()`` allowlist.

  Whitespace and commas separate entries. ``#`` starts an end-of-line comment.
  An **empty** list (missing ``[SYSLOG]``, blank ``allow_from``, or only
  comments) means **allow all IPv4** (``0.0.0.0/0``) for backward compatibility.
  IPv6-only networks are skipped with no error (syslog-ng filter uses
  ``netmask()`` IPv4 form in generated config).
  """
  _ensure_cfg_loaded()
  if not cfg.has_section('SYSLOG'):
    return []
  raw = cfg.get('SYSLOG', 'allow_from', fallback='').strip()
  if not raw:
    return []
  nets = []
  for line in raw.replace(',', '\n').splitlines():
    line = line.split('#', 1)[0].strip()
    if not line:
      continue
    for token in re.split(r'[\s,]+', line):
      token = token.strip()
      if not token or token.startswith('#'):
        continue
      try:
        net = ipaddress.ip_network(token, strict=False)
      except ValueError as exc:
        raise ValueError(
            "Invalid SYSLOG allow_from entry %r: %s" % (token, exc),
        ) from exc
      if net.version != 4:
        continue
      nets.append(str(net))
  return nets


def get_syslog_listen_tcp():
  """Return True if pipeline syslog-ng should listen on TCP 514 (default True)."""
  _ensure_cfg_loaded()
  if not cfg.has_section('SYSLOG'):
    return True
  return _parse_bool(cfg.get('SYSLOG', 'listen_tcp', fallback='yes'), default=True)


def get_syslog_listen_udp():
  """Return True if pipeline syslog-ng should listen on UDP 514 (default True)."""
  _ensure_cfg_loaded()
  if not cfg.has_section('SYSLOG'):
    return True
  return _parse_bool(cfg.get('SYSLOG', 'listen_udp', fallback='yes'), default=True)


def get_syslog_logs_current_path():
  """Directory for live per-host syslog files (under ``data_dir``)."""
  return os.path.normpath(
      os.path.join(get_data_dir_path(), 'logs', 'current'),
  )


def get_syslog_logs_archive_path():
  """Directory for sealed daily syslog tarballs (under ``data_dir``)."""
  return os.path.normpath(
      os.path.join(get_data_dir_path(), 'logs', 'log_archive'),
  )


def get_syslog_generated_config_path():
  """Runtime path for syslog-ng fragment (inside the pipeline container).

  Stored under ``/var/lib/`` (not ``services-conf/``) so a bind-mount over
  ``/home/hpcperfstats/services-conf`` cannot hide or make read-only the
  generated file that ``@include`` requires.
  """
  return '/var/lib/hpcperfstats-syslog/generated.conf'


def get_engine_name():
  """Return the Django database engine name from DEFAULT config (legacy: PORTAL)."""
  return _ini_option("DEFAULT", "engine_name", legacy_sections=("PORTAL",))


def get_username():
  """Return the portal DB username from DEFAULT config (legacy: PORTAL)."""
  return _ini_option("DEFAULT", "username", legacy_sections=("PORTAL",))


def get_password():
  """Return the portal DB password from DEFAULT config (legacy: PORTAL)."""
  return _ini_option("DEFAULT", "password", legacy_sections=("PORTAL",))


def get_host():
  """Return the portal DB host from DEFAULT config (legacy: PORTAL)."""
  return _ini_option("DEFAULT", "host", legacy_sections=("PORTAL",))


def get_port():
  """Return the portal DB port from DEFAULT config (legacy: PORTAL)."""
  return _ini_option("DEFAULT", "port", legacy_sections=("PORTAL",))


def get_xalt_engine():
  """Return the XALT database engine from XALT config."""
  return _get('XALT', 'xalt_engine')


def get_xalt_name():
  """Return the XALT database name from XALT config."""
  return _get('XALT', 'xalt_name')


def get_xalt_user():
  """Return the XALT DB user from XALT config."""
  return _get('XALT', 'xalt_user')


def get_xalt_password():
  """Return the XALT DB password from XALT config."""
  return _get('XALT', 'xalt_password')


def get_xalt_host():
  """Return the XALT DB host from XALT config."""
  return _get('XALT', 'xalt_host')


def get_oauth_client_id():
  """Return the OAuth2 client ID from OAUTH2 config."""
  return _get('OAUTH2', 'client_id')


def get_oauth_client_key():
  """Return the OAuth2 client key/secret from OAUTH2 config."""
  return _get('OAUTH2', 'client_key')


def get_oauth_authorize_url():
  """Return the OAuth2 authorization URL template from OAUTH2 config."""
  return _get('OAUTH2', 'authorize_url')


def get_oauth_base_url():
  """Return the OAuth2 tenant base URL from OAUTH2 config."""
  return _get('OAUTH2', 'oauth_base_url')


def get_staff_email_domain():
  """Return the staff email domain from DEFAULT config."""
  return _get('DEFAULT', 'staff_email_domain')


def get_timezone():
  """Return the timezone string from DEFAULT config."""
  return _get('DEFAULT', 'timezone')


def get_local_timezone():
  """Return the local timezone as a ZoneInfo for datetime conversion."""
  return ZoneInfo(get_timezone())


def get_total_cores():
  """Return the total cores count string from DEFAULT config.

  If ``total_cores`` is omitted, returns ``\"40\"`` (default when not set in ini).
  """
  _ensure_cfg_loaded()
  return cfg.get("DEFAULT", "total_cores", fallback=_DEFAULT_TOTAL_CORES).strip() or _DEFAULT_TOTAL_CORES


def get_ini_total_cores_int():
  """Return ``int(total_cores)`` from ini (or default 40 when missing)."""
  return int(get_total_cores())


def get_effective_cores():
  """Return ``min(ini total_cores, os.cpu_count())`` for pool / worker sizing.

  ``ini`` caps parallelism when the host has more CPUs; ``os.cpu_count()`` wins
  when ini overshoots hardware or inside a limited cgroup/cpuset.
  """
  host = os.cpu_count()
  if host is None or host < 1:
    host = 1
  ini_budget = get_ini_total_cores_int()
  return min(ini_budget, host)


def get_max_gunicorn_workers_cap():
  """Upper bound for Gunicorn workers (see ``django_startup.sh``).

  Default **32** pairs with a **40**-core ini budget: leaves headroom vs
  PostgreSQL ``max_connections`` (Compose default **500**) alongside metrics/sync pools.
  """
  _ensure_cfg_loaded()
  return int(_ini_get(
      "PORTAL",
      "max_gunicorn_workers",
      fallback="32",
      legacy_sections=("DEFAULT",),
  ))


def get_metrics_pool_process_cap():
  """Upper bound for ``multiprocessing.Pool`` process count in metrics compute."""
  env = os.environ.get("METRICS_POOL_PROCESS_CAP", "").strip()
  if env:
    return int(env)
  _ensure_cfg_loaded()
  return int(_ini_get(
      "PIPELINE",
      "metrics_pool_process_cap",
      fallback="32",
      legacy_sections=("DEFAULT",),
  ))


def get_metrics_pool_process_count():
  """Processes for metrics pool: ``min(max(1, effective//2), metrics_pool_process_cap)``."""
  raw = max(1, get_effective_cores() // 2)
  base = min(raw, get_metrics_pool_process_cap())
  if not get_sync_enable_cpuset_priority_budget():
    mode = get_pipeline_overlap_mode()
    if mode == "ingest_priority":
      scale = get_metrics_ingest_priority_scale()
      return max(get_metrics_min_processes(), int(math.floor(base * scale)))
    return base
  budget = derive_pipeline_cpuset_priority_budget()
  capped = max(1, min(base, budget["metrics_cap"]))
  mode = get_pipeline_overlap_mode()
  if mode == "ingest_priority":
    scale = get_metrics_ingest_priority_scale()
    return max(get_metrics_min_processes(), int(math.floor(capped * scale)))
  return capped


def get_cpuset_pin_min_total_cores():
  _ensure_cfg_loaded()
  return _pipeline_getint("cpuset_pin_min_total_cores", fallback=32)


def get_cpuset_pin_min_cores_per_node():
  _ensure_cfg_loaded()
  return _pipeline_getint("cpuset_pin_min_cores_per_node", fallback=16)


def get_web_numa_node():
  """Optional explicit sysfs node id for web+proxy; None if unset."""
  return _optional_default_int_option("web_numa_node")


def get_pipeline_numa_node():
  """Optional explicit sysfs node id for pipeline; None if unset."""
  return _optional_default_int_option("pipeline_numa_node")


def get_pin_proxy_for_compose():
  """If True, NUMA pinning script also sets ``cpuset`` on ``proxy`` (match web node)."""
  _ensure_cfg_loaded()
  return _parse_bool(_pipeline_get("pin_proxy_in_compose", fallback="no"))


def get_numa_pin_max_nodes_auto():
  """Auto compose pinning supports up to this many NUMA nodes without explicit ids."""
  _ensure_cfg_loaded()
  return _pipeline_getint("numa_pin_max_nodes_auto", fallback=16)


def get_parallel_db_prefetch_max_workers():
  """Max threads for parallel ORM prefetch (summary plots) and default API executor size.

  Default **4** (INI ``parallel_db_prefetch_max``); summary aggregate prefetch also applies a
  hard cap in ``summaryplot`` so nested pools do not multiply against the API executor.

  Override with ``[PORTAL] parallel_db_prefetch_max`` or env ``PARALLEL_DB_PREFETCH_MAX``.
  """
  env = os.environ.get("PARALLEL_DB_PREFETCH_MAX", "").strip()
  if env:
    return max(1, int(env))
  _ensure_cfg_loaded()
  return max(1, int(_ini_get(
      "PORTAL",
      "parallel_db_prefetch_max",
      fallback="4",
      legacy_sections=("DEFAULT",),
  )))


def get_api_small_executor_max_workers():
  """Max workers for shared ``ThreadPoolExecutor`` in ``site.machine.api``.

  If ``[PORTAL] api_small_executor_max_workers`` is set, it wins; otherwise
  ``get_parallel_db_prefetch_max_workers()`` (default **4**).
  """
  _ensure_cfg_loaded()
  if _ini_has_option_any(
      "PORTAL", "api_small_executor_max_workers", ("DEFAULT",),
  ):
    return max(1, int(_ini_get(
        "PORTAL",
        "api_small_executor_max_workers",
        legacy_sections=("DEFAULT",),
    )))
  return get_parallel_db_prefetch_max_workers()


def get_db_conn_max_age():
  """Django ``CONN_MAX_AGE`` in seconds (default **90**).

  Env ``DJANGO_CONN_MAX_AGE`` overrides ``[PORTAL] db_conn_max_age``.
  """
  return _env_or_cfg_int(
      "DJANGO_CONN_MAX_AGE", "PORTAL", "db_conn_max_age", 90,
      legacy_sections=("DEFAULT",),
  )


def get_db_statement_timeout_ms():
  """``statement_timeout`` in milliseconds for PostgreSQL session options.

  ``0`` means do not set (omit from Django ``OPTIONS``). Default **120000** (2 minutes).
  Env ``DJANGO_DB_STATEMENT_TIMEOUT_MS`` overrides ``[PORTAL] db_statement_timeout_ms``.
  """
  return _env_or_cfg_int(
      "DJANGO_DB_STATEMENT_TIMEOUT_MS",
      "PORTAL",
      "db_statement_timeout_ms",
      120000,
      legacy_sections=("DEFAULT",),
  )


def get_db_idle_in_transaction_session_timeout_ms():
  """``idle_in_transaction_session_timeout`` in ms; ``0`` = omit. Default **300000** (5 min)."""
  return _env_or_cfg_int(
      "DJANGO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS",
      "PORTAL",
      "db_idle_in_transaction_session_timeout_ms",
      300000,
      legacy_sections=("DEFAULT",),
  )


def build_postgres_connection_options():
  """Return Django ``DATABASES`` ``OPTIONS`` for libpq ``-c`` settings, or ``{}``."""
  parts = []
  st = get_db_statement_timeout_ms()
  if st > 0:
    parts.append("-c statement_timeout=%d" % st)
  it = get_db_idle_in_transaction_session_timeout_ms()
  if it > 0:
    parts.append("-c idle_in_transaction_session_timeout=%d" % it)
  if not parts:
    return {}
  return {"options": " ".join(parts)}


def get_worker_thread_count(divisor=4):
  """Return worker process count as ``effective_cores / divisor``, clamped to at least 1."""
  return max(1, get_effective_cores() // divisor)


def _apply_sync_pool_cap(size, cap):
  """Clamp *size* to *cap* when *cap* is set; result is at least 1."""
  n = max(1, int(size))
  if cap is None:
    return n
  return max(1, min(n, int(cap)))


def get_sync_pool_process_cap():
  """If set, caps ``sync_timedb`` main ingest pool. Env ``SYNC_POOL_PROCESS_CAP``."""
  env = os.environ.get("SYNC_POOL_PROCESS_CAP", "").strip()
  if env:
    return int(env)
  _ensure_cfg_loaded()
  if _pipeline_has_option("sync_pool_process_cap"):
    return _pipeline_getint("sync_pool_process_cap", fallback=1)
  return None


def get_archive_pool_process_cap():
  """If set, caps archive-side pool in ``sync_timedb``. Env ``ARCHIVE_POOL_PROCESS_CAP``."""
  env = os.environ.get("ARCHIVE_POOL_PROCESS_CAP", "").strip()
  if env:
    return int(env)
  _ensure_cfg_loaded()
  if _pipeline_has_option("archive_pool_process_cap"):
    return _pipeline_getint("archive_pool_process_cap", fallback=1)
  return None


def get_sync_ingest_pool_processes():
  """Worker count for ``sync_timedb`` after ``sync_pool_process_cap``.

  ``sync_timedb_archive`` uses half this count (minimum one process).
  """
  if get_sync_enable_cpuset_priority_budget():
    raw = derive_pipeline_cpuset_priority_budget()["sync_ingest_cap"]
  else:
    raw = get_worker_thread_count(2)
  return _apply_sync_pool_cap(raw, get_sync_pool_process_cap())


def get_sync_archive_pool_processes():
  """Archive pool size in ``sync_timedb`` (default 4, capped by ``archive_pool_process_cap``)."""
  if get_sync_enable_cpuset_priority_budget():
    raw = derive_pipeline_cpuset_priority_budget()["sync_archive_cap"]
  else:
    raw = 4
  return _apply_sync_pool_cap(raw, get_archive_pool_process_cap())


def _budget_ratio(name, fallback):
  return _pipeline_getfloat(name, fallback=fallback)


def _budget_floor_percent(name, fallback):
  return _pipeline_getint(name, fallback=fallback)


def get_pipeline_overlap_mode():
  """Pipeline overlap mode: balanced or ingest_priority."""
  env = os.environ.get("HPCPERFSTATS_PIPELINE_OVERLAP_MODE", "").strip().lower()
  if env in ("balanced", "ingest_priority"):
    return env
  _ensure_cfg_loaded()
  mode = _pipeline_get("pipeline_overlap_mode", fallback="balanced").strip().lower()
  return mode if mode in ("balanced", "ingest_priority") else "balanced"


def get_metrics_ingest_priority_scale():
  """Metrics pool downscale factor during ingest_priority overlap mode."""
  _ensure_cfg_loaded()
  return max(
      0.10,
      min(1.00, _pipeline_getfloat("metrics_ingest_priority_scale", fallback=0.75)),
  )


def get_metrics_min_processes():
  """Minimum metrics worker count under ingest-priority overlap mode."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("metrics_min_processes", fallback=1))


def get_metrics_scheduler_mode():
  """Metrics scheduler mode: strict_date, global_fifo, or global_priority."""
  env = os.environ.get("HPCPERFSTATS_METRICS_SCHEDULER_MODE", "").strip().lower()
  if env in ("strict_date", "global_fifo", "global_priority"):
    return env
  _ensure_cfg_loaded()
  mode = _pipeline_get(
      "metrics_scheduler_mode", fallback="global_priority",
  ).strip().lower()
  if mode in ("strict_date", "global_fifo", "global_priority"):
    return mode
  return "global_priority"


def get_metrics_scheduler_prefetch_chunks():
  """Max chunk descriptors prefetched ahead for global scheduler."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("metrics_scheduler_prefetch_chunks", fallback=8))


def get_metrics_scheduler_ready_queue_target():
  """Target ready-jid queue depth before compute dispatch."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("metrics_scheduler_ready_queue_target", fallback=2000))


def get_metrics_plot_prewarm_mode():
  """Prewarm mode for metrics pipeline: inline or pipeline_required."""
  env = os.environ.get("HPCPERFSTATS_METRICS_PLOT_PREWARM_MODE", "").strip().lower()
  if env in ("inline", "pipeline_required"):
    return env
  _ensure_cfg_loaded()
  mode = _pipeline_get(
      "metrics_plot_prewarm_mode", fallback="pipeline_required",
  ).strip().lower()
  if mode in ("inline", "pipeline_required"):
    return mode
  return "pipeline_required"


def get_metrics_scheduler_skip_prewarm():
  """When true, ``update_metrics`` persists metrics but skips job detail/plot prewarm."""
  env = os.environ.get("HPCPERFSTATS_METRICS_SCHEDULER_SKIP_PREWARM", "").strip()
  if env:
    return _parse_bool(env)
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("metrics_scheduler_skip_prewarm", fallback="no"),
  )


def get_metrics_per_jid_phase_diagnostics_enabled():
  """Env-only: emit per-batch jid phase lines from the metrics scheduler.

  Set ``HPCPERFSTATS_METRICS_PER_JID_PHASE_LOG`` to 1/true/yes/on. Default off.
  Intended for short compose-backed tuning runs (high log volume).
  """
  v = os.environ.get("HPCPERFSTATS_METRICS_PER_JID_PHASE_LOG", "").strip().lower()
  return v in ("1", "true", "yes", "on")


def get_metrics_prewarm_workers():
  """Thread workers for required plot prewarm stage."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("metrics_prewarm_workers", fallback=4))


def get_metrics_prewarm_backlog_cap():
  """Max queued async prewarm jobs before scheduler applies backpressure."""
  env = os.environ.get("HPCPERFSTATS_METRICS_PREWARM_BACKLOG_CAP", "").strip()
  if env:
    try:
      return max(1, int(env))
    except (TypeError, ValueError, OverflowError):
      return 32
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        int(_pipeline_get("metrics_prewarm_backlog_cap", fallback="32")),
    )
  except (TypeError, ValueError, OverflowError):
    return 32


def get_metrics_prewarm_backpressure_wait_s():
  """Seconds to wait for one async prewarm slot before inline fallback."""
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_PREWARM_BACKPRESSURE_WAIT_S", ""
  ).strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 0.25
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        _pipeline_getfloat("metrics_prewarm_backpressure_wait_s", fallback=0.25),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.25


def get_metrics_prewarm_drain_batch_budget_base_s():
  """Base seconds to drain async plot prewarm after each ``Metrics.run`` batch.

  Env ``HPCPERFSTATS_METRICS_PREWARM_DRAIN_BATCH_BUDGET_S`` overrides INI
  ``metrics_prewarm_drain_batch_budget_s``. Default 2.0 matches legacy constant.
  """
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_PREWARM_DRAIN_BATCH_BUDGET_S", ""
  ).strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 2.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        _pipeline_getfloat("metrics_prewarm_drain_batch_budget_s", fallback=2.0),
    )
  except (TypeError, ValueError, OverflowError):
    return 2.0


def get_metrics_prewarm_drain_batch_budget_max_s():
  """Ceiling for scaled per-batch prewarm drain budget (seconds)."""
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_PREWARM_DRAIN_BATCH_BUDGET_MAX_S", ""
  ).strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 60.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        _pipeline_getfloat("metrics_prewarm_drain_batch_budget_max_s", fallback=60.0),
    )
  except (TypeError, ValueError, OverflowError):
    return 60.0


def get_metrics_prewarm_drain_budget_per_successful_job_s():
  """Extra drain seconds added per successful jid in the batch (scaled budget)."""
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_PREWARM_DRAIN_PER_JOB_S", ""
  ).strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 0.5
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        _pipeline_getfloat("metrics_prewarm_drain_per_job_s", fallback=0.5),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.5


def get_metrics_compute_batch_max_window_seconds():
  """Max sum of job accounting-window seconds per compute batch (0 = disabled).

  Heterogeneity guard: avoids packing many multi-day jobs into one batch.
  Env ``HPCPERFSTATS_METRICS_COMPUTE_BATCH_MAX_WINDOW_S`` overrides INI
  ``metrics_compute_batch_max_window_s``.
  """
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_COMPUTE_BATCH_MAX_WINDOW_S", ""
  ).strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 0.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        _pipeline_getfloat("metrics_compute_batch_max_window_s", fallback=0.0),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.0


def get_metrics_compute_batch_max_single_job_runtime_seconds():
  """Max seconds for one non-artifact-only job in a batch (0 = disabled).

  When set, a job whose window exceeds this is still scheduled alone if it would
  otherwise block the batch (first slot rule in packer).
  """
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_COMPUTE_BATCH_MAX_SINGLE_JOB_S", ""
  ).strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 0.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        _pipeline_getfloat("metrics_compute_batch_max_single_job_s", fallback=0.0),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.0


def get_metrics_compute_batch_unknown_runtime_seconds():
  """Accounting window seconds assumed when start/end unavailable on a candidate."""
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_COMPUTE_BATCH_UNKNOWN_RUNTIME_S", ""
  ).strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 172800.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        _pipeline_getfloat("metrics_compute_batch_unknown_runtime_s", fallback=172800.0),
    )
  except (TypeError, ValueError, OverflowError):
    return 172800.0


def get_metrics_compute_watchdog_seconds():
  """Watchdog on metrics phase wall time inside a scheduler batch (seconds)."""
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_COMPUTE_WATCHDOG_S", ""
  ).strip()
  if env:
    try:
      return max(1.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 120.0
  _ensure_cfg_loaded()
  try:
    return max(
        1.0,
        _pipeline_getfloat("metrics_compute_watchdog_s", fallback=120.0),
    )
  except (TypeError, ValueError, OverflowError):
    return 120.0


def get_metrics_compute_total_watchdog_seconds():
  """Watchdog on full batch wall (metrics + prewarm submit/drain). 0 = use metrics-only.

  When 0, only ``get_metrics_compute_watchdog_seconds`` applies to the metrics
  slice; total batch time is logged but does not downshift batch cap.
  """
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_COMPUTE_TOTAL_WATCHDOG_S", ""
  ).strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 0.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        _pipeline_getfloat("metrics_compute_total_watchdog_s", fallback=0.0),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.0


def get_metrics_deferred_not_ready_retry_seconds():
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_DEFERRED_NOT_READY_RETRY_S", ""
  ).strip()
  if env:
    try:
      return max(0.1, float(env))
    except (TypeError, ValueError, OverflowError):
      return 10.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.1,
        _pipeline_getfloat("metrics_deferred_not_ready_retry_s", fallback=10.0),
    )
  except (TypeError, ValueError, OverflowError):
    return 10.0


def get_metrics_deferred_not_ready_max_retries():
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_DEFERRED_NOT_READY_MAX_RETRIES", ""
  ).strip()
  if env:
    try:
      return max(1, int(env))
    except (TypeError, ValueError, OverflowError):
      return 30
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        _pipeline_getint("metrics_deferred_not_ready_max_retries", fallback=30),
    )
  except (TypeError, ValueError, OverflowError):
    return 30


def get_metrics_deferred_not_ready_max_age_seconds():
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_DEFERRED_NOT_READY_MAX_AGE_S", ""
  ).strip()
  if env:
    try:
      return max(1.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 900.0
  _ensure_cfg_loaded()
  try:
    return max(
        1.0,
        _pipeline_getfloat("metrics_deferred_not_ready_max_age_s", fallback=900.0),
    )
  except (TypeError, ValueError, OverflowError):
    return 900.0


def get_metrics_deferred_not_ready_quarantine_seconds():
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_DEFERRED_NOT_READY_QUARANTINE_S", ""
  ).strip()
  if env:
    try:
      return max(1.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 300.0
  _ensure_cfg_loaded()
  try:
    return max(
        1.0,
        _pipeline_getfloat("metrics_deferred_not_ready_quarantine_s", fallback=300.0),
    )
  except (TypeError, ValueError, OverflowError):
    return 300.0


def get_metrics_readiness_require_window_coverage():
  """When True, defer metrics until in-window host_data covers start and end margins."""
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("metrics_readiness_require_window_coverage", fallback="yes"),
  )


def get_metrics_readiness_start_margin_seconds():
  """Seconds after job start_time; first in-window sample must be at or before this."""
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        float(
            _pipeline_get("metrics_readiness_start_margin_seconds", fallback="600"),
        ),
    )
  except (TypeError, ValueError, OverflowError):
    return 600.0


def get_metrics_readiness_end_margin_seconds():
  """Seconds before job end_time; last in-window sample must be at or after this."""
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        float(
            _pipeline_get("metrics_readiness_end_margin_seconds", fallback="600"),
        ),
    )
  except (TypeError, ValueError, OverflowError):
    return 600.0


def get_metrics_scheduler_compute_threads():
  """Thread workers for concurrent per-jid metrics+prewarm in update_metrics scheduler."""
  _ensure_cfg_loaded()
  return max(
      1,
      _pipeline_getint("metrics_scheduler_compute_threads", fallback=4),
  )


def get_metrics_run_poll_timeout_s():
  """Seconds for one ``imap_unordered`` poll in ``Metrics.run`` (host-side stall detection)."""
  env = os.environ.get("HPCPERFSTATS_METRICS_RUN_POLL_TIMEOUT_S", "").strip()
  if env:
    try:
      return max(0.1, float(env))
    except (TypeError, ValueError, OverflowError):
      return 5.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.1,
        float(_pipeline_get("metrics_run_poll_timeout_s", fallback="5")),
    )
  except (TypeError, ValueError, OverflowError):
    return 5.0


def get_sync_archive_validation_max_workers():
  """Max parallel threads for archive sealed/tar validation (read-lock scope)."""
  env = os.environ.get("HPCPERFSTATS_SYNC_ARCHIVE_VALIDATION_MAX_WORKERS", "").strip()
  if env:
    try:
      return max(1, int(env))
    except (TypeError, ValueError, OverflowError):
      return 2
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        int(_pipeline_get("sync_archive_validation_max_workers", fallback="2")),
    )
  except (TypeError, ValueError, OverflowError):
    return 2


def get_sync_pool_stall_abort_after_timeouts():
  """Consecutive pool poll timeouts without progress before aborting imap wait."""
  env = os.environ.get("HPCPERFSTATS_SYNC_POOL_STALL_ABORT_AFTER_TIMEOUTS", "").strip()
  if env:
    try:
      return max(1, int(env))
    except (TypeError, ValueError, OverflowError):
      return 120
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        int(_pipeline_get("sync_pool_stall_abort_after_timeouts", fallback="120")),
    )
  except (TypeError, ValueError, OverflowError):
    return 120


def get_sync_pool_poll_timeout_s():
  """Poll interval for sync_timedb pool waits (worker-death / OOM detection)."""
  env = os.environ.get("HPCPERFSTATS_SYNC_POOL_POLL_TIMEOUT_S", "").strip()
  if env:
    try:
      return max(0.05, float(env))
    except (TypeError, ValueError, OverflowError):
      return 5.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.05,
        float(_pipeline_get("sync_pool_poll_timeout_s", fallback="5")),
    )
  except (TypeError, ValueError, OverflowError):
    return 5.0


def get_sync_archive_members_cache_enabled():
  """Whether ingest/archive workers cache daily tar member maps per archive identity."""
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_archive_members_cache_enabled", fallback="yes"),
  )


def get_sync_archive_members_cache_max_entries():
  """Max cached daily archive member maps per worker process."""
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        int(_pipeline_get("sync_archive_members_cache_max_entries", fallback="64")),
    )
  except (TypeError, ValueError, OverflowError):
    return 64


def get_sync_ingest_per_file_timeout_s():
  """Wall-clock cap per ingest pool task in seconds (0 = disabled)."""
  env = os.environ.get("HPCPERFSTATS_SYNC_INGEST_PER_FILE_TIMEOUT_S", "").strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 0.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        float(_pipeline_get("sync_ingest_per_file_timeout_s", fallback="0")),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.0


def get_metrics_run_stall_timeout_s():
  """Max no-progress seconds allowed in ``Metrics.run`` before aborting batch."""
  env = os.environ.get("HPCPERFSTATS_METRICS_RUN_STALL_TIMEOUT_S", "").strip()
  if env:
    try:
      return max(5.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 600.0
  _ensure_cfg_loaded()
  try:
    return max(
        5.0,
        float(_pipeline_get("metrics_run_stall_timeout_s", fallback="900")),
    )
  except (TypeError, ValueError, OverflowError):
    return 600.0


def get_metrics_run_per_job_timeout_s():
  """Wall-clock cap for one ``compute_metrics`` call in a pool worker (0 → use stall timeout).

  Env ``HPCPERFSTATS_METRICS_RUN_PER_JOB_TIMEOUT_S`` overrides INI
  ``metrics_run_per_job_timeout_s``.
  """
  env = os.environ.get("HPCPERFSTATS_METRICS_RUN_PER_JOB_TIMEOUT_S", "").strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 0.0
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        float(_pipeline_get("metrics_run_per_job_timeout_s", fallback="0")),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.0


def get_metrics_persist_statement_timeout_ms():
  """Local PostgreSQL ``statement_timeout`` for parent metrics persistence."""
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_PERSIST_STATEMENT_TIMEOUT_MS", ""
  ).strip()
  if env:
    try:
      return max(1000, int(env))
    except (TypeError, ValueError, OverflowError):
      return 120000
  _ensure_cfg_loaded()
  try:
    return max(
        1000,
        int(_pipeline_get("metrics_persist_statement_timeout_ms", fallback="120000")),
    )
  except (TypeError, ValueError, OverflowError):
    return 120000


def get_metrics_persist_lock_timeout_ms():
  """Local PostgreSQL ``lock_timeout`` for parent metrics persistence."""
  env = os.environ.get("HPCPERFSTATS_METRICS_PERSIST_LOCK_TIMEOUT_MS", "").strip()
  if env:
    try:
      return max(1000, int(env))
    except (TypeError, ValueError, OverflowError):
      return 10000
  _ensure_cfg_loaded()
  try:
    return max(
        1000,
        int(_pipeline_get("metrics_persist_lock_timeout_ms", fallback="10000")),
    )
  except (TypeError, ValueError, OverflowError):
    return 10000


def get_metrics_prewarm_retry_attempts():
  """Retry attempts for plot artifact prewarm tasks."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("metrics_prewarm_retry_attempts", fallback=2))


def get_metrics_proxy_reject_jid_batch_size():
  """Max jids per DB round-trip in ``update_metrics`` proxy readiness (PostgreSQL)."""
  _ensure_cfg_loaded()
  return max(8, _pipeline_getint("metrics_proxy_reject_jid_batch_size", fallback=48))


def get_sync_enable_cpuset_priority_budget():
  """Enable cpuset-aware S/A/M budgeting for sync + metrics pools (default yes)."""
  env = os.environ.get("SYNC_ENABLE_CPUSET_PRIORITY_BUDGET", "").strip()
  if env:
    return _parse_bool(env)
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_enable_cpuset_priority_budget", fallback="yes"),
      default=True,
  )


def derive_pipeline_cpuset_priority_budget():
  """Return cpuset-aware thread budget dict for sync/metrics with reserve.

  Buckets:
  - real_time: sync ingest workers + listener/feed path
  - normal: sync archive workers + metrics pool
  - best_effort: maintenance and optional test/browser load
  """
  c = max(1, int(get_effective_cores()))
  ingest_ratio = _budget_ratio("sync_budget_ingest_ratio", 0.60)
  archive_ratio = _budget_ratio("sync_budget_archive_ratio", 0.15)
  metrics_ratio = _budget_ratio("sync_budget_metrics_ratio", 0.20)
  reserve_ratio = _budget_ratio("sync_budget_reserve_ratio", 0.05)

  s = max(1, int(math.floor(ingest_ratio * c)))
  a = max(1, int(math.floor(archive_ratio * c)))
  m = max(1, int(math.floor(metrics_ratio * c)))
  r = max(1, int(math.floor(reserve_ratio * c)))

  if get_sync_enable_overprovision_mode():
    s = max(1, int(math.floor(s * get_sync_overprovision_ingest_multiplier())))
    a = max(1, int(math.floor(a * get_sync_overprovision_archive_multiplier())))
    m = max(1, int(math.floor(m * get_sync_overprovision_metrics_multiplier())))

  total = s + a + m + r
  cap = max(1, int(math.floor(c * get_sync_budget_overcommit_factor())))
  while total > cap:
    if m > 1:
      m -= 1
    elif a > 1:
      a -= 1
    elif s > 1:
      s -= 1
    else:
      break
    total = s + a + m + r

  min_metrics = _budget_floor_percent("sync_budget_min_metrics_percent", 10)
  min_archive = _budget_floor_percent("sync_budget_min_archive_percent", 10)
  m_min = max(1, int(math.floor((min_metrics / 100.0) * c)))
  a_min = max(1, int(math.floor((min_archive / 100.0) * c)))
  if m < m_min:
    take = min(s - 1, m_min - m)
    if take > 0:
      s -= take
      m += take
  if a < a_min:
    take = min(s - 1, a_min - a)
    if take > 0:
      s -= take
      a += take

  return {
      "effective_cores": c,
      "sync_ingest_cap": max(1, s),
      "sync_archive_cap": max(1, a),
      "metrics_cap": max(1, m),
      "reserve_cap": max(1, r),
      "headroom_cap": cap,
  }


def get_sync_enable_overprovision_mode():
  """Enable bounded overprovision mode for S/A/M derivation (default disabled)."""
  env = os.environ.get("SYNC_ENABLE_OVERPROVISION_MODE", "").strip()
  if env:
    return _parse_bool(env)
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_enable_overprovision_mode", fallback="no"),
  )


def get_sync_overprovision_ingest_multiplier():
  return _env_or_cfg_bounded_float(
      "SYNC_OVERPROVISION_INGEST_MULTIPLIER",
      "PIPELINE",
      "sync_overprovision_ingest_multiplier",
      1.00,
      lower=1.00,
      upper=2.50,
      legacy_sections=_PIPELINE_LEGACY,
  )


def get_sync_overprovision_archive_multiplier():
  return _env_or_cfg_bounded_float(
      "SYNC_OVERPROVISION_ARCHIVE_MULTIPLIER",
      "PIPELINE",
      "sync_overprovision_archive_multiplier",
      1.00,
      lower=1.00,
      upper=2.50,
      legacy_sections=_PIPELINE_LEGACY,
  )


def get_sync_overprovision_metrics_multiplier():
  return _env_or_cfg_bounded_float(
      "SYNC_OVERPROVISION_METRICS_MULTIPLIER",
      "PIPELINE",
      "sync_overprovision_metrics_multiplier",
      1.00,
      lower=0.10,
      upper=2.50,
      legacy_sections=_PIPELINE_LEGACY,
  )


def get_sync_budget_overcommit_factor():
  return _env_or_cfg_bounded_float(
      "SYNC_BUDGET_OVERCOMMIT_FACTOR",
      "PIPELINE",
      "sync_budget_overcommit_factor",
      1.00,
      lower=1.00,
      upper=2.00,
      legacy_sections=_PIPELINE_LEGACY,
  )


def pipeline_cpu_process_buckets(include_browser_phase=False, include_rsync=False):
  """Return process inventory grouped by priority bucket for pipeline accounting."""
  best_effort = ["syslog-ng", "seal_syslog_daily.py"]
  if include_rsync:
    best_effort.append("rsync_data (optional)")
  if include_browser_phase:
    best_effort.append("browser/api phase test generator (optional)")
  return {
      "real_time": [
          "hpcperfstats-rabbitmq-listener",
          "sync_timedb ingest workers",
          "sync_timedb db-writer workers (feature-gated)",
      ],
      "normal": [
          "sync_timedb archive workers/retries",
          "update_metrics workers",
          "pipeline startup migrations/bootstrap",
      ],
      "best_effort": best_effort,
  }


def get_sync_ingest_queue_max_size():
  """Bound for in-memory ingest work queue (default 2000)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_ingest_queue_max_size", fallback=2000))


def get_sync_archive_queue_max_size():
  """Bound for in-memory archive work queue (default 1000)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_archive_queue_max_size", fallback=1000))


def get_sync_archive_retry_max_attempts():
  """Maximum archive retries before dead-letter behavior (default 5)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_archive_retry_max_attempts", fallback=5))


def get_sync_archive_retry_backoff_base_seconds():
  """Base archive retry backoff in seconds (default 1)."""
  _ensure_cfg_loaded()
  return max(0.0, float(_pipeline_get("sync_archive_retry_backoff_base_seconds", fallback="1")))


def get_sync_archive_retry_backoff_max_seconds():
  """Ceiling archive retry backoff in seconds (default 60)."""
  _ensure_cfg_loaded()
  return max(0.0, float(_pipeline_get("sync_archive_retry_backoff_max_seconds", fallback="60")))


def get_sync_checkpoint_flush_batch_size():
  """Number of processed-file state transitions between checkpoint writes (default 100)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_checkpoint_flush_batch_size", fallback=100))


def get_sync_host_itimes_cache_max_timestamps_per_entry():
  """Max distinct DB timestamps cached per host window in sync_timedb (default 100000)."""
  _ensure_cfg_loaded()
  return max(
      1,
      _pipeline_getint("sync_host_itimes_cache_max_timestamps_per_entry", fallback=100000),
  )


def get_sync_write_lock_shards():
  """Number of write-lock shards for sync_timedb ingest writes."""
  env = os.environ.get("SYNC_WRITE_LOCK_SHARDS", "").strip()
  if env:
    return max(1, int(env))
  _ensure_cfg_loaded()
  if _pipeline_has_option("sync_write_lock_shards"):
    return max(1, _pipeline_getint("sync_write_lock_shards", fallback=1))
  # Default scales modestly with cores to reduce write serialization without
  # exploding contention on smaller systems (40 effective cores -> 8 shards).
  return max(1, min(8, get_effective_cores() // 5))


def get_sync_enable_db_writer_pipeline():
  """Feature flag for optional parse-worker -> DB-writer queue pipeline (default disabled)."""
  _ensure_cfg_loaded()
  return _parse_bool(_pipeline_get("sync_enable_db_writer_pipeline", fallback="no"))


def get_sync_db_writer_combined_task():
  """Parse+write in one ingest worker (no parent DataFrame staging; default no)."""
  _ensure_cfg_loaded()
  return _parse_bool(_pipeline_get("sync_db_writer_combined_task", fallback="no"))


def get_sync_db_writer_stage_max_batch():
  """Max parse payloads staged in supervisor before db-writer drain (default 8)."""
  _ensure_cfg_loaded()
  return max(
      1,
      _pipeline_getint("sync_db_writer_stage_max_batch", fallback=8),
  )


def get_sync_ingest_chunk_size():
  """Stats files processed per ingest chunk (default 1000)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_ingest_chunk_size", fallback=1000))


def get_sync_supervisor_rss_limit_mb():
  """Supervisor RSS limit in MiB; 0 disables fail-fast exit (default 0)."""
  _ensure_cfg_loaded()
  return max(0, _pipeline_getint("sync_supervisor_rss_limit_mb", fallback=0))


def get_sync_supervisor_rss_check_every_n_chunks():
  """Check supervisor RSS every N processed chunks (default 1)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_supervisor_rss_check_every_n_chunks", fallback=1))


def get_sync_db_writer_pool_multiplier():
  """DB-writer pool size multiplier relative to ingest pool."""
  _ensure_cfg_loaded()
  return max(
      0.10,
      min(2.00, float(_pipeline_get("sync_db_writer_pool_multiplier", fallback="0.80"))),
  )


def get_sync_db_writer_pool_cap():
  env = os.environ.get("SYNC_DB_WRITER_POOL_CAP", "").strip()
  if env:
    return max(1, int(env))
  _ensure_cfg_loaded()
  if _pipeline_has_option("sync_db_writer_pool_cap"):
    return max(1, _pipeline_getint("sync_db_writer_pool_cap", fallback=1))
  return None


def get_sync_db_writer_pool_processes(ingest_processes=None):
  base = max(1, int(ingest_processes if ingest_processes is not None else get_sync_ingest_pool_processes()))
  n = max(1, int(math.floor(base * get_sync_db_writer_pool_multiplier())))
  cap = get_sync_db_writer_pool_cap()
  if cap is not None:
    n = min(n, cap)
  return max(1, n)


def get_sync_adaptive_dispatch_enabled():
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_adaptive_dispatch_enabled", fallback="yes"),
      default=True,
  )


def get_sync_dispatch_burst_factor():
  _ensure_cfg_loaded()
  return max(
      1.0,
      min(4.0, float(_pipeline_get("sync_dispatch_burst_factor", fallback="2.0"))),
  )


def get_sync_dispatch_archive_backoff_ratio():
  _ensure_cfg_loaded()
  return max(
      0.1,
      min(1.0, float(_pipeline_get("sync_dispatch_archive_backoff_ratio", fallback="0.50"))),
  )


def get_sync_dispatch_step_size():
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_dispatch_step_size", fallback=8))


def get_conf_parser_defaults_audit_snapshot():
  """Return categorized defaults/fallbacks for tuning/audit workflows."""
  return {
      "platform_constraints": {
          "total_cores_default": _DEFAULT_TOTAL_CORES,
          "cpuset_pin_min_total_cores": 32,
          "cpuset_pin_min_cores_per_node": 16,
          "numa_pin_max_nodes_auto": 16,
      },
      "sync_throughput": {
          "sync_enable_cpuset_priority_budget": "yes",
          "sync_budget_ingest_ratio": 0.60,
          "sync_budget_archive_ratio": 0.15,
          "sync_budget_metrics_ratio": 0.20,
          "sync_budget_reserve_ratio": 0.05,
          "sync_write_lock_shards_auto_rule": "max(1,min(8,effective_cores//5))",
          "sync_ingest_queue_max_size": 2000,
          "sync_archive_queue_max_size": 1000,
      },
      "overlap_contention": {
          "pipeline_overlap_mode": "balanced",
          "metrics_ingest_priority_scale": 0.75,
          "metrics_min_processes": 1,
          "metrics_scheduler_mode": "global_priority",
          "metrics_scheduler_prefetch_chunks": 8,
          "metrics_scheduler_ready_queue_target": 2000,
          "metrics_plot_prewarm_mode": "pipeline_required",
          "metrics_scheduler_skip_prewarm": "no",
          "metrics_prewarm_workers": 4,
      "metrics_prewarm_backlog_cap": 32,
      "metrics_prewarm_backpressure_wait_s": 0.25,
          "metrics_scheduler_compute_threads": 4,
          "metrics_prewarm_retry_attempts": 2,
      },
      "stability": {
          "sync_archive_retry_max_attempts": 5,
          "sync_archive_retry_backoff_base_seconds": 1.0,
          "sync_archive_retry_backoff_max_seconds": 60.0,
          "sync_checkpoint_flush_batch_size": 100,
          "sync_host_itimes_cache_max_timestamps_per_entry": 100000,
          "parallel_db_prefetch_max": 4,
          "db_conn_max_age": 90,
          "db_statement_timeout_ms": 120000,
          "db_idle_in_transaction_session_timeout_ms": 300000,
      },
  }


def get_sync_enable_ingest_first_durability_mode():
  """Checkpoint after DB write even when tar append fails (default on)."""
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_enable_ingest_first_durability_mode", fallback="yes"),
  )


def get_archive_maintenance_idle_seconds():
  """Optional idle dwell before janitor tick budget bonus (default 300s)."""
  _ensure_cfg_loaded()
  return max(
      0.0,
      float(_pipeline_get("archive_maintenance_idle_seconds", fallback="300")),
  )


def get_archive_janitor_budget_seconds():
  """Max wall seconds per archive janitor micro-batch tick (default 30)."""
  _ensure_cfg_loaded()
  return max(
      1.0,
      float(_pipeline_get("archive_janitor_budget_seconds", fallback="30")),
  )


def get_archive_janitor_days_per_tick():
  """Max calendar days processed per janitor tick (default 2)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("archive_janitor_days_per_tick", fallback=2))


def get_archive_janitor_debt_high_watermark():
  """Debt queue depth before temporary burst scaling (default 50)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("archive_janitor_debt_high_watermark", fallback=50))


def get_archive_janitor_debt_burst_factor():
  """Budget multiplier when debt exceeds high watermark (default 1.5)."""
  _ensure_cfg_loaded()
  return max(
      1.0,
      min(4.0, float(_pipeline_get("archive_janitor_debt_burst_factor", fallback="1.5"))),
  )


def get_archive_janitor_debt_max_entries():
  """Cap in-memory janitor debt queue size (default 200)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("archive_janitor_debt_max_entries", fallback=200))


def get_archive_janitor_raw_paths_per_tick():
  """Max raw stats file deletes per janitor RAW_REMOVE debt item (default 1000)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("archive_janitor_raw_paths_per_tick", fallback=1000))


def get_sync_unparsable_raw_quarantine_max_per_tick():
  """Deprecated: ingest quarantines unparseable raw at parse failure (default 50)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_unparsable_raw_quarantine_max_per_tick", fallback=50))


def get_sync_startup_raw_removal_preflight():
  """Enable startup async verify + gated delete for sealed archived raw (default on)."""
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_startup_raw_removal_preflight", fallback="yes"),
  )


def get_sync_startup_raw_removal_verify_budget_seconds():
  """Wall-clock budget per startup raw-removal verification slice (default 60s)."""
  _ensure_cfg_loaded()
  return max(
      1.0,
      float(_pipeline_get(
          "sync_startup_raw_removal_verify_budget_seconds",
          fallback="60",
      )),
  )


def get_sync_startup_raw_removal_verify_days_per_slice():
  """Max calendar days verified per startup raw-removal slice (default 5)."""
  _ensure_cfg_loaded()
  return max(
      1,
      _pipeline_getint("sync_startup_raw_removal_verify_days_per_slice", fallback=5),
  )


def get_sync_startup_raw_removal_max_deletes_per_pass():
  """Max deletes per gated startup delete pass; 0 means unlimited (default 0)."""
  _ensure_cfg_loaded()
  raw = _pipeline_get("sync_startup_raw_removal_max_deletes_per_pass", fallback="0")
  try:
    return max(0, int(raw))
  except (TypeError, ValueError):
    return 0


def get_sync_day_close_raw_removal_preflight():
  """Enable per-day async verify + chunk-boundary batch delete after DAY_CLOSE seal."""
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_day_close_raw_removal_preflight", fallback="yes"),
  )


def get_sync_day_close_raw_removal_verify_budget_seconds():
  """Reserved wall-clock budget knob for day-close verify slices (default 30s)."""
  _ensure_cfg_loaded()
  return max(
      1.0,
      float(_pipeline_get(
          "sync_day_close_raw_removal_verify_budget_seconds",
          fallback="30",
      )),
  )


def get_sync_day_close_raw_removal_max_deletes_per_pass():
  """Max deletes per day-close batch delete; 0 means unlimited (default 0)."""
  _ensure_cfg_loaded()
  raw = _pipeline_get("sync_day_close_raw_removal_max_deletes_per_pass", fallback="0")
  try:
    return max(0, int(raw))
  except (TypeError, ValueError):
    return 0


def get_sync_archive_max_inflight_jobs():
  """Max concurrent disjoint daily-tar archive append jobs (default 2)."""
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_archive_max_inflight_jobs", fallback=2))


def get_sync_archive_worker_stall_seconds():
  """Seconds before treating an archive pool job as stalled (default 600)."""
  _ensure_cfg_loaded()
  return max(
      60.0,
      float(_pipeline_get("sync_archive_worker_stall_seconds", fallback="600")),
  )


def get_sync_archive_require_db_head_ingest():
  """Require head timestamp in host_data before tar append or raw stats removal."""
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_archive_require_db_head_ingest", fallback="yes"),
  )


def get_sync_archive_maint_hints():
  """Persist host-dir/path hints for faster archive maintenance restarts (default on)."""
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_archive_maint_hints", fallback="yes"),
  )


def get_sync_archive_discovery_workers():
  """Max concurrent raw stats head-line reads during maintenance snapshot."""
  _ensure_cfg_loaded()
  raw = _ini_get(
      "PIPELINE",
      "sync_archive_discovery_workers",
      fallback="",
      legacy_sections=("PORTAL",),
  )
  if str(raw).strip():
    try:
      return max(1, int(raw))
    except (TypeError, ValueError):
      pass
  return max(1, int(get_sync_archive_pool_processes()))


def get_redis_location():
  """Return the Redis URL for cache from CACHE config.

    Defaults to redis://127.0.0.1:6379/1 if [CACHE] or redis_location is missing.
    """
  _ensure_cfg_loaded()
  if cfg.has_section("CACHE") and cfg.has_option("CACHE", "redis_location"):
    return cfg.get("CACHE", "redis_location").strip() or "redis://127.0.0.1:6379/1"
  return "redis://127.0.0.1:6379/1"


def get_large_job_host_data_row_threshold():
  """When host_data row count for a job window exceeds this, sample times in jid_table.

  Env ``HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS`` overrides (minimum 1000). Default
  1_500_000 keeps interactive metrics/plots bounded on huge jobs.
  """
  env = os.environ.get("HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS", "").strip()
  if not env:
    return 1_500_000
  try:
    return max(1000, int(env))
  except (TypeError, ValueError, OverflowError):
    return 1_500_000


def get_large_job_time_buckets():
  """Max distinct time buckets used when large-job sampling is active.

  Env ``HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS`` overrides (minimum 32). Default 2048.
  """
  env = os.environ.get("HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS", "").strip()
  if not env:
    return 2048
  try:
    return max(32, int(env))
  except (TypeError, ValueError, OverflowError):
    return 2048


def get_large_job_window_row_count_cache_ttl():
  """TTL (seconds) for caching ``COUNT(*)`` over job window in ``jid_table``; 0 disables.

  Reduces repeated full-window counts when the same job is opened multiple times
  shortly after ingest. Invalidate via ``invalidate_jid_derived_cache_keys``.
  """
  env = os.environ.get(
      "HPCPERFSTATS_LARGE_JOB_WINDOW_ROW_COUNT_CACHE_TTL", ""
  ).strip()
  if not env:
    return 300
  try:
    return max(0, int(env))
  except (TypeError, ValueError, OverflowError):
    return 300


def get_large_job_time_sample_sql_mode():
  """How to pick strided sample timestamps for large jobs: ``ntile`` or ``date_bin``.

  Default ``date_bin``: PostgreSQL uses per-stride-bucket ``MAX(time)`` queries
  merged across host chunks (same stride grid as the legacy Python path),
  avoiding a full-window batched ``DISTINCT time`` pass when that succeeds.
  Set env ``HPCPERFSTATS_LARGE_JOB_TIME_SQL=ntile`` for the legacy index-space
  stride (distinct times, equal-count buckets in Python).
  """
  env = os.environ.get("HPCPERFSTATS_LARGE_JOB_TIME_SQL", "").strip().lower()
  if env in ("ntile",):
    return "ntile"
  if env in ("date_bin", "date-bin"):
    return "date_bin"
  return "date_bin"


def get_live_distinct_use_legacy_hostlist():
  """If True, ``LiveDistinctHostTimeCount`` unnests ``host_list`` (legacy).

  Default False: use ``LiveJidScopedDistinctHostTimeCount`` (``host_data.jid`` + window),
  which matches indexed access and typical ingest. Env:
  ``HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST`` = 1 to restore old SQL.

  **Sunset:** keep only for emergency rollback on sites that cannot use jid-scoped
  live distinct SQL; remove this flag and branch once no deployment depends on it.
  """
  return os.environ.get(
      "HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", ""
  ).strip().lower() in ("1", "true", "yes")
