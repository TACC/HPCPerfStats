"""Configuration parser for HPCPerfStats. Reads hpcperfstats.ini and exposes getters for portal, RMQ, XALT, and OAuth2 settings.

"""
import configparser
import math
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
  raw = _get('PORTAL', 'archive_dir').strip()
  return os.path.normpath(raw) if raw else raw


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
  raw = _get('PORTAL', 'daily_archive_dir').strip()
  return os.path.normpath(raw) if raw else raw


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


def get_archive_pigz_threads():
  """pigz worker thread count (``-p``) for archive compress/decompress. Default 8."""
  _ensure_cfg_loaded()
  n = int(cfg.get('PORTAL', 'archive_pigz_threads', fallback='8'))
  return max(1, n)


def get_archive_pigz_interval_seconds():
  """Seconds between ``pigz`` seal runs and removal of verified raw stats (default 8h)."""
  _ensure_cfg_loaded()
  default_interval = float(8 * 3600)
  raw_value = cfg.get(
      'PORTAL',
      'archive_pigz_interval_seconds',
      fallback=str(default_interval),
  )
  try:
    interval = float(raw_value)
  except (TypeError, ValueError):
    return default_interval
  if (not math.isfinite(interval)) or interval <= 0:
    return default_interval
  return interval


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
  _ensure_cfg_loaded()
  return float(cfg.get("DEFAULT", name, fallback=str(fallback)))


def _budget_floor_percent(name, fallback):
  _ensure_cfg_loaded()
  return int(cfg.get("DEFAULT", name, fallback=str(fallback)))


def get_pipeline_overlap_mode():
  """Pipeline overlap mode: balanced or ingest_priority."""
  env = os.environ.get("HPCPERFSTATS_PIPELINE_OVERLAP_MODE", "").strip().lower()
  if env in ("balanced", "ingest_priority"):
    return env
  _ensure_cfg_loaded()
  mode = cfg.get("DEFAULT", "pipeline_overlap_mode", fallback="balanced").strip().lower()
  return mode if mode in ("balanced", "ingest_priority") else "balanced"


def get_metrics_ingest_priority_scale():
  """Metrics pool downscale factor during ingest_priority overlap mode."""
  _ensure_cfg_loaded()
  return max(
      0.10,
      min(1.00, float(cfg.get("DEFAULT", "metrics_ingest_priority_scale", fallback="0.75"))),
  )


def get_metrics_min_processes():
  """Minimum metrics worker count under ingest-priority overlap mode."""
  _ensure_cfg_loaded()
  return max(1, int(cfg.get("DEFAULT", "metrics_min_processes", fallback="1")))


def get_metrics_scheduler_mode():
  """Metrics scheduler mode: strict_date, global_fifo, or global_priority."""
  env = os.environ.get("HPCPERFSTATS_METRICS_SCHEDULER_MODE", "").strip().lower()
  if env in ("strict_date", "global_fifo", "global_priority"):
    return env
  _ensure_cfg_loaded()
  mode = cfg.get(
      "DEFAULT", "metrics_scheduler_mode", fallback="global_priority"
  ).strip().lower()
  if mode in ("strict_date", "global_fifo", "global_priority"):
    return mode
  return "global_priority"


def get_metrics_scheduler_prefetch_chunks():
  """Max chunk descriptors prefetched ahead for global scheduler."""
  _ensure_cfg_loaded()
  return max(1, int(cfg.get("DEFAULT", "metrics_scheduler_prefetch_chunks", fallback="8")))


def get_metrics_scheduler_ready_queue_target():
  """Target ready-jid queue depth before compute dispatch."""
  _ensure_cfg_loaded()
  return max(1, int(cfg.get("DEFAULT", "metrics_scheduler_ready_queue_target", fallback="2000")))


def get_metrics_plot_prewarm_mode():
  """Prewarm mode for metrics pipeline: inline or pipeline_required."""
  env = os.environ.get("HPCPERFSTATS_METRICS_PLOT_PREWARM_MODE", "").strip().lower()
  if env in ("inline", "pipeline_required"):
    return env
  _ensure_cfg_loaded()
  mode = cfg.get(
      "DEFAULT", "metrics_plot_prewarm_mode", fallback="pipeline_required"
  ).strip().lower()
  if mode in ("inline", "pipeline_required"):
    return mode
  return "pipeline_required"


def get_metrics_prewarm_workers():
  """Thread workers for required plot prewarm stage."""
  _ensure_cfg_loaded()
  return max(1, int(cfg.get("DEFAULT", "metrics_prewarm_workers", fallback="4")))


def get_metrics_scheduler_compute_threads():
  """Thread workers for concurrent per-jid metrics+prewarm in update_metrics scheduler."""
  _ensure_cfg_loaded()
  return max(
      1,
      int(cfg.get("DEFAULT", "metrics_scheduler_compute_threads", fallback="4")),
  )


def get_metrics_prewarm_retry_attempts():
  """Retry attempts for plot artifact prewarm tasks."""
  _ensure_cfg_loaded()
  return max(1, int(cfg.get("DEFAULT", "metrics_prewarm_retry_attempts", fallback="2")))


def get_metrics_proxy_reject_jid_batch_size():
  """Max jids per DB round-trip in ``update_metrics`` proxy readiness (PostgreSQL)."""
  _ensure_cfg_loaded()
  return max(8, int(cfg.get("DEFAULT", "metrics_proxy_reject_jid_batch_size", fallback="48")))


def get_sync_enable_cpuset_priority_budget():
  """Enable cpuset-aware S/A/M budgeting for sync + metrics pools (default yes)."""
  env = os.environ.get("SYNC_ENABLE_CPUSET_PRIORITY_BUDGET", "").strip().lower()
  if env:
    return env in ("1", "yes", "true")
  _ensure_cfg_loaded()
  return cfg.get(
      "DEFAULT", "sync_enable_cpuset_priority_budget", fallback="yes"
  ).strip().lower() in ("1", "yes", "true")


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
  env = os.environ.get("SYNC_ENABLE_OVERPROVISION_MODE", "").strip().lower()
  if env:
    return env in ("1", "yes", "true")
  _ensure_cfg_loaded()
  return cfg.get(
      "DEFAULT", "sync_enable_overprovision_mode", fallback="no"
  ).strip().lower() in ("1", "yes", "true")


def get_sync_overprovision_ingest_multiplier():
  env = os.environ.get("SYNC_OVERPROVISION_INGEST_MULTIPLIER", "").strip()
  if env:
    return max(1.00, min(2.50, float(env)))
  _ensure_cfg_loaded()
  return max(
      1.00,
      min(2.50, float(cfg.get("DEFAULT", "sync_overprovision_ingest_multiplier", fallback="1.00"))),
  )


def get_sync_overprovision_archive_multiplier():
  env = os.environ.get("SYNC_OVERPROVISION_ARCHIVE_MULTIPLIER", "").strip()
  if env:
    return max(1.00, min(2.50, float(env)))
  _ensure_cfg_loaded()
  return max(
      1.00,
      min(2.50, float(cfg.get("DEFAULT", "sync_overprovision_archive_multiplier", fallback="1.00"))),
  )


def get_sync_overprovision_metrics_multiplier():
  env = os.environ.get("SYNC_OVERPROVISION_METRICS_MULTIPLIER", "").strip()
  if env:
    return max(0.10, min(2.50, float(env)))
  _ensure_cfg_loaded()
  return max(
      0.10,
      min(2.50, float(cfg.get("DEFAULT", "sync_overprovision_metrics_multiplier", fallback="1.00"))),
  )


def get_sync_budget_overcommit_factor():
  env = os.environ.get("SYNC_BUDGET_OVERCOMMIT_FACTOR", "").strip()
  if env:
    return max(1.00, min(2.00, float(env)))
  _ensure_cfg_loaded()
  return max(
      1.00,
      min(2.00, float(cfg.get("DEFAULT", "sync_budget_overcommit_factor", fallback="1.00"))),
  )


def pipeline_cpu_process_buckets(include_browser_phase=False, include_rsync=False):
  """Return process inventory grouped by priority bucket for pipeline accounting."""
  best_effort = ["syslog-ng", "logrotate.sh"]
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
  return max(1, int(cfg.get("DEFAULT", "sync_ingest_queue_max_size", fallback="2000")))


def get_sync_archive_queue_max_size():
  """Bound for in-memory archive work queue (default 1000)."""
  _ensure_cfg_loaded()
  return max(1, int(cfg.get("DEFAULT", "sync_archive_queue_max_size", fallback="1000")))


def get_sync_archive_retry_max_attempts():
  """Maximum archive retries before dead-letter behavior (default 5)."""
  _ensure_cfg_loaded()
  return max(1, int(cfg.get("DEFAULT", "sync_archive_retry_max_attempts", fallback="5")))


def get_sync_archive_retry_backoff_base_seconds():
  """Base archive retry backoff in seconds (default 1)."""
  _ensure_cfg_loaded()
  return max(0.0, float(cfg.get("DEFAULT", "sync_archive_retry_backoff_base_seconds", fallback="1")))


def get_sync_archive_retry_backoff_max_seconds():
  """Ceiling archive retry backoff in seconds (default 60)."""
  _ensure_cfg_loaded()
  return max(0.0, float(cfg.get("DEFAULT", "sync_archive_retry_backoff_max_seconds", fallback="60")))


def get_sync_checkpoint_flush_batch_size():
  """Number of processed-file state transitions between checkpoint writes (default 100)."""
  _ensure_cfg_loaded()
  return max(1, int(cfg.get("DEFAULT", "sync_checkpoint_flush_batch_size", fallback="100")))


def get_sync_write_lock_shards():
  """Number of write-lock shards for sync_timedb ingest writes."""
  env = os.environ.get("SYNC_WRITE_LOCK_SHARDS", "").strip()
  if env:
    return max(1, int(env))
  _ensure_cfg_loaded()
  if cfg.has_option("DEFAULT", "sync_write_lock_shards"):
    return max(1, int(cfg.get("DEFAULT", "sync_write_lock_shards")))
  # Default scales modestly with cores to reduce write serialization without
  # exploding contention on smaller systems.
  return max(1, min(8, get_effective_cores() // 8))


def get_sync_enable_db_writer_pipeline():
  """Feature flag for optional parse-worker -> DB-writer queue pipeline (default disabled)."""
  _ensure_cfg_loaded()
  return cfg.get(
      "DEFAULT", "sync_enable_db_writer_pipeline", fallback="no"
  ).strip().lower() in ("1", "yes", "true")


def get_sync_db_writer_pool_multiplier():
  """DB-writer pool size multiplier relative to ingest pool."""
  _ensure_cfg_loaded()
  return max(
      0.10,
      min(2.00, float(cfg.get("DEFAULT", "sync_db_writer_pool_multiplier", fallback="0.50"))),
  )


def get_sync_db_writer_pool_cap():
  env = os.environ.get("SYNC_DB_WRITER_POOL_CAP", "").strip()
  if env:
    return max(1, int(env))
  _ensure_cfg_loaded()
  if cfg.has_option("DEFAULT", "sync_db_writer_pool_cap"):
    return max(1, int(cfg.get("DEFAULT", "sync_db_writer_pool_cap")))
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
  return cfg.get(
      "DEFAULT", "sync_adaptive_dispatch_enabled", fallback="yes"
  ).strip().lower() in ("1", "yes", "true")


def get_sync_dispatch_burst_factor():
  _ensure_cfg_loaded()
  return max(
      1.0,
      min(4.0, float(cfg.get("DEFAULT", "sync_dispatch_burst_factor", fallback="2.0"))),
  )


def get_sync_dispatch_archive_backoff_ratio():
  _ensure_cfg_loaded()
  return max(
      0.1,
      min(1.0, float(cfg.get("DEFAULT", "sync_dispatch_archive_backoff_ratio", fallback="0.50"))),
  )


def get_sync_dispatch_step_size():
  _ensure_cfg_loaded()
  return max(1, int(cfg.get("DEFAULT", "sync_dispatch_step_size", fallback="8")))


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
          "sync_write_lock_shards_auto_rule": "max(1,min(8,effective_cores//8))",
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
          "metrics_prewarm_workers": 4,
          "metrics_scheduler_compute_threads": 4,
          "metrics_prewarm_retry_attempts": 2,
      },
      "stability": {
          "sync_archive_retry_max_attempts": 5,
          "sync_archive_retry_backoff_base_seconds": 1.0,
          "sync_archive_retry_backoff_max_seconds": 60.0,
          "sync_checkpoint_flush_batch_size": 100,
          "db_conn_max_age": 90,
          "db_statement_timeout_ms": 120000,
          "db_idle_in_transaction_session_timeout_ms": 300000,
      },
  }


def get_sync_enable_ingest_first_durability_mode():
  """Feature flag for ingest-first durability semantics (default disabled)."""
  _ensure_cfg_loaded()
  return cfg.get(
      "DEFAULT", "sync_enable_ingest_first_durability_mode", fallback="no"
  ).strip().lower() in ("1", "yes", "true")


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
  except (TypeError, ValueError):
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
  except (TypeError, ValueError):
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
  except (TypeError, ValueError):
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

  **Sunset:** keep only for emergency rollback on sites that cannot use jid-scoped
  live distinct SQL; remove this flag and branch once no deployment depends on it.
  """
  return os.environ.get(
      "HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", ""
  ).strip().lower() in ("1", "true", "yes")
