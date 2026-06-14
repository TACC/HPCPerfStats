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
  monkeypatch.setenv("ARCHIVE_POOL_PROCESS_CAP", "64")
  monkeypatch.delenv("SYNC_POOL_PROCESS_CAP", raising=False)
  monkeypatch.setenv("SYNC_ENABLE_CPUSET_PRIORITY_BUDGET", "0")
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


def test_get_archive_zstd_priority_defaults(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_nice() == 10
  assert cfg.get_archive_zstd_ionice_class() == 2
  assert cfg.get_archive_zstd_ionice_level() == 6
  assert cfg.get_archive_seal_parallel_workers() == 4
  assert cfg.get_archive_zstd_drop_page_cache() is True


def test_get_archive_zstd_drop_page_cache_opt_out(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
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


def test_get_archive_zstd_level_clamps(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
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


def test_get_archive_zstd_threads_and_maintenance_interval(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_threads() == 0
  assert cfg.get_archive_maintenance_interval_seconds() == 8 * 3600

  with open(temp_ini) as f:
    base = f.read()
  content = base.replace(
      "daily_archive_dir = /tmp",
      "daily_archive_dir = /tmp\n"
      "archive_zstd_threads = 4\n"
      "archive_maintenance_interval_seconds = 600",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_archive_zstd_threads() == 4
  assert cfg.get_archive_maintenance_interval_seconds() == 600.0


def test_get_archive_maintenance_interval_seconds_rejects_nonfinite_and_nonpositive(
    temp_ini, monkeypatch
):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_maintenance_interval_seconds() == 8 * 3600

  with open(temp_ini) as f:
    base = f.read()

  for raw in ("nan", "inf", "0", "-5", "not-a-number"):
    content = base.replace(
        "daily_archive_dir = /tmp",
        "daily_archive_dir = /tmp\narchive_maintenance_interval_seconds = %s"
        % raw,
    )
    with open(temp_ini, "w") as f:
      f.write(content)
    importlib.reload(cfg)
    assert cfg.get_archive_maintenance_interval_seconds() == 8 * 3600

  content = base.replace(
      "daily_archive_dir = /tmp",
      "daily_archive_dir = /tmp\narchive_maintenance_interval_seconds = 120",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_archive_maintenance_interval_seconds() == 120.0


def test_get_archive_maintenance_max_defer_seconds_defaults_and_override(
    temp_ini, monkeypatch
):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_maintenance_max_defer_seconds() == 3600.0

  with open(temp_ini) as f:
    base = f.read()
  content = base.replace(
      "daily_archive_dir = /tmp",
      "daily_archive_dir = /tmp\narchive_maintenance_max_defer_seconds = 90",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_archive_maintenance_max_defer_seconds() == 90.0


def test_get_archive_maintenance_max_defer_seconds_rejects_nonfinite_and_nonpositive(
    temp_ini, monkeypatch
):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_maintenance_max_defer_seconds() == 3600.0

  with open(temp_ini) as f:
    base = f.read()

  for raw in ("nan", "inf", "0", "-5", "not-a-number"):
    content = base.replace(
        "daily_archive_dir = /tmp",
        "daily_archive_dir = /tmp\narchive_maintenance_max_defer_seconds = %s"
        % raw,
    )
    with open(temp_ini, "w") as f:
      f.write(content)
    importlib.reload(cfg)
    assert cfg.get_archive_maintenance_max_defer_seconds() == 3600.0


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


def test_get_metrics_pool_process_cap_env_override(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  monkeypatch.setenv("METRICS_POOL_PROCESS_CAP", "7")
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_metrics_pool_process_cap() == 7


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
  assert cfg.get_parallel_db_prefetch_max_workers() == 4
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
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_api_small_executor_max_workers() == 3
  assert cfg.get_parallel_db_prefetch_max_workers() == 4


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


def test_sync_archive_pool_default_is_four_without_cpuset(temp_ini, monkeypatch):
  with open(temp_ini) as f:
    content = f.read()
  content = content.replace(
      "total_cores = 4",
      "total_cores = 4\narchive_pool_process_cap = 64",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("ARCHIVE_POOL_PROCESS_CAP", "64")
  monkeypatch.delenv("SYNC_POOL_PROCESS_CAP", raising=False)
  monkeypatch.setenv("SYNC_ENABLE_CPUSET_PRIORITY_BUDGET", "0")
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 64)
  assert cfg.get_sync_archive_pool_processes() == 4


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
  assert cfg.get_metrics_prewarm_backlog_cap() == 32
  assert cfg.get_metrics_prewarm_backpressure_wait_s() == 0.25
  assert cfg.get_metrics_scheduler_compute_threads() == 4
  assert cfg.get_metrics_run_poll_timeout_s() == 5.0
  assert cfg.get_metrics_run_stall_timeout_s() == 900.0
  assert cfg.get_metrics_run_per_job_timeout_s() == 0.0
  assert cfg.get_metrics_persist_statement_timeout_ms() == 120000
  assert cfg.get_metrics_persist_lock_timeout_ms() == 10000
  assert cfg.get_metrics_prewarm_retry_attempts() == 2
  assert cfg.get_metrics_proxy_reject_jid_batch_size() == 48
  assert cfg.get_metrics_scheduler_skip_prewarm() is False
  assert cfg.get_metrics_prewarm_drain_batch_budget_base_s() == 2.0
  assert cfg.get_metrics_prewarm_drain_batch_budget_max_s() == 60.0
  assert cfg.get_metrics_prewarm_drain_budget_per_successful_job_s() == 0.5
  assert cfg.get_metrics_compute_batch_max_window_seconds() == 0.0
  assert cfg.get_metrics_compute_batch_max_single_job_runtime_seconds() == 0.0
  assert cfg.get_metrics_compute_batch_unknown_runtime_seconds() == 172800.0
  assert cfg.get_metrics_compute_watchdog_seconds() == 120.0
  assert cfg.get_metrics_compute_total_watchdog_seconds() == 0.0
  assert cfg.get_metrics_deferred_not_ready_retry_seconds() == 10.0
  assert cfg.get_metrics_deferred_not_ready_max_retries() == 30
  assert cfg.get_metrics_deferred_not_ready_max_age_seconds() == 900.0
  assert cfg.get_metrics_deferred_not_ready_quarantine_seconds() == 300.0
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
      "metrics_prewarm_workers = 7\n"
      "metrics_prewarm_backlog_cap = 13\n"
      "metrics_prewarm_backpressure_wait_s = 0.75\n"
      "metrics_scheduler_compute_threads = 6\n"
      "metrics_run_poll_timeout_s = 1.5\n"
      "metrics_run_stall_timeout_s = 120\n"
      "metrics_persist_statement_timeout_ms = 45000\n"
      "metrics_persist_lock_timeout_ms = 7000\n"
      "metrics_prewarm_retry_attempts = 5\n"
      "metrics_proxy_reject_jid_batch_size = 32\n"
      "metrics_scheduler_skip_prewarm = yes",
  )
  with open(temp_ini, "w") as f:
    f.write(content)
  importlib.reload(cfg)
  assert cfg.get_metrics_scheduler_skip_prewarm() is True
  assert cfg.get_metrics_scheduler_mode() == "global_fifo"
  assert cfg.get_metrics_scheduler_prefetch_chunks() == 3
  assert cfg.get_metrics_scheduler_ready_queue_target() == 111
  assert cfg.get_metrics_plot_prewarm_mode() == "inline"
  assert cfg.get_metrics_prewarm_workers() == 7
  assert cfg.get_metrics_prewarm_backlog_cap() == 13
  assert cfg.get_metrics_prewarm_backpressure_wait_s() == 0.75
  assert cfg.get_metrics_scheduler_compute_threads() == 6
  assert cfg.get_metrics_run_poll_timeout_s() == 1.5
  assert cfg.get_metrics_run_stall_timeout_s() == 120.0
  assert cfg.get_metrics_persist_statement_timeout_ms() == 45000
  assert cfg.get_metrics_persist_lock_timeout_ms() == 7000
  assert cfg.get_metrics_prewarm_retry_attempts() == 5
  assert cfg.get_metrics_proxy_reject_jid_batch_size() == 32
  monkeypatch.setenv("HPCPERFSTATS_METRICS_SCHEDULER_MODE", "strict_date")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PLOT_PREWARM_MODE", "pipeline_required")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PREWARM_BACKLOG_CAP", "9")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PREWARM_BACKPRESSURE_WAIT_S", "1.25")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_RUN_POLL_TIMEOUT_S", "2.5")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_RUN_STALL_TIMEOUT_S", "45")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PERSIST_STATEMENT_TIMEOUT_MS", "9000")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PERSIST_LOCK_TIMEOUT_MS", "3000")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PREWARM_DRAIN_BATCH_BUDGET_S", "3.5")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_COMPUTE_WATCHDOG_S", "90")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_COMPUTE_TOTAL_WATCHDOG_S", "600")
  monkeypatch.setenv("HPCPERFSTATS_METRICS_DEFERRED_NOT_READY_RETRY_S", "15")
  assert cfg.get_metrics_scheduler_mode() == "strict_date"
  assert cfg.get_metrics_plot_prewarm_mode() == "pipeline_required"
  assert cfg.get_metrics_prewarm_backlog_cap() == 9
  assert cfg.get_metrics_prewarm_backpressure_wait_s() == 1.25
  assert cfg.get_metrics_run_poll_timeout_s() == 2.5
  assert cfg.get_metrics_run_stall_timeout_s() == 45.0
  assert cfg.get_metrics_persist_statement_timeout_ms() == 9000
  assert cfg.get_metrics_persist_lock_timeout_ms() == 3000
  assert cfg.get_metrics_prewarm_drain_batch_budget_base_s() == 3.5
  assert cfg.get_metrics_compute_watchdog_seconds() == 90.0
  assert cfg.get_metrics_compute_total_watchdog_seconds() == 600.0
  assert cfg.get_metrics_deferred_not_ready_retry_seconds() == 15.0


def test_get_metrics_readiness_window_coverage_defaults(temp_ini, monkeypatch):
  """Default coverage gate: require=yes, margins=600s."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg

  importlib.reload(cfg)
  assert cfg.get_metrics_readiness_require_window_coverage() is True
  assert cfg.get_metrics_readiness_start_margin_seconds() == 600.0
  assert cfg.get_metrics_readiness_end_margin_seconds() == 600.0


def test_get_metrics_readiness_window_coverage_ini_override(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg

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
  import hpcperfstats.conf_parser as cfg

  monkeypatch.delenv("HPCPERFSTATS_METRICS_PER_JID_PHASE_LOG", raising=False)
  assert cfg.get_metrics_per_jid_phase_diagnostics_enabled() is False
  monkeypatch.setenv("HPCPERFSTATS_METRICS_PER_JID_PHASE_LOG", "true")
  assert cfg.get_metrics_per_jid_phase_diagnostics_enabled() is True


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
  assert cfg.get_sync_pool_stall_abort_after_timeouts() == 192
  assert cfg.get_sync_pool_poll_timeout_s() == 5.0
  assert cfg.get_sync_ingest_per_file_timeout_s() == 900.0
  assert cfg.get_sync_ingest_per_file_timeout_max_s() == 14400.0
  assert cfg.get_sync_ingest_per_file_timeout_s_per_mib() == pytest.approx(
      13500.0 / 5120.0,
  )
  assert cfg.get_sync_archive_members_cache_enabled() is True
  assert cfg.get_sync_archive_members_cache_max_entries() == 64
  assert cfg.get_sync_archive_members_redis_enabled() is True
  assert cfg.get_sync_archive_members_redis_ttl_seconds() == 86400
  assert cfg.get_sync_archive_members_redis_populate_lock_seconds() == 3600
  assert cfg.get_sync_archive_members_redis_populate_stall_seconds() == 120
  assert cfg.get_sync_archive_members_redis_populate_max_seconds() == 7200
  assert cfg.get_sync_archive_members_redis_wait_poll_seconds() == 0.25
  assert cfg.get_sync_archive_members_redis_hset_batch_size() == 500
  assert cfg.get_sync_archive_members_redis_max_payload_bytes() == 8388608

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
      "sync_checkpoint_flush_batch_size = 42\n"
      "sync_pool_stall_abort_after_timeouts = 90\n"
      "sync_pool_poll_timeout_s = 2.5\n"
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
      "sync_archive_members_redis_wait_poll_seconds = 0.5\n"
      "sync_archive_members_redis_hset_batch_size = 100\n"
      "sync_archive_members_redis_max_payload_bytes = 1048576",
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
  assert cfg.get_sync_pool_stall_abort_after_timeouts() == 90
  assert cfg.get_sync_pool_poll_timeout_s() == 2.5
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
  assert cfg.get_sync_archive_members_redis_wait_poll_seconds() == 0.5
  assert cfg.get_sync_archive_members_redis_hset_batch_size() == 100
  assert cfg.get_sync_archive_members_redis_max_payload_bytes() == 1048576
  monkeypatch.setenv("HPCPERFSTATS_SYNC_INGEST_PER_FILE_TIMEOUT_S", "45")
  assert cfg.get_sync_ingest_per_file_timeout_s() == 45.0


def test_sync_host_itimes_cache_max_timestamps_per_entry(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg

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


def test_sync_write_lock_shards_auto_scales_to_eight_at_forty_cores(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg

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
  import hpcperfstats.conf_parser as cfg

  importlib.reload(cfg)
  monkeypatch.setattr(cfg.os, "cpu_count", lambda: 16)
  assert cfg.get_sync_write_lock_shards() == 1
  assert cfg.get_sync_enable_db_writer_pipeline() is False
  # Ingest-first durability is on by default (fallback=yes in conf_parser).
  assert cfg.get_sync_enable_ingest_first_durability_mode() is True

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
  assert cfg.get_sync_db_writer_pool_processes(ingest_processes=8) == 6

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
  assert snapshot["stability"]["parallel_db_prefetch_max"] == 4


def test_get_syslog_allow_from_ipv4_networks_empty_default(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.format_cors_allowed_origins_csv_from_ini() == "https://test"


def test_format_cors_allowed_origins_csv_empty_when_debug(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  with open(temp_ini) as f:
    content = f.read().replace("debug = no", "debug = yes")
  with open(temp_ini, "w") as f:
    f.write(content)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.format_cors_allowed_origins_csv_from_ini() == ""


def test_format_cors_allowed_origins_multiple_hosts(temp_ini, monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  with open(temp_ini) as f:
    content = f.read().replace("server = test\n", "server = a.example, b.example\n")
  with open(temp_ini, "w") as f:
    f.write(content)
  import importlib
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
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
  import hpcperfstats.conf_parser as cfg
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
      "sync_archive_require_db_head_ingest = no\n"
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
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_sync_archive_require_db_head_ingest() is False


def test_archive_janitor_and_dispatch_defaults(temp_ini, monkeypatch):
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_janitor_budget_seconds() == 30.0
  assert cfg.get_archive_janitor_days_per_tick() == 2
  assert cfg.get_archive_janitor_debt_high_watermark() == 50
  assert cfg.get_archive_janitor_debt_burst_factor() == 1.5
  assert cfg.get_archive_janitor_debt_max_entries() == 200
  assert cfg.get_archive_janitor_raw_paths_per_tick() == 1000
  assert cfg.get_sync_unparsable_raw_quarantine_max_per_tick() == 50
  assert cfg.get_sync_startup_raw_removal_preflight() is True
  assert cfg.get_sync_startup_raw_removal_verify_budget_seconds() == 60.0
  assert cfg.get_sync_startup_raw_removal_verify_days_per_slice() == 5
  assert cfg.get_sync_startup_raw_removal_max_deletes_per_pass() == 0
  assert cfg.get_sync_startup_drain_day_close_before_ingest() is True
  assert cfg.get_sync_day_close_candidate_report() is True
  assert cfg.get_sync_startup_day_close_preflight() is True
  assert cfg.get_sync_startup_day_close_budget_seconds() == 300.0
  assert cfg.get_sync_startup_day_close_max_inflight() == cfg.get_archive_seal_parallel_workers()
  assert cfg.get_sync_startup_day_close_days_per_slice() == cfg.get_sync_startup_day_close_max_inflight()
  assert cfg.get_sync_day_close_async_workers() == 1
  assert cfg.get_sync_day_close_max_inflight() == 1
  assert cfg.get_sync_day_close_raw_removal_wait_seconds() == 3600.0
  assert cfg.get_sync_day_close_async_stale_seconds() == 7200.0
  assert cfg.get_sync_startup_day_close_backoff_seconds() == 30.0
  assert cfg.get_sync_day_close_raw_removal_preflight() is True
  assert cfg.get_sync_day_close_raw_removal_verify_budget_seconds() == 30.0
  assert cfg.get_sync_day_close_raw_removal_max_deletes_per_pass() == 0
  assert cfg.get_archive_keep_uncompressed_tar() is False
  assert cfg.get_archive_today_uncompressed_tar_grace_hours() == 8.0
  assert cfg.get_archive_maintenance_idle_seconds() == 300.0
  assert cfg.get_sync_archive_max_inflight_jobs() == 2
  assert cfg.get_sync_archive_worker_stall_seconds() == 600.0
  assert cfg.get_sync_enable_ingest_first_durability_mode() is True


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
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_archive_dir_path() == "/legacy/archive"
  assert cfg.get_accounting_path() == "/legacy/acct"
  assert cfg.get_daily_archive_dir_path() == "/legacy/daily"
