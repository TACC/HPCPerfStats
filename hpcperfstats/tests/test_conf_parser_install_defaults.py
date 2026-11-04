"""Install defaults: a minimal hpcperfstats.ini resolves the compose-stack keys.

data_dir, debug, the PostgreSQL connection, RabbitMQ, the archive paths and
the Valkey cache URL all have code defaults in ``INI_OPTION_DEFAULTS``. Path
defaults interpolate ``%(data_dir)s`` / ``%(machine)s`` like ini values do.
"""

import importlib
import os

import pytest


def _load(tmp_path, monkeypatch, text):
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(text)
  monkeypatch.setenv("HPCPERFSTATS_INI", str(ini))
  import hpcperfstats.dbload.lib.conf_parser as cfg

  importlib.reload(cfg)
  return cfg


def test_minimal_ini_resolves_every_install_default(tmp_path, monkeypatch):
  cfg = _load(tmp_path, monkeypatch, "[DEFAULT]\nmachine = c1\n")

  assert cfg.get_data_dir_path() == "/hpcperfstats/"
  assert cfg.get_debug() is False
  assert cfg.get_engine_name() == "django.db.backends.postgresql"
  assert cfg.get_db_name() == "hpcperfstats"
  assert cfg.get_username() == "hpcperfstats"
  assert cfg.get_password() == "hpcperfstats"
  assert cfg.get_host() == "/run/postgresql"
  assert cfg.get_port() == "5432"
  assert cfg.get_rmq_server() == "rabbitmq"
  assert cfg.get_rmq_queue() == "c1"
  assert (
    os.path.normpath(cfg.get_accounting_path()) == "/hpcperfstats/accounting"
  )
  assert cfg.get_archive_dir_path() == "/hpcperfstats/archive"
  assert cfg.get_daily_archive_dir_path() == "/hpcperfstats/daily_archive"
  assert cfg.get_valkey_location() == "unix:///run/valkey/valkey.sock?db=1"


def test_path_defaults_interpolate_the_ini_data_dir(tmp_path, monkeypatch):
  cfg = _load(
    tmp_path,
    monkeypatch,
    "[DEFAULT]\nmachine = c1\ndata_dir = /srv/hps\n[PIPELINE]\n",
  )

  assert cfg.get_archive_dir_path() == "/srv/hps/archive"
  assert cfg.get_daily_archive_dir_path() == "/srv/hps/daily_archive"
  assert cfg.get_accounting_path() == "/srv/hps/accounting"


def test_ini_values_and_legacy_sections_beat_defaults(tmp_path, monkeypatch):
  cfg = _load(
    tmp_path,
    monkeypatch,
    "[DEFAULT]\nmachine = c1\ndebug = yes\n"
    "[PORTAL]\nhost = legacy-db\narchive_dir = /legacy/archive\n"
    "[RMQ]\nrmq_server = mq.example\n"
    "[CACHE]\nvalkey_location = valkey://valkey:6379/1\n",
  )

  assert cfg.get_debug() is True
  assert cfg.get_host() == "legacy-db"
  assert cfg.get_archive_dir_path() == "/legacy/archive"
  assert cfg.get_rmq_server() == "mq.example"
  assert cfg.get_valkey_location() == "valkey://valkey:6379/1"


def test_rmq_queue_default_still_needs_machine(tmp_path, monkeypatch):
  import configparser

  cfg = _load(tmp_path, monkeypatch, "[DEFAULT]\ndata_dir = /x\n")

  with pytest.raises(configparser.InterpolationMissingOptionError):
    cfg.get_rmq_queue()
