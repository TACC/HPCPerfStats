"""scripts/rewrite_hpcperfstats_ini.py: site ini rewritten onto the example."""

import configparser
import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "rewrite_hpcperfstats_ini.py"
_EXAMPLE = _REPO / "hpcperfstats.ini.example"


def _load_cli():
  spec = importlib.util.spec_from_file_location("rewrite_ini_cli", _SCRIPT)
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = mod
  spec.loader.exec_module(mod)
  return mod


_SITE = """\
[DEFAULT]
machine = frontera
host_name_ext = %(machine)s.tacc.utexas.edu
data_dir = /hpcperfstats/
server = stats.frontera.tacc.utexas.edu
restricted_queue_keywords = ['secure']
debug = no
staff_email_domain = tacc.utexas.edu
timezone = America/Chicago
secret_key = s3cr3t
engine_name = django.db.backends.postgresql
dbname = hpcperfstats
username = hpcperfstats
password = hunter2
port = 5432

[PORTAL]
host = db
gunicorn_workers = 16
retired_knob = 1

[PIPELINE]
acct_path = %(data_dir)s/accounting
archive_dir = /bigdisk/archive
daily_archive_dir = %(data_dir)s/daily_archive
archive_seal_parallel_workers = 2.0

[RMQ]
rmq_server = rabbitmq
rmq_queue = %(machine)s

[OAUTH2]
client_id = abc
client_key = def
oauth_base_url = https://tacc.tapis.io/v3
"""


def _rewrite(tmp_path):
  cli = _load_cli()
  site = configparser.ConfigParser(
    interpolation=None, default_section=cli.NO_DEFAULT_SECTION
  )
  site.read_string(_SITE)
  return cli, cli.rewrite_from_example(site, _EXAMPLE.read_text())


def _lines(text):
  return text.splitlines()


def test_non_default_site_values_are_active_lines(tmp_path):
  _cli, result = _rewrite(tmp_path)
  lines = _lines(result.text)
  for line in (
    "machine = frontera",
    "server = stats.frontera.tacc.utexas.edu",
    "secret_key = s3cr3t",
    "password = hunter2",
    "archive_dir = /bigdisk/archive",
    "client_id = abc",
  ):
    assert line in lines, line


def test_site_values_equal_to_defaults_stay_commented(tmp_path):
  _cli, result = _rewrite(tmp_path)
  lines = _lines(result.text)
  for key in (
    "data_dir",
    "debug",
    "dbname",
    "port",
    "acct_path",
    "daily_archive_dir",
    "rmq_server",
    "rmq_queue",
    "archive_seal_parallel_workers",
  ):
    assert not any(ln.startswith(f"{key} =") for ln in lines), key
    assert any(ln.startswith(f"# {key} =") for ln in lines), key


def test_legacy_section_keys_move_to_the_example_section(tmp_path):
  _cli, result = _rewrite(tmp_path)
  parsed = configparser.ConfigParser(interpolation=None)
  parsed.read_string(result.text)
  assert parsed.get("DEFAULT", "host") == "db"
  assert parsed.get("PORTAL", "gunicorn_workers") == "16"


def test_unknown_site_keys_are_reported_not_written(tmp_path):
  _cli, result = _rewrite(tmp_path)
  assert ("PORTAL", "retired_knob") in result.dropped
  assert "retired_knob" not in result.text


def test_non_default_value_sits_under_its_commented_default(tmp_path):
  _cli, result = _rewrite(tmp_path)
  lines = _lines(result.text)
  for default_line, site_line in (
    ("# password = hpcperfstats", "password = hunter2"),
    ("# host = /run/postgresql", "host = db"),
    ("# archive_dir = %(data_dir)s/archive", "archive_dir = /bigdisk/archive"),
    ("# gunicorn_workers = 32", "gunicorn_workers = 16"),
  ):
    assert lines[lines.index(site_line) - 1] == default_line, site_line


def test_rest_of_example_is_kept_in_order(tmp_path):
  cli, result = _rewrite(tmp_path)
  out = iter(_lines(result.text))
  for line in _lines(_EXAMPLE.read_text()):
    if cli.ACTIVE_OPTION_RE.match(line):
      continue
    assert any(o == line for o in out), line


def test_effective_values_survive_the_rewrite(tmp_path):
  cli, result = _rewrite(tmp_path)
  site = configparser.ConfigParser(
    interpolation=None, default_section=cli.NO_DEFAULT_SECTION
  )
  site.read_string(_SITE)
  assert cli.changed_effective_values(site, result) == []
  for _section, option in result.missing_required:
    assert option not in _SITE


def test_main_writes_backup_and_rewrites_in_place(tmp_path, capsys):
  cli = _load_cli()
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(_SITE)
  assert cli.main([str(ini), "--example", str(_EXAMPLE)]) == 0
  backups = list(tmp_path.glob("hpcperfstats.ini.bak.*"))
  assert len(backups) == 1 and backups[0].read_text() == _SITE
  assert "secret_key = s3cr3t" in ini.read_text()
  assert "retired_knob" in capsys.readouterr().out


def test_dry_run_leaves_the_ini_untouched(tmp_path):
  cli = _load_cli()
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(_SITE)
  assert cli.main([str(ini), "--example", str(_EXAMPLE), "--dry-run"]) == 0
  assert ini.read_text() == _SITE
  assert not list(tmp_path.glob("hpcperfstats.ini.bak.*"))


def test_multiline_site_value_round_trips(tmp_path):
  cli = _load_cli()
  site = cli.parse_ini_text(
    "[DEFAULT]\nmachine = m\n[SYSLOG]\nallow_from = 10.0.0.0/8\n  192.168.0.0/16\n"
  )
  result = cli.rewrite_from_example(site, _EXAMPLE.read_text())
  out = cli.parse_ini_text(result.text)
  assert out.get("SYSLOG", "allow_from") == "10.0.0.0/8\n192.168.0.0/16"


def test_missing_required_key_keeps_example_line_and_warns(tmp_path):
  cli = _load_cli()
  site = cli.parse_ini_text("[DEFAULT]\nmachine = m\n")
  result = cli.rewrite_from_example(site, _EXAMPLE.read_text())
  assert ("DEFAULT", "secret_key") in result.missing_required


@pytest.mark.parametrize(
  ("value", "default", "equal"),
  [("2", "2", True), ("2.0", "2", True), ("no", "no", True), ("3", "2", False)],
)
def test_value_equals_default(value, default, equal):
  assert _load_cli().value_equals_default(value, default) is equal


def _mode(path):
  return os.stat(path).st_mode & 0o7777


def test_rewrite_keeps_the_ini_mode_and_owner(tmp_path):
  cli = _load_cli()
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(_SITE)
  ini.chmod(0o640)
  before = os.stat(ini)
  old_umask = os.umask(0o022)
  try:
    assert cli.main([str(ini), "--example", str(_EXAMPLE)]) == 0
  finally:
    os.umask(old_umask)
  after = os.stat(ini)
  assert after.st_ino != before.st_ino  # replaced, not edited in place
  assert _mode(ini) == 0o640
  assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
  (backup,) = tmp_path.glob("hpcperfstats.ini.bak.*")
  assert _mode(backup) == 0o640
  bst = os.stat(backup)
  assert (bst.st_uid, bst.st_gid) == (before.st_uid, before.st_gid)
  assert backup.read_text() == _SITE


def test_output_file_gets_the_ini_permissions(tmp_path):
  cli = _load_cli()
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(_SITE)
  ini.chmod(0o600)
  out = tmp_path / "new.ini"
  args = [str(ini), "--example", str(_EXAMPLE), "--output", str(out)]
  assert cli.main(args) == 0
  assert _mode(out) == 0o600 and _mode(ini) == 0o600
  assert ini.read_text() == _SITE


def test_secret_file_is_never_world_readable_while_written(tmp_path):
  cli = _load_cli()
  path = tmp_path / "f.ini"
  seen = []
  real_write = os.write

  def spy(fd, data):
    seen.append(os.fstat(fd).st_mode & 0o777)
    return real_write(fd, data)

  original = os.write
  os.write = spy
  try:
    cli.write_with_permissions(path, "x = 1\n", os.stat(tmp_path))
  finally:
    os.write = original
  assert seen and all(m & 0o077 == 0 for m in seen)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can chown to anyone")
def test_owner_that_cannot_be_restored_is_reported(tmp_path, capsys):
  cli = _load_cli()
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(_SITE)
  st = os.stat(ini)
  fields = list(st)
  fields[4] = st.st_uid + 4242  # st_uid
  foreign = os.stat_result(fields)
  cli.write_with_permissions(tmp_path / "out.ini", "x = 1\n", foreign)
  assert "could not restore owner" in capsys.readouterr().err
