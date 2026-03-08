"""Unit tests for conf_parser with a temporary INI file.

"""
import os

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


def test_get_worker_thread_count(temp_ini, monkeypatch):
  """get_worker_thread_count returns total_cores // divisor, clamped to at least 1."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  # temp_ini has total_cores = 4
  assert cfg.get_worker_thread_count(4) == 1
  assert cfg.get_worker_thread_count(2) == 2
  assert cfg.get_worker_thread_count(8) == 1  # 4//8 = 0 -> clamped to 1


def test_get_memcached_location_default(temp_ini, monkeypatch):
  """get_memcached_location returns default when CACHE section missing."""
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_memcached_location() == "127.0.0.1:11211"


def test_get_memcached_location_from_config(temp_ini, monkeypatch):
  """get_memcached_location returns value from [CACHE] when set."""
  with open(temp_ini) as f:
    content = f.read()
  content += "\n[CACHE]\nmemcached_location = 192.168.1.1:11211\n"
  with open(temp_ini, "w") as f:
    f.write(content)
  monkeypatch.setenv("HPCPERFSTATS_INI", temp_ini)
  import importlib
  import hpcperfstats.conf_parser as cfg
  importlib.reload(cfg)
  assert cfg.get_memcached_location() == "192.168.1.1:11211"
