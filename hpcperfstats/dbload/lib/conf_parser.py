"""
Configuration parser for HPCPerfStats. Reads hpcperfstats.ini and exposes
getters for portal, RMQ, XALT, and OAuth2 settings.

Attributes:
  INI_OPTION_DEFAULTS: Attribute.
  INI_OPTION_REGISTRY: Attribute.
  _ACTIVE_CONFIG_PATH: Attribute.
  _DEFAULT_TOTAL_CORES: Attribute.
  _INI_FALLBACK_SENTINEL: Attribute.
  _INI_OPTION_REGISTRY_KEYS: Attribute.
  _INI_REGISTRY_DEFAULT_BY_OPTION: Attribute.
  _PIPELINE_ARCHIVE_LEGACY: Attribute.
  _PIPELINE_DERIVED_AUDIT_SKIP: Attribute.
  _PIPELINE_LEGACY: Attribute.
  _PIPELINE_PATH_OPTIONS: Attribute.
  _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_LARGE_MAX_BYTES_DEFAULT: Attribute.
  _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_MAX_BYTES_DEFAULT: Attribute.
  _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_QUEUE_MULTIPLIER_DEFAULT: Attribute.
  _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_TRIGGER_BUDGET_S_DEFAULT: Attribute.
  _SYNC_INGEST_PER_FILE_TIMEOUT_MAX_S_DEFAULT: Attribute.
  _SYNC_INGEST_PER_FILE_TIMEOUT_REFERENCE_MIB: Attribute.
  _SYNC_INGEST_PER_FILE_TIMEOUT_SLOPE_FLOOR_S: Attribute.
  _SYNC_INGEST_PER_FILE_TIMEOUT_S_PER_MIB_DEFAULT: Attribute.
  _SYNC_TIMEDB_CONFIG_AUDIT_ENV_KEYS: Attribute.
  _SYNC_TIMEDB_CONFIG_BASELINE_PARSER: Attribute.
  _SYNC_TIMEDB_CONFIG_BASELINE_PATH: Attribute.
  cfg: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import configparser
import inspect
import ipaddress
import math
import os
import re
from contextlib import contextmanager
from zoneinfo import ZoneInfo

_DEFAULT_TOTAL_CORES = "40"

cfg = None
_ACTIVE_CONFIG_PATH = None

# Canonical (section, option, default) tuples for hpcperfstats.ini.
# *default* is a str code fallback, or None when the key is required / has no
# code default. Used by drift guards; keep in sync when adding getters.
_INI_OPTION_REGISTRY_KEYS = (
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
    ("PORTAL", "gunicorn_workers"),
    ("PORTAL", "summary_aggregate_prefetch_max_threads"),
    ("PORTAL", "parallel_db_prefetch_max"),
    ("PORTAL", "api_small_executor_max_workers"),
    ("PORTAL", "db_conn_max_age"),
    ("PORTAL", "db_statement_timeout_ms"),
    ("PORTAL", "db_idle_in_transaction_session_timeout_ms"),
    # [PIPELINE] — sync_timedb, update_metrics, sync_acct, archive paths/tuning
    ("PIPELINE", "metrics_pool_processes"),
    ("PIPELINE", "metrics_pool_maxtasksperchild"),
    ("PIPELINE", "metrics_scheduler_mode"),
    ("PIPELINE", "metrics_scheduler_prefetch_chunks"),
    ("PIPELINE", "metrics_scheduler_ready_queue_target"),
    ("PIPELINE", "metrics_idle_slot_supplement_enabled"),
    ("PIPELINE", "metrics_supplement_sample_soft_max"),
    ("PIPELINE", "metrics_supplement_sample_hard_max"),
    ("PIPELINE", "metrics_plot_prewarm_mode"),
    ("PIPELINE", "metrics_plot_aggregate_time_slice_s"),
    ("PIPELINE", "metrics_plot_aggregate_max_host_time_points"),
    ("PIPELINE", "metrics_run_poll_timeout_s"),
    ("PIPELINE", "metrics_run_stall_timeout_s"),
    ("PIPELINE", "metrics_run_per_job_timeout_s"),
    ("PIPELINE", "metrics_worker_statement_timeout_ms"),
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
    ("PIPELINE", "sync_ingest_pool_processes"),
    ("PIPELINE", "sync_archive_pool_processes"),
    ("PIPELINE", "sync_ingest_queue_max_size"),
    ("PIPELINE", "sync_ingest_rescan_mtime_days"),
    ("PIPELINE", "sync_ingest_rescan_full_every"),
    ("PIPELINE", "sync_ingest_current_proximity_days"),
    ("PIPELINE", "sync_archive_queue_max_size"),
    ("PIPELINE", "sync_timedb_archive_max_concurrent_sealed_days"),
    ("PIPELINE", "sync_archive_retry_max_attempts"),
    ("PIPELINE", "sync_archive_retry_backoff_base_seconds"),
    ("PIPELINE", "sync_archive_retry_backoff_max_seconds"),
    ("PIPELINE", "sync_checkpoint_flush_batch_size"),
    ("PIPELINE", "sync_timedb_tar_append_batch_size"),
    ("PIPELINE", "sync_host_itimes_cache_max_timestamps_per_entry"),
    ("PIPELINE", "sync_pool_poll_timeout_s"),
    ("PIPELINE", "sync_pool_stall_defer_log_interval_s"),
    ("PIPELINE", "sync_pool_stall_abort_after_timeouts"),
    ("PIPELINE", "sync_pool_worker_recycle_grace_polls"),
    ("PIPELINE", "sync_pool_worker_recycle_grace_seconds"),
    ("PIPELINE", "sync_pool_idle_reconcile_max_rounds"),
    ("PIPELINE", "sync_pool_idle_reconcile_polls_per_round"),
    ("PIPELINE", "sync_ingest_per_file_timeout_s"),
    ("PIPELINE", "sync_ingest_per_file_timeout_max_s"),
    ("PIPELINE", "sync_ingest_per_file_timeout_s_per_mib"),
    ("PIPELINE", "sync_ingest_giant_pool_supplement_enabled"),
    ("PIPELINE", "sync_ingest_giant_pool_supplement_max_bytes"),
    ("PIPELINE", "sync_ingest_giant_pool_supplement_large_max_bytes"),
    ("PIPELINE", "sync_ingest_giant_pool_supplement_queue_multiplier"),
    ("PIPELINE", "sync_ingest_giant_pool_supplement_trigger_budget_s"),
    ("PIPELINE", "sync_ingest_idle_slot_supplement_enabled"),
    ("PIPELINE", "sync_archive_members_cache_enabled"),
    ("PIPELINE", "sync_archive_members_cache_max_entries"),
    ("PIPELINE", "sync_archive_members_redis_enabled"),
    ("PIPELINE", "sync_archive_members_redis_ttl_seconds"),
    ("PIPELINE", "sync_archive_members_redis_populate_lock_seconds"),
    ("PIPELINE", "sync_archive_members_redis_populate_stall_seconds"),
    ("PIPELINE", "sync_archive_members_redis_populate_max_seconds"),
    ("PIPELINE", "sync_daily_tar_restore_lease_seconds"),
    ("PIPELINE", "sync_archive_members_fnctl_read_lock_timeout_seconds"),
    ("PIPELINE", "sync_archive_members_redis_wait_poll_seconds"),
    ("PIPELINE", "sync_archive_members_redis_hset_batch_size"),
    ("PIPELINE", "sync_archive_members_redis_max_payload_bytes"),
    ("PIPELINE", "sync_archive_members_populate_pool_processes"),
    ("PIPELINE", "sync_write_lock_shards"),
    ("PIPELINE", "sync_bulk_create_batch_size"),
    ("PIPELINE", "sync_supervisor_rss_limit_mb"),
    ("PIPELINE", "sync_supervisor_rss_check_every_n_chunks"),
    ("PIPELINE", "sync_process_tree_rss_limit_mb"),
    ("PIPELINE", "sync_process_tree_rss_check_every_n_chunks"),
    ("PIPELINE", "sync_process_tree_rss_exit_mb"),
    ("PIPELINE", "sync_ingest_max_file_read_bytes"),
    ("PIPELINE", "sync_ingest_stream_duplicate_scan_bytes"),
    ("PIPELINE", "sync_ingest_db_complete_tail_window_lines"),
    ("PIPELINE", "sync_ingest_pool_maxtasksperchild"),
    ("PIPELINE", "sync_ingest_malloc_trim_after_file"),
    ("PIPELINE", "sync_ingest_worker_memory_telemetry"),
    ("PIPELINE", "sync_ingest_worker_memory_telemetry_every_n_chunks"),
    ("PIPELINE", "sync_ingest_recycle_worker_on_failure"),
    ("PIPELINE", "sync_ingest_cooperative_recycle_rss_fraction"),
    ("PIPELINE", "sync_ingest_rss_recheck_delay_ms"),
    ("PIPELINE", "sync_enable_ingest_first_durability_mode"),
    ("PIPELINE", "sync_archive_require_db_ingest"),
    ("PIPELINE", "sync_archive_maint_hints"),
    ("PIPELINE", "listend_db_ingest_enabled"),
    ("PIPELINE", "listend_db_ingest_pool_processes"),
    ("PIPELINE", "listend_db_ingest_queue_max_gb"),
    ("PIPELINE", "listend_db_ingest_batch_samples"),
    ("PIPELINE", "acct_path"),
    ("PIPELINE", "archive_dir"),
    ("PIPELINE", "daily_archive_dir"),
    ("PIPELINE", "archive_keep_uncompressed_tar"),
    ("PIPELINE", "archive_today_uncompressed_tar_grace_hours"),
    ("PIPELINE", "archive_zstd_threads"),
    ("PIPELINE", "ingest_zstd_threads"),
    ("PIPELINE", "archive_zstd_level"),
    ("PIPELINE", "archive_zstd_nice"),
    ("PIPELINE", "archive_zstd_ionice_class"),
    ("PIPELINE", "archive_zstd_ionice_level"),
    ("PIPELINE", "archive_zstd_drop_page_cache"),
    ("PIPELINE", "archive_seal_parallel_workers"),
    ("PIPELINE", "archive_maintenance_idle_seconds"),
    ("PIPELINE", "archive_janitor_budget_seconds"),
    ("PIPELINE", "archive_janitor_debt_high_watermark"),
    ("PIPELINE", "archive_janitor_debt_burst_factor"),
    ("PIPELINE", "archive_janitor_debt_max_entries"),
    ("PIPELINE", "archive_janitor_raw_paths_per_tick"),
    ("PIPELINE", "sync_day_close_candidate_report"),
    ("PIPELINE", "sync_startup_snapshot_wait_seconds"),
    ("PIPELINE", "sync_day_close_max_inflight"),
    ("PIPELINE", "sync_day_close_manifest_stale_seconds"),
    ("PIPELINE", "sync_day_close_raw_removal_max_deletes_per_pass"),
    ("PIPELINE", "sync_archive_max_inflight_jobs"),
    ("PIPELINE", "sync_archive_validation_max_workers"),
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
INI_OPTION_DEFAULTS = {
    'machine': None,
    'host_name_ext': None,
    'data_dir': None,
    'server': None,
    'restricted_queue_keywords': None,
    'debug': 'no',
    'staff_email_domain': None,
    'timezone': None,
    'secret_key': None,
    'total_cores': '40',
    'engine_name': None,
    'dbname': None,
    'username': None,
    'password': None,
    'host': None,
    'port': None,
    'cpuset_pin_min_total_cores': '32',
    'cpuset_pin_min_cores_per_node': '16',
    'numa_pin_max_nodes_auto': '16',
    'pin_proxy_in_compose': 'no',
    'web_numa_node': None,
    'pipeline_numa_node': None,
    'cors_origin_scheme': '',
    'gunicorn_workers': '32',
    'summary_aggregate_prefetch_max_threads': '2',
    'parallel_db_prefetch_max': '4',
    'api_small_executor_max_workers': None,
    'db_conn_max_age': '90',
    'db_statement_timeout_ms': '120000',
    'db_idle_in_transaction_session_timeout_ms': '300000',
    'metrics_pool_processes': '24',
    'metrics_pool_maxtasksperchild': '16',
    'metrics_scheduler_mode': 'global_priority',
    'metrics_scheduler_prefetch_chunks': '8',
    'metrics_scheduler_ready_queue_target': '100',
    'metrics_idle_slot_supplement_enabled': 'yes',
    'metrics_supplement_sample_soft_max': '10000',
    'metrics_supplement_sample_hard_max': '80000',
    'metrics_plot_prewarm_mode': 'pipeline_required',
        # Host×time SQL chunk wall seconds (1h @ 1-min sample); design 5000×48×60.
    'metrics_plot_aggregate_time_slice_s': '3600',
    # Max host×time rows materialised per plot aggregate DF (not 14.4M).
    'metrics_plot_aggregate_max_host_time_points': '1000000',
    'metrics_run_poll_timeout_s': '5',
    'metrics_run_stall_timeout_s': '900',
    'metrics_run_per_job_timeout_s': '0',
    # Non-zero arms host__in chunk-on-timeout during compute (0 = disable).
    'metrics_worker_statement_timeout_ms': '120000',
    'metrics_persist_statement_timeout_ms': '120000',
    'metrics_persist_lock_timeout_ms': '10000',
    'metrics_proxy_reject_jid_batch_size': '48',
    'metrics_compute_batch_max_window_s': '0.0',
    'metrics_compute_batch_max_single_job_s': '0.0',
    'metrics_compute_batch_unknown_runtime_s': '172800.0',
    'metrics_compute_watchdog_s': '120.0',
    'metrics_compute_total_watchdog_s': '0.0',
    'metrics_deferred_not_ready_retry_s': '10.0',
    'metrics_deferred_not_ready_max_retries': '30',
    'metrics_deferred_not_ready_max_age_s': '900.0',
    'metrics_deferred_not_ready_quarantine_s': '300.0',
    'metrics_readiness_require_window_coverage': 'yes',
    'metrics_readiness_start_margin_seconds': '600',
    'metrics_readiness_end_margin_seconds': '600',
    'sync_ingest_pool_processes': '16',
    'sync_archive_pool_processes': '2',
        'sync_ingest_queue_max_size': '3000',
    'sync_ingest_rescan_mtime_days': '1',
    'sync_ingest_rescan_full_every': '100',
    'sync_ingest_current_proximity_days': '2',
    'sync_archive_queue_max_size': '1000',
    'sync_timedb_archive_max_concurrent_sealed_days': '1',
    'sync_archive_retry_max_attempts': '5',
    'sync_archive_retry_backoff_base_seconds': '1',
    'sync_archive_retry_backoff_max_seconds': '60',
    'sync_checkpoint_flush_batch_size': '100',
    'sync_timedb_tar_append_batch_size': '1024',
    'sync_host_itimes_cache_max_timestamps_per_entry': '100000',
    'sync_pool_poll_timeout_s': '5',
    'sync_pool_stall_defer_log_interval_s': '60',
    'sync_pool_stall_abort_after_timeouts': '17320',
    'sync_pool_worker_recycle_grace_polls': '2',
    'sync_pool_worker_recycle_grace_seconds': '60',
    'sync_pool_idle_reconcile_max_rounds': '3',
    'sync_pool_idle_reconcile_polls_per_round': '4',
    'sync_ingest_per_file_timeout_s': '3600',
    'sync_ingest_per_file_timeout_max_s': '86400',
    'sync_ingest_per_file_timeout_s_per_mib': '2.783203125',
    'sync_ingest_giant_pool_supplement_enabled': 'yes',
    'sync_ingest_giant_pool_supplement_max_bytes': '1073741824',
    'sync_ingest_giant_pool_supplement_large_max_bytes': '8589934592',
    'sync_ingest_giant_pool_supplement_queue_multiplier': '2',
    'sync_ingest_giant_pool_supplement_trigger_budget_s': '6600',
    'sync_ingest_idle_slot_supplement_enabled': 'yes',
    'sync_archive_members_cache_enabled': 'yes',
    'sync_archive_members_cache_max_entries': '64',
    'sync_archive_members_redis_enabled': 'yes',
    'sync_archive_members_redis_ttl_seconds': '86400',
    'sync_archive_members_redis_populate_lock_seconds': '3600',
    'sync_archive_members_redis_populate_stall_seconds': '120',
    'sync_archive_members_redis_populate_max_seconds': '7200',
    'sync_daily_tar_restore_lease_seconds': '14400',
    'sync_archive_members_fnctl_read_lock_timeout_seconds': '180',
    'sync_archive_members_redis_wait_poll_seconds': '0.25',
    'sync_archive_members_redis_hset_batch_size': '500',
    'sync_archive_members_redis_max_payload_bytes': '8388608',
    'sync_archive_members_populate_pool_processes': '4',
    'sync_write_lock_shards': '8',
    'sync_bulk_create_batch_size': '10000',
    'sync_supervisor_rss_limit_mb': '0',
    'sync_supervisor_rss_check_every_n_chunks': '1',
    'sync_process_tree_rss_limit_mb': '110000',
    'sync_process_tree_rss_check_every_n_chunks': '1',
    'sync_process_tree_rss_exit_mb': '0',
    'sync_ingest_max_file_read_bytes': '536870912',
    'sync_ingest_stream_duplicate_scan_bytes': '8388608',
    'sync_ingest_db_complete_tail_window_lines': '500',
    'sync_ingest_pool_maxtasksperchild': '0',
    'sync_ingest_malloc_trim_after_file': 'yes',
    'sync_ingest_worker_memory_telemetry': 'no',
    'sync_ingest_worker_memory_telemetry_every_n_chunks': '1',
    'sync_ingest_recycle_worker_on_failure': 'yes',
    'sync_ingest_cooperative_recycle_rss_fraction': '0.5',
    'sync_ingest_rss_recheck_delay_ms': '50',
    'sync_enable_ingest_first_durability_mode': 'yes',
    'sync_archive_require_db_ingest': 'yes',
    'sync_archive_maint_hints': 'yes',
    'listend_db_ingest_enabled': 'yes',
    'listend_db_ingest_pool_processes': '32',
    'listend_db_ingest_queue_max_gb': '8',
    'listend_db_ingest_batch_samples': '100',
    'acct_path': None,
    'archive_dir': None,
    'daily_archive_dir': None,
    'archive_keep_uncompressed_tar': 'no',
    'archive_today_uncompressed_tar_grace_hours': '24.0',
    'archive_zstd_threads': '0',
    'ingest_zstd_threads': '4',
    'archive_zstd_level': '7',
    'archive_zstd_nice': '10',
    'archive_zstd_ionice_class': '2',
    'archive_zstd_ionice_level': '6',
    'archive_zstd_drop_page_cache': 'yes',
    'archive_seal_parallel_workers': '4',
    'archive_maintenance_idle_seconds': '300',
    'archive_janitor_budget_seconds': '30',
    'archive_janitor_debt_high_watermark': '50',
    'archive_janitor_debt_burst_factor': '1.5',
    'archive_janitor_debt_max_entries': '200',
    'archive_janitor_raw_paths_per_tick': '1000',
    'sync_day_close_candidate_report': 'yes',
    'sync_startup_snapshot_wait_seconds': '300',
    'sync_day_close_max_inflight': '4',
    'sync_day_close_manifest_stale_seconds': '7200',
    'sync_day_close_raw_removal_max_deletes_per_pass': '0',
    'sync_archive_max_inflight_jobs': '2',
    'sync_archive_validation_max_workers': '2',
    'sync_archive_worker_stall_seconds': '600',
    'client_id': None,
    'client_key': None,
    'oauth_base_url': None,
    'authorize_url': None,
    'rmq_server': None,
    'rmq_queue': None,
    'allow_from': '',
    'listen_tcp': 'yes',
    'listen_udp': 'yes',
    'redis_location': None,
    'xalt_engine': None,
    'xalt_name': None,
    'xalt_user': None,
    'xalt_password': None,
    'xalt_host': None,
}

INI_OPTION_REGISTRY = tuple(
    (section, option, INI_OPTION_DEFAULTS[option])
    for section, option in _INI_OPTION_REGISTRY_KEYS
)

_INI_REGISTRY_DEFAULT_BY_OPTION = {
    option: default for _section, option, default in INI_OPTION_REGISTRY
}

_INI_FALLBACK_SENTINEL = object()


def ini_registry_default(option: Any) -> Any:
  """
  Return the code default for *option* (may be None).
  
  Args:
    option (Any): Option passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    KeyError: Raised when ``ini_registry_default`` hits a ``KeyError`` failure
    path.
  
  Examples:
    >>> ini_registry_default(None)  # doctest: +SKIP
  """
  try:
    return _INI_REGISTRY_DEFAULT_BY_OPTION[option]
  except KeyError as exc:
    raise KeyError('unknown ini option %r' % option) from exc


def _ini_registry_default_str(option: Any) -> Any:
  """
  Return registry default as str; raises NoOptionError when default is None.
  
  Args:
    option (Any): Option passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    configparser.NoOptionError: Raised when ``_ini_registry_default_str`` hits
    a ``configparser.NoOptionError`` failure path.
  
  Examples:
    >>> _ini_registry_default_str(None)  # doctest: +SKIP
  """
  default = ini_registry_default(option)
  if default is None:
    raise configparser.NoOptionError(option, 'registry')
  return str(default)


def _ini_get_from_registry(
  primary_section: Any,
  option: Any,
  *,
  legacy_sections: tuple[Any, ...] = (),
) -> Any:
  """
  Like ``_ini_get`` but uses ``INI_OPTION_DEFAULTS`` for the fallback.
  
  Args:
    primary_section (Any): Primary section passed to this helper.
    option (Any): Option passed to this helper.
    legacy_sections (tuple[Any, ...]): Sequence for legacy sections.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ini_get_from_registry(None, None, [])  # doctest: +SKIP
  """
  return _ini_get(
      primary_section,
      option,
      fallback=_ini_registry_default_str(option),
      legacy_sections=legacy_sections,
  )


def ini_option_registry_pairs() -> Any:
  """
  Return list of (section, option) from INI_OPTION_REGISTRY.
  
  Returns:
    Any: Open return polymorphism from ``ini_option_registry_pairs``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> ini_option_registry_pairs()  # doctest: +SKIP
  """
  return [(section, option) for section, option, _default in INI_OPTION_REGISTRY]


def ini_option_registry_set() -> Any:
  """
  Return the set of (section, option) tuples in INI_OPTION_REGISTRY.
  
  Returns:
    Any: Open return polymorphism from ``ini_option_registry_set``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> ini_option_registry_set()  # doctest: +SKIP
  """
  return {(section, option) for section, option, _default in INI_OPTION_REGISTRY}


def _candidate_config_paths() -> Any:
  """
  Return candidate config paths in lookup order.
  
  Search order is explicit env override first, then common runtime locations,
  then the bundled example as a development fallback.
  
  Returns:
    Any: Open return polymorphism from ``_candidate_config_paths``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _candidate_config_paths()  # doctest: +SKIP
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


def _load_cfg() -> None:
  """
  Load config from first existing candidate path.
  
  Returns:
    None
  
  Raises:
    FileNotFoundError: Raised when ``_load_cfg`` hits a ``FileNotFoundError``
    failure path.
  
  Examples:
    >>> _load_cfg()  # doctest: +SKIP
  """
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


def _ensure_cfg_loaded() -> None:
  """
  Initialize config parser lazily.
  
  Returns:
    None
  
  Examples:
    >>> _ensure_cfg_loaded()  # doctest: +SKIP
  """
  if cfg is None:
    _load_cfg()


def _get(section: Any, option: Any) -> Any:
  """
  Return config value for section/option. Single place for simple getters.
  
  Args:
    section (Any): Section passed to this helper.
    option (Any): Option passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    configparser.NoSectionError: Raised when ``_get`` hits a
    ``configparser.NoSectionError`` failure path.
  
  Examples:
    >>> _get(None, None)  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  if not cfg.has_section(section) and section != "DEFAULT":
    raise configparser.NoSectionError(
        "Missing section '%s' in %s" % (
            section,
            _ACTIVE_CONFIG_PATH,
        )
    )
  return cfg.get(section, option)


def _ini_has_option(section: Any, option: Any) -> Any:
  """
  Return True when *option* is set under *section* (DEFAULT always exists).
  
  Args:
    section (Any): Section passed to this helper.
    option (Any): Option passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ini_has_option(None, None)  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  if section == "DEFAULT":
    return cfg.has_option("DEFAULT", option)
  return cfg.has_section(section) and cfg.has_option(section, option)


def _ini_option(
  primary_section: Any,
  option: Any,
  legacy_sections: tuple[Any, ...] = (),
) -> Any:
  """
  Read *option* from *primary_section*, then legacy sections in order.
  
  Args:
    primary_section (Any): Primary section passed to this helper.
    option (Any): Option passed to this helper.
    legacy_sections (tuple[Any, ...]): Sequence for legacy sections.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    configparser.NoOptionError: Raised when ``_ini_option`` hits a
    ``configparser.NoOptionError`` failure path.
    configparser.NoSectionError: Raised when ``_ini_option`` hits a
    ``configparser.NoSectionError`` failure path.
  
  Examples:
    >>> _ini_option(None, None, [])  # doctest: +SKIP
  """
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


def _ini_get(
  primary_section: Any,
  option: Any,
  *,
  fallback: Any | None = None,
  legacy_sections: tuple[Any, ...] = (),
) -> Any:
  """
  Like ``_ini_option`` but returns *fallback* when absent everywhere.
  
  Args:
    primary_section (Any): Primary section passed to this helper.
    option (Any): Option passed to this helper.
    fallback (Any | None): One of ``Any``, ``None``.
    legacy_sections (tuple[Any, ...]): Sequence for legacy sections.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ini_get(None, None, None, [])  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  if _ini_has_option(primary_section, option):
    return cfg.get(primary_section, option)
  for legacy in legacy_sections:
    if _ini_has_option(legacy, option):
      return cfg.get(legacy, option)
  if fallback is not None:
    return str(fallback)
  return _ini_option(primary_section, option, legacy_sections=legacy_sections)


def _ini_getint(
  primary_section: Any,
  option: Any,
  *,
  legacy_sections: tuple[Any, ...] = (),
) -> Any:
  """
  Internal helper to handle ini getint.
  
  Args:
    primary_section (Any): Primary section passed to this helper.
    option (Any): Option passed to this helper.
    legacy_sections (tuple[Any, ...]): Sequence for legacy sections.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ini_getint(None, None, [])  # doctest: +SKIP
  """
  return int(_ini_option(primary_section, option, legacy_sections=legacy_sections))


def _ini_has_option_any(
  primary_section: Any,
  option: Any,
  legacy_sections: tuple[Any, ...] = (),
) -> Any:
  """
  Internal helper to handle ini has option any.
  
  Args:
    primary_section (Any): Primary section passed to this helper.
    option (Any): Option passed to this helper.
    legacy_sections (tuple[Any, ...]): Sequence for legacy sections.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ini_has_option_any(None, None, [])  # doctest: +SKIP
  """
  if _ini_has_option(primary_section, option):
    return True
  return any(_ini_has_option(legacy, option) for legacy in legacy_sections)


_PIPELINE_LEGACY = ("DEFAULT",)
_PIPELINE_ARCHIVE_LEGACY = ("PORTAL",)


def _pipeline_get(
  option: Any,
  *,
  fallback: Any = _INI_FALLBACK_SENTINEL,
) -> Any:
  """
  Read a PIPELINE option with DEFAULT legacy fallback.
  
  Args:
    option (Any): Option passed to this helper.
    fallback (Any): Fallback passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _pipeline_get(None, None)  # doctest: +SKIP
  """
  if fallback is _INI_FALLBACK_SENTINEL:
    fallback = _ini_registry_default_str(option)
  return _ini_get(
      "PIPELINE", option, fallback=str(fallback), legacy_sections=_PIPELINE_LEGACY,
  )


def _pipeline_has_option(option: Any) -> Any:
  """
  Internal helper to handle pipeline has option.
  
  Args:
    option (Any): Option passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _pipeline_has_option(None)  # doctest: +SKIP
  """
  return _ini_has_option_any("PIPELINE", option, _PIPELINE_LEGACY)


def _pipeline_getint(
  option: Any,
  *,
  fallback: Any = _INI_FALLBACK_SENTINEL,
) -> Any:
  """
  Internal helper to handle pipeline getint.
  
  Args:
    option (Any): Option passed to this helper.
    fallback (Any): Fallback passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _pipeline_getint(None, None)  # doctest: +SKIP
  """
  return int(_pipeline_get(option, fallback=fallback))


def _pipeline_getfloat(
  option: Any,
  *,
  fallback: Any = _INI_FALLBACK_SENTINEL,
) -> Any:
  """
  Internal helper to handle pipeline getfloat.
  
  Args:
    option (Any): Option passed to this helper.
    fallback (Any): Fallback passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _pipeline_getfloat(None, None)  # doctest: +SKIP
  """
  return float(_pipeline_get(option, fallback=fallback))


def _parse_bool(raw: Any, *, default: bool = False) -> Any:
  """
  Internal helper to parse the bool.
  
  Args:
    raw (Any): Raw passed to this helper.
    default (bool): Boolean flag for default.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _parse_bool(None, True)  # doctest: +SKIP
  """
  if raw is None:
    return default
  value = str(raw).strip().lower()
  if value in ("1", "yes", "true", "on"):
    return True
  if value in ("0", "no", "false", "off"):
    return False
  return default


def _env_or_cfg_int(
  env_key: Any,
  section: Any,
  option: Any,
  fallback: Any = _INI_FALLBACK_SENTINEL,
  legacy_sections: tuple[Any, ...] = (),
) -> Any:
  """
  Internal helper to handle env or config int.
  
  Args:
    env_key (Any): Env key passed to this helper.
    section (Any): Section passed to this helper.
    option (Any): Option passed to this helper.
    fallback (Any): Fallback passed to this helper.
    legacy_sections (tuple[Any, ...]): Sequence for legacy sections.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _env_or_cfg_int(None, None, None, None, [])  # doctest: +SKIP
  """
  env = os.environ.get(env_key, "").strip()
  if env:
    return int(env)
  _ensure_cfg_loaded()
  if fallback is _INI_FALLBACK_SENTINEL:
    fallback = _ini_registry_default_str(option)
  return int(_ini_get(
      section, option, fallback=str(fallback), legacy_sections=legacy_sections,
  ))


def _env_or_cfg_bounded_float(
  env_key: Any,
  section: Any,
  option: Any,
  fallback: Any = _INI_FALLBACK_SENTINEL,
  *,
  lower: Any,
  upper: Any,
  legacy_sections: tuple[Any, ...] = (),
) -> Any:
  """
  Internal helper to handle env or config bounded float.
  
  Args:
    env_key (Any): Env key passed to this helper.
    section (Any): Section passed to this helper.
    option (Any): Option passed to this helper.
    fallback (Any): Fallback passed to this helper.
    lower (Any): Lower passed to this helper.
    upper (Any): Upper passed to this helper.
    legacy_sections (tuple[Any, ...]): Sequence for legacy sections.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _env_or_cfg_bounded_float(None, None, None, None, None, None, [])
  """
  env = os.environ.get(env_key, "").strip()
  if env:
    return max(lower, min(upper, float(env)))
  _ensure_cfg_loaded()
  if fallback is _INI_FALLBACK_SENTINEL:
    fallback = _ini_registry_default_str(option)
  return max(
      lower,
      min(upper, float(_ini_get(
          section, option, fallback=str(fallback), legacy_sections=legacy_sections,
      ))),
  )


def _optional_default_int_option(
  option_name: Any,
  *,
  legacy_sections: tuple[Any, ...] = (),
) -> Any:
  """
  Internal helper to handle optional default int option.
  
  Args:
    option_name (Any): Option name passed to this helper.
    legacy_sections (tuple[Any, ...]): Sequence for legacy sections.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _optional_default_int_option(None, [])  # doctest: +SKIP
  """
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


def get_db_name() -> Any:
  """
  Return the database name from DEFAULT config (legacy: PORTAL).
  
  Returns:
    Any: Open return polymorphism from ``get_db_name``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_db_name()  # doctest: +SKIP
  """
  return _ini_option("DEFAULT", "dbname", legacy_sections=("PORTAL",))


def get_debug() -> Any:
  """
  Return True if DEFAULT.debug is yes/true/1, else False.
  
  Missing DEFAULT.debug is treated as False to keep startup resilient when
  older/minimal configs omit this optional setting.
  
  Returns:
    Any: Open return polymorphism from ``get_debug``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_debug()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return cfg.get(
      'DEFAULT', 'debug', fallback=ini_registry_default('debug'),
  ).lower() in ("yes", "true", "1")


def get_secret_key() -> Any:
  """
  Return Django SECRET_KEY from DEFAULT.secret_key, or None if not set.
  
  Prefer environment variable SECRET_KEY over ini; settings.py should check
  os.environ first, then this, then fail or use dev default.
  
  Returns:
    Any: Open return polymorphism from ``get_secret_key``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_secret_key()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  if cfg.has_option('DEFAULT', 'secret_key'):
    return cfg.get('DEFAULT', 'secret_key').strip() or None
  return None


def get_archive_dir_path() -> Any:
  """
  Return the archive directory path from PIPELINE config (legacy: PORTAL).
  
  Returns:
    Any: Open return polymorphism from ``get_archive_dir_path``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_archive_dir_path()  # doctest: +SKIP
  """
  raw = _ini_option("PIPELINE", "archive_dir", legacy_sections=("PORTAL",)).strip()
  return os.path.normpath(raw) if raw else raw


def get_host_name_ext() -> Any:
  """
  Return the host name extension (domain) from DEFAULT config.
  
  Returns:
    Any: Open return polymorphism from ``get_host_name_ext``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_host_name_ext()  # doctest: +SKIP
  """
  return _get('DEFAULT', 'host_name_ext')


def get_restricted_queue_keywords() -> Any:
  """
  Return restricted queue keywords string from DEFAULT config.
  
  Returns:
    Any: Open return polymorphism from ``get_restricted_queue_keywords``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_restricted_queue_keywords()  # doctest: +SKIP
  """
  return _get('DEFAULT', 'restricted_queue_keywords')


def get_accounting_path() -> Any:
  """
  Return the accounting (sacct) file path from PIPELINE config (legacy: PORTAL).
  
  Returns:
    Any: Open return polymorphism from ``get_accounting_path``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_accounting_path()  # doctest: +SKIP
  """
  return _ini_option("PIPELINE", "acct_path", legacy_sections=("PORTAL",))


def get_daily_archive_dir_path() -> Any:
  """
  Return the daily archive directory path from PIPELINE config (legacy: PORTAL).
  
  Returns:
    Any: Open return polymorphism from ``get_daily_archive_dir_path``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_daily_archive_dir_path()  # doctest: +SKIP
  """
  raw = _ini_option(
      "PIPELINE", "daily_archive_dir", legacy_sections=("PORTAL",),
  ).strip()
  return os.path.normpath(raw) if raw else raw


def get_archive_keep_uncompressed_tar() -> Any:
  """
  Return True if daily ``.tar`` should always be kept after sealing.
  
  Default False; calendar-today grace is handled by
  ``effective_keep_uncompressed_tar`` unless this global override is yes.
  
  Returns:
    Any: Open return polymorphism from ``get_archive_keep_uncompressed_tar``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_archive_keep_uncompressed_tar()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _ini_get_from_registry(
      "PIPELINE",
      "archive_keep_uncompressed_tar",
      legacy_sections=("PORTAL",),
  )
  return raw.lower() in ('yes', 'true', '1')


def get_archive_today_uncompressed_tar_grace_hours() -> Any:
  """
  Hours after local midnight to retain today's uncompressed ``.tar`` when.
  
    global.
  
    keep is off.
  
  Returns:
    Any: Open return polymorphism from
    ``get_archive_today_uncompressed_tar_grace_hours``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_archive_today_uncompressed_tar_grace_hours()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      0.0,
      _pipeline_getfloat("archive_today_uncompressed_tar_grace_hours"),
  )


def _ini_bool(value: Any, default: bool = False) -> Any:
  """
  Internal helper to handle ini bool.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
    default (bool): Boolean flag for default.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ini_bool(None, True)  # doctest: +SKIP
  """
  if value is None:
    return default
  return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def get_archive_zstd_level() -> Any:
  """
  Native zstd compression level (1--19) for sealing ``.tar.zst``. Default 7.
  
  Returns:
    Any: Open return polymorphism from ``get_archive_zstd_level``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_archive_zstd_level()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _ini_get_from_registry(
      "PIPELINE", "archive_zstd_level", legacy_sections=("PORTAL",),
  )
  level = int(raw)
  return max(1, min(19, level))


def get_archive_zstd_threads() -> Any:
  """
  zstd ``-T`` thread count for archive compress/decompress. Default 0 (-T0).
  
  Returns:
    Any: Open return polymorphism from ``get_archive_zstd_threads``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_archive_zstd_threads()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _ini_get_from_registry(
      "PIPELINE", "archive_zstd_threads", legacy_sections=("PORTAL",),
  )
  return max(0, int(raw))


def get_ingest_zstd_threads() -> Any:
  """
  zstd ``-T`` for un-niced ingest/populate sealed streams. Default 4 (-T4).
  
  Returns:
    Any: Open return polymorphism from ``get_ingest_zstd_threads``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_ingest_zstd_threads()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _ini_get_from_registry(
      "PIPELINE", "ingest_zstd_threads", legacy_sections=("PORTAL",),
  )
  return max(0, int(raw))


def get_archive_zstd_nice() -> Any:
  """
  Added nice for archive zstd child processes (0 disables). Default 10.
  
  Returns:
    Any: Open return polymorphism from ``get_archive_zstd_nice``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_archive_zstd_nice()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _ini_get_from_registry(
      "PIPELINE", "archive_zstd_nice", legacy_sections=("PORTAL",),
  )
  return max(0, int(raw))


def get_archive_zstd_ionice_class() -> Any:
  """
  I/O scheduling class for archive zstd (0=none, 2=best-effort, 3=idle).
  
    Default.
  
    2.
  
  Returns:
    Any: Open return polymorphism from ``get_archive_zstd_ionice_class``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_archive_zstd_ionice_class()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _ini_get_from_registry(
      "PIPELINE",
      "archive_zstd_ionice_class",
      legacy_sections=("PORTAL",),
  )
  return max(0, min(3, int(raw)))


def get_archive_zstd_ionice_level() -> Any:
  """
  I/O priority level within class for archive zstd (0-7). Default 6.
  
  Returns:
    Any: Open return polymorphism from ``get_archive_zstd_ionice_level``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_archive_zstd_ionice_level()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _ini_get_from_registry(
      "PIPELINE",
      "archive_zstd_ionice_level",
      legacy_sections=("PORTAL",),
  )
  return max(0, min(7, int(raw)))


def get_archive_zstd_drop_page_cache() -> Any:
  """
  Linux posix_fadvise hints around archive zstd I/O (default on).
  
  Returns:
    Any: Open return polymorphism from ``get_archive_zstd_drop_page_cache``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_archive_zstd_drop_page_cache()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("archive_zstd_drop_page_cache"),
  )


def get_archive_seal_parallel_workers() -> Any:
  """
  Max concurrent daily tar seals during maintenance. Default 4.
  
  Returns:
    Any: Open return polymorphism from ``get_archive_seal_parallel_workers``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_archive_seal_parallel_workers()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _ini_get_from_registry(
      "PIPELINE",
      "archive_seal_parallel_workers",
      legacy_sections=("PORTAL",),
  )
  return max(1, int(raw))


def get_rmq_server() -> Any:
  """
  Return the RabbitMQ server host from RMQ config.
  
  Returns:
    Any: Open return polymorphism from ``get_rmq_server``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_rmq_server()  # doctest: +SKIP
  """
  return _get('RMQ', 'rmq_server')


def get_rmq_queue() -> Any:
  """
  Return the RabbitMQ queue name from RMQ config.
  
  Returns:
    Any: Open return polymorphism from ``get_rmq_queue``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_rmq_queue()  # doctest: +SKIP
  """
  return _get('RMQ', 'rmq_queue')


def get_server_name() -> Any:
  """
  Return the server name from DEFAULT config.
  
  Returns:
    Any: Open return polymorphism from ``get_server_name``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_server_name()  # doctest: +SKIP
  """
  return _get('DEFAULT', 'server')


def get_cors_origin_scheme() -> Any:
  """
  Return ``http`` or ``https`` when building CORS origins from ``[DEFAULT].
  
    server``.
  
  Optional ``[PORTAL] cors_origin_scheme`` may be set to ``http`` or ``https``
  (legacy: ``[DEFAULT]``). When omitted, defaults to ``https``.
  
  Returns:
    Any: Open return polymorphism from ``get_cors_origin_scheme``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_cors_origin_scheme()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _ini_get_from_registry(
      "PORTAL",
      "cors_origin_scheme",
      legacy_sections=("DEFAULT",),
  ).strip().lower()
  if raw in ('http', 'https'):
    return raw
  return 'https'


def format_cors_allowed_origins_csv_from_ini() -> Any:
  """
  Build comma-separated browser ``Origin`` values from ``[DEFAULT] server``.
  
  Mirrors how Django ``ALLOWED_HOSTS`` is populated from the same ``server``
  key: each comma-separated hostname becomes ``{scheme}://{host}`` unless the
  token already contains a scheme.
  
  Returns an empty string when ``debug`` is enabled (Django applies Vite dev
  origins instead) or when ``server`` is blank.
  
  Returns:
    Any: Open return polymorphism from
    ``format_cors_allowed_origins_csv_from_ini``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> format_cors_allowed_origins_csv_from_ini()  # doctest: +SKIP
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


def get_data_dir_path() -> Any:
  """
  Return the data directory path from DEFAULT config.
  
  Returns:
    Any: Open return polymorphism from ``get_data_dir_path``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_data_dir_path()  # doctest: +SKIP
  """
  return _get('DEFAULT', 'data_dir')


def get_syslog_allow_from_ipv4_networks() -> Any:
  """
  Return IPv4 CIDR strings for pipeline syslog-ng ``netmask()`` allowlist.
  
  Whitespace and commas separate entries. ``#`` starts an end-of-line comment.
  An **empty** list (missing ``[SYSLOG]``, blank ``allow_from``, or only
  comments) means **allow all IPv4** (``0.0.0.0/0``) for backward compatibility.
  IPv6-only networks are skipped with no error (syslog-ng filter uses
  ``netmask()`` IPv4 form in generated config).
  
  Returns:
    Any: Open return polymorphism from
    ``get_syslog_allow_from_ipv4_networks``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Raises:
    ValueError: Raised when ``get_syslog_allow_from_ipv4_networks`` hits a
    ``ValueError`` failure path.
  
  Examples:
    >>> get_syslog_allow_from_ipv4_networks()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  if not cfg.has_section('SYSLOG'):
    return []
  raw = cfg.get(
      'SYSLOG', 'allow_from', fallback=ini_registry_default('allow_from'),
  ).strip()
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


def get_syslog_listen_tcp() -> Any:
  """
  Return True if pipeline syslog-ng should listen on TCP 514 (default True).
  
  Returns:
    Any: Open return polymorphism from ``get_syslog_listen_tcp``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_syslog_listen_tcp()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  if not cfg.has_section('SYSLOG'):
    return True
  return _parse_bool(
      cfg.get('SYSLOG', 'listen_tcp', fallback=ini_registry_default('listen_tcp')),
      default=True,
  )


def get_syslog_listen_udp() -> Any:
  """
  Return True if pipeline syslog-ng should listen on UDP 514 (default True).
  
  Returns:
    Any: Open return polymorphism from ``get_syslog_listen_udp``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_syslog_listen_udp()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  if not cfg.has_section('SYSLOG'):
    return True
  return _parse_bool(
      cfg.get('SYSLOG', 'listen_udp', fallback=ini_registry_default('listen_udp')),
      default=True,
  )


def get_syslog_logs_current_path() -> Any:
  """
  Directory for live per-host syslog files (under ``data_dir``).
  
  Returns:
    Any: Open return polymorphism from ``get_syslog_logs_current_path``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_syslog_logs_current_path()  # doctest: +SKIP
  """
  return os.path.normpath(
      os.path.join(get_data_dir_path(), 'logs', 'current'),
  )


def get_syslog_logs_archive_path() -> Any:
  """
  Directory for sealed daily syslog tarballs (under ``data_dir``).
  
  Returns:
    Any: Open return polymorphism from ``get_syslog_logs_archive_path``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_syslog_logs_archive_path()  # doctest: +SKIP
  """
  return os.path.normpath(
      os.path.join(get_data_dir_path(), 'logs', 'log_archive'),
  )


def get_syslog_generated_config_path() -> Any:
  """
  Runtime path for syslog-ng fragment (inside the pipeline container).
  
  Stored under ``/var/lib/`` (not ``services-conf/``) so a bind-mount over
  ``/home/hpcperfstats/services-conf`` cannot hide or make read-only the
  generated file that ``@include`` requires.
  
  Returns:
    Any: Open return polymorphism from ``get_syslog_generated_config_path``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_syslog_generated_config_path()  # doctest: +SKIP
  """
  return '/var/lib/hpcperfstats-syslog/generated.conf'


def get_engine_name() -> Any:
  """
  Return the Django database engine name from DEFAULT config (legacy: PORTAL).
  
  Returns:
    Any: Open return polymorphism from ``get_engine_name``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_engine_name()  # doctest: +SKIP
  """
  return _ini_option("DEFAULT", "engine_name", legacy_sections=("PORTAL",))


def get_username() -> Any:
  """
  Return the portal DB username from DEFAULT config (legacy: PORTAL).
  
  Returns:
    Any: Open return polymorphism from ``get_username``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_username()  # doctest: +SKIP
  """
  return _ini_option("DEFAULT", "username", legacy_sections=("PORTAL",))


def get_password() -> Any:
  """
  Return the portal DB password from DEFAULT config (legacy: PORTAL).
  
  Returns:
    Any: Open return polymorphism from ``get_password``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_password()  # doctest: +SKIP
  """
  return _ini_option("DEFAULT", "password", legacy_sections=("PORTAL",))


def get_host() -> Any:
  """
  Return the portal DB host from DEFAULT config (legacy: PORTAL).
  
  Returns:
    Any: Open return polymorphism from ``get_host``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_host()  # doctest: +SKIP
  """
  return _ini_option("DEFAULT", "host", legacy_sections=("PORTAL",))


def get_port() -> Any:
  """
  Return the portal DB port from DEFAULT config (legacy: PORTAL).
  
  Returns:
    Any: Open return polymorphism from ``get_port``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_port()  # doctest: +SKIP
  """
  return _ini_option("DEFAULT", "port", legacy_sections=("PORTAL",))


def get_xalt_engine() -> Any:
  """
  Return the XALT database engine from XALT config.
  
  Returns:
    Any: Open return polymorphism from ``get_xalt_engine``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_xalt_engine()  # doctest: +SKIP
  """
  return _get('XALT', 'xalt_engine')


def get_xalt_name() -> Any:
  """
  Return the XALT database name from XALT config.
  
  Returns:
    Any: Open return polymorphism from ``get_xalt_name``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_xalt_name()  # doctest: +SKIP
  """
  return _get('XALT', 'xalt_name')


def get_xalt_user() -> Any:
  """
  Return the XALT DB user from XALT config.
  
  Returns:
    Any: Open return polymorphism from ``get_xalt_user``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_xalt_user()  # doctest: +SKIP
  """
  return _get('XALT', 'xalt_user')


def get_xalt_password() -> Any:
  """
  Return the XALT DB password from XALT config.
  
  Returns:
    Any: Open return polymorphism from ``get_xalt_password``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_xalt_password()  # doctest: +SKIP
  """
  return _get('XALT', 'xalt_password')


def get_xalt_host() -> Any:
  """
  Return the XALT DB host from XALT config.
  
  Returns:
    Any: Open return polymorphism from ``get_xalt_host``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_xalt_host()  # doctest: +SKIP
  """
  return _get('XALT', 'xalt_host')


def get_oauth_client_id() -> Any:
  """
  Return the OAuth2 client ID from OAUTH2 config.
  
  Returns:
    Any: Open return polymorphism from ``get_oauth_client_id``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_oauth_client_id()  # doctest: +SKIP
  """
  return _get('OAUTH2', 'client_id')


def get_oauth_client_key() -> Any:
  """
  Return the OAuth2 client key/secret from OAUTH2 config.
  
  Returns:
    Any: Open return polymorphism from ``get_oauth_client_key``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_oauth_client_key()  # doctest: +SKIP
  """
  return _get('OAUTH2', 'client_key')


def get_oauth_authorize_url() -> Any:
  """
  Return the OAuth2 authorization URL template from OAUTH2 config.
  
  Returns:
    Any: Open return polymorphism from ``get_oauth_authorize_url``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_oauth_authorize_url()  # doctest: +SKIP
  """
  return _get('OAUTH2', 'authorize_url')


def get_oauth_base_url() -> Any:
  """
  Return the OAuth2 tenant base URL from OAUTH2 config.
  
  Returns:
    Any: Open return polymorphism from ``get_oauth_base_url``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_oauth_base_url()  # doctest: +SKIP
  """
  return _get('OAUTH2', 'oauth_base_url')


def get_staff_email_domain() -> Any:
  """
  Return the staff email domain from DEFAULT config.
  
  Returns:
    Any: Open return polymorphism from ``get_staff_email_domain``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_staff_email_domain()  # doctest: +SKIP
  """
  return _get('DEFAULT', 'staff_email_domain')


def get_timezone() -> Any:
  """
  Return the timezone string from DEFAULT config.
  
  Returns:
    Any: Open return polymorphism from ``get_timezone``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_timezone()  # doctest: +SKIP
  """
  return _get('DEFAULT', 'timezone')


def get_local_timezone() -> Any:
  """
  Return the local timezone as a ZoneInfo for datetime conversion.
  
  Returns:
    Any: Open return polymorphism from ``get_local_timezone``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_local_timezone()  # doctest: +SKIP
  """
  return ZoneInfo(get_timezone())


def get_total_cores() -> Any:
  """
  Return the total cores count string from DEFAULT config.
  
  If ``total_cores`` is omitted, returns ``"40"`` (default when not set in ini).
  
  Returns:
    Any: Open return polymorphism from ``get_total_cores``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_total_cores()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return cfg.get(
      "DEFAULT", "total_cores", fallback=ini_registry_default("total_cores"),
  ).strip() or ini_registry_default("total_cores")


def get_ini_total_cores_int() -> Any:
  """
  Return ``int(total_cores)`` from ini (or default 40 when missing).
  
  Returns:
    Any: Open return polymorphism from ``get_ini_total_cores_int``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_ini_total_cores_int()  # doctest: +SKIP
  """
  return int(get_total_cores())


def get_effective_cores() -> Any:
  """
  Return ``min(ini total_cores, os.cpu_count())`` for pool / worker sizing.
  
  ``ini`` caps parallelism when the host has more CPUs; ``os.cpu_count()`` wins
  when ini overshoots hardware or inside a limited cgroup/cpuset.
  
  Returns:
    Any: Open return polymorphism from ``get_effective_cores``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_effective_cores()  # doctest: +SKIP
  """
  host = os.cpu_count()
  if host is None or host < 1:
    host = 1
  ini_budget = get_ini_total_cores_int()
  return min(ini_budget, host)


def get_summary_aggregate_prefetch_max_threads() -> Any:
  """
  Absolute ceiling for nested Summary aggregate prefetch threads.

  INI ``[PORTAL] summary_aggregate_prefetch_max_threads`` (default **2**).

  Returns:
    Any: Positive int thread ceiling.

  Examples:
    >>> get_summary_aggregate_prefetch_max_threads()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      1,
      int(
          _ini_get_from_registry(
              "PORTAL",
              "summary_aggregate_prefetch_max_threads",
              legacy_sections=("DEFAULT",),
          )
      ),
  )


def get_gunicorn_workers() -> Any:
  """
  Absolute Gunicorn worker count from ``[PORTAL] gunicorn_workers``.

  Default **32**. ``WEB_CONCURRENCY`` may still override in
  ``django_startup.sh``.

  Returns:
    Any: Positive int worker count.

  Examples:
    >>> get_gunicorn_workers()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      1,
      int(
          _ini_get_from_registry(
              "PORTAL",
              "gunicorn_workers",
              legacy_sections=("DEFAULT",),
          )
      ),
  )


def get_metrics_pool_processes() -> Any:
  """
  Absolute metrics (+ prewarm) process pool size.

  INI ``[PIPELINE] metrics_pool_processes`` (default **24**).

  Returns:
    Any: Positive int process count.

  Examples:
    >>> get_metrics_pool_processes()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("metrics_pool_processes"))


def get_metrics_pool_maxtasksperchild() -> Any:
  """
  Recycle metrics-pool workers after N tasks; 0 means unlimited.

  INI ``[PIPELINE] metrics_pool_maxtasksperchild`` (default **16**). Caps
  per-worker RSS growth without relying on cgroup OOM. Pass to both
  metrics ``Pool(...)`` sites and into ``pool_health_context`` so recycle
  exits are not misread as attrition.

  Returns:
    Any: Non-negative int task count (0 = no maxtasksperchild).

  Examples:
    >>> get_metrics_pool_maxtasksperchild()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0, _pipeline_getint("metrics_pool_maxtasksperchild"))


def get_metrics_pool_process_count() -> Any:
  """
  Absolute metrics pool size (same as ``get_metrics_pool_processes``).

  Returns:
    Any: Positive int process count.

  Examples:
    >>> get_metrics_pool_process_count()  # doctest: +SKIP
  """
  return get_metrics_pool_processes()

def get_cpuset_pin_min_total_cores() -> Any:
  """
  Return the cpuset pin min total cores.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_cpuset_pin_min_total_cores()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return int(_ini_get_from_registry("DEFAULT", "cpuset_pin_min_total_cores"))


def get_cpuset_pin_min_cores_per_node() -> Any:
  """
  Return the cpuset pin min cores per node.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_cpuset_pin_min_cores_per_node()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return int(_ini_get_from_registry("DEFAULT", "cpuset_pin_min_cores_per_node"))


def get_web_numa_node() -> Any:
  """
  Optional explicit sysfs node id for web+proxy; None if unset.
  
  Returns:
    Any: Open return polymorphism from ``get_web_numa_node``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_web_numa_node()  # doctest: +SKIP
  """
  return _optional_default_int_option("web_numa_node")


def get_pipeline_numa_node() -> Any:
  """
  Optional explicit sysfs node id for pipeline; None if unset.
  
  Returns:
    Any: Open return polymorphism from ``get_pipeline_numa_node``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_pipeline_numa_node()  # doctest: +SKIP
  """
  return _optional_default_int_option("pipeline_numa_node")


def get_pin_proxy_in_compose() -> Any:
  """
  If True, NUMA pinning script also sets ``cpuset`` on ``proxy`` (match web.
  
    node).
  
  Returns:
    Any: Open return polymorphism from ``get_pin_proxy_in_compose``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_pin_proxy_in_compose()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _ini_get_from_registry("DEFAULT", "pin_proxy_in_compose"),
  )


def get_numa_pin_max_nodes_auto() -> Any:
  """
  Auto compose pinning supports up to this many NUMA nodes without explicit ids.
  
  Returns:
    Any: Open return polymorphism from ``get_numa_pin_max_nodes_auto``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_numa_pin_max_nodes_auto()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return int(_ini_get_from_registry("DEFAULT", "numa_pin_max_nodes_auto"))


def get_parallel_db_prefetch_max() -> Any:
  """
  Max threads for parallel ORM prefetch (summary plots) and default API.
  
    executor.
  
    size.
  
  Default **4** (INI ``parallel_db_prefetch_max``); summary aggregate prefetch
    also applies a
  hard cap in ``summaryplot`` so nested pools do not multiply against the API
    executor.
  
  Override with ``[PORTAL] parallel_db_prefetch_max`` or env
    ``PARALLEL_DB_PREFETCH_MAX``.
  
  Returns:
    Any: Open return polymorphism from ``get_parallel_db_prefetch_max``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_parallel_db_prefetch_max()  # doctest: +SKIP
  """
  env = os.environ.get("PARALLEL_DB_PREFETCH_MAX", "").strip()
  if env:
    return max(1, int(env))
  _ensure_cfg_loaded()
  return max(1, int(_ini_get_from_registry(
      "PORTAL",
      "parallel_db_prefetch_max",
      legacy_sections=("DEFAULT",),
  )))


def get_api_small_executor_max_workers() -> Any:
  """
  Max workers for shared ``ThreadPoolExecutor`` in ``site.machine.api``.
  
  If ``[PORTAL] api_small_executor_max_workers`` is set, it wins; otherwise
  ``get_parallel_db_prefetch_max()`` (default **4**).
  
  Returns:
    Any: Open return polymorphism from ``get_api_small_executor_max_workers``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_api_small_executor_max_workers()  # doctest: +SKIP
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
  return get_parallel_db_prefetch_max()


def get_db_conn_max_age() -> Any:
  """
  Django ``CONN_MAX_AGE`` in seconds (default **90**).
  
  Env ``DJANGO_CONN_MAX_AGE`` overrides ``[PORTAL] db_conn_max_age``.
  
  Returns:
    Any: Open return polymorphism from ``get_db_conn_max_age``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_db_conn_max_age()  # doctest: +SKIP
  """
  return _env_or_cfg_int(
      "DJANGO_CONN_MAX_AGE", "PORTAL", "db_conn_max_age",
      legacy_sections=("DEFAULT",),
  )


def get_db_statement_timeout_ms() -> Any:
  """
  ``statement_timeout`` in milliseconds for PostgreSQL session options.
  
  ``0`` means do not set (omit from Django ``OPTIONS``). Default **120000** (2
    minutes).
  Env ``DJANGO_DB_STATEMENT_TIMEOUT_MS`` overrides ``[PORTAL]
    db_statement_timeout_ms``.
  
  Returns:
    Any: Open return polymorphism from ``get_db_statement_timeout_ms``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_db_statement_timeout_ms()  # doctest: +SKIP
  """
  return _env_or_cfg_int(
      "DJANGO_DB_STATEMENT_TIMEOUT_MS",
      "PORTAL",
      "db_statement_timeout_ms",
      legacy_sections=("DEFAULT",),
  )


def get_db_idle_in_transaction_session_timeout_ms() -> Any:
  """
  ``idle_in_transaction_session_timeout`` in ms; ``0`` = omit. Default.
  
    **300000** (5 min).
  
  Returns:
    Any: Open return polymorphism from
    ``get_db_idle_in_transaction_session_timeout_ms``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_db_idle_in_transaction_session_timeout_ms()  # doctest: +SKIP
  """
  return _env_or_cfg_int(
      "DJANGO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS",
      "PORTAL",
      "db_idle_in_transaction_session_timeout_ms",
      legacy_sections=("DEFAULT",),
  )


def build_postgres_connection_options() -> Any:
  """
  Return Django ``DATABASES`` ``OPTIONS`` for libpq ``-c`` settings, or ``{}``.
  
  Returns:
    Any: Open return polymorphism from ``build_postgres_connection_options``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> build_postgres_connection_options()  # doctest: +SKIP
  """
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


def get_worker_process_count(divisor: int = 4) -> Any:
  """
  Return worker process count as ``effective_cores / divisor``, clamped to at.
  
    least 1.
  
  Args:
    divisor (int): Integer value for divisor.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_worker_process_count(0)  # doctest: +SKIP
  """
  return max(1, get_effective_cores() // divisor)


def get_sync_ingest_pool_processes() -> Any:
  """
  Absolute ``sync_timedb`` ingest process pool size.

  Also used as the archive discovery / day-raw thread ceiling (replace,
  do not alias a separate discovery key). INI default **16**.

  Returns:
    Any: Positive int process/thread count.

  Examples:
    >>> get_sync_ingest_pool_processes()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_ingest_pool_processes"))

def get_sync_archive_pool_processes() -> Any:
  """
  Archive pool size / concurrent daily-tar append slots (INI default 2).
  
  Sole source of archive append concurrency — not derived from cpuset budget.
  
  Returns:
    Any: Open return polymorphism from ``get_sync_archive_pool_processes``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_pool_processes()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_archive_pool_processes"))


def get_sync_timedb_archive_max_concurrent_sealed_days() -> Any:
  """
  Max concurrent sealed-day stream workers in ``sync_timedb_archive`` (default.
  
    1).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_timedb_archive_max_concurrent_sealed_days``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_timedb_archive_max_concurrent_sealed_days()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _pipeline_getint("sync_timedb_archive_max_concurrent_sealed_days")
  pool_cap = max(1, int(get_sync_archive_pool_processes()))
  return max(1, min(pool_cap, raw))


def get_metrics_scheduler_mode() -> Any:
  """
  Metrics scheduler mode: strict_date, global_fifo, or global_priority.
  
  Returns:
    Any: Open return polymorphism from ``get_metrics_scheduler_mode``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_metrics_scheduler_mode()  # doctest: +SKIP
  """
  env = os.environ.get("HPCPERFSTATS_METRICS_SCHEDULER_MODE", "").strip().lower()
  if env in ("strict_date", "global_fifo", "global_priority"):
    return env
  _ensure_cfg_loaded()
  mode = _pipeline_get("metrics_scheduler_mode").strip().lower()
  if mode in ("strict_date", "global_fifo", "global_priority"):
    return mode
  return "global_priority"


def get_metrics_scheduler_prefetch_chunks() -> Any:
  """
  Max chunk descriptors prefetched ahead for global scheduler.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_scheduler_prefetch_chunks``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_scheduler_prefetch_chunks()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("metrics_scheduler_prefetch_chunks"))


def get_metrics_scheduler_ready_queue_target() -> Any:
  """
  Target ready-jid queue depth before compute dispatch.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_scheduler_ready_queue_target``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_scheduler_ready_queue_target()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("metrics_scheduler_ready_queue_target"))


def get_metrics_idle_slot_supplement_enabled() -> bool:
  """
  Whether update_metrics fills idle pool slots from the ready queue.

  Returns:
    bool: True when idle-slot sample-count supplement is enabled.

  Examples:
    >>> get_metrics_idle_slot_supplement_enabled()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _pipeline_get(
      "metrics_idle_slot_supplement_enabled",
  ).strip().lower() in ("1", "true", "yes", "on")


def get_metrics_supplement_sample_soft_max() -> int:
  """
  Prefer supplement jobs with estimated_sample_count below this soft max.

  Returns:
    int: Soft sample ceiling (exclusive preference band).

  Examples:
    >>> get_metrics_supplement_sample_soft_max()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("metrics_supplement_sample_soft_max"))


def get_metrics_supplement_sample_hard_max() -> int:
  """
  Never supplement jobs with estimated_sample_count at or above this hard max.

  Returns:
    int: Hard sample ceiling (inclusive reject).

  Examples:
    >>> get_metrics_supplement_sample_hard_max()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  soft = get_metrics_supplement_sample_soft_max()
  return max(soft, _pipeline_getint("metrics_supplement_sample_hard_max"))


def get_metrics_plot_prewarm_mode() -> Any:
  """
  Prewarm mode for metrics pipeline: inline or pipeline_required.
  
  Returns:
    Any: Open return polymorphism from ``get_metrics_plot_prewarm_mode``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_metrics_plot_prewarm_mode()  # doctest: +SKIP
  """
  env = os.environ.get("HPCPERFSTATS_METRICS_PLOT_PREWARM_MODE", "").strip().lower()
  if env in ("inline", "pipeline_required"):
    return env
  _ensure_cfg_loaded()
  mode = _pipeline_get("metrics_plot_prewarm_mode").strip().lower()
  if mode in ("inline", "pipeline_required"):
    return mode
  return "pipeline_required"


def get_metrics_per_jid_phase_diagnostics_enabled() -> Any:
  """
  Env-only: emit per-batch jid phase lines from the metrics scheduler.
  
  Set ``HPCPERFSTATS_METRICS_PER_JID_PHASE_LOG`` to 1/true/yes/on. Default off.
  Intended for short compose-backed tuning runs (high log volume).
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_per_jid_phase_diagnostics_enabled``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_per_jid_phase_diagnostics_enabled()  # doctest: +SKIP
  """
  v = os.environ.get("HPCPERFSTATS_METRICS_PER_JID_PHASE_LOG", "").strip().lower()
  return v in ("1", "true", "yes", "on")


def get_metrics_compute_batch_max_window_s() -> Any:
  """
  Max sum of job accounting-window seconds per compute batch (0 = disabled).
  
  Heterogeneity guard: avoids packing many multi-day jobs into one batch.
  Env ``HPCPERFSTATS_METRICS_COMPUTE_BATCH_MAX_WINDOW_S`` overrides INI
  ``metrics_compute_batch_max_window_s``.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_compute_batch_max_window_s``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_compute_batch_max_window_s()  # doctest: +SKIP
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
        _pipeline_getfloat("metrics_compute_batch_max_window_s"),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.0


def get_metrics_compute_batch_max_single_job_s() -> Any:
  """
  Max seconds for one non-artifact-only job in a batch (0 = disabled).
  
  When set, a job whose window exceeds this is still scheduled alone if it would
  otherwise block the batch (first slot rule in packer).
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_compute_batch_max_single_job_s``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_compute_batch_max_single_job_s()  # doctest: +SKIP
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
        _pipeline_getfloat("metrics_compute_batch_max_single_job_s"),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.0


def get_metrics_compute_batch_unknown_runtime_s() -> Any:
  """
  Accounting window seconds assumed when start/end unavailable on a candidate.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_compute_batch_unknown_runtime_s``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_compute_batch_unknown_runtime_s()  # doctest: +SKIP
  """
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
        _pipeline_getfloat("metrics_compute_batch_unknown_runtime_s"),
    )
  except (TypeError, ValueError, OverflowError):
    return 172800.0


def get_metrics_compute_watchdog_s() -> Any:
  """
  Watchdog on metrics phase wall time inside a scheduler batch (seconds).
  
  Returns:
    Any: Open return polymorphism from ``get_metrics_compute_watchdog_s``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_metrics_compute_watchdog_s()  # doctest: +SKIP
  """
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
        _pipeline_getfloat("metrics_compute_watchdog_s"),
    )
  except (TypeError, ValueError, OverflowError):
    return 120.0


def get_metrics_compute_total_watchdog_s() -> Any:
  """
  Watchdog on full batch wall (metrics + prewarm submit/drain). 0 = use.
  
    metrics-.
  
    only.
  
  When 0, only ``get_metrics_compute_watchdog_s`` applies to the metrics
  slice; total batch time is logged but does not downshift batch cap.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_compute_total_watchdog_s``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_compute_total_watchdog_s()  # doctest: +SKIP
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
        _pipeline_getfloat("metrics_compute_total_watchdog_s"),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.0


def get_metrics_deferred_not_ready_retry_s() -> Any:
  """
  Return the metrics deferred not ready retry s.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_metrics_deferred_not_ready_retry_s()  # doctest: +SKIP
  """
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
        _pipeline_getfloat("metrics_deferred_not_ready_retry_s"),
    )
  except (TypeError, ValueError, OverflowError):
    return 10.0


def get_metrics_deferred_not_ready_max_retries() -> Any:
  """
  Return the metrics deferred not ready max retries.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_metrics_deferred_not_ready_max_retries()  # doctest: +SKIP
  """
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
        _pipeline_getint("metrics_deferred_not_ready_max_retries"),
    )
  except (TypeError, ValueError, OverflowError):
    return 30


def get_metrics_deferred_not_ready_max_age_s() -> Any:
  """
  Return the metrics deferred not ready max age s.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_metrics_deferred_not_ready_max_age_s()  # doctest: +SKIP
  """
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
        _pipeline_getfloat("metrics_deferred_not_ready_max_age_s"),
    )
  except (TypeError, ValueError, OverflowError):
    return 900.0


def get_metrics_deferred_not_ready_quarantine_s() -> Any:
  """
  Return the metrics deferred not ready quarantine s.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_metrics_deferred_not_ready_quarantine_s()  # doctest: +SKIP
  """
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
        _pipeline_getfloat("metrics_deferred_not_ready_quarantine_s"),
    )
  except (TypeError, ValueError, OverflowError):
    return 300.0


def get_metrics_readiness_require_window_coverage() -> Any:
  """
  When True, defer metrics until in-window host_data covers start and end.
  
    margins.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_readiness_require_window_coverage``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_readiness_require_window_coverage()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("metrics_readiness_require_window_coverage"),
  )


def get_metrics_readiness_start_margin_seconds() -> Any:
  """
  Seconds after job start_time; first in-window sample must be at or before.
  
    this.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_readiness_start_margin_seconds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_readiness_start_margin_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        float(
            _pipeline_get("metrics_readiness_start_margin_seconds"),
        ),
    )
  except (TypeError, ValueError, OverflowError):
    return 600.0


def get_metrics_readiness_end_margin_seconds() -> Any:
  """
  Seconds before job end_time; last in-window sample must be at or after this.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_readiness_end_margin_seconds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_readiness_end_margin_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        float(
            _pipeline_get("metrics_readiness_end_margin_seconds"),
        ),
    )
  except (TypeError, ValueError, OverflowError):
    return 600.0


def get_metrics_run_poll_timeout_s() -> Any:
  """
  Seconds for one ``imap_unordered`` poll in ``Metrics.run`` (host-side stall.
  
    detection).
  
  Returns:
    Any: Open return polymorphism from ``get_metrics_run_poll_timeout_s``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_metrics_run_poll_timeout_s()  # doctest: +SKIP
  """
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
        float(_pipeline_get("metrics_run_poll_timeout_s")),
    )
  except (TypeError, ValueError, OverflowError):
    return 5.0


def get_sync_archive_validation_max_workers() -> Any:
  """
  Max parallel threads for archive sealed/tar validation (read-lock scope).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_validation_max_workers``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_validation_max_workers()  # doctest: +SKIP
  """
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
        int(_pipeline_get("sync_archive_validation_max_workers")),
    )
  except (TypeError, ValueError, OverflowError):
    return 2


def get_sync_pool_stall_abort_after_timeouts() -> Any:
  """
  Maximum consecutive pool poll timeouts before aborting imap wait (ceiling).
  
  Per-batch abort counts are computed from the largest resolved per-file ingest
  budget in the current imap sub-batch and clamped to this ceiling.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_pool_stall_abort_after_timeouts``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_stall_abort_after_timeouts()  # doctest: +SKIP
  """
  env = os.environ.get("HPCPERFSTATS_SYNC_POOL_STALL_ABORT_AFTER_TIMEOUTS", "").strip()
  if env:
    try:
      return max(1, int(env))
    except (TypeError, ValueError, OverflowError):
      return 17320
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        int(_pipeline_get("sync_pool_stall_abort_after_timeouts")),
    )
  except (TypeError, ValueError, OverflowError):
    return 17320


def get_sync_pool_worker_recycle_grace_polls() -> Any:
  """
  Polls to tolerate dead workers with exitcode 0 during maxtasksperchild.
  
    recycle.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_pool_worker_recycle_grace_polls``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_worker_recycle_grace_polls()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(
        0,
        int(_pipeline_get("sync_pool_worker_recycle_grace_polls")),
    )
  except (TypeError, ValueError, OverflowError):
    return 2


def get_sync_pool_worker_recycle_grace_seconds() -> Any:
  """
  Wall-clock seconds before WARN on slow maxtasksperchild replacement per dead.
  
    PID.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_pool_worker_recycle_grace_seconds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_worker_recycle_grace_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        float(_pipeline_get("sync_pool_worker_recycle_grace_seconds")),
    )
  except (TypeError, ValueError, OverflowError):
    poll_grace = get_sync_pool_worker_recycle_grace_polls()
    poll_timeout = get_sync_pool_poll_timeout_s()
    return max(30.0, float(poll_grace) * float(poll_timeout))


def get_sync_pool_idle_reconcile_max_rounds() -> Any:
  """
  Redispatch rounds before idle-pool ghost fail-fast (exit 124 last resort).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_pool_idle_reconcile_max_rounds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_idle_reconcile_max_rounds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(
        0,
        int(_pipeline_get("sync_pool_idle_reconcile_max_rounds")),
    )
  except (TypeError, ValueError, OverflowError):
    return 3


def get_sync_pool_idle_reconcile_polls_per_round() -> Any:
  """
  Idle polls between orphan-async reconcile redispatch rounds.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_pool_idle_reconcile_polls_per_round``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_idle_reconcile_polls_per_round()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        int(_pipeline_get("sync_pool_idle_reconcile_polls_per_round")),
    )
  except (TypeError, ValueError, OverflowError):
    return 4


def get_sync_pool_poll_timeout_s() -> Any:
  """
  Poll interval for sync_timedb pool waits (worker-death / OOM detection).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_pool_poll_timeout_s``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_poll_timeout_s()  # doctest: +SKIP
  """
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
        float(_pipeline_get("sync_pool_poll_timeout_s")),
    )
  except (TypeError, ValueError, OverflowError):
    return 5.0


def get_sync_pool_stall_defer_log_interval_s() -> Any:
  """
  Minimum wall seconds between repeated pool imap stall defer WARN lines (0 =.
  
    every poll).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_pool_stall_defer_log_interval_s``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_stall_defer_log_interval_s()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        float(_pipeline_get("sync_pool_stall_defer_log_interval_s")),
    )
  except (TypeError, ValueError, OverflowError):
    return 60.0


def get_sync_archive_members_cache_enabled() -> Any:
  """
  Whether ingest/archive workers cache daily tar member maps per archive.
  
    identity.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_cache_enabled``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_members_cache_enabled()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_archive_members_cache_enabled"),
  )


def get_sync_archive_members_cache_max_entries() -> Any:
  """
  Max cached daily archive member maps per worker process.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_cache_max_entries``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_members_cache_max_entries()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        int(_pipeline_get("sync_archive_members_cache_max_entries")),
    )
  except (TypeError, ValueError, OverflowError):
    return 64


def get_sync_archive_members_redis_enabled() -> Any:
  """
  Whether cross-worker Redis L2 backs daily archive member maps.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_redis_enabled``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_members_redis_enabled()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_archive_members_redis_enabled"),
  )


def get_sync_archive_members_redis_ttl_seconds() -> Any:
  """
  TTL for Redis archive member HASH / complete keys.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_redis_ttl_seconds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_members_redis_ttl_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(60, int(_pipeline_get("sync_archive_members_redis_ttl_seconds")))
  except (TypeError, ValueError, OverflowError):
    return 86400


def get_sync_archive_members_redis_populate_lock_seconds() -> Any:
  """
  Populate lock lease (renewed during scan).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_redis_populate_lock_seconds``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_archive_members_redis_populate_lock_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(30, int(_pipeline_get("sync_archive_members_redis_populate_lock_seconds")))
  except (TypeError, ValueError, OverflowError):
    return 3600


def get_sync_archive_members_redis_populate_stall_seconds() -> Any:
  """
  Waiter abort when populate shows no lock renewal or HASH growth.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_redis_populate_stall_seconds``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_archive_members_redis_populate_stall_seconds()
  """
  _ensure_cfg_loaded()
  try:
    return max(5, int(_pipeline_get("sync_archive_members_redis_populate_stall_seconds")))
  except (TypeError, ValueError, OverflowError):
    return 120


def get_sync_archive_members_redis_populate_max_seconds() -> Any:
  """
  Absolute cap for populate/waiter loops (0 = disabled).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_redis_populate_max_seconds``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_archive_members_redis_populate_max_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(0, int(_pipeline_get("sync_archive_members_redis_populate_max_seconds")))
  except (TypeError, ValueError, OverflowError):
    return 7200


def get_sync_daily_tar_restore_lease_seconds() -> Any:
  """
  Exclusive sealed→tar restore lease TTL (renewed while decompressing).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_daily_tar_restore_lease_seconds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_daily_tar_restore_lease_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(60, int(_pipeline_get("sync_daily_tar_restore_lease_seconds")))
  except (TypeError, ValueError, OverflowError):
    return 14400


def get_sync_archive_members_fnctl_read_lock_timeout_seconds() -> Any:
  """
  Shared read-lock wait for archive populate/verify paths (fnctl sidecar).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_fnctl_read_lock_timeout_seconds``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_members_fnctl_read_lock_timeout_seconds()
  """
  _ensure_cfg_loaded()
  try:
    return max(60, min(3600, int(
        _pipeline_get("sync_archive_members_fnctl_read_lock_timeout_seconds"),
    )))
  except (TypeError, ValueError, OverflowError):
    return 180


def get_sync_archive_members_redis_wait_poll_seconds() -> Any:
  """
  Waiter poll interval for incremental member lookups.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_redis_wait_poll_seconds``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_archive_members_redis_wait_poll_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(
        0.05,
        float(_pipeline_get("sync_archive_members_redis_wait_poll_seconds")),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.25


def get_sync_archive_members_redis_hset_batch_size() -> Any:
  """
  Pipeline batch size for incremental Redis HASH writes during populate.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_redis_hset_batch_size``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_members_redis_hset_batch_size()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(1, int(_pipeline_get("sync_archive_members_redis_hset_batch_size")))
  except (TypeError, ValueError, OverflowError):
    return 500


def get_sync_archive_members_redis_max_payload_bytes() -> Any:
  """
  Refuse populate when estimated Redis HASH payload exceeds this size.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_redis_max_payload_bytes``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_archive_members_redis_max_payload_bytes()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  try:
    return max(
        65536,
        int(_pipeline_get("sync_archive_members_redis_max_payload_bytes")),
    )
  except (TypeError, ValueError, OverflowError):
    return 8388608


def get_sync_archive_members_populate_pool_processes() -> Any:
  """
  Dedicated populate-pool workers for Redis L2 sealed/tar member streaming.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_members_populate_pool_processes``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_archive_members_populate_pool_processes()  # doctest: +SKIP
  """
  env = os.environ.get(
      "HPCPERFSTATS_SYNC_ARCHIVE_MEMBERS_POPULATE_POOL_PROCESSES", "",
  ).strip()
  if env:
    try:
      return max(0, int(env))
    except (TypeError, ValueError, OverflowError):
      return 4
  _ensure_cfg_loaded()
  try:
    return max(
        0,
        int(_pipeline_get("sync_archive_members_populate_pool_processes")),
    )
  except (TypeError, ValueError, OverflowError):
    return 4


def get_sync_ingest_per_file_timeout_s() -> Any:
  """
  Wall-clock floor per ingest pool task in seconds (0 = disabled).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_ingest_per_file_timeout_s``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_per_file_timeout_s()  # doctest: +SKIP
  """
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
        float(_pipeline_get("sync_ingest_per_file_timeout_s")),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.0


# 30 GiB × per_mib + historical floor-900 slope reaches max at default slope.
# Floor default (3600) is independent; keep per_mib anchored to (86400−900)/30720.
_SYNC_INGEST_PER_FILE_TIMEOUT_REFERENCE_MIB = 30720  # 30 GiB
_SYNC_INGEST_PER_FILE_TIMEOUT_MAX_S_DEFAULT = 86400.0  # 24h at reference size
_SYNC_INGEST_PER_FILE_TIMEOUT_SLOPE_FLOOR_S = 900.0  # historical slope anchor
_SYNC_INGEST_PER_FILE_TIMEOUT_S_PER_MIB_DEFAULT = (
    (_SYNC_INGEST_PER_FILE_TIMEOUT_MAX_S_DEFAULT - _SYNC_INGEST_PER_FILE_TIMEOUT_SLOPE_FLOOR_S)
    / _SYNC_INGEST_PER_FILE_TIMEOUT_REFERENCE_MIB
)
# 2 GiB ingest budget at default slope (giant pool supplement trigger).
_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_TRIGGER_BUDGET_S_DEFAULT = (
    _SYNC_INGEST_PER_FILE_TIMEOUT_SLOPE_FLOOR_S
    + 2048.0 * _SYNC_INGEST_PER_FILE_TIMEOUT_S_PER_MIB_DEFAULT
)
_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_MAX_BYTES_DEFAULT = 1073741824  # 1 GiB
_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_LARGE_MAX_BYTES_DEFAULT = 8589934592  # 8 GiB
_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_QUEUE_MULTIPLIER_DEFAULT = 2


def get_sync_ingest_per_file_timeout_max_s() -> Any:
  """
  Ceiling for size-proportional per-file ingest timeout (0 = no ceiling).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_per_file_timeout_max_s``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_per_file_timeout_max_s()  # doctest: +SKIP
  """
  env = os.environ.get(
      "HPCPERFSTATS_SYNC_INGEST_PER_FILE_TIMEOUT_MAX_S", "",
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
        float(_pipeline_get("sync_ingest_per_file_timeout_max_s")),
    )
  except (TypeError, ValueError, OverflowError):
    return _SYNC_INGEST_PER_FILE_TIMEOUT_MAX_S_DEFAULT


def get_sync_ingest_per_file_timeout_s_per_mib() -> Any:
  """
  Added seconds per ceiling MiB for size-proportional ingest timeout.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_per_file_timeout_s_per_mib``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_per_file_timeout_s_per_mib()  # doctest: +SKIP
  """
  env = os.environ.get(
      "HPCPERFSTATS_SYNC_INGEST_PER_FILE_TIMEOUT_S_PER_MIB", "",
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
        float(_pipeline_get("sync_ingest_per_file_timeout_s_per_mib")),
    )
  except (TypeError, ValueError, OverflowError):
    return _SYNC_INGEST_PER_FILE_TIMEOUT_S_PER_MIB_DEFAULT


def get_sync_ingest_giant_pool_supplement_enabled() -> Any:
  """
  Backfill idle ingest pool slots from pending tail while giants run.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_giant_pool_supplement_enabled``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_giant_pool_supplement_enabled()  # doctest: +SKIP
  """
  env = os.environ.get(
      "HPCPERFSTATS_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_ENABLED", "",
  ).strip().lower()
  if env in ("1", "true", "yes", "on"):
    return True
  if env in ("0", "false", "no", "off"):
    return False
  _ensure_cfg_loaded()
  try:
    return _pipeline_get("sync_ingest_giant_pool_supplement_enabled").strip().lower() in ("1", "true", "yes", "on")
  except (TypeError, ValueError, OverflowError):
    return True


def get_sync_ingest_idle_slot_supplement_enabled() -> Any:
  """
  Allow giant-pool supplement when idle slots exist without a giant budget.
  
  When enabled (default), ``supplement_paths_fn`` may dispatch from the pending
  tail whenever the primary chunk iterator is exhausted and workers are idle,
  even if no in-flight path meets the giant trigger budget (RC-3).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_idle_slot_supplement_enabled``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_idle_slot_supplement_enabled()  # doctest: +SKIP
  """
  env = os.environ.get(
      "HPCPERFSTATS_SYNC_INGEST_IDLE_SLOT_SUPPLEMENT_ENABLED", "",
  ).strip().lower()
  if env in ("1", "true", "yes", "on"):
    return True
  if env in ("0", "false", "no", "off"):
    return False
  _ensure_cfg_loaded()
  try:
    return _pipeline_get(
        "sync_ingest_idle_slot_supplement_enabled",
    ).strip().lower() in ("1", "true", "yes", "on")
  except (TypeError, ValueError, OverflowError):
    return True


def get_sync_ingest_giant_pool_supplement_max_bytes() -> Any:
  """
  Soft max (bytes): prefer supplement paths strictly under this size (default 1.
  
    GiB).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_giant_pool_supplement_max_bytes``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_giant_pool_supplement_max_bytes()  # doctest: +SKIP
  """
  env = os.environ.get(
      "HPCPERFSTATS_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_MAX_BYTES", "",
  ).strip()
  if env:
    try:
      return max(1, int(env))
    except (TypeError, ValueError, OverflowError):
      return _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_MAX_BYTES_DEFAULT
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        int(_pipeline_get("sync_ingest_giant_pool_supplement_max_bytes")),
    )
  except (TypeError, ValueError, OverflowError):
    return _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_MAX_BYTES_DEFAULT


def get_sync_ingest_giant_pool_supplement_large_max_bytes() -> Any:
  """
  Hard max (bytes) for second-pass supplement ([soft, large); default 8 GiB).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_giant_pool_supplement_large_max_bytes``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_ingest_giant_pool_supplement_large_max_bytes()
  """
  env = os.environ.get(
      "HPCPERFSTATS_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_LARGE_MAX_BYTES", "",
  ).strip()
  if env:
    try:
      return max(1, int(env))
    except (TypeError, ValueError, OverflowError):
      return _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_LARGE_MAX_BYTES_DEFAULT
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        int(_pipeline_get("sync_ingest_giant_pool_supplement_large_max_bytes")),
    )
  except (TypeError, ValueError, OverflowError):
    return _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_LARGE_MAX_BYTES_DEFAULT


def get_sync_ingest_giant_pool_supplement_queue_multiplier() -> Any:
  """
  Multiplier: supplement_queue = ingest_queue_max * this (default 2 → 6000).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_giant_pool_supplement_queue_multiplier``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_ingest_giant_pool_supplement_queue_multiplier()
  """
  env = os.environ.get(
      "HPCPERFSTATS_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_QUEUE_MULTIPLIER", "",
  ).strip()
  if env:
    try:
      return max(1, int(env))
    except (TypeError, ValueError, OverflowError):
      return _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_QUEUE_MULTIPLIER_DEFAULT
  _ensure_cfg_loaded()
  try:
    return max(
        1,
        int(_pipeline_get("sync_ingest_giant_pool_supplement_queue_multiplier")),
    )
  except (TypeError, ValueError, OverflowError):
    return _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_QUEUE_MULTIPLIER_DEFAULT


def get_sync_ingest_giant_pool_supplement_queue_size() -> Any:
  """
  Ceiling for giant-supplement pending_tail reservoir (startup + mid-imap.
  
    refresh).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_giant_pool_supplement_queue_size``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_ingest_giant_pool_supplement_queue_size()  # doctest: +SKIP
  """
  return max(
      1,
      int(get_sync_ingest_queue_max_size())
      * int(get_sync_ingest_giant_pool_supplement_queue_multiplier()),
  )


def get_sync_ingest_giant_pool_supplement_trigger_budget_s() -> Any:
  """
  Min resolved per-file ingest budget (s) for an in-flight path to count as.
  
    giant.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_giant_pool_supplement_trigger_budget_s``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_ingest_giant_pool_supplement_trigger_budget_s()
  """
  env = os.environ.get(
      "HPCPERFSTATS_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_TRIGGER_BUDGET_S", "",
  ).strip()
  if env:
    try:
      return max(0.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_TRIGGER_BUDGET_S_DEFAULT
  _ensure_cfg_loaded()
  try:
    return max(
        0.0,
        float(_pipeline_get("sync_ingest_giant_pool_supplement_trigger_budget_s")),
    )
  except (TypeError, ValueError, OverflowError):
    return _SYNC_INGEST_GIANT_POOL_SUPPLEMENT_TRIGGER_BUDGET_S_DEFAULT


def get_metrics_run_stall_timeout_s() -> Any:
  """
  Max no-progress seconds allowed in ``Metrics.run`` before aborting batch.
  
  Returns:
    Any: Open return polymorphism from ``get_metrics_run_stall_timeout_s``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_metrics_run_stall_timeout_s()  # doctest: +SKIP
  """
  env = os.environ.get("HPCPERFSTATS_METRICS_RUN_STALL_TIMEOUT_S", "").strip()
  if env:
    try:
      return max(5.0, float(env))
    except (TypeError, ValueError, OverflowError):
      return 900.0
  _ensure_cfg_loaded()
  try:
    return max(
        5.0,
        float(_pipeline_get("metrics_run_stall_timeout_s")),
    )
  except (TypeError, ValueError, OverflowError):
    return 900.0


def get_metrics_run_per_job_timeout_s() -> Any:
  """
  Wall-clock cap for one ``compute_metrics`` call in a pool worker (0 → use.
  
    stall timeout).
  
  Env ``HPCPERFSTATS_METRICS_RUN_PER_JOB_TIMEOUT_S`` overrides INI
  ``metrics_run_per_job_timeout_s``.
  
  Returns:
    Any: Open return polymorphism from ``get_metrics_run_per_job_timeout_s``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_metrics_run_per_job_timeout_s()  # doctest: +SKIP
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
        float(_pipeline_get("metrics_run_per_job_timeout_s")),
    )
  except (TypeError, ValueError, OverflowError):
    return 0.0


def get_metrics_worker_statement_timeout_ms() -> Any:
  """
  PostgreSQL ``statement_timeout`` ms for metrics pool compute.
  
  Default ``120000`` so ``_host_data_metric_rows_with_host_chunk_retry`` can
  split large ``host__in`` queries before the per-job SIGALRM. ``0`` disables
  the session timeout for the compute window (SIGALRM remains the wall clock).
  Env ``HPCPERFSTATS_METRICS_WORKER_STATEMENT_TIMEOUT_MS`` overrides
  ``[PIPELINE] metrics_worker_statement_timeout_ms``.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_worker_statement_timeout_ms``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_worker_statement_timeout_ms()  # doctest: +SKIP
  """
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_WORKER_STATEMENT_TIMEOUT_MS", ""
  ).strip()
  if env:
    try:
      return max(0, int(env))
    except (TypeError, ValueError, OverflowError):
      return 120000
  _ensure_cfg_loaded()
  try:
    return max(0, int(_pipeline_get("metrics_worker_statement_timeout_ms")))
  except (TypeError, ValueError, OverflowError):
    return 120000


def get_metrics_persist_statement_timeout_ms() -> Any:
  """
  Local PostgreSQL ``statement_timeout`` for parent metrics persistence.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_persist_statement_timeout_ms``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_persist_statement_timeout_ms()  # doctest: +SKIP
  """
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
        int(_pipeline_get("metrics_persist_statement_timeout_ms")),
    )
  except (TypeError, ValueError, OverflowError):
    return 120000


def get_metrics_persist_lock_timeout_ms() -> Any:
  """
  Local PostgreSQL ``lock_timeout`` for parent metrics persistence.
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_persist_lock_timeout_ms``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_persist_lock_timeout_ms()  # doctest: +SKIP
  """
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
        int(_pipeline_get("metrics_persist_lock_timeout_ms")),
    )
  except (TypeError, ValueError, OverflowError):
    return 10000


def get_metrics_plot_aggregate_time_slice_s() -> Any:
  """
  Wall-clock seconds per plot-aggregate SQL time chunk (default 3600).

  Design capacity ``5000×48×60`` host-samples uses one-hour slices at 1-min
  cadence so each statement_timeout covers a bounded host×time chunk.
  Env ``HPCPERFSTATS_METRICS_PLOT_AGGREGATE_TIME_SLICE_S`` overrides INI.

  Returns:
    Any: Positive integer seconds (minimum 60).

  Examples:
    >>> get_metrics_plot_aggregate_time_slice_s()  # doctest: +SKIP
  """
  env = os.environ.get(
      "HPCPERFSTATS_METRICS_PLOT_AGGREGATE_TIME_SLICE_S", ""
  ).strip()
  if env:
    try:
      return max(60, int(env))
    except (TypeError, ValueError, OverflowError):
      return 3600
  _ensure_cfg_loaded()
  try:
    return max(60, _pipeline_getint("metrics_plot_aggregate_time_slice_s"))
  except (TypeError, ValueError, OverflowError):
    return 3600


def get_plot_aggregate_max_host_time_points() -> Any:
  """
  Max host×time rows one plot aggregate DataFrame may materialise.

  Caps memory for design capacity ``5000×48×60`` (14.4M samples): adaptive
  large-job time buckets use ``floor(budget / n_hosts)``. Default 1_000_000.
  Env ``HPCPERFSTATS_PLOT_AGGREGATE_MAX_HOST_TIME_POINTS`` overrides INI.

  Returns:
    Any: Positive integer row budget (minimum 1000).

  Examples:
    >>> get_plot_aggregate_max_host_time_points()  # doctest: +SKIP
  """
  env = os.environ.get(
      "HPCPERFSTATS_PLOT_AGGREGATE_MAX_HOST_TIME_POINTS", ""
  ).strip()
  if env:
    try:
      return max(1000, int(env))
    except (TypeError, ValueError, OverflowError):
      return 1_000_000
  _ensure_cfg_loaded()
  try:
    return max(
        1000,
        _pipeline_getint("metrics_plot_aggregate_max_host_time_points"),
    )
  except (TypeError, ValueError, OverflowError):
    return 1_000_000


def get_metrics_proxy_reject_jid_batch_size() -> Any:
  """
  Max jids per DB round-trip in ``update_metrics`` proxy readiness (PostgreSQL).
  
  Returns:
    Any: Open return polymorphism from
    ``get_metrics_proxy_reject_jid_batch_size``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_metrics_proxy_reject_jid_batch_size()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(8, _pipeline_getint("metrics_proxy_reject_jid_batch_size"))


def get_sync_ingest_queue_max_size() -> Any:
  """
  Bound for in-memory ingest work queue (default 3000; no-supplement process.
  
    queue).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_ingest_queue_max_size``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_queue_max_size()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_ingest_queue_max_size"))


def get_sync_ingest_rescan_mtime_days() -> Any:
  """
  Incremental pending rescan find ``-mtime -N`` window in days (default 1).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_ingest_rescan_mtime_days``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_rescan_mtime_days()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_ingest_rescan_mtime_days"))


def get_sync_ingest_rescan_full_every() -> Any:
  """
  Force full-age find every N incremental rescans (default 100).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_ingest_rescan_full_every``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_rescan_full_every()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_ingest_rescan_full_every"))


def get_sync_ingest_current_proximity_days() -> Any:
  """
  Days within which CLI ``backlog`` exits when near a live ``current``.
  
    heartbeat.
  
    (default 2).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_current_proximity_days``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_current_proximity_days()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0, _pipeline_getint("sync_ingest_current_proximity_days"))


def get_sync_archive_queue_max_size() -> Any:
  """
  Bound for in-memory archive work queue (default 1000).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_archive_queue_max_size``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_queue_max_size()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_archive_queue_max_size"))


def get_sync_archive_retry_max_attempts() -> Any:
  """
  Maximum archive retries before dead-letter behavior (default 5).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_retry_max_attempts``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_retry_max_attempts()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_archive_retry_max_attempts"))


def get_sync_archive_retry_backoff_base_seconds() -> Any:
  """
  Base archive retry backoff in seconds (default 1).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_retry_backoff_base_seconds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_retry_backoff_base_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0.0, float(_pipeline_get("sync_archive_retry_backoff_base_seconds")))


def get_sync_archive_retry_backoff_max_seconds() -> Any:
  """
  Ceiling archive retry backoff in seconds (default 60).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_retry_backoff_max_seconds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_retry_backoff_max_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0.0, float(_pipeline_get("sync_archive_retry_backoff_max_seconds")))


def get_sync_checkpoint_flush_batch_size() -> Any:
  """
  Number of processed-file state transitions between checkpoint writes (default.
  
    100).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_checkpoint_flush_batch_size``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_checkpoint_flush_batch_size()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_checkpoint_flush_batch_size"))


def get_sync_timedb_tar_append_batch_size() -> Any:
  """
  Max raw paths per ``tar -T`` append batch in sync_timedb (default 1024).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_timedb_tar_append_batch_size``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_timedb_tar_append_batch_size()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_timedb_tar_append_batch_size"))


def get_sync_bulk_create_batch_size() -> Any:
  """
  Rows per host_data/proc_data bulk_create batch and incremental parse flush.
  
    (default 10000).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_bulk_create_batch_size``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_bulk_create_batch_size()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_bulk_create_batch_size"))


def get_sync_host_itimes_cache_max_timestamps_per_entry() -> Any:
  """
  Max distinct DB timestamps cached per host window in sync_timedb (default.
  
    100000).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_host_itimes_cache_max_timestamps_per_entry``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_host_itimes_cache_max_timestamps_per_entry()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      1,
      _pipeline_getint("sync_host_itimes_cache_max_timestamps_per_entry"),
  )


def get_sync_write_lock_shards() -> Any:
  """
  Absolute write-lock shard count for sync_timedb ingest writes.

  INI ``[PIPELINE] sync_write_lock_shards`` (default **8**).

  Returns:
    Any: Positive int shard count.

  Examples:
    >>> get_sync_write_lock_shards()  # doctest: +SKIP
  """
  env = os.environ.get("SYNC_WRITE_LOCK_SHARDS", "").strip()
  if env:
    return max(1, int(env))
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_write_lock_shards"))

def get_sync_ingest_chunk_size() -> Any:
  """
  Stats files processed per ingest chunk — alias of queue max (default 3000).
  
  Not an independent INI key; leftover ``sync_ingest_chunk_size=`` lines are
    ignored.
  
  Returns:
    Any: Open return polymorphism from ``get_sync_ingest_chunk_size``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_chunk_size()  # doctest: +SKIP
  """
  return get_sync_ingest_queue_max_size()


def get_sync_supervisor_rss_limit_mb() -> Any:
  """
  Supervisor RSS limit in MiB; 0 disables fail-fast exit (default 0).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_supervisor_rss_limit_mb``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_supervisor_rss_limit_mb()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0, _pipeline_getint("sync_supervisor_rss_limit_mb"))


def get_sync_supervisor_rss_check_every_n_chunks() -> Any:
  """
  Check supervisor RSS every N processed chunks (default 1).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_supervisor_rss_check_every_n_chunks``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_supervisor_rss_check_every_n_chunks()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_supervisor_rss_check_every_n_chunks"))


def get_sync_process_tree_rss_limit_mb() -> Any:
  """
  Process-tree RSS defer limit in MiB; 0 disables backpressure (default 110000).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_process_tree_rss_limit_mb``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_process_tree_rss_limit_mb()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0, _pipeline_getint("sync_process_tree_rss_limit_mb"))


def get_sync_process_tree_rss_check_every_n_chunks() -> Any:
  """
  Check process-tree RSS every N ingest chunks (default 1).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_process_tree_rss_check_every_n_chunks``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_process_tree_rss_check_every_n_chunks()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_process_tree_rss_check_every_n_chunks"))


def get_sync_process_tree_rss_exit_mb() -> Any:
  """
  Hard exit when process-tree RSS exceeds MiB; 0 disables (default 0).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_process_tree_rss_exit_mb``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_process_tree_rss_exit_mb()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0, _pipeline_getint("sync_process_tree_rss_exit_mb"))


def get_sync_ingest_max_file_read_bytes() -> Any:
  """
  Max stats file size for ``readlines()`` fast path (default 512 MiB).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_max_file_read_bytes``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_max_file_read_bytes()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0, _pipeline_getint("sync_ingest_max_file_read_bytes"))


def get_sync_ingest_stream_duplicate_scan_bytes() -> Any:
  """
  Route duplicate scan through streaming path above this size (default 8 MiB).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_stream_duplicate_scan_bytes``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_stream_duplicate_scan_bytes()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0, _pipeline_getint("sync_ingest_stream_duplicate_scan_bytes"))


def get_sync_ingest_db_complete_tail_window_lines() -> Any:
  """
  Tail timestamp lines to probe before full duplicate scan on large files.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_db_complete_tail_window_lines``: concrete type depends
    on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_db_complete_tail_window_lines()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      1,
      _pipeline_getint("sync_ingest_db_complete_tail_window_lines"),
  )


def get_sync_ingest_pool_maxtasksperchild() -> Any:
  """
  Recycle ingest-pool workers after N tasks; 0 unlimited (default 0).
  
  When 0, supervisor retires on failure/RSS only. Archive and sealed-archive
  spawn pools always use maxtasksperchild=1.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_pool_maxtasksperchild``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_pool_maxtasksperchild()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0, _pipeline_getint("sync_ingest_pool_maxtasksperchild"))


def get_sync_ingest_malloc_trim_after_file() -> Any:
  """
  After each ingest pool task on Linux, gc.collect() and malloc_trim(0).
  
    (default.
  
    yes).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_malloc_trim_after_file``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_malloc_trim_after_file()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_ingest_malloc_trim_after_file"),
  )


def get_sync_ingest_worker_memory_telemetry() -> Any:
  """
  Log one worker_memory batch_summary line per ingest chunk when yes (default.
  
    no).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_worker_memory_telemetry``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_worker_memory_telemetry()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_ingest_worker_memory_telemetry"),
  )


def get_sync_ingest_worker_memory_telemetry_every_n_chunks() -> Any:
  """
  Emit worker_memory batch_summary every N ingest chunks (default 1).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_worker_memory_telemetry_every_n_chunks``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_ingest_worker_memory_telemetry_every_n_chunks()
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("sync_ingest_worker_memory_telemetry_every_n_chunks"))


def get_sync_ingest_recycle_worker_on_failure() -> Any:
  """
  Supervisor-retire ingest workers after failed outcomes (default yes).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_recycle_worker_on_failure``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_recycle_worker_on_failure()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(_pipeline_get("sync_ingest_recycle_worker_on_failure"))


def get_sync_ingest_cooperative_recycle_rss_fraction() -> Any:
  """
  Fair-share RSS fraction for success-path cooperative recycle (default 0.5).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_cooperative_recycle_rss_fraction``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_ingest_cooperative_recycle_rss_fraction()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      0.0,
      min(1.0, float(_pipeline_get("sync_ingest_cooperative_recycle_rss_fraction"))),
  )


def get_sync_ingest_rss_recheck_delay_ms() -> Any:
  """
  Optional RSS re-measure delay after release when above threshold (default 50).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_ingest_rss_recheck_delay_ms``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_ingest_rss_recheck_delay_ms()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0, _pipeline_getint("sync_ingest_rss_recheck_delay_ms"))


_SYNC_TIMEDB_CONFIG_BASELINE_PATH = "<sync_timedb_config_baseline>"
_SYNC_TIMEDB_CONFIG_BASELINE_PARSER = None

_PIPELINE_PATH_OPTIONS = frozenset({
    "acct_path",
    "archive_dir",
    "daily_archive_dir",
})

_PIPELINE_DERIVED_AUDIT_SKIP = frozenset()

_SYNC_TIMEDB_CONFIG_AUDIT_ENV_KEYS = (
    "HPCPERFSTATS_METRICS_COMPUTE_BATCH_MAX_SINGLE_JOB_S",
    "HPCPERFSTATS_METRICS_COMPUTE_BATCH_MAX_WINDOW_S",
    "HPCPERFSTATS_METRICS_COMPUTE_BATCH_UNKNOWN_RUNTIME_S",
    "HPCPERFSTATS_METRICS_COMPUTE_TOTAL_WATCHDOG_S",
    "HPCPERFSTATS_METRICS_COMPUTE_WATCHDOG_S",
    "HPCPERFSTATS_METRICS_DEFERRED_NOT_READY_MAX_AGE_S",
    "HPCPERFSTATS_METRICS_DEFERRED_NOT_READY_MAX_RETRIES",
    "HPCPERFSTATS_METRICS_DEFERRED_NOT_READY_QUARANTINE_S",
    "HPCPERFSTATS_METRICS_DEFERRED_NOT_READY_RETRY_S",
    "HPCPERFSTATS_METRICS_PERSIST_LOCK_TIMEOUT_MS",
    "HPCPERFSTATS_METRICS_PERSIST_STATEMENT_TIMEOUT_MS",
    "HPCPERFSTATS_METRICS_PLOT_PREWARM_MODE",
    "HPCPERFSTATS_METRICS_PREWARM_BACKLOG_CAP",
    "HPCPERFSTATS_METRICS_PREWARM_BACKPRESSURE_WAIT_S",
    "HPCPERFSTATS_METRICS_PREWARM_DRAIN_BATCH_BUDGET_MAX_S",
    "HPCPERFSTATS_METRICS_PREWARM_DRAIN_BATCH_BUDGET_S",
    "HPCPERFSTATS_METRICS_PREWARM_DRAIN_PER_JOB_S",
    "HPCPERFSTATS_METRICS_RUN_PER_JOB_TIMEOUT_S",
    "HPCPERFSTATS_METRICS_RUN_POLL_TIMEOUT_S",
    "HPCPERFSTATS_METRICS_RUN_STALL_TIMEOUT_S",
    "HPCPERFSTATS_METRICS_SCHEDULER_MODE",
    "HPCPERFSTATS_PIPELINE_OVERLAP_MODE",
    "HPCPERFSTATS_SYNC_ARCHIVE_VALIDATION_MAX_WORKERS",
    "HPCPERFSTATS_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_ENABLED",
    "HPCPERFSTATS_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_MAX_BYTES",
    "HPCPERFSTATS_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_LARGE_MAX_BYTES",
    "HPCPERFSTATS_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_QUEUE_MULTIPLIER",
    "HPCPERFSTATS_SYNC_INGEST_GIANT_POOL_SUPPLEMENT_TRIGGER_BUDGET_S",
    "HPCPERFSTATS_SYNC_INGEST_PER_FILE_TIMEOUT_MAX_S",
    "HPCPERFSTATS_SYNC_INGEST_PER_FILE_TIMEOUT_S",
    "HPCPERFSTATS_SYNC_INGEST_PER_FILE_TIMEOUT_S_PER_MIB",
    "HPCPERFSTATS_SYNC_POOL_POLL_TIMEOUT_S",
    "HPCPERFSTATS_SYNC_POOL_STALL_ABORT_AFTER_TIMEOUTS",
    "METRICS_POOL_PROCESS_CAP",
    "SYNC_DB_WRITER_POOL_CAP",
    "SYNC_ENABLE_CPUSET_PRIORITY_BUDGET",
    "SYNC_ENABLE_OVERPROVISION_MODE",
    "SYNC_POOL_PROCESS_CAP",
    "SYNC_WRITE_LOCK_SHARDS",
)


def _sync_timedb_config_baseline_parser() -> Any:
  """
  Minimal ini with only install paths; all tunables use getter fallbacks.
  
  Returns:
    Any: Open return polymorphism from
    ``_sync_timedb_config_baseline_parser``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> _sync_timedb_config_baseline_parser()  # doctest: +SKIP
  """
  global _SYNC_TIMEDB_CONFIG_BASELINE_PARSER
  if _SYNC_TIMEDB_CONFIG_BASELINE_PARSER is not None:
    return _SYNC_TIMEDB_CONFIG_BASELINE_PARSER
  parser = configparser.ConfigParser()
  parser.read_dict({
      "DEFAULT": {
          "machine": "baseline",
          "host_name_ext": "baseline.example",
          "data_dir": "/data",
          "server": "baseline",
          "debug": "no",
          "secret_key": "baseline",
          "staff_email_domain": "example",
          "timezone": "UTC",
          "total_cores": _DEFAULT_TOTAL_CORES,
          "engine_name": "django.db.backends.postgresql",
          "dbname": "baseline",
          "username": "u",
          "password": "p",
          "host": "localhost",
          "port": "5432",
      },
      "PIPELINE": {
          "acct_path": "/data/accounting",
          "archive_dir": "/data/archive",
          "daily_archive_dir": "/data/daily_archive",
      },
      "RMQ": {
          "rmq_server": "localhost",
          "rmq_queue": "baseline",
      },
      "OAUTH2": {
          "client_id": "id",
          "client_key": "key",
          "authorize_url": "http://localhost",
          "oauth_base_url": "http://localhost",
      },
      "XALT": {
          "xalt_engine": "django.db.backends.postgresql",
          "xalt_name": "xalt",
          "xalt_user": "u",
          "xalt_password": "p",
          "xalt_host": "localhost",
      },
  })
  _SYNC_TIMEDB_CONFIG_BASELINE_PARSER = parser
  return parser


@contextmanager
def _cfg_audit_context(parser: Any, path: str) -> Iterator[Any]:
  """
  Internal helper to handle config audit context.
  
  Args:
    parser (Any): Parser passed to this helper.
    path (str): String for path.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _cfg_audit_context(None, "x")  # doctest: +SKIP
  """
  global cfg, _ACTIVE_CONFIG_PATH
  saved_cfg, saved_path = cfg, _ACTIVE_CONFIG_PATH
  cfg, _ACTIVE_CONFIG_PATH = parser, path
  try:
    yield
  finally:
    cfg, _ACTIVE_CONFIG_PATH = saved_cfg, saved_path


@contextmanager
def _cleared_sync_timedb_config_audit_env() -> Iterator[Any]:
  """
  Internal helper to handle cleared sync timedb config audit env.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _cleared_sync_timedb_config_audit_env()  # doctest: +SKIP
  """
  saved = {
      key: os.environ.pop(key)
      for key in _SYNC_TIMEDB_CONFIG_AUDIT_ENV_KEYS
      if key in os.environ
  }
  try:
    yield
  finally:
    for key, value in saved.items():
      os.environ[key] = value


def _audit_values_equal(current: Any, baseline: Any) -> Any:
  """
  Internal helper to handle audit values equal.
  
  Args:
    current (Any): Current passed to this helper.
    baseline (Any): Baseline passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _audit_values_equal(None, None)  # doctest: +SKIP
  """
  if current is None or baseline is None:
    return current is baseline
  if isinstance(current, bool) or isinstance(baseline, bool):
    return bool(current) == bool(baseline)
  if isinstance(current, (int, float)) or isinstance(baseline, (int, float)):
    return abs(float(current) - float(baseline)) < 1e-6
  return str(current) == str(baseline)


def _format_sync_timedb_audit_value(value: Any) -> Any:
  """
  Internal helper to format the sync timedb audit value.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _format_sync_timedb_audit_value(None)  # doctest: +SKIP
  """
  if value is None:
    return "(unset)"
  if isinstance(value, bool):
    return "yes" if value else "no"
  if isinstance(value, float):
    if value == int(value):
      return str(int(value))
    return format(value, ".9g")
  return str(value)


def _iter_sync_timedb_config_audit_getters() -> Iterator[Any]:
  """
  Internal helper to iterate over the sync timedb config audit getters.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _iter_sync_timedb_config_audit_getters()  # doctest: +SKIP
  """
  yield ("total_cores", get_ini_total_cores_int)
  yield ("effective_cores", get_effective_cores)
  yield ("cpuset_pin_min_total_cores", get_cpuset_pin_min_total_cores)
  yield ("cpuset_pin_min_cores_per_node", get_cpuset_pin_min_cores_per_node)
  yield ("numa_pin_max_nodes_auto", get_numa_pin_max_nodes_auto)
  yield ("pin_proxy_in_compose", get_pin_proxy_in_compose)
  yield ("web_numa_node", get_web_numa_node)
  yield ("pipeline_numa_node", get_pipeline_numa_node)
  for _section, option, _default in INI_OPTION_REGISTRY:
    if _section != "PIPELINE":
      continue
    if option in _PIPELINE_PATH_OPTIONS or option in _PIPELINE_DERIVED_AUDIT_SKIP:
      continue
    getter_name = f"get_{option}"
    getter = globals().get(getter_name)
    if getter is None or not callable(getter):
      continue
    if inspect.signature(getter).parameters:
      continue
    yield (option, getter)


def collect_sync_timedb_non_default_settings() -> Any:
  """
  Return sorted ``(name, effective_value)`` pairs differing from code defaults.
  
  Returns:
    Any: Open return polymorphism from
    ``collect_sync_timedb_non_default_settings``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> collect_sync_timedb_non_default_settings()  # doctest: +SKIP
  """
  baseline = _sync_timedb_config_baseline_parser()
  non_default = []
  for name, getter in _iter_sync_timedb_config_audit_getters():
    current = getter()
    with _cleared_sync_timedb_config_audit_env():
      with _cfg_audit_context(baseline, _SYNC_TIMEDB_CONFIG_BASELINE_PATH):
        default = getter()
    if not _audit_values_equal(current, default):
      non_default.append((name, current))
  non_default.sort(key=lambda item: item[0])
  return non_default


def format_sync_timedb_non_default_settings_line() -> Any:
  """
  One-line operator summary for sync_timedb startup logging.
  
  Returns:
    Any: Open return polymorphism from
    ``format_sync_timedb_non_default_settings_line``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> format_sync_timedb_non_default_settings_line()  # doctest: +SKIP
  """
  entries = collect_sync_timedb_non_default_settings()
  if not entries:
    return "sync_timedb: non-default settings: (none)"
  parts = [
      f"{name}={_format_sync_timedb_audit_value(value)}"
      for name, value in entries
  ]
  return "sync_timedb: non-default settings: " + " ".join(parts)


def get_sync_enable_ingest_first_durability_mode() -> Any:
  """
  Checkpoint after DB write even when tar append fails (default on).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_enable_ingest_first_durability_mode``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_enable_ingest_first_durability_mode()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_enable_ingest_first_durability_mode"),
  )


def get_archive_maintenance_idle_seconds() -> Any:
  """
  Optional idle dwell before janitor tick budget bonus (default 300s).
  
  Returns:
    Any: Open return polymorphism from
    ``get_archive_maintenance_idle_seconds``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_archive_maintenance_idle_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      0.0,
      float(_pipeline_get("archive_maintenance_idle_seconds")),
  )


def get_archive_janitor_budget_seconds() -> Any:
  """
  Max wall seconds per archive janitor micro-batch tick (default 30).
  
  Returns:
    Any: Open return polymorphism from ``get_archive_janitor_budget_seconds``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_archive_janitor_budget_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      1.0,
      float(_pipeline_get("archive_janitor_budget_seconds")),
  )


def get_archive_janitor_debt_high_watermark() -> Any:
  """
  Debt queue depth before temporary burst scaling (default 50).
  
  Returns:
    Any: Open return polymorphism from
    ``get_archive_janitor_debt_high_watermark``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_archive_janitor_debt_high_watermark()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("archive_janitor_debt_high_watermark"))


def get_archive_janitor_debt_burst_factor() -> Any:
  """
  Budget multiplier when debt exceeds high watermark (default 1.5).
  
  Returns:
    Any: Open return polymorphism from
    ``get_archive_janitor_debt_burst_factor``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_archive_janitor_debt_burst_factor()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      1.0,
      min(4.0, float(_pipeline_get("archive_janitor_debt_burst_factor"))),
  )


def get_archive_janitor_debt_max_entries() -> Any:
  """
  Cap in-memory janitor debt queue size (default 200).
  
  Returns:
    Any: Open return polymorphism from
    ``get_archive_janitor_debt_max_entries``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_archive_janitor_debt_max_entries()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("archive_janitor_debt_max_entries"))


def get_archive_janitor_raw_paths_per_tick() -> Any:
  """
  Max raw stats file deletes per janitor RAW_REMOVE debt item (default 1000).
  
  Returns:
    Any: Open return polymorphism from
    ``get_archive_janitor_raw_paths_per_tick``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_archive_janitor_raw_paths_per_tick()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("archive_janitor_raw_paths_per_tick"))


def get_sync_day_close_candidate_report() -> Any:
  """
  Log day-close candidate report (queued/disqualified only; default on).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_day_close_candidate_report``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_day_close_candidate_report()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_day_close_candidate_report"),
  )


def get_sync_startup_snapshot_wait_seconds() -> Any:
  """
  Max wait for canonical startup archive snapshot before single-flight build.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_startup_snapshot_wait_seconds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_startup_snapshot_wait_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      120.0,
      float(_pipeline_get("sync_startup_snapshot_wait_seconds")),
  )


def get_sync_day_close_raw_removal_max_deletes_per_pass() -> Any:
  """
  Max deletes per day-close batch delete; 0 means unlimited (default 0).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_day_close_raw_removal_max_deletes_per_pass``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_sync_day_close_raw_removal_max_deletes_per_pass()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _pipeline_get("sync_day_close_raw_removal_max_deletes_per_pass")
  try:
    return max(0, int(raw))
  except (TypeError, ValueError):
    return 0


def get_sync_day_close_max_inflight() -> Any:
  """
  Pipeline occupancy and parallel ``DAY_CLOSE`` worker count (default 4).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_day_close_max_inflight``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_day_close_max_inflight()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _pipeline_get("sync_day_close_max_inflight")
  try:
    return max(1, int(raw))
  except (TypeError, ValueError):
    return 4


def get_sync_day_close_manifest_stale_seconds() -> Any:
  """
  Recover stale day-close manifest entries on coordinator init (default 7200;.
  
    0=off).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_day_close_manifest_stale_seconds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_day_close_manifest_stale_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  raw = _pipeline_get("sync_day_close_manifest_stale_seconds")
  try:
    return max(0.0, float(raw))
  except (TypeError, ValueError):
    return 7200.0


def get_sync_archive_max_inflight_jobs() -> Any:
  """
  Concurrent daily-tar append slots (= ``sync_archive_pool_processes``).
  
  Legacy INI key ``sync_archive_max_inflight_jobs`` is ignored. Capacity follows
  ``get_sync_archive_pool_processes()`` so a narrow site inflight cannot leave
  archive pool workers idle while overflow days sit on the heap.
  
  Returns:
    Any: Open return polymorphism from ``get_sync_archive_max_inflight_jobs``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_max_inflight_jobs()  # doctest: +SKIP
  """
  return max(1, int(get_sync_archive_pool_processes()))


def get_sync_archive_worker_stall_seconds() -> Any:
  """
  Seconds before treating an archive pool job as stalled (default 600).
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_archive_worker_stall_seconds``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_worker_stall_seconds()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(
      60.0,
      float(_pipeline_get("sync_archive_worker_stall_seconds")),
  )


def get_sync_archive_require_db_ingest() -> Any:
  """
  Require head+tail DB ingest readiness before tar append or raw removal.
  
  Returns:
    Any: Open return polymorphism from ``get_sync_archive_require_db_ingest``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_require_db_ingest()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_archive_require_db_ingest"),
  )


def get_sync_archive_maint_hints() -> Any:
  """
  Persist host-dir/path hints for faster archive maintenance restarts (default.
  
    on).
  
  Returns:
    Any: Open return polymorphism from ``get_sync_archive_maint_hints``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_archive_maint_hints()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("sync_archive_maint_hints"),
  )


def get_listend_db_ingest_enabled() -> Any:
  """
  Whether listend asynchronously dual-writes samples to Timescale (default on).
  
  Returns:
    Any: Open return polymorphism from ``get_listend_db_ingest_enabled``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_listend_db_ingest_enabled()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return _parse_bool(
      _pipeline_get("listend_db_ingest_enabled"),
      default=True,
  )


def get_listend_db_ingest_pool_processes() -> Any:
  """
  Host-affine spawn workers for listend live DB ingest (default 32).
  
  Returns:
    Any: Open return polymorphism from
    ``get_listend_db_ingest_pool_processes``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_listend_db_ingest_pool_processes()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("listend_db_ingest_pool_processes"))


def get_listend_db_ingest_queue_max_gb() -> Any:
  """
  Total in-flight listend DB-queue memory budget in GiB across workers (default.
  
    8).
  
  Returns:
    Any: Open return polymorphism from ``get_listend_db_ingest_queue_max_gb``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_listend_db_ingest_queue_max_gb()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(0.001, _pipeline_getfloat("listend_db_ingest_queue_max_gb"))


def get_listend_db_ingest_batch_samples() -> Any:
  """
  Samples cached per listend DB worker before bulk_create (default 100).
  
  Returns:
    Any: Open return polymorphism from
    ``get_listend_db_ingest_batch_samples``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_listend_db_ingest_batch_samples()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  return max(1, _pipeline_getint("listend_db_ingest_batch_samples"))


def get_redis_location() -> Any:
  """
  Return the Redis URL for cache from CACHE config.
  
  Defaults to redis://127.0.0.1:6379/1 if [CACHE] or redis_location is missing.
  
  Returns:
    Any: Open return polymorphism from ``get_redis_location``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> get_redis_location()  # doctest: +SKIP
  """
  _ensure_cfg_loaded()
  if cfg.has_section("CACHE") and cfg.has_option("CACHE", "redis_location"):
    return cfg.get("CACHE", "redis_location").strip() or "redis://127.0.0.1:6379/1"
  return "redis://127.0.0.1:6379/1"


def get_large_job_host_data_row_threshold() -> Any:
  """
  When host_data row count for a job window exceeds this, sample times in.
  
    jid_table.
  
  Env ``HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS`` overrides (minimum 1000).
    Default
  1_500_000 keeps interactive metrics/plots bounded on huge jobs.
  
  Returns:
    Any: Open return polymorphism from
    ``get_large_job_host_data_row_threshold``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_large_job_host_data_row_threshold()  # doctest: +SKIP
  """
  env = os.environ.get("HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS", "").strip()
  if not env:
    return 1_500_000
  try:
    return max(1000, int(env))
  except (TypeError, ValueError, OverflowError):
    return 1_500_000


def get_large_job_time_buckets() -> Any:
  """
  Max distinct time buckets used when large-job sampling is active.
  
  Env ``HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS`` overrides (minimum 32). Default
    2048.
  
  Returns:
    Any: Open return polymorphism from ``get_large_job_time_buckets``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_large_job_time_buckets()  # doctest: +SKIP
  """
  env = os.environ.get("HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS", "").strip()
  if not env:
    return 2048
  try:
    return max(32, int(env))
  except (TypeError, ValueError, OverflowError):
    return 2048


def get_large_job_window_row_count_cache_ttl() -> Any:
  """
  TTL (seconds) for caching ``COUNT(*)`` over job window in ``jid_table``; 0.
  
    disables.
  
  Reduces repeated full-window counts when the same job is opened multiple times
  shortly after ingest. Invalidate via ``invalidate_jid_derived_cache_keys``.
  
  Returns:
    Any: Open return polymorphism from
    ``get_large_job_window_row_count_cache_ttl``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_large_job_window_row_count_cache_ttl()  # doctest: +SKIP
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


def get_large_job_time_sample_sql_mode() -> Any:
  """
  How to pick strided sample timestamps for large jobs: ``ntile`` or.
  
    ``date_bin``.
  
  Default ``date_bin``: PostgreSQL uses per-stride-bucket ``MAX(time)`` queries
  merged across host chunks (same stride grid as the legacy Python path),
  avoiding a full-window batched ``DISTINCT time`` pass when that succeeds.
  Set env ``HPCPERFSTATS_LARGE_JOB_TIME_SQL=ntile`` for the legacy index-space
  stride (distinct times, equal-count buckets in Python).
  
  Returns:
    Any: Open return polymorphism from ``get_large_job_time_sample_sql_mode``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_large_job_time_sample_sql_mode()  # doctest: +SKIP
  """
  env = os.environ.get("HPCPERFSTATS_LARGE_JOB_TIME_SQL", "").strip().lower()
  if env in ("ntile",):
    return "ntile"
  if env in ("date_bin", "date-bin"):
    return "date_bin"
  return "date_bin"


def get_live_distinct_use_legacy_hostlist() -> Any:
  """
  If True, ``LiveDistinctHostTimeCount`` unnests ``host_list`` (legacy).
  
  Default False: use ``LiveJidScopedDistinctHostTimeCount`` (``host_data.jid`` +
    window),
  which matches indexed access and typical ingest. Env:
  ``HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST`` = 1 to restore old SQL.
  
  **Sunset:** keep only for emergency rollback on sites that cannot use jid-
    scoped
  live distinct SQL; remove this flag and branch once no deployment depends on
    it.
  
  Returns:
    Any: Open return polymorphism from
    ``get_live_distinct_use_legacy_hostlist``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_live_distinct_use_legacy_hostlist()  # doctest: +SKIP
  """
  return os.environ.get(
      "HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", ""
  ).strip().lower() in ("1", "true", "yes")
