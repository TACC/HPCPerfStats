"""Unit tests for conf_parser with a temporary INI file.

"""

import pytest


def test_config_path_from_env(temp_ini, monkeypatch):
  """Config is read from HPCPERFSTATS_INI when set.

    """
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  # Re-import so conf_parser reads the new env
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_debug() is False
  assert cfg.get_total_cores() == "4"
  assert cfg.get_db_name() == "test"
  assert cfg.get_host_name_ext() == "local"


def test_get_debug_true(temp_ini, monkeypatch):
  """get_debug returns True for yes/true/1.

    """
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace("debug = no", "debug = yes")
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_debug() is True


def test_absolute_concurrency_defaults(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_sync_ingest_pool_processes() == 16
  assert cfg.get_metrics_pool_processes() == 24
  assert cfg.get_metrics_pool_maxtasksperchild() == 16
  assert cfg.get_metrics_pool_process_count() == 24
  assert cfg.get_gunicorn_workers() == 32
  assert cfg.get_summary_aggregate_prefetch_max_threads() == 2
  assert cfg.get_sync_write_lock_shards() == 8
  assert cfg.get_listend_db_ingest_pool_processes() == 32
  assert cfg.get_listend_db_ingest_backpressure() == "drop"
  assert cfg.get_metrics_plot_prewarm_mode() == "pipeline_required"
  assert cfg.get_sync_process_tree_rss_limit_mb() == 110000
  for dead in (
      "get_max_gunicorn_workers",
      "get_sync_pool_process_cap",
      "get_metrics_pool_process_cap",
      "get_pipeline_overlap_mode",
      "get_metrics_ingest_priority_scale",
      "get_metrics_min_processes",
      "derive_pipeline_cpuset_priority_budget",
      "pipeline_cpu_process_buckets",
      "get_metrics_prewarm_workers",
      "get_metrics_prewarm_backlog_cap",
      "get_metrics_prewarm_backpressure_wait_s",
      "get_metrics_prewarm_retry_attempts",
      "get_metrics_prewarm_drain_batch_budget_s",
      "get_metrics_prewarm_drain_batch_budget_max_s",
      "get_metrics_prewarm_drain_per_job_s",
      "get_metrics_prewarm_processing_updates_log_s",
      "_apply_sync_pool_cap",
  ):
    assert not hasattr(cfg, dead)


def test_absolute_concurrency_ini_overrides(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\n"
      "sync_ingest_pool_processes = 3\n"
      "metrics_pool_processes = 5\n"
      "metrics_pool_maxtasksperchild = 8\n"
      "gunicorn_workers = 9\n"
      "summary_aggregate_prefetch_max_threads = 1\n"
      "sync_write_lock_shards = 4\n"
      "listend_db_ingest_pool_processes = 11\n"
      "listend_db_ingest_backpressure = pause\n"
      "metrics_plot_prewarm_mode = inline\n",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_sync_ingest_pool_processes() == 3
  assert cfg.get_metrics_pool_processes() == 5
  assert cfg.get_metrics_pool_maxtasksperchild() == 8
  assert cfg.get_gunicorn_workers() == 9
  assert cfg.get_summary_aggregate_prefetch_max_threads() == 1
  assert cfg.get_sync_write_lock_shards() == 4
  assert cfg.get_listend_db_ingest_pool_processes() == 11
  assert cfg.get_listend_db_ingest_backpressure() == "pause"
  assert cfg.get_metrics_plot_prewarm_mode() == "inline"





def test_listend_db_ingest_backpressure_unknown_falls_back_to_drop(
    temp_ini, monkeypatch
):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\n"
      "listend_db_ingest_backpressure = no_such_mode\n",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_listend_db_ingest_backpressure() == "drop"


def test_get_worker_process_count(temp_ini, monkeypatch):
  """get_worker_process_count uses effective_cores // divisor, clamped to at least 1."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 64)
  # temp_ini has total_cores = 4 -> effective 4
  assert cfg.get_worker_process_count(4) == 1
  assert cfg.get_worker_process_count(2) == 2
  assert cfg.get_worker_process_count(8) == 1  # 4//8 = 0 -> clamped to 1


def test_get_archive_zstd_priority_defaults(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_nice() == 10
  assert cfg.get_archive_zstd_ionice_class() == 2
  assert cfg.get_archive_zstd_ionice_level() == 6
  assert cfg.get_archive_seal_parallel_workers() == 4
  assert cfg.get_archive_zstd_drop_page_cache() is True


def test_get_archive_zstd_drop_page_cache_opt_out(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_drop_page_cache() is True

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "daily_archive_dir = /tmp",
      "daily_archive_dir = /tmp\narchive_zstd_drop_page_cache = no",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_drop_page_cache() is False


def test_get_archive_zstd_threads_default_and_override(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_threads() == 0

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "daily_archive_dir = /tmp",
      "daily_archive_dir = /tmp\narchive_zstd_threads = 12",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_threads() == 12


def test_get_ingest_zstd_threads_default_and_override(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_ingest_zstd_threads() == 4
  assert not hasattr(cfg, "get_sync_ingest_imap_inflight_cap")

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "daily_archive_dir = /tmp",
      "daily_archive_dir = /tmp\ningest_zstd_threads = 8",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_ingest_zstd_threads() == 8



def test_get_archive_zstd_level_clamps(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_level() == 7

  with open(temp_ini) as f:
    base = f.read()
  for raw, expected in (("0", 1), ("99", 19), ("7", 7)):
    content = base.replace(
        "daily_archive_dir = /tmp",
        "daily_archive_dir = /tmp\narchive_zstd_level = %s" % raw,
    )
    with open(temp_ini, "w") as f:
      f.write(content)
    importlib.reload(cfg)
    assert cfg.get_archive_zstd_level() == expected


def test_get_archive_zstd_threads_override(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_threads() == 0

  with open(temp_ini) as f:
    base = f.read()
  content = base.replace(
      "daily_archive_dir = /tmp",
      "daily_archive_dir = /tmp\narchive_zstd_threads = 4",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_threads() == 4



def test_get_effective_cores_caps_by_host(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 2)
  assert cfg.get_effective_cores() == 2


def test_get_effective_cores_caps_by_ini(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 64)
  assert cfg.get_effective_cores() == 4


def test_total_cores_defaults_to_40_when_missing(monkeypatch, tmp_path):
  ini = tmp_path / "no-total.ini"
  ini.write_text(
      "[DEFAULT]\n"
      "machine = test\n"
      "server = test\n"
      "data_dir = /tmp\n"
      "staff_email_domain = local\n"
      "timezone = UTC\n"
      "debug = no\n"
      "[PORTAL]\n"
      "dbname = test\n"
      "username = u\n"
      "password = p\n"
      "port = 5432\n"
      "host = localhost\n"
      "archive_dir = /tmp\n"
      "acct_path = /tmp\n"
      "daily_archive_dir = /tmp\n"
      "engine_name = django.db.backends.postgresql\n"
      "[RMQ]\n"
      "rmq_server = localhost\n"
      "rmq_queue = test\n"
      "[XALT]\n"
      "xalt_engine = django.db.backends.sqlite3\n"
      "xalt_name = xalt\n"
      "xalt_user = u\n"
      "xalt_password = p\n"
      "xalt_host = localhost\n"
      "[OAUTH2]\n"
      "client_id = id\n"
      "client_key = key\n"
      "authorize_url = http://localhost\n"
      "oauth_base_url = http://localhost\n")
  monkeypatch.setenv("HPCPERFSTATS_INI", str(ini))
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_total_cores() == "40"
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 4)
  assert cfg.get_effective_cores() == 4




def test_get_redis_location_default(temp_ini, monkeypatch):
  """get_redis_location returns default when CACHE section missing."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_redis_location() == "redis://127.0.0.1:6379/1"


def test_get_redis_location_from_config(temp_ini, monkeypatch):
  """get_redis_location returns value from [CACHE] when set."""
  with open(temp_ini) as f:
    content = f.read()
  content += "\n[CACHE]\nredis_location = redis://192.168.1.1:6379/2\n"
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_redis_location() == "redis://192.168.1.1:6379/2"


def test_get_secret_key_missing(temp_ini, monkeypatch):
  """get_secret_key returns None when DEFAULT.secret_key is not set."""
  with open(temp_ini) as f:
    content = f.read()
  content = "\n".join(
      line for line in content.splitlines()
      if not line.strip().startswith("secret_key"))
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_secret_key() is None


def test_get_secret_key_from_config(temp_ini, monkeypatch):
  """get_secret_key returns value from DEFAULT.secret_key when set."""
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "secret_key = test-secret-key-do-not-use-in-production",
      "secret_key = my-secret-key-value")
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_secret_key() == "my-secret-key-value"


def test_get_local_timezone(temp_ini, monkeypatch):
  """get_local_timezone returns ZoneInfo for DEFAULT.timezone."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  from zoneinfo import ZoneInfo
  tz = cfg.get_local_timezone()
  assert tz == ZoneInfo("UTC")


def test_missing_debug_defaults_to_false(monkeypatch, tmp_path):
  ini = tmp_path / "missing-debug.ini"
  ini.write_text(
      "[DEFAULT]\n"
      "machine = test\n"
      "server = test\n"
      "data_dir = /tmp\n"
      "staff_email_domain = local\n"
      "timezone = UTC\n"
      "total_cores = 4\n"
      "[PORTAL]\n"
      "dbname = test\n"
      "username = u\n"
      "password = p\n"
      "port = 5432\n"
      "host = localhost\n"
      "archive_dir = /tmp\n"
      "acct_path = /tmp\n"
      "daily_archive_dir = /tmp\n"
      "engine_name = django.db.backends.postgresql\n"
      "[RMQ]\n"
      "rmq_server = localhost\n"
      "rmq_queue = test\n"
      "[XALT]\n"
      "xalt_engine = django.db.backends.sqlite3\n"
      "xalt_name = xalt\n"
      "xalt_user = u\n"
      "xalt_password = p\n"
      "xalt_host = localhost\n"
      "[OAUTH2]\n"
      "client_id = id\n"
      "client_key = key\n"
      "authorize_url = http://localhost\n"
      "oauth_base_url = http://localhost\n")
  monkeypatch.setenv("HPCPERFSTATS_INI", str(ini))
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)

  assert cfg.get_debug() is False


def test_fallback_to_cwd_hpcperfstats_ini(monkeypatch, tmp_path):
  """When env is unset, conf_parser loads ./hpcperfstats.ini."""
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(
      "[DEFAULT]\n"
      "machine = test\n"
      "server = test\n"
      "host_name_ext = local\n"
      "data_dir = /tmp\n"
      "staff_email_domain = local\n"
      "timezone = UTC\n"
      "total_cores = 4\n"
      "[PORTAL]\n"
      "dbname = test\n"
      "username = u\n"
      "password = p\n"
      "port = 5432\n"
      "host = localhost\n"
      "archive_dir = /tmp\n"
      "acct_path = /tmp\n"
      "daily_archive_dir = /tmp\n"
      "engine_name = django.db.backends.postgresql\n"
      "[RMQ]\n"
      "rmq_server = localhost\n"
      "rmq_queue = test\n"
      "[XALT]\n"
      "xalt_engine = django.db.backends.sqlite3\n"
      "xalt_name = xalt\n"
      "xalt_user = u\n"
      "xalt_password = p\n"
      "xalt_host = localhost\n"
      "[OAUTH2]\n"
      "client_id = id\n"
      "client_key = key\n"
      "authorize_url = http://localhost\n"
      "oauth_base_url = http://localhost\n")
  monkeypatch.delenv("HPCPERFSTATS_INI", raising=False)
  monkeypatch.chdir(tmp_path)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)

  assert cfg.get_rmq_server() == "localhost"
  assert cfg.get_db_name() == "test"


def test_missing_config_file_raises_helpful_error(monkeypatch, tmp_path):
  """Raise FileNotFoundError when explicit env path does not exist."""
  missing_ini = tmp_path / "does-not-exist.ini"
  monkeypatch.setenv("HPCPERFSTATS_INI", str(missing_ini))
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)

  with pytest.raises(FileNotFoundError, match="Unable to locate HPCPerfStats"):
    cfg.get_db_name()


def test_parallel_db_prefetch_and_api_defaults(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_parallel_db_prefetch_max() == 4
  assert cfg.get_api_small_executor_max_workers() == 4


def test_api_small_executor_override(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\napi_small_executor_max_workers = 3",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_api_small_executor_max_workers() == 3
  assert cfg.get_parallel_db_prefetch_max() == 4


def test_db_conn_max_age_default(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_db_conn_max_age() == 90
  monkeypatch.setenv("DJANGO_CONN_MAX_AGE", "30")
  importlib.reload(cfg)
  assert cfg.get_db_conn_max_age() == 30


def test_build_postgres_options_statement_timeout(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  opts = cfg.build_postgres_connection_options()
  assert "options" in opts
  assert "statement_timeout=120000" in opts["options"]
  assert "idle_in_transaction_session_timeout=300000" in opts["options"]


def test_build_postgres_options_disabled_by_env(monkeypatch, temp_ini):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  monkeypatch.setenv("DJANGO_DB_STATEMENT_TIMEOUT_MS", "0")
  monkeypatch.setenv("DJANGO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", "0")
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.build_postgres_connection_options() == {}



def test_sync_archive_pool_processes_ini_knob_default_and_override(temp_ini, monkeypatch):
  """Archive slots come only from sync_archive_pool_processes (default 2)."""
  monkeypatch.delenv("SYNC_ARCHIVE_POOL_PROCESS_CAP", raising=False)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 64)
  assert cfg.get_sync_archive_pool_processes() == 2
  assert not hasattr(cfg, "get_sync_archive_pool_process_cap")
  with open(temp_ini) as f:
    content = f.read()
  if "sync_archive_pool_processes" not in content:
    content = content.replace(
        "total_cores = 4",
        "total_cores = 4\nsync_archive_pool_processes = 5",
    )
  else:
    content = content.replace(
        "sync_archive_pool_processes = 2",
        "sync_archive_pool_processes = 5",
    )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_sync_archive_pool_processes() == 5






def test_get_metrics_readiness_window_coverage_defaults(temp_ini, monkeypatch):
  """Default coverage gate: require=yes, margins=600s."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_metrics_readiness_require_window_coverage() is True
  assert cfg.get_metrics_readiness_start_margin_seconds() == 600.0
  assert cfg.get_metrics_readiness_end_margin_seconds() == 600.0


def test_get_metrics_readiness_window_coverage_ini_override(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_metrics_readiness_require_window_coverage() is True
  assert cfg.get_metrics_readiness_start_margin_seconds() == 600.0
  assert cfg.get_metrics_readiness_end_margin_seconds() == 600.0

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\n"
      "metrics_readiness_require_window_coverage = no\n"
      "metrics_readiness_start_margin_seconds = 120\n"
      "metrics_readiness_end_margin_seconds = 90",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_metrics_readiness_require_window_coverage() is False
  assert cfg.get_metrics_readiness_start_margin_seconds() == 120.0
  assert cfg.get_metrics_readiness_end_margin_seconds() == 90.0


def test_get_metrics_per_jid_phase_diagnostics_enabled_env(monkeypatch):
  import hpcperfstats.dbload.lib.conf_parser as cfg

  monkeypatch.delenv("HPCPERFSTATS_METRICS_PER_JID_PHASE_LOG", raising=False)
  assert cfg.get_metrics_per_jid_phase_diagnostics_enabled() is False
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PER_JID_PHASE_LOG", "true")
  assert cfg.get_metrics_per_jid_phase_diagnostics_enabled() is True





def test_metrics_scheduler_tunables_without_prewarm_pool_keys(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_metrics_scheduler_mode() == "global_priority"
  assert cfg.get_metrics_scheduler_prefetch_chunks() == 8
  assert cfg.get_metrics_scheduler_ready_queue_target() == 100
  assert cfg.get_metrics_idle_slot_supplement_enabled() is True
  assert cfg.get_metrics_supplement_sample_soft_max() == 10000
  assert cfg.get_metrics_supplement_sample_hard_max() == 80000
  assert cfg.get_metrics_plot_prewarm_mode() == "pipeline_required"
  assert cfg.get_metrics_run_poll_timeout_s() == 5.0
  assert cfg.get_metrics_run_stall_timeout_s() == 900.0
  assert cfg.get_metrics_run_per_job_timeout_s() == 0.0
  assert cfg.get_metrics_worker_statement_timeout_ms() == 120000
  assert cfg.get_metrics_persist_statement_timeout_ms() == 120000
  assert cfg.get_metrics_persist_lock_timeout_ms() == 10000
  assert cfg.get_metrics_proxy_reject_jid_batch_size() == 48
  assert cfg.get_metrics_compute_batch_max_window_s() == 0.0
  assert cfg.get_metrics_compute_batch_max_single_job_s() == 0.0
  assert cfg.get_metrics_compute_batch_unknown_runtime_s() == 172800.0
  assert cfg.get_metrics_compute_watchdog_s() == 120.0
  assert cfg.get_metrics_compute_total_watchdog_s() == 0.0
  assert cfg.get_metrics_deferred_not_ready_retry_s() == 10.0
  assert cfg.get_metrics_deferred_not_ready_max_retries() == 30
  assert cfg.get_metrics_deferred_not_ready_max_age_s() == 900.0
  assert cfg.get_metrics_deferred_not_ready_quarantine_s() == 300.0
  assert cfg.get_metrics_readiness_require_window_coverage() is True
  assert cfg.get_metrics_readiness_start_margin_seconds() == 600.0
  assert cfg.get_metrics_readiness_end_margin_seconds() == 600.0

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\n"
      "metrics_scheduler_mode = global_fifo\n"
      "metrics_scheduler_prefetch_chunks = 3\n"
      "metrics_scheduler_ready_queue_target = 111\n"
      "metrics_plot_prewarm_mode = inline\n"
      "metrics_run_poll_timeout_s = 1.5\n"
      "metrics_run_stall_timeout_s = 120\n"
      "metrics_worker_statement_timeout_ms = 300000\n"
      "metrics_persist_statement_timeout_ms = 45000\n"
      "metrics_persist_lock_timeout_ms = 7000\n"
      "metrics_proxy_reject_jid_batch_size = 32",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_metrics_scheduler_mode() == "global_fifo"
  assert cfg.get_metrics_scheduler_prefetch_chunks() == 3
  assert cfg.get_metrics_scheduler_ready_queue_target() == 111
  assert cfg.get_metrics_plot_prewarm_mode() == "inline"
  assert cfg.get_metrics_run_poll_timeout_s() == 1.5
  assert cfg.get_metrics_run_stall_timeout_s() == 120.0
  assert cfg.get_metrics_worker_statement_timeout_ms() == 300000
  assert cfg.get_metrics_persist_statement_timeout_ms() == 45000
  assert cfg.get_metrics_persist_lock_timeout_ms() == 7000
  assert cfg.get_metrics_proxy_reject_jid_batch_size() == 32
  monkeypatch.setenv("HPCPERFSTATS_METRICS_SCHEDULER_MODE", "strict_date")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PLOT_PREWARM_MODE", "pipeline_required")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_RUN_POLL_TIMEOUT_S", "2.5")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_RUN_STALL_TIMEOUT_S", "45")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_WORKER_STATEMENT_TIMEOUT_MS", "90000")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PERSIST_STATEMENT_TIMEOUT_MS", "9000")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PERSIST_LOCK_TIMEOUT_MS", "3000")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_COMPUTE_WATCHDOG_S", "90")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_COMPUTE_TOTAL_WATCHDOG_S", "600")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_DEFERRED_NOT_READY_RETRY_S", "15")
  assert cfg.get_metrics_scheduler_mode() == "strict_date"
  assert cfg.get_metrics_plot_prewarm_mode() == "pipeline_required"
  assert cfg.get_metrics_run_poll_timeout_s() == 2.5
  assert cfg.get_metrics_run_stall_timeout_s() == 45.0
  assert cfg.get_metrics_worker_statement_timeout_ms() == 90000
  assert cfg.get_metrics_persist_statement_timeout_ms() == 9000
  assert cfg.get_metrics_persist_lock_timeout_ms() == 3000
  assert cfg.get_metrics_compute_watchdog_s() == 90.0
  assert cfg.get_metrics_compute_total_watchdog_s() == 600.0
  assert cfg.get_metrics_deferred_not_ready_retry_s() == 15.0


def test_get_large_job_time_sample_sql_mode_defaults_and_env(temp_ini, monkeypatch):
  """Default strided time SQL mode is date_bin; ntile is opt-in via env."""
  monkeypatch.delenv("HPCPERFSTATS_LARGE_JOB_TIME_SQL", raising=False)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_large_job_time_sample_sql_mode() == "date_bin"
  monkeypatch.setenv("HPCPERFSTATS_LARGE_JOB_TIME_SQL", "ntile")
  assert cfg.get_large_job_time_sample_sql_mode() == "ntile"


def test_large_job_numeric_env_invalid_falls_back_to_defaults(temp_ini, monkeypatch):
  """Non-numeric large-job env (e.g. mistaken hostname export) must not raise."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  bogus = "c636-041.vista.tacc.utexas.edu"
  monkeypatch.setenv("HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS", bogus)
  monkeypatch.setenv("HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS", bogus)
  monkeypatch.setenv("HPCPERFSTATS_LARGE_JOB_WINDOW_ROW_COUNT_CACHE_TTL", bogus)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_large_job_host_data_row_threshold() == 1_500_000
  assert cfg.get_large_job_time_buckets() == 2048
  assert cfg.get_large_job_window_row_count_cache_ttl() == 300


def test_plot_aggregate_chunk_budget_defaults_and_env(temp_ini, monkeypatch):
  """Plot aggregate time-slice and host×time budget (design 5000×48×60)."""
  monkeypatch.delenv("HPCPERFSTATS_METRICS_PLOT_AGGREGATE_TIME_SLICE_S", raising=False)
  monkeypatch.delenv("HPCPERFSTATS_PLOT_AGGREGATE_MAX_HOST_TIME_POINTS", raising=False)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_metrics_plot_aggregate_time_slice_s() == 3600
  assert cfg.get_plot_aggregate_max_host_time_points() == 1_000_000
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PLOT_AGGREGATE_TIME_SLICE_S", "1800")
  monkeypatch.setenv("HPCPERFSTATS_PLOT_AGGREGATE_MAX_HOST_TIME_POINTS", "500000")
  assert cfg.get_metrics_plot_aggregate_time_slice_s() == 1800
  assert cfg.get_plot_aggregate_max_host_time_points() == 500000


def test_sync_pipeline_tunable_defaults_and_overrides(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_sync_ingest_queue_max_size() == 3000
  assert cfg.get_sync_ingest_rescan_mtime_days() == 1
  # Thrown B keys: getters return hard-coded retired defaults (not INI).
  assert cfg.get_sync_ingest_rescan_full_every() == 0
  assert cfg.get_sync_ingest_current_proximity_days() == 2
  assert cfg.get_sync_ingest_chunk_size() == 3000
  assert cfg.get_sync_ingest_chunk_size() == cfg.get_sync_ingest_queue_max_size()
  assert cfg.get_sync_archive_queue_max_size() == 1000
  assert cfg.get_sync_archive_retry_max_attempts() == 5
  assert cfg.get_sync_archive_retry_backoff_base_seconds() == 1.0
  assert cfg.get_sync_archive_retry_backoff_max_seconds() == 60.0
  assert cfg.get_sync_checkpoint_flush_batch_size() == 100
  assert cfg.get_sync_timedb_tar_append_batch_size() == 1024
  assert cfg.get_sync_bulk_create_batch_size() == 10000
  assert cfg.get_sync_pool_stall_abort_after_timeouts() == 17320
  assert cfg.get_sync_pool_poll_timeout_s() == 5.0
  assert cfg.get_sync_pool_worker_recycle_grace_seconds() == 60.0
  assert cfg.get_sync_pool_stall_defer_log_interval_s() == 60.0
  assert cfg.get_sync_ingest_per_file_timeout_s() == 3600.0
  assert cfg.get_sync_ingest_per_file_timeout_max_s() == 86400.0
  assert cfg.get_sync_ingest_per_file_timeout_s_per_mib() == pytest.approx(
      (86400.0 - 900.0) / 30720.0,
  )
  assert cfg.get_sync_ingest_giant_pool_supplement_enabled() is False
  assert cfg.get_sync_ingest_idle_slot_supplement_enabled() is False
  assert cfg.get_sync_ingest_giant_pool_supplement_max_bytes() == 1073741824
  assert cfg.get_sync_ingest_giant_pool_supplement_large_max_bytes() == 8589934592
  assert cfg.get_sync_ingest_giant_pool_supplement_queue_multiplier() == 2
  assert cfg.get_sync_ingest_giant_pool_supplement_queue_size() == max(
      1, int(cfg.get_sync_ingest_pool_processes()) * 2
  )
  assert cfg.get_sync_ingest_giant_pool_supplement_trigger_budget_s() == pytest.approx(
      6600.0,
  )
  assert cfg.get_sync_archive_members_cache_enabled() is True
  assert cfg.get_sync_archive_members_cache_max_entries() == 64
  assert cfg.get_sync_archive_members_redis_enabled() is True
  assert cfg.get_sync_archive_members_redis_ttl_seconds() == 86400
  assert cfg.get_sync_archive_members_redis_populate_lock_seconds() == 3600
  assert cfg.get_sync_archive_members_redis_populate_stall_seconds() == 120
  assert cfg.get_sync_archive_members_redis_populate_max_seconds() == 7200
  assert cfg.get_sync_archive_members_fnctl_read_lock_timeout_seconds() == 180
  assert cfg.get_sync_archive_members_redis_wait_poll_seconds() == 0.25
  assert cfg.get_sync_archive_members_redis_hset_batch_size() == 500
  assert cfg.get_sync_archive_members_redis_max_payload_bytes() == 8388608

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\n"
      "sync_ingest_queue_max_size = 111\n"
      "sync_ingest_rescan_mtime_days = 3\n"
      "sync_ingest_rescan_full_every = 50\n"
      "sync_ingest_current_proximity_days = -3\n"
      "sync_archive_queue_max_size = 222\n"
      "sync_archive_retry_max_attempts = 7\n"
      "sync_archive_retry_backoff_base_seconds = 2.5\n"
      "sync_archive_retry_backoff_max_seconds = 12.5\n"
      "sync_checkpoint_flush_batch_size = 42\n"
      "sync_timedb_tar_append_batch_size = 2048\n"
      "sync_pool_stall_abort_after_timeouts = 90\n"
      "sync_pool_poll_timeout_s = 2.5\n"
      "sync_pool_stall_defer_log_interval_s = 30\n"
      "sync_ingest_per_file_timeout_s = 900\n"
      "sync_ingest_per_file_timeout_max_s = 7200\n"
      "sync_ingest_per_file_timeout_s_per_mib = 1.0\n"
      "sync_archive_members_cache_enabled = no\n"
      "sync_archive_members_cache_max_entries = 32\n"
      "sync_archive_members_redis_enabled = no\n"
      "sync_archive_members_redis_ttl_seconds = 7200\n"
      "sync_archive_members_redis_populate_lock_seconds = 120\n"
      "sync_archive_members_redis_populate_stall_seconds = 45\n"
      "sync_archive_members_redis_populate_max_seconds = 1800\n"
      "sync_archive_members_fnctl_read_lock_timeout_seconds = 300\n"
      "sync_archive_members_redis_wait_poll_seconds = 0.5\n"
      "sync_archive_members_redis_hset_batch_size = 100\n"
      "sync_archive_members_redis_max_payload_bytes = 1048576",
  )
  with open(temp_ini, "w") as f:
    f.write(content)

  importlib.reload(cfg)
  assert cfg.get_sync_ingest_queue_max_size() == 111
  assert cfg.get_sync_ingest_rescan_mtime_days() == 3
  # Thrown: INI overrides ignored; hard-coded retired defaults.
  assert cfg.get_sync_ingest_rescan_full_every() == 0
  assert cfg.get_sync_ingest_current_proximity_days() == 2
  assert cfg.get_sync_ingest_chunk_size() == 111
  assert cfg.get_sync_archive_queue_max_size() == 222
  assert cfg.get_sync_archive_retry_max_attempts() == 7
  assert cfg.get_sync_archive_retry_backoff_base_seconds() == 2.5
  assert cfg.get_sync_archive_retry_backoff_max_seconds() == 12.5
  assert cfg.get_sync_checkpoint_flush_batch_size() == 42
  assert cfg.get_sync_timedb_tar_append_batch_size() == 2048
  assert cfg.get_sync_pool_stall_abort_after_timeouts() == 90
  assert cfg.get_sync_pool_poll_timeout_s() == 2.5
  assert cfg.get_sync_pool_stall_defer_log_interval_s() == 30.0
  assert cfg.get_sync_ingest_per_file_timeout_s() == 900.0
  assert cfg.get_sync_ingest_per_file_timeout_max_s() == 7200.0
  assert cfg.get_sync_ingest_per_file_timeout_s_per_mib() == 1.0
  assert cfg.get_sync_archive_members_cache_enabled() is False
  assert cfg.get_sync_archive_members_cache_max_entries() == 32
  assert cfg.get_sync_archive_members_redis_enabled() is False
  assert cfg.get_sync_archive_members_redis_ttl_seconds() == 7200
  assert cfg.get_sync_archive_members_redis_populate_lock_seconds() == 120
  assert cfg.get_sync_archive_members_redis_populate_stall_seconds() == 45
  assert cfg.get_sync_archive_members_redis_populate_max_seconds() == 1800
  assert cfg.get_sync_archive_members_fnctl_read_lock_timeout_seconds() == 300
  assert cfg.get_sync_archive_members_redis_wait_poll_seconds() == 0.5
  assert cfg.get_sync_archive_members_redis_hset_batch_size() == 100
  assert cfg.get_sync_archive_members_redis_max_payload_bytes() == 1048576
  monkeypatch.setenv("HPCPERFSTATS_SYNC_INGEST_PER_FILE_TIMEOUT_S", "45")
  assert cfg.get_sync_ingest_per_file_timeout_s() == 45.0


def test_sync_ingest_rescan_mtime_and_full_every_clamp(temp_ini, monkeypatch):
  """rescan mtime_days still INI-backed; full_every thrown (hard-coded 0)."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_sync_ingest_rescan_mtime_days() == 1
  assert cfg.get_sync_ingest_rescan_full_every() == 0

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\n"
      "sync_ingest_rescan_mtime_days = 0\n"
      "sync_ingest_rescan_full_every = -5\n",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_sync_ingest_rescan_mtime_days() == 1
  assert cfg.get_sync_ingest_rescan_full_every() == 0


def test_sync_archive_max_inflight_jobs_thrown_constant(monkeypatch, temp_ini):
  """Thrown max_inflight stub is constant 2 (capacity = archive pool)."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  del temp_ini, monkeypatch
  assert cfg.get_sync_archive_max_inflight_jobs() == 2


def test_sync_host_itimes_cache_max_timestamps_per_entry(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_sync_host_itimes_cache_max_timestamps_per_entry() == 100000

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\nsync_host_itimes_cache_max_timestamps_per_entry = 50000",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_sync_host_itimes_cache_max_timestamps_per_entry() == 50000


def test_sync_write_lock_shards_absolute_default_eight(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace("total_cores = 4", "total_cores = 40")
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 40)
  assert cfg.get_sync_write_lock_shards() == 8


def test_sync_phase2_feature_flags_and_shards(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 16)
  assert cfg.get_sync_write_lock_shards() == 8
  # Ingest-first durability is on by default (fallback=yes in conf_parser).
  assert cfg.get_sync_enable_ingest_first_durability_mode() is True

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\n"
      "sync_write_lock_shards = 4\n"
      "sync_enable_ingest_first_durability_mode = true",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_sync_write_lock_shards() == 4
  assert cfg.get_sync_enable_ingest_first_durability_mode() is True

  assert cfg.get_sync_process_tree_rss_limit_mb() == 110000


def test_get_syslog_allow_from_ipv4_networks_empty_default(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_syslog_allow_from_ipv4_networks() == []


def test_get_syslog_allow_from_ipv4_networks_parses_csv(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content += (
      "\n[SYSLOG]\n"
      "allow_from = 10.0.0.0/8, 192.168.1.2/32\n"
      "listen_tcp = no\n"
      "listen_udp = yes\n"
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_syslog_allow_from_ipv4_networks() == ["10.0.0.0/8", "192.168.1.2/32"]
  assert cfg.get_syslog_listen_tcp() is False
  assert cfg.get_syslog_listen_udp() is True


def test_get_syslog_allow_from_ipv4_networks_rejects_invalid(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content += "\n[SYSLOG]\nallow_from = not-a-network\n"
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  with pytest.raises(ValueError, match="allow_from"):
    cfg.get_syslog_allow_from_ipv4_networks()


def test_render_syslog_ng_generated_text_allowlist(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace("data_dir = /tmp", "data_dir = /tmp/syslog-data")
  content += "\n[SYSLOG]\nallow_from = 10.0.0.0/8\n"
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  import hpcperfstats.render_syslog_ng_generated as rsg
  importlib.reload(cfg)
  importlib.reload(rsg)
  text = rsg.render_syslog_ng_generated_text()
  assert "netmask(10.0.0.0/8)" in text
  assert "source s_net" in text
  assert "filter f_hps_syslog_allow_net" in text


def test_format_cors_allowed_origins_csv_from_ini_production(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.format_cors_allowed_origins_csv_from_ini() == "https://test"


def test_format_cors_allowed_origins_csv_empty_when_debug(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  with open(temp_ini) as f:
    content = f.read().replace("debug = no", "debug = yes")
  with open(temp_ini, "w") as f:
    f.write(content)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.format_cors_allowed_origins_csv_from_ini() == ""


def test_format_cors_allowed_origins_multiple_hosts(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  with open(temp_ini) as f:
    content = f.read().replace("server = test\n", "server = a.example, b.example\n")
  with open(temp_ini, "w") as f:
    f.write(content)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.format_cors_allowed_origins_csv_from_ini() == (
      "https://a.example,https://b.example"
  )


def test_format_cors_allowed_origins_respects_scheme_ini(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  with open(temp_ini) as f:
    content = f.read().replace(
        "server = test\n",
        "server = legacy.example\ncors_origin_scheme = http\n",
    )
  with open(temp_ini, "w") as f:
    f.write(content)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_cors_origin_scheme() == "http"
  assert cfg.format_cors_allowed_origins_csv_from_ini() == "http://legacy.example"


def test_format_cors_allowed_origins_preserves_full_url_token(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  with open(temp_ini) as f:
    content = f.read().replace(
        "server = test\n",
        "server = https://already.example\n",
    )
  with open(temp_ini, "w") as f:
    f.write(content)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.format_cors_allowed_origins_csv_from_ini() == "https://already.example"


def test_legacy_portal_fallback_for_dbname(tmp_path, monkeypatch):
  ini = tmp_path / "legacy-portal-db.ini"
  ini.write_text(
      "[DEFAULT]\n"
      "machine = test\nserver = test\ndata_dir = /tmp\n"
      "staff_email_domain = local\ntimezone = UTC\ndebug = no\n"
      "host_name_ext = local\nrestricted_queue_keywords =\n"
      "total_cores = 4\n"
      "[PORTAL]\n"
      "dbname = legacydb\nusername = u\npassword = p\nport = 5432\n"
      "host = legacy-host\nengine_name = django.db.backends.postgresql\n"
      "[PIPELINE]\narchive_dir = /tmp\nacct_path = /tmp\ndaily_archive_dir = /tmp\n"
      "[RMQ]\nrmq_server = localhost\nrmq_queue = test\n"
      "[XALT]\nxalt_engine = django.db.backends.sqlite3\nxalt_name = xalt\n"
      "xalt_user = u\nxalt_password = p\nxalt_host = localhost\n"
      "[OAUTH2]\nclient_id = id\nclient_key = key\n"
      "authorize_url = http://localhost\noauth_base_url = http://localhost\n",
      encoding="utf-8",
  )
  monkeypatch.setenv("HPCPERFSTATS_INI", str(ini))
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_db_name() == "legacydb"
  assert cfg.get_host() == "legacy-host"


def test_legacy_default_fallback_for_moved_pipeline_key(tmp_path, monkeypatch):
  ini = tmp_path / "legacy-default-pipeline.ini"
  ini.write_text(
      "[DEFAULT]\n"
      "machine = test\nserver = test\ndata_dir = /tmp\n"
      "staff_email_domain = local\ntimezone = UTC\ndebug = no\n"
      "host_name_ext = local\nrestricted_queue_keywords =\n"
      "total_cores = 4\n"
      "sync_archive_require_db_ingest = no\n"
      "engine_name = django.db.backends.postgresql\n"
      "dbname = test\nusername = u\npassword = p\nport = 5432\nhost = localhost\n"
      "[PIPELINE]\narchive_dir = /tmp\nacct_path = /tmp\ndaily_archive_dir = /tmp\n"
      "[RMQ]\nrmq_server = localhost\nrmq_queue = test\n"
      "[XALT]\nxalt_engine = django.db.backends.sqlite3\nxalt_name = xalt\n"
      "xalt_user = u\nxalt_password = p\nxalt_host = localhost\n"
      "[OAUTH2]\nclient_id = id\nclient_key = key\n"
      "authorize_url = http://localhost\noauth_base_url = http://localhost\n",
      encoding="utf-8",
  )
  monkeypatch.setenv("HPCPERFSTATS_INI", str(ini))
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_sync_archive_require_db_ingest() is False


def test_sync_archive_db_ingest_gate_mode_removed(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert not hasattr(cfg, "get_sync_archive_db_ingest_gate_mode")
  assert not hasattr(cfg, "sync_archive_db_ingest_gate_uses_sample_mode")
  assert not hasattr(cfg, "get_sync_archive_db_ingest_gate_sample_stride")
  assert cfg.get_sync_archive_require_db_ingest() is True


def test_archive_janitor_and_dispatch_defaults(temp_ini, monkeypatch):
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  # Thrown B janitor keys: hard-coded stubs; NEW day_close keys remain live.
  assert cfg.get_archive_janitor_budget_seconds() == 30.0
  assert cfg.get_archive_janitor_debt_high_watermark() == 50
  assert cfg.get_archive_janitor_debt_burst_factor() == 1.5
  assert cfg.get_archive_janitor_debt_max_entries() == 200
  assert cfg.get_sync_day_close_raw_paths_per_batch() == 1000
  assert cfg.get_sync_day_close_candidate_report() is False
  assert cfg.get_sync_day_close_max_inflight() == 4
  assert cfg.get_sync_day_close_manifest_stale_seconds() == 7200.0
  assert cfg.get_sync_day_close_raw_removal_max_deletes_per_pass() == 0
  assert cfg.get_archive_keep_uncompressed_tar() is False
  assert cfg.get_archive_today_uncompressed_tar_grace_hours() == 24.0
  assert cfg.get_archive_maintenance_idle_seconds() == 300
  assert cfg.get_sync_archive_max_inflight_jobs() == 2
  assert cfg.get_sync_archive_worker_stall_seconds() == 600.0
  assert cfg.get_sync_enable_ingest_first_durability_mode() is True
  assert not hasattr(cfg, "get_archive_janitor_days_per_tick")
  assert not hasattr(cfg, "get_archive_janitor_raw_paths_per_tick")
  assert not hasattr(cfg, "get_sync_startup_day_close_max_inflight")
  assert not hasattr(cfg, "get_archive_seal_idle_seconds")
  assert not hasattr(cfg, "get_archive_maintenance_max_defer_seconds")
  assert not hasattr(cfg, "get_sync_day_close_raw_removal_wait_seconds")
  assert not hasattr(cfg, "get_sync_cold_path_max_concurrent_seals")
  assert not hasattr(cfg, "get_sync_dispatch_step_size")
  assert not hasattr(cfg, "get_metrics_scheduler_compute_threads")
  assert not hasattr(cfg, "get_db_connection_string")
  assert not hasattr(cfg, "get_machine_name")
  assert cfg.get_sync_day_close_manifest_stale_seconds() == 7200.0
  assert cfg.get_sync_ingest_pool_processes() == 16
  assert not hasattr(cfg, "get_sync_archive_pool_process_cap")


def test_day_close_max_inflight_default_4(temp_ini, monkeypatch):
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_sync_day_close_max_inflight() == 4


def test_legacy_portal_fallback_for_archive_dir(tmp_path, monkeypatch):
  ini = tmp_path / "legacy-portal-archive.ini"
  ini.write_text(
      "[DEFAULT]\n"
      "machine = test\nserver = test\ndata_dir = /tmp\n"
      "staff_email_domain = local\ntimezone = UTC\ndebug = no\n"
      "host_name_ext = local\nrestricted_queue_keywords =\n"
      "total_cores = 4\n"
      "engine_name = django.db.backends.postgresql\n"
      "dbname = test\nusername = u\npassword = p\nport = 5432\nhost = localhost\n"
      "[PORTAL]\narchive_dir = /legacy/archive\n"
      "acct_path = /legacy/acct\ndaily_archive_dir = /legacy/daily\n"
      "[RMQ]\nrmq_server = localhost\nrmq_queue = test\n"
      "[XALT]\nxalt_engine = django.db.backends.sqlite3\nxalt_name = xalt\n"
      "xalt_user = u\nxalt_password = p\nxalt_host = localhost\n"
      "[OAUTH2]\nclient_id = id\nclient_key = key\n"
      "authorize_url = http://localhost\noauth_base_url = http://localhost\n",
      encoding="utf-8",
  )
  monkeypatch.setenv("HPCPERFSTATS_INI", str(ini))
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_dir_path() == "/legacy/archive"
  assert cfg.get_accounting_path() == "/legacy/acct"
  assert cfg.get_daily_archive_dir_path() == "/legacy/daily"


def test_collect_sync_timedb_non_default_settings_reports_ini_overrides(
    temp_ini, monkeypatch,
):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\nsync_ingest_queue_max_size = 111",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)

  entries = dict(cfg.collect_sync_timedb_non_default_settings())
  assert entries["sync_ingest_queue_max_size"] == 111
  assert entries["total_cores"] == 4


def test_format_sync_timedb_non_default_settings_line(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  line = cfg.format_sync_timedb_non_default_settings_line()
  assert line.startswith("sync_timedb: non-default settings:")
  assert "total_cores=4" in line
