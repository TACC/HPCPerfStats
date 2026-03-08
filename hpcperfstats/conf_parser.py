"""Configuration parser for HPCPerfStats. Reads hpcperfstats.ini and exposes getters for portal, RMQ, XALT, and OAuth2 settings.

"""
import configparser
import os
from zoneinfo import ZoneInfo

cfg = configparser.ConfigParser()

# Config path: HPCPERFSTATS_INI env, or default. Override for testing.
_CONFIG_PATH = os.environ.get("HPCPERFSTATS_INI",
                              "/home/hpcperfstats/hpcperfstats.ini")
cfg.read(_CONFIG_PATH)


def _get(section, option):
  """Return config value for section/option. Single place for simple getters."""
  return cfg.get(section, option)


def get_db_connection_string():
  """Return a PostgreSQL connection string from PORTAL config (dbname, user, password, port, host)."""
  return "dbname={0} user={1} password={2} port={3} host={4}".format(
      _get('PORTAL', 'dbname'), _get('PORTAL', 'username'),
      _get('PORTAL', 'password'), _get('PORTAL', 'port'), _get('PORTAL', 'host'))


def get_db_name():
  """Return the database name from PORTAL config."""
  return _get('PORTAL', 'dbname')


def get_debug():
  """Return True if DEFAULT.debug is yes/true/1, else False."""
  return _get('DEFAULT', 'debug').lower() in ("yes", "true", "1")


def get_secret_key():
  """Return Django SECRET_KEY from DEFAULT.secret_key, or None if not set.

    Prefer environment variable SECRET_KEY over ini; settings.py should check
    os.environ first, then this, then fail or use dev default.
    """
  if cfg.has_option('DEFAULT', 'secret_key'):
    return cfg.get('DEFAULT', 'secret_key').strip() or None
  return None


def get_archive_dir_path():
  """Return the archive directory path from PORTAL config."""
  return _get('PORTAL', 'archive_dir')


def get_host_name_ext():
  """Return the host name extension (domain) from DEFAULT config."""
  return _get('DEFAULT', 'host_name_ext')


def get_restricted_queue_keywords():
  """Return restricted queue keywords string from DEFAULT config."""
  return _get('DEFAULT', 'restricted_queue_keywords')


def get_accounting_path():
  """Return the accounting (sacct) file path from PORTAL config."""
  return _get('PORTAL', 'acct_path')


def get_daily_archive_dir_path():
  """Return the daily archive directory path from PORTAL config."""
  return _get('PORTAL', 'daily_archive_dir')


def get_rmq_server():
  """Return the RabbitMQ server host from RMQ config."""
  return _get('RMQ', 'rmq_server')


def get_rmq_queue():
  """Return the RabbitMQ queue name from RMQ config."""
  return _get('RMQ', 'rmq_queue')


def get_machine_name():
  """Return the machine name from DEFAULT config."""
  return _get('DEFAULT', 'machine')


def get_server_name():
  """Return the server name from DEFAULT config."""
  return _get('DEFAULT', 'server')


def get_data_dir_path():
  """Return the data directory path from DEFAULT config."""
  return _get('DEFAULT', 'data_dir')


def get_engine_name():
  """Return the Django database engine name from PORTAL config."""
  return _get('PORTAL', 'engine_name')


def get_username():
  """Return the portal DB username from PORTAL config."""
  return _get('PORTAL', 'username')


def get_password():
  """Return the portal DB password from PORTAL config."""
  return _get('PORTAL', 'password')


def get_host():
  """Return the portal DB host from PORTAL config."""
  return _get('PORTAL', 'host')


def get_port():
  """Return the portal DB port from PORTAL config."""
  return _get('PORTAL', 'port')


def get_xalt_engine():
  """Return the XALT database engine from XALT config."""
  return _get('XALT', 'xalt_engine')


def get_xalt_name():
  """Return the XALT database name from XALT config."""
  return _get('XALT', 'xalt_name')


def get_xalt_user():
  """Return the XALT DB user from XALT config."""
  return _get('XALT', 'xalt_user')


def get_xalt_password():
  """Return the XALT DB password from XALT config."""
  return _get('XALT', 'xalt_password')


def get_xalt_host():
  """Return the XALT DB host from XALT config."""
  return _get('XALT', 'xalt_host')


def get_oauth_client_id():
  """Return the OAuth2 client ID from OAUTH2 config."""
  return _get('OAUTH2', 'client_id')


def get_oauth_client_key():
  """Return the OAuth2 client key/secret from OAUTH2 config."""
  return _get('OAUTH2', 'client_key')


def get_oauth_authorize_url():
  """Return the OAuth2 authorization URL template from OAUTH2 config."""
  return _get('OAUTH2', 'authorize_url')


def get_oauth_base_url():
  """Return the OAuth2 tenant base URL from OAUTH2 config."""
  return _get('OAUTH2', 'oauth_base_url')


def get_staff_email_domain():
  """Return the staff email domain from DEFAULT config."""
  return _get('DEFAULT', 'staff_email_domain')


def get_timezone():
  """Return the timezone string from DEFAULT config."""
  return _get('DEFAULT', 'timezone')


def get_total_cores():
  """Return the total cores count from DEFAULT config."""
  return _get('DEFAULT', 'total_cores')


def get_worker_thread_count(divisor=4):
  """Return worker thread count as total_cores / divisor, clamped to at least 1."""
  return max(1, int(_get('DEFAULT', 'total_cores')) // divisor)


def get_redis_location():
  """Return the Redis URL for cache from CACHE config.

    Defaults to redis://127.0.0.1:6379/1 if [CACHE] or redis_location is missing.
    """
  if cfg.has_section("CACHE") and cfg.has_option("CACHE", "redis_location"):
    return cfg.get("CACHE", "redis_location").strip() or "redis://127.0.0.1:6379/1"
  return "redis://127.0.0.1:6379/1"
