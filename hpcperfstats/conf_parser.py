"""Configuration parser for HPCPerfStats. Reads hpcperfstats.ini and exposes getters for portal, RMQ, XALT, and OAuth2 settings.

"""
import configparser
import os

cfg = configparser.ConfigParser()

# Config path: HPCPERFSTATS_INI env, or default. Override for testing.
_CONFIG_PATH = os.environ.get("HPCPERFSTATS_INI",
                              "/home/hpcperfstats/hpcperfstats.ini")
cfg.read(_CONFIG_PATH)


def get_db_connection_string():
  """Return a PostgreSQL connection string from PORTAL config (dbname, user, password, port, host).

    """
  temp_string = "dbname={0} user=" + cfg.get(
      'PORTAL', 'username') + " password=" + cfg.get(
          'PORTAL', 'password') + " port=" + cfg.get(
              'PORTAL', 'port') + " host=" + cfg.get('PORTAL', 'host')
  connection_string = temp_string.format(cfg.get('PORTAL', 'dbname'))
  return connection_string


def get_db_name():
  """Return the database name from PORTAL config.

    """
  db_name = cfg.get('PORTAL', 'dbname')
  return db_name


def get_debug():
  """Return True if DEFAULT.debug is yes/true/1, else False.

    """
  debug = cfg.get('DEFAULT', 'debug')
  # cast this as a bool instead of a string
  return debug.lower() in ("yes", "true", "1")


def get_secret_key():
  """Return Django SECRET_KEY from DEFAULT.secret_key, or None if not set.

    Prefer environment variable SECRET_KEY over ini; settings.py should check
    os.environ first, then this, then fail or use dev default.
    """
  if cfg.has_option('DEFAULT', 'secret_key'):
    return cfg.get('DEFAULT', 'secret_key').strip() or None
  return None


def get_archive_dir_path():
  """Return the archive directory path from PORTAL config.

    """
  archive_dir_path = cfg.get('PORTAL', 'archive_dir')
  return archive_dir_path


def get_host_name_ext():
  """Return the host name extension (domain) from DEFAULT config.

    """
  host_name_ext = cfg.get('DEFAULT', 'host_name_ext')
  return host_name_ext


def get_restricted_queue_keywords():
  """Return restricted queue keywords string from DEFAULT config.

    """
  restricted_queue_keywords = cfg.get('DEFAULT', 'restricted_queue_keywords')
  return restricted_queue_keywords


def get_accounting_path():
  """Return the accounting (sacct) file path from PORTAL config.

    """
  accounting_path = cfg.get('PORTAL', 'acct_path')
  return accounting_path


def get_daily_archive_dir_path():
  """Return the daily archive directory path from PORTAL config.

    """
  daily_archive_dir_path = cfg.get('PORTAL', 'daily_archive_dir')
  return daily_archive_dir_path


def get_rmq_server():
  """Return the RabbitMQ server host from RMQ config.

    """
  rmq_server = cfg.get('RMQ', 'rmq_server')
  return rmq_server


def get_rmq_queue():
  """Return the RabbitMQ queue name from RMQ config.

    """
  rmq_queue = cfg.get('RMQ', 'rmq_queue')
  return rmq_queue


def get_machine_name():
  """Return the machine name from DEFAULT config.

    """
  machine_name = cfg.get('DEFAULT', 'machine')
  return machine_name


def get_server_name():
  """Return the server name from DEFAULT config.

    """
  server_name = cfg.get('DEFAULT', 'server')
  return server_name


def get_data_dir_path():
  """Return the data directory path from DEFAULT config.

    """
  data_dir_path = cfg.get('DEFAULT', 'data_dir')
  return data_dir_path


def get_engine_name():
  """Return the Django database engine name from PORTAL config.

    """
  engine_name = cfg.get('PORTAL', 'engine_name')
  return engine_name


def get_username():
  """Return the portal DB username from PORTAL config.

    """
  username = cfg.get('PORTAL', 'username')
  return username


def get_password():
  """Return the portal DB password from PORTAL config.

    """
  password = cfg.get('PORTAL', 'password')
  return password


def get_host():
  """Return the portal DB host from PORTAL config.

    """
  host = cfg.get('PORTAL', 'host')
  return host


def get_port():
  """Return the portal DB port from PORTAL config.

    """
  port = cfg.get('PORTAL', 'port')
  return port


def get_xalt_engine():
  """Return the XALT database engine from XALT config.

    """
  xalt_engine = cfg.get('XALT', 'xalt_engine')
  return xalt_engine


def get_xalt_name():
  """Return the XALT database name from XALT config.

    """
  xalt_name = cfg.get('XALT', 'xalt_name')
  return xalt_name


def get_xalt_user():
  """Return the XALT DB user from XALT config.

    """
  xalt_user = cfg.get('XALT', 'xalt_user')
  return xalt_user


def get_xalt_password():
  """Return the XALT DB password from XALT config.

    """
  xalt_password = cfg.get('XALT', 'xalt_password')
  return xalt_password


def get_xalt_host():
  """Return the XALT DB host from XALT config.

    """
  xalt_host = cfg.get('XALT', 'xalt_host')
  return xalt_host


def get_oauth_client_id():
  """Return the OAuth2 client ID from OAUTH2 config.

    """
  return cfg.get('OAUTH2', 'client_id')


def get_oauth_client_key():
  """Return the OAuth2 client key/secret from OAUTH2 config.

    """
  return cfg.get('OAUTH2', 'client_key')


def get_oauth_authorize_url():
  """Return the OAuth2 authorization URL template from OAUTH2 config.

    """
  return cfg.get('OAUTH2', 'authorize_url')


def get_oauth_base_url():
  """Return the OAuth2 tenant base URL from OAUTH2 config.

    """
  return cfg.get('OAUTH2', 'oauth_base_url')


def get_staff_email_domain():
  """Return the staff email domain from DEFAULT config.

    """
  return cfg.get('DEFAULT', 'staff_email_domain')


def get_timezone():
  """Return the timezone string from DEFAULT config.

    """
  return cfg.get('DEFAULT', 'timezone')


def get_total_cores():
  """Return the total cores count from DEFAULT config.

    """
  return cfg.get('DEFAULT', 'total_cores')


def get_worker_thread_count(divisor=4):
  """Return worker thread count as total_cores / divisor, clamped to at least 1."""
  n = int(cfg.get('DEFAULT', 'total_cores')) // divisor
  return max(1, n)


def get_redis_location():
  """Return the Redis URL for cache from CACHE config.

    Defaults to redis://127.0.0.1:6379/1 if [CACHE] or redis_location is missing.
    """
  if cfg.has_section("CACHE") and cfg.has_option("CACHE", "redis_location"):
    return cfg.get("CACHE", "redis_location").strip() or "redis://127.0.0.1:6379/1"
  return "redis://127.0.0.1:6379/1"
