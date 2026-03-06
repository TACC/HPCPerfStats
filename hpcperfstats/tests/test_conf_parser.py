"""Unit tests for conf_parser with a temporary INI file.

AI generated.
"""
import os

import pytest


def test_config_path_from_env(temp_ini, monkeypatch):
  """Config is read from HPCPERFSTATS_INI when set.

    AI generated.
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

    AI generated.
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
