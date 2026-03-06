"""Configuration parser for HPCPerfStats. Reads hpcperfstats.ini and exposes getters for portal, RMQ, XALT, and OAuth2 settings.

AI generated.
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

    AI generated.
    """
  temp_string = "dbname={0} user=" + cfg.get(
      'PORTAL', 'username') + " password=" + cfg.get(
          'PORTAL', 'password') + " port=" + cfg.get(
              'PORTAL', 'port') + " host=" + cfg.get('PORTAL', 'host')
  connection_string = temp_string.format(cfg.get('PORTAL', 'dbname'))
  return connection_string


def get_db_name():
  """Return the database name from PORTAL config.

    AI generated.
    """
  db_name = cfg.get('PORTAL', 'dbname')
  return db_name


def get_debug():
  """Return True if DEFAULT.debug is yes/true/1, else False.

    AI generated.
    """
  debug = cfg.get('DEFAULT', 'debug')
  # cast this as a bool instead of a string
  return debug.lower() in ("yes", "true", "1")


def get_archive_dir_path():
  """Return the archive directory path from PORTAL config.

    AI generated.
    """
  archive_dir_path = cfg.get('PORTAL', 'archive_dir')
  return archive_dir_path


def get_host_name_ext():
  """Return the host name extension (domain) from DEFAULT config.

    AI generated.
    """
  host_name_ext = cfg.get('DEFAULT', 'host_name_ext')
  return host_name_ext


def get_restricted_queue_keywords():
  """Return restricted queue keywords string from DEFAULT config.

    AI generated.
    """
  restricted_queue_keywords = cfg.get('DEFAULT', 'restricted_queue_keywords')
  return restricted_queue_keywords


def get_accounting_path():
  """Return the accounting (sacct) file path from PORTAL config.

    AI generated.
    """
  accounting_path = cfg.get('PORTAL', 'acct_path')
  return accounting_path


def get_daily_archive_dir_path():
  """Return the daily archive directory path from PORTAL config.

    AI generated.
    """
  daily_archive_dir_path = cfg.get('PORTAL', 'daily_archive_dir')
  return daily_archive_dir_path


def get_rmq_server():
  """Return the RabbitMQ server host from RMQ config.

    AI generated.
    """
  rmq_server = cfg.get('RMQ', 'rmq_server')
  return rmq_server


def get_rmq_queue():
  """Return the RabbitMQ queue name from RMQ config.

    AI generated.
    """
  rmq_queue = cfg.get('RMQ', 'rmq_queue')
  return rmq_queue


def get_machine_name():
  """Return the machine name from DEFAULT config.

    AI generated.
    """
  machine_name = cfg.get('DEFAULT', 'machine')
  return machine_name


def get_server_name():
  """Return the server name from DEFAULT config.

    AI generated.
    """
  server_name = cfg.get('DEFAULT', 'server')
  return server_name


def get_data_dir_path():
  """Return the data directory path from DEFAULT config.

    AI generated.
    """
  data_dir_path = cfg.get('DEFAULT', 'data_dir')
  return data_dir_path


def get_engine_name():
  """Return the Django database engine name from PORTAL config.

    AI generated.
    """
  engine_name = cfg.get('PORTAL', 'engine_name')
  return engine_name


def get_username():
  """Return the portal DB username from PORTAL config.

    AI generated.
    """
  username = cfg.get('PORTAL', 'username')
  return username


def get_password():
  """Return the portal DB password from PORTAL config.

    AI generated.
    """
  password = cfg.get('PORTAL', 'password')
  return password


def get_host():
  """Return the portal DB host from PORTAL config.

    AI generated.
    """
  host = cfg.get('PORTAL', 'host')
  return host


def get_port():
  """Return the portal DB port from PORTAL config.

    AI generated.
    """
  port = cfg.get('PORTAL', 'port')
  return port


def get_xalt_engine():
  """Return the XALT database engine from XALT config.

    AI generated.
    """
  xalt_engine = cfg.get('XALT', 'xalt_engine')
  return xalt_engine


def get_xalt_name():
  """Return the XALT database name from XALT config.

    AI generated.
    """
  xalt_name = cfg.get('XALT', 'xalt_name')
  return xalt_name


def get_xalt_user():
  """Return the XALT DB user from XALT config.

    AI generated.
    """
  xalt_user = cfg.get('XALT', 'xalt_user')
  return xalt_user


def get_xalt_password():
  """Return the XALT DB password from XALT config.

    AI generated.
    """
  xalt_password = cfg.get('XALT', 'xalt_password')
  return xalt_password


def get_xalt_host():
  """Return the XALT DB host from XALT config.

    AI generated.
    """
  xalt_host = cfg.get('XALT', 'xalt_host')
  return xalt_host


def get_oauth_client_id():
  """Return the OAuth2 client ID from OAUTH2 config.

    AI generated.
    """
  return cfg.get('OAUTH2', 'client_id')


def get_oauth_client_key():
  """Return the OAuth2 client key/secret from OAUTH2 config.

    AI generated.
    """
  return cfg.get('OAUTH2', 'client_key')


def get_oauth_authorize_url():
  """Return the OAuth2 authorization URL template from OAUTH2 config.

    AI generated.
    """
  return cfg.get('OAUTH2', 'authorize_url')


def get_oauth_base_url():
  """Return the OAuth2 tenant base URL from OAUTH2 config.

    AI generated.
    """
  return cfg.get('OAUTH2', 'oauth_base_url')


def get_staff_email_domain():
  """Return the staff email domain from DEFAULT config.

    AI generated.
    """
  return cfg.get('DEFAULT', 'staff_email_domain')


def get_timezone():
  """Return the timezone string from DEFAULT config.

    AI generated.
    """
  return cfg.get('DEFAULT', 'timezone')


def get_total_cores():
  """Return the total cores count from DEFAULT config.

    AI generated.
    """
  return cfg.get('DEFAULT', 'total_cores')


def get_memcached_location():
  """Return the memcached server location (host:port) from CACHE config.

    Defaults to 127.0.0.1:11211 if [CACHE] or memcached_location is missing.
    """
  if cfg.has_section("CACHE") and cfg.has_option("CACHE", "memcached_location"):
    return cfg.get("CACHE", "memcached_location").strip() or "127.0.0.1:11211"
  return "127.0.0.1:11211"
