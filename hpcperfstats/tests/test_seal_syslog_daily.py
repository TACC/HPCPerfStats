"""Tests for seal_syslog_daily."""

import importlib
import tarfile
from datetime import date

def _write_ini(path, data_dir):
  path.write_text(
      "[DEFAULT]\ndebug = no\nsecret_key = x\nhost_name_ext = local\n"
      "restricted_queue_keywords =\nmachine = test\nserver = test\n"
      "data_dir = %s\nstaff_email_domain = local\ntimezone = UTC\n"
      "total_cores = 4\n"
      "[PORTAL]\ndbname = test\nusername = u\npassword = p\nport = 5432\n"
      "host = localhost\narchive_dir = /tmp\nacct_path = /tmp\n"
      "daily_archive_dir = /tmp\nengine_name = django.db.backends.postgresql\n"
      "[RMQ]\nrmq_server = localhost\nrmq_queue = test\n"
      "[XALT]\nxalt_engine = django.db.backends.sqlite3\nxalt_name = xalt\n"
      "xalt_user = u\nxalt_password = p\nxalt_host = localhost\n"
      "[OAUTH2]\nclient_id = id\nclient_key = key\n"
      "authorize_url = http://localhost\noauth_base_url = http://localhost\n"
      % data_dir,
      encoding="utf-8",
  )


def test_seal_day_writes_tar_and_removes_sources(tmp_path, monkeypatch):
  root = tmp_path / "data"
  root.mkdir()
  _write_ini(tmp_path / "t.ini", str(root))
  cur = root / "logs" / "current"
  cur.mkdir(parents=True)
  arch = root / "logs" / "log_archive"
  arch.mkdir(parents=True)
  (cur / "n1.20240102.log").write_text("line1\n", encoding="utf-8")
  (cur / "n2.20240102.log").write_text("line2\n", encoding="utf-8")
  (cur / "n3.20240103.log").write_text("keep\n", encoding="utf-8")

  monkeypatch.setenv("HPCPERFSTATS_INI", str(tmp_path / "t.ini"))
  import hpcperfstats.conf_parser as cfg
  import hpcperfstats.seal_syslog_daily as seal
  importlib.reload(cfg)
  importlib.reload(seal)

  logs = []
  assert seal.seal_day(date(2024, 1, 2), log_fn=logs.append) is True
  tar_path = arch / "2024-01-02-syslog.tar.gz"
  assert tar_path.is_file()
  with tarfile.open(tar_path, "r:gz") as tf:
    names = sorted(m.name for m in tf.getmembers() if m.isfile())
  assert names == ["syslog/n1.20240102.log", "syslog/n2.20240102.log"]
  assert not (cur / "n1.20240102.log").exists()
  assert not (cur / "n2.20240102.log").exists()
  assert (cur / "n3.20240103.log").exists()


def test_seal_day_removes_leftovers_when_valid_tar_exists(tmp_path, monkeypatch):
  root = tmp_path / "data"
  root.mkdir()
  _write_ini(tmp_path / "t.ini", str(root))
  cur = root / "logs" / "current"
  cur.mkdir(parents=True)
  arch = root / "logs" / "log_archive"
  arch.mkdir(parents=True)
  tar_path = arch / "2024-01-02-syslog.tar.gz"
  with tarfile.open(tar_path, "w:gz") as tf:
    pass
  (cur / "n1.20240102.log").write_text("orphan\n", encoding="utf-8")

  monkeypatch.setenv("HPCPERFSTATS_INI", str(tmp_path / "t.ini"))
  import hpcperfstats.conf_parser as cfg
  import hpcperfstats.seal_syslog_daily as seal
  importlib.reload(cfg)
  importlib.reload(seal)

  assert seal.seal_day(date(2024, 1, 2), log_fn=lambda *_a, **_k: None) is True
  assert not (cur / "n1.20240102.log").exists()
