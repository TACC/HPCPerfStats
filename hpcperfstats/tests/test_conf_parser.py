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
