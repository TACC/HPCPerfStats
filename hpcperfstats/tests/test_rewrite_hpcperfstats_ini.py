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


def _rewrite_text(tmp_path, capsys, site_text):
  """Rewrite *site_text* via main(); return (stdout, rewritten text)."""
  cli = _load_cli()
  ini = tmp_path / "hpcperfstats.ini"
  ini.write_text(site_text)
  out = tmp_path / "out.ini"
  assert (
    cli.main([str(ini), "--example", str(_EXAMPLE), "--output", str(out)]) == 0
  )
  return capsys.readouterr().out, out.read_text()


def _migrate_site(tmp_path, capsys, cache_lines):
  """Rewrite _SITE plus a [CACHE] block via main(); return (stdout, text)."""
  return _rewrite_text(tmp_path, capsys, _SITE + "\n[CACHE]\n" + cache_lines)


def test_redis_location_compose_socket_becomes_commented_valkey_default(
  tmp_path, capsys
):
  stdout, text = _migrate_site(
    tmp_path, capsys, "redis_location = unix:///run/redis/redis.sock?db=1\n"
  )
  assert "renamed: [CACHE] redis_location -> valkey_location" in stdout
  assert "redis_location" not in text
  assert "# valkey_location = unix:///run/valkey/valkey.sock?db=1" in _lines(
    text
  )
  assert not any(line.startswith("valkey_location") for line in _lines(text))


def test_redis_location_non_default_db_keeps_index(tmp_path, capsys):
  _stdout, text = _migrate_site(
    tmp_path, capsys, "redis_location = unix:///run/redis/redis.sock?db=3\n"
  )
  assert "valkey_location = unix:///run/valkey/valkey.sock?db=3" in _lines(text)


def test_redis_location_compose_host_maps_to_valkey_host(tmp_path, capsys):
  _stdout, text = _migrate_site(
    tmp_path, capsys, "redis_location = redis://redis:6379/1\n"
  )
  assert "valkey_location = valkey://valkey:6379/1" in _lines(text)


@pytest.mark.parametrize(
  ("old", "new"),
  [
    (
      "redis://u:p@cache.example.org:6380/2",
      "valkey://u:p@cache.example.org:6380/2",
    ),
    ("rediss://cache.example.org:6380/0", "valkeys://cache.example.org:6380/0"),
  ],
)
def test_redis_location_external_url_keeps_host(tmp_path, capsys, old, new):
  """External caches must survive the rename (no silent fall back to socket)."""
  _stdout, text = _migrate_site(tmp_path, capsys, f"redis_location = {old}\n")
  assert f"valkey_location = {new}" in _lines(text)


def test_valkey_location_wins_over_redis_location(tmp_path, capsys):
  stdout, text = _migrate_site(
    tmp_path,
    capsys,
    "redis_location = redis://old:6379/1\n"
    "valkey_location = valkey://new:6379/1\n",
  )
  assert "renamed:" not in stdout
  assert "dropped (not in example): [CACHE] redis_location" in stdout
  assert "valkey_location = valkey://new:6379/1" in _lines(text)
  assert "redis_location" not in text


def test_site_without_cache_section_reports_no_rename(tmp_path, capsys):
  cli = _load_cli()
  site = cli.parse_ini_text(_SITE)
  assert cli.migrate_renamed_options(site) == []


_PAGE_CACHE_RENAMED = (
  "renamed: [{}] archive_zstd_drop_page_cache -> sync_pipeline_drop_page_cache"
)


def _site_with(section, lines):
  """Return _SITE with *lines* added at the top of its [*section*] block."""
  return _SITE.replace(f"[{section}]\n", f"[{section}]\n{lines}", 1)


@pytest.mark.parametrize("section", ["PIPELINE", "DEFAULT"])
def test_page_cache_alias_opt_out_becomes_new_key(tmp_path, capsys, section):
  """A site that turned the hints off keeps them off (no silent re-enable)."""
  stdout, text = _rewrite_text(
    tmp_path,
    capsys,
    _site_with(section, "archive_zstd_drop_page_cache = no\n"),
  )
  assert _PAGE_CACHE_RENAMED.format(section) in stdout
  assert "archive_zstd_drop_page_cache" not in text
  out = _load_cli().parse_ini_text(text)
  assert out.get("PIPELINE", "sync_pipeline_drop_page_cache") == "no"
  assert not out.has_option("DEFAULT", "sync_pipeline_drop_page_cache")


def test_page_cache_alias_default_value_stays_commented(tmp_path, capsys):
  stdout, text = _rewrite_text(
    tmp_path,
    capsys,
    _site_with("PIPELINE", "archive_zstd_drop_page_cache = yes\n"),
  )
  assert _PAGE_CACHE_RENAMED.format("PIPELINE") in stdout
  assert "archive_zstd_drop_page_cache" not in text
  assert "# sync_pipeline_drop_page_cache = yes" in _lines(text)
  assert not any(
    line.startswith("sync_pipeline_drop_page_cache") for line in _lines(text)
  )


@pytest.mark.parametrize("old_section", ["PIPELINE", "DEFAULT"])
def test_page_cache_new_key_wins_over_alias(tmp_path, capsys, old_section):
  site = _site_with(old_section, "archive_zstd_drop_page_cache = yes\n")
  stdout, text = _rewrite_text(
    tmp_path,
    capsys,
    site.replace(
      "[PIPELINE]\n", "[PIPELINE]\nsync_pipeline_drop_page_cache = no\n", 1
    ),
  )
  assert "renamed:" not in stdout
  assert (
    f"dropped (not in example): [{old_section}] archive_zstd_drop_page_cache"
    in stdout
  )
  assert "sync_pipeline_drop_page_cache = no" in _lines(text)
  assert "archive_zstd_drop_page_cache" not in text


_OLD_LAYOUT_BASE = """\
[DEFAULT]
machine = m
data_dir = /hpcperfstats/
secret_key = s3cr3t
"""

# (old section, home section, key, site value, getter, getter result)
_PORTAL_DB_KEYS = [
  ("PORTAL", "DEFAULT", "dbname", "statsdb", "get_db_name", "statsdb"),
  ("PORTAL", "DEFAULT", "engine_name", "x.backend", "get_engine_name", None),
  ("PORTAL", "DEFAULT", "username", "statsuser", "get_username", None),
  ("PORTAL", "DEFAULT", "password", "hunter2", "get_password", None),
  ("PORTAL", "DEFAULT", "host", "db.example.org", "get_host", None),
  ("PORTAL", "DEFAULT", "port", "5433", "get_port", None),
]
_PORTAL_ARCHIVE_KEYS = [
  ("PORTAL", "PIPELINE", "archive_dir", "/big/archive", "get_archive_dir_path"),
  ("PORTAL", "PIPELINE", "acct_path", "/acct/sacct", "get_accounting_path"),
  (
    "PORTAL",
    "PIPELINE",
    "daily_archive_dir",
    "/big/daily",
    "get_daily_archive_dir_path",
  ),
  (
    "PORTAL",
    "PIPELINE",
    "archive_keep_uncompressed_tar",
    "yes",
    "get_archive_keep_uncompressed_tar",
    True,
  ),
  (
    "PORTAL",
    "PIPELINE",
    "archive_zstd_level",
    "12",
    "get_archive_zstd_level",
    12,
  ),
  (
    "PORTAL",
    "PIPELINE",
    "archive_zstd_threads",
    "8",
    "get_archive_zstd_threads",
    8,
  ),
  (
    "PORTAL",
    "PIPELINE",
    "ingest_zstd_threads",
    "6",
    "get_ingest_zstd_threads",
    6,
  ),
  (
    "PORTAL",
    "PIPELINE",
    "archive_seal_parallel_workers",
    "3",
    "get_archive_seal_parallel_workers",
    3,
  ),
]
_DEFAULT_PIPELINE_KEYS = [
  (
    "DEFAULT",
    "PIPELINE",
    "sync_ingest_pool_processes",
    "48",
    "get_sync_ingest_pool_processes",
    48,
  ),
  (
    "DEFAULT",
    "PIPELINE",
    "metrics_pool_processes",
    "16",
    "get_metrics_pool_processes",
    16,
  ),
]
_DEFAULT_PORTAL_KEYS = [
  ("DEFAULT", "PORTAL", "gunicorn_workers", "12", "get_gunicorn_workers", 12),
  ("DEFAULT", "PORTAL", "db_conn_max_age", "30", "get_db_conn_max_age", 30),
  (
    "DEFAULT",
    "PORTAL",
    "db_statement_timeout_ms",
    "60000",
    "get_db_statement_timeout_ms",
    60000,
  ),
  (
    "DEFAULT",
    "PORTAL",
    "db_idle_in_transaction_session_timeout_ms",
    "100000",
    "get_db_idle_in_transaction_session_timeout_ms",
    100000,
  ),
  (
    "DEFAULT",
    "PORTAL",
    "cors_origin_scheme",
    "http",
    "get_cors_origin_scheme",
    "http",
  ),
  (
    "DEFAULT",
    "PORTAL",
    "parallel_db_prefetch_max",
    "6",
    "get_parallel_db_prefetch_max",
    6,
  ),
  (
    "DEFAULT",
    "PORTAL",
    "api_small_executor_max_workers",
    "5",
    "get_api_small_executor_max_workers",
    5,
  ),
]
_GETTER_ENV_OVERRIDES = (
  "DJANGO_CONN_MAX_AGE",
  "DJANGO_DB_STATEMENT_TIMEOUT_MS",
  "DJANGO_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS",
  "PARALLEL_DB_PREFETCH_MAX",
)


def _old_layout_site(rows):
  """Old-layout ini text with each row's key under its old section."""
  blocks = {}
  for old, _home, key, value, *_getter in rows:
    blocks.setdefault(old, []).append(f"{key} = {value}\n")
  text = _OLD_LAYOUT_BASE + "".join(blocks.pop("DEFAULT", []))
  return text + "".join(
    f"\n[{section}]\n" + "".join(lines) for section, lines in blocks.items()
  )


def _assert_moved_and_read_back(monkeypatch, tmp_path, text, rows):
  """Each row's key sits only in its home section; conf_parser reads it."""
  from hpcperfstats.dbload.lib import conf_parser

  out = _load_cli().parse_ini_text(text)
  rewritten = tmp_path / "rewritten.ini"
  rewritten.write_text(text)
  for name in _GETTER_ENV_OVERRIDES:
    monkeypatch.delenv(name, raising=False)
  monkeypatch.setenv("HPCPERFSTATS_INI", str(rewritten))
  monkeypatch.setattr(conf_parser, "cfg", None)
  for old, home, key, value, getter, *expected in rows:
    assert out.get(home, key) == value, key
    assert not out.has_option(old, key) or old == home, key
    assert conf_parser._ini_get_from_registry(home, key) == value, key
    want = expected[0] if expected and expected[0] is not None else value
    assert getattr(conf_parser, getter)() == want, key


@pytest.mark.parametrize(
  "rows",
  [
    _PORTAL_DB_KEYS,
    _PORTAL_ARCHIVE_KEYS,
    _DEFAULT_PIPELINE_KEYS,
    _DEFAULT_PORTAL_KEYS,
  ],
  ids=["portal-db", "portal-archive", "default-pipeline", "default-portal"],
)
def test_old_layout_keys_move_to_their_section(
  tmp_path, capsys, monkeypatch, rows
):
  """Keys conf_parser stopped reading in old sections keep their value."""
  site_text = _old_layout_site(rows)
  cli = _load_cli()
  site = cli.parse_ini_text(site_text)
  for _old, _home, key, value, *_getter in rows:
    assert cli.effective_values(site)[key] == value, key
  _stdout, text = _rewrite_text(tmp_path, capsys, site_text)
  _assert_moved_and_read_back(monkeypatch, tmp_path, text, rows)


def test_old_layout_site_with_every_legacy_case(tmp_path, capsys, monkeypatch):
  """No [PIPELINE]; DB/archive keys in [PORTAL]; tuning in [DEFAULT]."""
  rows = [
    *_PORTAL_DB_KEYS,
    *_PORTAL_ARCHIVE_KEYS,
    *_DEFAULT_PIPELINE_KEYS,
    *_DEFAULT_PORTAL_KEYS,
  ]
  site_text = _old_layout_site(rows).replace(
    "secret_key = s3cr3t\n",
    "secret_key = s3cr3t\narchive_zstd_drop_page_cache = no\n",
  )
  assert "[PIPELINE]" not in site_text
  cli = _load_cli()
  site = cli.parse_ini_text(site_text)
  cli.migrate_renamed_options(site)
  effective = cli.effective_values(site)
  for _old, _home, key, value, *_getter in rows:
    assert effective[key] == value, key
  assert effective["sync_pipeline_drop_page_cache"] == "no"
  stdout, text = _rewrite_text(tmp_path, capsys, site_text)
  assert _PAGE_CACHE_RENAMED.format("DEFAULT") in stdout
  assert "archive_zstd_drop_page_cache" not in text
  page_cache_row = (
    "DEFAULT",
    "PIPELINE",
    "sync_pipeline_drop_page_cache",
    "no",
    "get_sync_pipeline_drop_page_cache",
    False,
  )
  _assert_moved_and_read_back(
    monkeypatch, tmp_path, text, [*rows, page_cache_row]
  )


@pytest.mark.parametrize(
  ("site_text", "key", "value"),
  [
    # [DEFAULT] is the DB keys' own section; [PORTAL] was only a fallback.
    ("[PORTAL]\nport = 1\n[DEFAULT]\nport = 2\n", "port", "2"),
    # With [PIPELINE] present, [PIPELINE] inherited [DEFAULT] first.
    (
      "[PORTAL]\narchive_zstd_level = 5\n[DEFAULT]\narchive_zstd_level = 7\n"
      "[PIPELINE]\n",
      "archive_zstd_level",
      "7",
    ),
    # Without [PIPELINE], the [PORTAL] fallback (own value) came first.
    (
      "[DEFAULT]\narchive_zstd_level = 7\n[PORTAL]\narchive_zstd_level = 5\n",
      "archive_zstd_level",
      "5",
    ),
    # [PORTAL] tuning beats the [DEFAULT] fallback.
    (
      "[DEFAULT]\ngunicorn_workers = 8\n[PORTAL]\ngunicorn_workers = 4\n",
      "gunicorn_workers",
      "4",
    ),
  ],
  ids=["db-key", "pipeline-present", "pipeline-absent", "portal-key"],
)
def test_duplicate_old_layout_key_takes_the_old_parser_value(
  site_text, key, value
):
  """The effective-value check compares against the old fallback order."""
  cli = _load_cli()
  site = cli.parse_ini_text(site_text)
  result = cli.rewrite_from_example(site, _EXAMPLE.read_text())
  assert cli.effective_values(site)[key] == value
  assert (
    cli.parse_ini_text(result.text).get(cli._registry()[key][0], key) == value
  )
  assert cli.changed_effective_values(site, result) == []
