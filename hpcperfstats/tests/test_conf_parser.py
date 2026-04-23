"""Unit tests for conf_parser with a temporary INI file.

"""

import pytest


def test_config_path_from_env(temp_ini, monkeypatch):
  """Config is read from HPCPERFSTATS_INI when set.

    """
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  # Re-import so conf_parser reads the new env
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_debug() is False
  assert cfg.get_machine_name() == "test"
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
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_debug() is True


def test_get_db_connection_string(temp_ini, monkeypatch):
  """get_db_connection_string returns connection string from PORTAL section."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  s = cfg.get_db_connection_string()
  assert "dbname=test" in s
  assert "user=u" in s or " user=u " in s
  assert "password=p" in s
  assert "host=localhost" in s
  assert "port=5432" in s


def test_get_max_gunicorn_workers_cap_default(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_max_gunicorn_workers_cap() == 32


def test_get_worker_thread_count(temp_ini, monkeypatch):
  """get_worker_thread_count uses effective_cores // divisor, clamped to at least 1."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 64)
  # temp_ini has total_cores = 4 -> effective 4
  assert cfg.get_worker_thread_count(4) == 1
  assert cfg.get_worker_thread_count(2) == 2
  assert cfg.get_worker_thread_count(8) == 1  # 4//8 = 0 -> clamped to 1


def test_get_archive_pigz_threads_default_and_override(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_pigz_threads() == 8

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "daily_archive_dir = /tmp",
      "daily_archive_dir = /tmp\narchive_pigz_threads = 12",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_archive_pigz_threads() == 12


def test_get_effective_cores_caps_by_host(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 2)
  assert cfg.get_effective_cores() == 2


def test_get_effective_cores_caps_by_ini(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_total_cores() == "40"
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 4)
  assert cfg.get_effective_cores() == 4


def test_get_metrics_pool_process_count_respects_cap(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 128\nmetrics_pool_process_cap = 5",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 128)
  assert cfg.get_metrics_pool_process_count() == 5


def test_get_redis_location_default(temp_ini, monkeypatch):
  """get_redis_location returns default when CACHE section missing."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_secret_key() == "my-secret-key-value"


def test_get_local_timezone(temp_ini, monkeypatch):
  """get_local_timezone returns ZoneInfo for DEFAULT.timezone."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)

  assert cfg.get_rmq_server() == "localhost"
  assert cfg.get_db_name() == "test"


def test_missing_config_file_raises_helpful_error(monkeypatch, tmp_path):
  """Raise FileNotFoundError when explicit env path does not exist."""
  missing_ini = tmp_path / "does-not-exist.ini"
  monkeypatch.setenv("HPCPERFSTATS_INI", str(missing_ini))
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)

  with pytest.raises(FileNotFoundError, match="Unable to locate HPCPerfStats"):
    cfg.get_machine_name()


def test_parallel_db_prefetch_and_api_defaults(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_parallel_db_prefetch_max_workers() == 6
  assert cfg.get_api_small_executor_max_workers() == 6


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
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_api_small_executor_max_workers() == 3
  assert cfg.get_parallel_db_prefetch_max_workers() == 6


def test_db_conn_max_age_default(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_db_conn_max_age() == 90
  monkeypatch.setenv("DJANGO_CONN_MAX_AGE", "30")
  importlib.reload(cfg)
  assert cfg.get_db_conn_max_age() == 30


def test_build_postgres_options_statement_timeout(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.build_postgres_connection_options() == {}


def test_sync_ingest_pool_respects_cap(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 64\nsync_pool_process_cap = 2",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 64)
  assert cfg.get_worker_thread_count(4) == 16
  assert cfg.get_sync_ingest_pool_processes() == 2


def test_sync_archive_pool_respects_cap(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 64\nsync_pool_process_cap = 8\narchive_pool_process_cap = 2",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 64)
  assert cfg.get_sync_ingest_pool_processes() == 8
  assert cfg.get_sync_archive_pool_processes() == 2


def test_cpuset_priority_budget_derivation_defaults(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 16)
  budget = cfg.derive_pipeline_cpuset_priority_budget()
  assert budget["effective_cores"] == 4
  assert budget["sync_ingest_cap"] >= 1
  assert budget["sync_archive_cap"] >= 1
  assert budget["metrics_cap"] >= 1
  assert budget["reserve_cap"] >= 1
  assert (
      budget["sync_ingest_cap"]
      + budget["sync_archive_cap"]
      + budget["metrics_cap"]
      + budget["reserve_cap"]
  ) <= budget["headroom_cap"]


def test_metrics_pool_respects_cpuset_priority_budget(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 40\nmetrics_pool_process_cap = 32\nsync_enable_cpuset_priority_budget = yes",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 20)
  assert cfg.get_metrics_pool_process_count() == 4


def test_metrics_pool_ingest_priority_overlap_mode(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 40\nmetrics_pool_process_cap = 32\nsync_enable_cpuset_priority_budget = yes\npipeline_overlap_mode = ingest_priority\nmetrics_ingest_priority_scale = 0.5\nmetrics_min_processes = 2",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 20)
  assert cfg.get_metrics_pool_process_count() == 2


def test_metrics_scheduler_and_prewarm_tunables(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_metrics_scheduler_mode() == "global_priority"
  assert cfg.get_metrics_scheduler_prefetch_chunks() == 8
  assert cfg.get_metrics_scheduler_ready_queue_target() == 2000
  assert cfg.get_metrics_plot_prewarm_mode() == "pipeline_required"
  assert cfg.get_metrics_prewarm_workers() == 4
  assert cfg.get_metrics_scheduler_compute_threads() == 4
  assert cfg.get_metrics_prewarm_retry_attempts() == 2
  assert cfg.get_metrics_proxy_reject_jid_batch_size() == 48

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\n"
      "metrics_scheduler_mode = global_fifo\n"
      "metrics_scheduler_prefetch_chunks = 3\n"
      "metrics_scheduler_ready_queue_target = 111\n"
      "metrics_plot_prewarm_mode = inline\n"
      "metrics_prewarm_workers = 7\n"
      "metrics_scheduler_compute_threads = 6\n"
      "metrics_prewarm_retry_attempts = 5\n"
      "metrics_proxy_reject_jid_batch_size = 32",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_metrics_scheduler_mode() == "global_fifo"
  assert cfg.get_metrics_scheduler_prefetch_chunks() == 3
  assert cfg.get_metrics_scheduler_ready_queue_target() == 111
  assert cfg.get_metrics_plot_prewarm_mode() == "inline"
  assert cfg.get_metrics_prewarm_workers() == 7
  assert cfg.get_metrics_scheduler_compute_threads() == 6
  assert cfg.get_metrics_prewarm_retry_attempts() == 5
  assert cfg.get_metrics_proxy_reject_jid_batch_size() == 32
  monkeypatch.setenv("HPCPERFSTATS_METRICS_SCHEDULER_MODE", "strict_date")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PLOT_PREWARM_MODE", "pipeline_required")
  assert cfg.get_metrics_scheduler_mode() == "strict_date"
  assert cfg.get_metrics_plot_prewarm_mode() == "pipeline_required"


def test_metrics_proxy_reject_jid_batch_size_clamps_minimum(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\nmetrics_proxy_reject_jid_batch_size = 3",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_metrics_proxy_reject_jid_batch_size() == 8


def test_cpuset_priority_budget_overprovision_mode(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 40\n"
      "sync_enable_cpuset_priority_budget = yes\n"
      "sync_enable_overprovision_mode = yes\n"
      "sync_budget_overcommit_factor = 1.25\n"
      "sync_overprovision_ingest_multiplier = 1.20\n"
      "sync_overprovision_archive_multiplier = 1.10\n"
      "sync_overprovision_metrics_multiplier = 0.90",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 20)
  budget = cfg.derive_pipeline_cpuset_priority_budget()
  assert budget["effective_cores"] == 20
  assert budget["headroom_cap"] == 25
  assert budget["sync_ingest_cap"] >= 12


def test_pipeline_cpu_process_buckets_flags(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  buckets = cfg.pipeline_cpu_process_buckets(
      include_browser_phase=True,
      include_rsync=True,
  )
  assert "hpcperfstats-rabbitmq-listener" in buckets["real_time"]
  assert "update_metrics workers" in buckets["normal"]
  assert "rsync_data (optional)" in buckets["best_effort"]
  assert any("browser/api" in item for item in buckets["best_effort"])


def test_get_large_job_time_sample_sql_mode_defaults_and_env(temp_ini, monkeypatch):
  """Default strided time SQL mode is date_bin; ntile is opt-in via env."""
  monkeypatch.delenv("HPCPERFSTATS_LARGE_JOB_TIME_SQL", raising=False)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg

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
  import hpcperfstats.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_large_job_host_data_row_threshold() == 1_500_000
  assert cfg.get_large_job_time_buckets() == 2048
  assert cfg.get_large_job_window_row_count_cache_ttl() == 300


def test_sync_pipeline_tunable_defaults_and_overrides(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_sync_ingest_queue_max_size() == 2000
  assert cfg.get_sync_archive_queue_max_size() == 1000
  assert cfg.get_sync_archive_retry_max_attempts() == 5
  assert cfg.get_sync_archive_retry_backoff_base_seconds() == 1.0
  assert cfg.get_sync_archive_retry_backoff_max_seconds() == 60.0
  assert cfg.get_sync_checkpoint_flush_batch_size() == 100

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\n"
      "sync_ingest_queue_max_size = 111\n"
      "sync_archive_queue_max_size = 222\n"
      "sync_archive_retry_max_attempts = 7\n"
      "sync_archive_retry_backoff_base_seconds = 2.5\n"
      "sync_archive_retry_backoff_max_seconds = 12.5\n"
      "sync_checkpoint_flush_batch_size = 42",
  )
  with open(temp_ini, "w") as f:
    f.write(content)

  importlib.reload(cfg)
  assert cfg.get_sync_ingest_queue_max_size() == 111
  assert cfg.get_sync_archive_queue_max_size() == 222
  assert cfg.get_sync_archive_retry_max_attempts() == 7
  assert cfg.get_sync_archive_retry_backoff_base_seconds() == 2.5
  assert cfg.get_sync_archive_retry_backoff_max_seconds() == 12.5
  assert cfg.get_sync_checkpoint_flush_batch_size() == 42


def test_sync_phase2_feature_flags_and_shards(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg

  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 16)
  assert cfg.get_sync_write_lock_shards() == 1
  assert cfg.get_sync_enable_db_writer_pipeline() is False
  assert cfg.get_sync_enable_ingest_first_durability_mode() is False

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\n"
      "sync_write_lock_shards = 4\n"
      "sync_enable_db_writer_pipeline = yes\n"
      "sync_enable_ingest_first_durability_mode = true",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_sync_write_lock_shards() == 4
  assert cfg.get_sync_enable_db_writer_pipeline() is True
  assert cfg.get_sync_enable_ingest_first_durability_mode() is True


def test_sync_db_writer_pool_defaults_and_cap(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_sync_db_writer_pool_processes(ingest_processes=8) == 4

  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\nsync_db_writer_pool_multiplier = 0.75\nsync_db_writer_pool_cap = 3",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_sync_db_writer_pool_processes(ingest_processes=8) == 3


def test_conf_parser_defaults_audit_snapshot(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  snapshot = cfg.get_conf_parser_defaults_audit_snapshot()
  assert "platform_constraints" in snapshot
  assert "sync_throughput" in snapshot
  assert "overlap_contention" in snapshot
  assert "stability" in snapshot
  assert snapshot["sync_throughput"]["sync_budget_ingest_ratio"] == 0.60
