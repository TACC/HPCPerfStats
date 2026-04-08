"""Configuration parser for HPCPerfStats. Reads hpcperfstats.ini and exposes getters for portal, RMQ, XALT, and OAuth2 settings.

"""
import configparser
import os
from zoneinfo import ZoneInfo

_DEFAULT_TOTAL_CORES = "40"

cfg = None
_ACTIVE_CONFIG_PATH = None


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


def get_db_connection_string():
  """Return a PostgreSQL connection string from PORTAL config (dbname, user, password, port, host)."""
  return "dbname={0} user={1} password={2} port={3} host={4}".format(
      _get('PORTAL', 'dbname'), _get('PORTAL', 'username'),
      _get('PORTAL', 'password'), _get('PORTAL', 'port'), _get('PORTAL', 'host'))


def get_db_name():
  """Return the database name from PORTAL config."""
  return _get('PORTAL', 'dbname')


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
  """Return the archive directory path from PORTAL config."""
  return _get('PORTAL', 'archive_dir')


def get_host_name_ext():
  """Return the host name extension (domain) from DEFAULT config."""
  return _get('DEFAULT', 'host_name_ext')


def get_restricted_queue_keywords():
  """Return restricted queue keywords string from DEFAULT config."""
  return _get('DEFAULT', 'restricted_queue_keywords')


def get_accounting_path():
  """Return the accounting (sacct) file path from PORTAL config."""
  return _get('PORTAL', 'acct_path')


def get_daily_archive_dir_path():
  """Return the daily archive directory path from PORTAL config."""
  return _get('PORTAL', 'daily_archive_dir')


def get_archive_keep_uncompressed_tar():
  """Return True if daily ``.tar`` should be kept after sealing (default True).

  If False, only ``.tar.gz`` remains; the next append will decompress again.
  """
  _ensure_cfg_loaded()
  return cfg.get(
      'PORTAL', 'archive_keep_uncompressed_tar', fallback='yes',
  ).lower() in ('yes', 'true', '1')


def get_archive_seal_idle_seconds():
  """Minimum seconds since last ``.tar`` mtime before sealing *today's* archive.

  Prior calendar days seal as soon as the tar/gz pair is dirty (no idle wait).
  """
  _ensure_cfg_loaded()
  return float(cfg.get('PORTAL', 'archive_seal_idle_seconds', fallback='60'))


def get_archive_pigz_level():
  """pigz compression level (1--11) for sealing daily archives. Default 8."""
  _ensure_cfg_loaded()
  return int(cfg.get('PORTAL', 'archive_pigz_level', fallback='8'))


def get_archive_pigz_interval_seconds():
  """Seconds between ``pigz`` seal runs and removal of verified raw stats (default 4h)."""
  _ensure_cfg_loaded()
  return float(
      cfg.get('PORTAL', 'archive_pigz_interval_seconds', fallback=str(4 * 3600))
  )


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


def get_data_dir_path():
  """Return the data directory path from DEFAULT config."""
  return _get('DEFAULT', 'data_dir')


def get_engine_name():
  """Return the Django database engine name from PORTAL config."""
  return _get('PORTAL', 'engine_name')


def get_username():
  """Return the portal DB username from PORTAL config."""
  return _get('PORTAL', 'username')


def get_password():
  """Return the portal DB password from PORTAL config."""
  return _get('PORTAL', 'password')


def get_host():
  """Return the portal DB host from PORTAL config."""
  return _get('PORTAL', 'host')


def get_port():
  """Return the portal DB port from PORTAL config."""
  return _get('PORTAL', 'port')


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
  ``max_connections`` alongside metrics/sync pools on one Postgres.
  """
  _ensure_cfg_loaded()
  return int(cfg.get("DEFAULT", "max_gunicorn_workers", fallback="32"))


def get_metrics_pool_process_cap():
  """Upper bound for ``multiprocessing.Pool`` process count in metrics compute."""
  _ensure_cfg_loaded()
  return int(cfg.get("DEFAULT", "metrics_pool_process_cap", fallback="32"))


def get_metrics_pool_process_count():
  """Processes for metrics pool: ``min(max(1, effective//2), metrics_pool_process_cap)``."""
  raw = max(1, get_effective_cores() // 2)
  return min(raw, get_metrics_pool_process_cap())


def get_cpuset_pin_min_total_cores():
  _ensure_cfg_loaded()
  return int(cfg.get("DEFAULT", "cpuset_pin_min_total_cores", fallback="32"))


def get_cpuset_pin_min_cores_per_node():
  _ensure_cfg_loaded()
  return int(cfg.get("DEFAULT", "cpuset_pin_min_cores_per_node", fallback="16"))


def get_web_numa_node():
  """Optional explicit sysfs node id for web+proxy; None if unset."""
  _ensure_cfg_loaded()
  if not cfg.has_option("DEFAULT", "web_numa_node"):
    return None
  s = cfg.get("DEFAULT", "web_numa_node").strip()
  if not s:
    return None
  return int(s)


def get_pipeline_numa_node():
  """Optional explicit sysfs node id for pipeline; None if unset."""
  _ensure_cfg_loaded()
  if not cfg.has_option("DEFAULT", "pipeline_numa_node"):
    return None
  s = cfg.get("DEFAULT", "pipeline_numa_node").strip()
  if not s:
    return None
  return int(s)


def get_pin_proxy_for_compose():
  """If True, NUMA pinning script also sets ``cpuset`` on ``proxy`` (match web node)."""
  _ensure_cfg_loaded()
  return cfg.get("DEFAULT", "pin_proxy_in_compose", fallback="no").lower() in (
      "yes",
      "true",
      "1",
  )


def get_numa_pin_max_nodes_auto():
  """Auto compose pinning supports up to this many NUMA nodes without explicit ids."""
  _ensure_cfg_loaded()
  return int(cfg.get("DEFAULT", "numa_pin_max_nodes_auto", fallback="16"))


def get_parallel_db_prefetch_max_workers():
  """Max threads for parallel ORM prefetch (summary plots) and default API executor size.

  Override with ``[DEFAULT] parallel_db_prefetch_max`` or env ``PARALLEL_DB_PREFETCH_MAX``.
  """
  env = os.environ.get("PARALLEL_DB_PREFETCH_MAX", "").strip()
  if env:
    return max(1, int(env))
  _ensure_cfg_loaded()
  return max(1, int(cfg.get("DEFAULT", "parallel_db_prefetch_max", fallback="6")))


def get_api_small_executor_max_workers():
  """Max workers for shared ``ThreadPoolExecutor`` in ``site.machine.api``.

  If ``[DEFAULT] api_small_executor_max_workers`` is set, it wins; otherwise
  ``get_parallel_db_prefetch_max_workers()`` (default **6**).
  """
  _ensure_cfg_loaded()
  if cfg.has_option("DEFAULT", "api_small_executor_max_workers"):
    return max(1, int(cfg.get("DEFAULT", "api_small_executor_max_workers")))
  return get_parallel_db_prefetch_max_workers()


def get_db_conn_max_age():
  """Django ``CONN_MAX_AGE`` in seconds (default **90**).

  Env ``DJANGO_CONN_MAX_AGE`` overrides ``[DEFAULT] db_conn_max_age``.
  """
  env = os.environ.get("DJANGO_CONN_MAX_AGE", "").strip()
  if env:
    return int(env)
  _ensure_cfg_loaded()
  return int(cfg.get("DEFAULT", "db_conn_max_age", fallback="90"))


def get_db_statement_timeout_ms():
  """``statement_timeout`` in milliseconds for PostgreSQL session options.

  ``0`` means do not set (omit from Django ``OPTIONS``). Default **120000** (2 minutes).
  Env ``DJANGO_DB_STATEMENT_TIMEOUT_MS`` overrides ``[DEFAULT] db_statement_timeout_ms``.
  """
  env = os.environ.get("DJANGO_DB_STATEMENT_TIMEOUT_MS", "").strip()
  if env:
    return int(env)
  _ensure_cfg_loaded()
  return int(cfg.get("DEFAULT", "db_statement_timeout_ms", fallback="120000"))


def get_db_idle_in_transaction_session_timeout_ms():
  """``idle_in_transaction_session_timeout`` in ms; ``0`` = omit. Default **300000** (5 min)."""
  env = os.environ.get("DJANGO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", "").strip()
  if env:
    return int(env)
  _ensure_cfg_loaded()
  return int(
      cfg.get("DEFAULT", "db_idle_in_transaction_session_timeout_ms", fallback="300000"))


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
  if cfg.has_option("DEFAULT", "sync_pool_process_cap"):
    return cfg.getint("DEFAULT", "sync_pool_process_cap")
  return None


def get_archive_pool_process_cap():
  """If set, caps archive-side pool in ``sync_timedb``. Env ``ARCHIVE_POOL_PROCESS_CAP``."""
  env = os.environ.get("ARCHIVE_POOL_PROCESS_CAP", "").strip()
  if env:
    return int(env)
  _ensure_cfg_loaded()
  if cfg.has_option("DEFAULT", "archive_pool_process_cap"):
    return cfg.getint("DEFAULT", "archive_pool_process_cap")
  return None


def get_sync_ingest_pool_processes():
  """Worker count for ``sync_timedb`` / ``sync_timedb_archive`` after ``sync_pool_process_cap``."""
  raw = get_worker_thread_count(2)
  return _apply_sync_pool_cap(raw, get_sync_pool_process_cap())


def get_sync_archive_pool_processes():
  """Archive pool size in ``sync_timedb`` (half of ingest, capped by ``archive_pool_process_cap``)."""
  ingest = get_sync_ingest_pool_processes()
  raw = max(1, ingest // 2)
  return _apply_sync_pool_cap(raw, get_archive_pool_process_cap())


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
  if env:
    return max(1000, int(env))
  return 1_500_000


def get_large_job_time_buckets():
  """Max distinct time buckets used when large-job sampling is active.

  Env ``HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS`` overrides (minimum 32). Default 2048.
  """
  env = os.environ.get("HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS", "").strip()
  if env:
    return max(32, int(env))
  return 2048


def get_large_job_window_row_count_cache_ttl():
  """TTL (seconds) for caching ``COUNT(*)`` over job window in ``jid_table``; 0 disables.

  Reduces repeated full-window counts when the same job is opened multiple times
  shortly after ingest. Invalidate via ``invalidate_jid_derived_cache_keys``.
  """
  env = os.environ.get(
      "HPCPERFSTATS_LARGE_JOB_WINDOW_ROW_COUNT_CACHE_TTL", ""
  ).strip()
  if env:
    return max(0, int(env))
  return 300


def get_large_job_time_sample_sql_mode():
  """How to pick strided sample timestamps for large jobs: ``ntile`` or ``date_bin``.

  Default ``date_bin`` (PostgreSQL 14+): ``GROUP BY date_bin(...)`` avoids building
  a full ``DISTINCT`` time set + ``NTILE``, which often hits ``statement_timeout``
  on large windows. Set env ``HPCPERFSTATS_LARGE_JOB_TIME_SQL=ntile`` for the
  legacy index-space stride (distinct times, equal-count buckets).
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
  """
  return os.environ.get(
      "HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", ""
  ).strip().lower() in ("1", "true", "yes")
