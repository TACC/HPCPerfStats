"""
settings.

Attributes:
  ADMINS: Attribute.
  CACHES: Attribute.
  CORS_ALLOWED_ORIGINS: Attribute.
  CORS_ALLOW_CREDENTIALS: Attribute.
  CSRF_COOKIE_SAMESITE: Attribute.
  CSRF_COOKIE_SECURE: Attribute.
  CSRF_TRUSTED_ORIGINS: Attribute.
  DATABASES: Attribute.
  DEBUG: Attribute.
  DEFAULT_AUTO_FIELD: Attribute.
  DIR: Attribute.
  INSTALLED_APPS: Attribute.
  INTERNAL_IPS: Attribute.
  JOB_PLOT_REDIS_MAX_BYTES: Attribute.
  LANGUAGE_CODE: Attribute.
  LOGGING: Attribute.
  MANAGERS: Attribute.
  MEDIA_ROOT: Attribute.
  MEDIA_URL: Attribute.
  MESSAGE_TAGS: Attribute.
  MIDDLEWARE: Attribute.
  OPENBLAS_NUM_THREADS: Attribute.
  REST_FRAMEWORK: Attribute.
  ROOT_URLCONF: Attribute.
  SACCT_INGEST_MAX_BODY_BYTES: Attribute.
  SECRET_KEY: Attribute.
  SECURE_CONTENT_TYPE_NOSNIFF: Attribute.
  SECURE_HSTS_INCLUDE_SUBDOMAINS: Attribute.
  SECURE_HSTS_PRELOAD: Attribute.
  SECURE_HSTS_SECONDS: Attribute.
  SECURE_PROXY_SSL_HEADER: Attribute.
  SECURE_REFERRER_POLICY: Attribute.
  SESSION_ABSOLUTE_TIMEOUT_SECONDS: Attribute.
  SESSION_COOKIE_AGE: Attribute.
  SESSION_COOKIE_HTTPONLY: Attribute.
  SESSION_COOKIE_SAMESITE: Attribute.
  SESSION_COOKIE_SECURE: Attribute.
  SESSION_ENGINE: Attribute.
  SESSION_IDLE_TIMEOUT_SECONDS: Attribute.
  SESSION_SAVE_EVERY_REQUEST: Attribute.
  SESSION_SERIALIZER: Attribute.
  SITE_ID: Attribute.
  SPECTACULAR_SETTINGS: Attribute.
  STATICFILES_DIRS: Attribute.
  STATICFILES_FINDERS: Attribute.
  STATIC_ROOT: Attribute.
  STATIC_URL: Attribute.
  TEMPLATES: Attribute.
  TIME_ZONE: Attribute.
  USE_I18N: Attribute.
  USE_TZ: Attribute.
  WSGI_APPLICATION: Attribute.
  X_FRAME_OPTIONS: Attribute.
  _ALLOWED: Attribute.
  _db_conn_max_age: Attribute.
  _default_db: Attribute.
  _default_engine: Attribute.
  _static_dir: Attribute.
  _use_live_redis_cache: Attribute.
"""

from __future__ import annotations

from typing import Any

# Django settings for hpcperfstats_site project. Database, auth, templates, static, and app config from conf_parser and env.
#
import os
import sys
import warnings
from datetime import timezone as _datetime_timezone

import django.utils.timezone as _django_utils_timezone

import hpcperfstats.dbload.lib.conf_parser as cfg

# Django 5+ removed django.utils.timezone.utc; keep alias for code/tests that still reference it.
if not hasattr(_django_utils_timezone, "utc"):
  _django_utils_timezone.utc = _datetime_timezone.utc
from django.core.cache.backends.base import CacheKeyWarning

DIR = os.path.dirname(os.path.abspath(__file__))

from django.contrib.messages import constants as messages

MESSAGE_TAGS = {
    messages.ERROR: 'danger',
}

# Number of threads for OpenBLAS. Used by wsgi.py as the default value for
# OPENBLAS_NUM_THREADS when the environment variable is not already set.
OPENBLAS_NUM_THREADS = 4

# SECRET_KEY: env overrides ini; required in production (set in env or hpcperfstats.ini [DEFAULT] secret_key).
SECRET_KEY = os.environ.get("SECRET_KEY") or cfg.get_secret_key()
DEBUG = cfg.get_debug()


def _env_int(name: Any, default: Any) -> Any:
    """
    Read integer env var with safe fallback.
    
    Args:
      name (Any): Name passed to this helper.
      default (Any): Default passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _env_int(None, None)  # doctest: +SKIP
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _parse_cors_allowed_origins() -> Any:
    """
    Resolve CORS origins from env, then ``hpcperfstats.ini``, then dev defaults.
    
    Returns:
      Any: Open return polymorphism from ``_parse_cors_allowed_origins``:
      concrete type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> _parse_cors_allowed_origins()  # doctest: +SKIP
    """
    env_origins = (os.environ.get("CORS_ALLOWED_ORIGINS") or "").strip()
    if env_origins:
        return [o.strip() for o in env_origins.split(",") if o.strip()]
    if DEBUG:
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    ini_csv = cfg.format_cors_allowed_origins_csv_from_ini()
    if ini_csv:
        return [o.strip() for o in ini_csv.split(",") if o.strip()]
    return []


def _validate_cors_allowed_origins(origins: Any) -> None:
    """
    Fail fast in production when CORS origins are missing or dev-only.
    
    Args:
      origins (Any): Origins passed to this helper.
    
    Returns:
      None
    
    Raises:
      ValueError: Raised when ``_validate_cors_allowed_origins`` hits a
      ``ValueError`` failure path.
    
    Examples:
      >>> _validate_cors_allowed_origins(None)  # doctest: +SKIP
    """
    if DEBUG or _is_non_http_management_command():
        return
    if not origins:
        raise ValueError(
            "CORS_ALLOWED_ORIGINS must be set in production "
            "(env CORS_ALLOWED_ORIGINS), or set [DEFAULT] server in "
            "hpcperfstats.ini so origins can be derived."
        )
    disallowed = {"http://localhost:5173", "http://127.0.0.1:5173"}
    if any(origin in disallowed for origin in origins):
        raise ValueError(
            "Production CORS_ALLOWED_ORIGINS cannot include localhost dev origins."
        )


def _is_non_http_management_command() -> Any:
    """
    Return True when Django loads for CLI helpers that never serve browsers.
    
    Returns:
      Any: Open return polymorphism from ``_is_non_http_management_command``:
      concrete type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> _is_non_http_management_command()  # doctest: +SKIP
    """
    argv = sys.argv
    # Many builds collapse argv for stdin / ``-c`` (e.g. ``['-']``, ``['']``, ``['-c']`` alone).
    if len(argv) == 1:
        arg0 = str(argv[0] or "")
        if arg0 in {"-", "", "-c"}:
            return True
        # ``python <<EOF`` or ``cat script.py | python``: interpreter path only, script on stdin.
        if not sys.stdin.isatty():
            return True
        return False
    if len(argv) < 2:
        return False
    # ``python /path/python - <<'PY'`` (two-arg form): argv is ``[..., '-']``.
    if argv[1] == "-":
        return True
    # ``python -c "..."`` when argv preserves the executable path.
    if argv[1] == "-c":
        return True
    non_http_commands = {
        "collectstatic",
        "migrate",
        "makemigrations",
        "showmigrations",
        "createsuperuser",
        "shell",
        "dbshell",
        "test",
        "check",
    }
    # Typical: ``python path/to/manage.py <subcommand>`` — subcommand is argv[2], not argv[1].
    for i, arg in enumerate(argv):
        arg_str = str(arg or "")
        if arg_str.endswith("manage.py"):
            if i + 1 < len(argv):
                command = str(argv[i + 1] or "").strip().lower()
                if command in non_http_commands:
                    return True
            break
    return False

# Skip Redis caching for job plot json_items when UTF-8 JSON exceeds this size (default 512 KiB).
JOB_PLOT_REDIS_MAX_BYTES = int(os.environ.get("JOB_PLOT_REDIS_MAX_BYTES", "524288"))
SACCT_INGEST_MAX_BODY_BYTES = _env_int("SACCT_INGEST_MAX_BODY_BYTES", 8 * 1024 * 1024)

# Django 6+: ADMINS/MANAGERS are list of email strings (name in tuple deprecated).
ADMINS = ["sharrell@tacc.utexas.edu"]
MANAGERS = ["sharrell@tacc.utexas.edu"]

# Set cookies properly: HttpOnly and Secure in production
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = _env_int("SESSION_COOKIE_AGE_SECONDS", 43200)
SESSION_SAVE_EVERY_REQUEST = True
SESSION_IDLE_TIMEOUT_SECONDS = _env_int("SESSION_IDLE_TIMEOUT_SECONDS", 3600)
SESSION_ABSOLUTE_TIMEOUT_SECONDS = _env_int(
    "SESSION_ABSOLUTE_TIMEOUT_SECONDS",
    SESSION_COOKIE_AGE,
)

# SecurityMiddleware configuration.
#
# nginx is responsible for TLS termination, so teach Django to trust
# X-Forwarded-Proto to decide when a request is secure (e.g. for HSTS).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# HSTS matches the previous nginx policy (max-age + includeSubDomains),
# but is now generated by Django so responses are consistent behind gunicorn
# (direct) and behind nginx (proxy).
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False

# Keep UI framing policy aligned with prior nginx behavior.
X_FRAME_OPTIONS = "SAMEORIGIN"

# Reduce MIME-sniffing risk.
SECURE_CONTENT_TYPE_NOSNIFF = True

# Avoid leaking full referrer URLs.
SECURE_REFERRER_POLICY = "same-origin"

# CSRF cookies are set by Django; ensure they are also marked Secure when
# running in production-style deployments behind TLS-terminating nginx.
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"

# Give a name that is unique for the computing platform
# CONN_MAX_AGE: persistent connections per worker/thread (see conf_parser.get_db_conn_max_age).
# Default 90s returns idle backends sooner under load; override via DJANGO_CONN_MAX_AGE or ini.
# CONN_HEALTH_CHECKS: verify connection before reuse (Django 4.1+).
# PostgreSQL OPTIONS: statement_timeout and idle_in_transaction_session_timeout (see conf_parser).
_db_conn_max_age = cfg.get_db_conn_max_age()
_default_engine = cfg.get_engine_name()
_default_db = {
    'ENGINE': _default_engine,
    'NAME': '{0}'.format(cfg.get_db_name()),
    'USER': cfg.get_username(),
    'PASSWORD': cfg.get_password(),
    'HOST': cfg.get_host(),
    'PORT': cfg.get_port(),
    'CONN_MAX_AGE': _db_conn_max_age,
    'CONN_HEALTH_CHECKS': True,
}
if _default_engine == 'django.db.backends.postgresql':
    _pg_opts = cfg.build_postgres_connection_options()
    if _pg_opts:
        _default_db['OPTIONS'] = _pg_opts
DATABASES = {
    'default': _default_db,
    # Uncomment this portion if an xalt database exists
    'xalt': {
        #'ENGINE' : 'mysql.connector.django',
        'ENGINE': cfg.get_xalt_engine(),
        'NAME': cfg.get_xalt_name(),
        'USER': cfg.get_xalt_user(),
        'PASSWORD': cfg.get_xalt_password(),
        'HOST': cfg.get_xalt_host(),
        'CONN_MAX_AGE': _db_conn_max_age,
        'CONN_HEALTH_CHECKS': True,
    }
}

# Hosts/domain names that are valid for this site; required if DEBUG is False.
# ALLOWED_HOSTS env (comma-separated) overrides; else use [DEFAULT] server from hpcperfstats.ini.
# See https://docs.djangoproject.com/en/stable/ref/settings/#allowed-hosts
_ALLOWED = os.environ.get("ALLOWED_HOSTS", "").strip()
if _ALLOWED:
    ALLOWED_HOSTS = [h.strip() for h in _ALLOWED.split(",") if h.strip()]
else:
    _server = (cfg.get_server_name() or "").strip()
    if _server:
        ALLOWED_HOSTS = [h.strip() for h in _server.split(",") if h.strip()]
    else:
        ALLOWED_HOSTS = ["*"] if DEBUG else []

# Always allow the Django test host so RequestFactory and Django's test client
# work without DisallowedHost errors during tests.
if "testserver" not in ALLOWED_HOSTS and "*" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS = list(ALLOWED_HOSTS) + ["testserver"]

# Allow Docker service hostname for internal health checks (e.g. supervisor_startup.sh curling http://web:8000)
if "*" not in ALLOWED_HOSTS and "web" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS = list(ALLOWED_HOSTS) + ["web"]

# Always allow loopback hosts for local development access through docker-compose
# port publishing (e.g. http://localhost:8000).
if "*" not in ALLOWED_HOSTS:
    for _loopback_host in ("localhost", "127.0.0.1"):
        if _loopback_host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS = list(ALLOWED_HOSTS) + [_loopback_host]

# Local time zone for this installation. Choices can be found here:
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# although not all choices may be available on all operating systems.
# In a Windows environment this must be set to your system time zone.
# Value comes from hpcperfstats.ini [DEFAULT] timezone.
TIME_ZONE = cfg.get_timezone()

# Language code for this installation. All choices can be found here:
# http://www.i18nguy.com/unicode/language-identifiers.html
LANGUAGE_CODE = 'en-us'

SITE_ID = 1

# If you set this to False, Django will make some optimizations so as not
# to load the internationalization machinery.
USE_I18N = True

# If you set this to False, Django will not format dates, numbers and
# calendars according to the current locale.

# If you set this to False, Django will not use timezone-aware datetimes.
USE_TZ = True

# Absolute filesystem path to the directory that will hold user-uploaded files.
# Example: "/var/www/example.com/media/"
MEDIA_ROOT = os.path.join(DIR, 'media/')

# URL that handles the media served from MEDIA_ROOT. Make sure to use a
# trailing slash.
# Examples: "http://example.com/media/", "http://media.example.com/"
MEDIA_URL = '/media/'

# Absolute path to the directory static files should be collected to.
# Don't put anything in this directory yourself; store your static files
# in apps' "static/" subdirectories and in STATICFILES_DIRS.
# Example: "/var/www/example.com/static/"
STATIC_ROOT = os.environ.get("STATIC_ROOT") or "/home/hpcperfstats/staticfiles"

# URL prefix for static files.
# Example: "http://example.com/static/", "http://static.example.com/"
STATIC_URL = '/static/'

# Additional locations of static files.
# In some packaged/containerized installs this app-level static directory may
# not exist as a real filesystem path, so only include existing directories to
# avoid Django staticfiles.W004 warnings at startup.
_static_dir = os.path.join(DIR, "static")
STATICFILES_DIRS = (_static_dir,) if os.path.isdir(_static_dir) else ()

# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
)

# Make this unique, and don't share it with anybody. Never commit a real key.
# In production set SECRET_KEY in the environment or in hpcperfstats.ini [DEFAULT] secret_key.
if not SECRET_KEY and DEBUG:
    warnings.warn("SECRET_KEY not set; using a dev-only default. Set SECRET_KEY in env for production.")
    SECRET_KEY = "dev-only-insecure-change-me"
elif not SECRET_KEY:
    raise ValueError("SECRET_KEY must be set in the environment for production.")

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            # insert your TEMPLATE_DIRS here
            #    'hpcperfstats_site/templates',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                # Insert your TEMPLATE_CONTEXT_PROCESSORS here or use this
                # list if you haven't customized them:
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# Redis for ORM and view caching
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": cfg.get_redis_location(),
        "OPTIONS": {},
        "KEY_PREFIX": "hpcperfstats",
        "TIMEOUT": 300,
    }
}

# Suppress memcached key warnings emitted by Django key validation.
warnings.filterwarnings(
    "ignore",
    category=CacheKeyWarning,
    module=r"django\.core\.cache\.backends\.base",
)

# During test runs, avoid requiring a real Redis instance by switching to the
# in-memory cache backend. This keeps production configuration unchanged.
# Set HPCPERFSTATS_PYTEST_LIVE_REDIS=1 (e.g. tests/run_redis_cache_pytest_workflow.sh)
# to exercise Django's RedisCache against a real Redis on the compose network.
_use_live_redis_cache = os.environ.get(
    "HPCPERFSTATS_PYTEST_LIVE_REDIS", ""
).strip().lower() in ("1", "yes", "true")


def _running_under_pytest() -> Any:
    """
    Detect pytest including ``python -m pytest`` (argv does not end with.
    
      ``pytest``).
    
    Returns:
      Any: Open return polymorphism from ``_running_under_pytest``: concrete
      type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> _running_under_pytest()  # doctest: +SKIP
    """
    if "pytest" in sys.argv:
        return True
    prog = os.path.basename(str(sys.argv[0] or ""))
    if prog == "pytest" or prog.startswith("pytest."):
        return True
    return "_pytest" in sys.modules


if _running_under_pytest() and not _use_live_redis_cache:
    CACHES["default"] = {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "hpcperfstats-tests",
    }
# Full-page cache middleware removed in Django 4.0; ORM uses cache_utils.
MIDDLEWARE = (
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "hpcperfstats.site.hpcperfstats_site.middleware.DefaultSecurityHeadersMiddleware",
    "hpcperfstats.site.hpcperfstats_site.middleware.DefaultCacheControlMiddleware",
)

ROOT_URLCONF = 'hpcperfstats.site.hpcperfstats_site.urls'
# Python dotted path to the WSGI application used by Django's runserver.
WSGI_APPLICATION = 'hpcperfstats.site.hpcperfstats_site.wsgi'

# Django 6: DEFAULT_AUTO_FIELD defaults to BigAutoField; set explicitly for clarity.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = (
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "hpcperfstats.site.lib.machine.apps.MachineConfig",
    "hpcperfstats.site.xalt",
    "hpcperfstats.site.hpcperfstats_site",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # Required for ArrayField and postgres ops (Django 6 system checks).
)
INTERNAL_IPS = ["127.0.0.1"]

# Django REST Framework: session auth for same-origin; allow credentials for SPA
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "hpcperfstats.site.lib.machine.renderers.SafeJSONRenderer",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "hpcperfstats.site.lib.machine.throttles.AuthenticatedUserOrApiKeyThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "authenticated_user_or_api_key": os.environ.get(
            "API_THROTTLE_AUTHENTICATED_RATE",
            "1200/min",
        ),
        "expensive_read": os.environ.get(
            "API_THROTTLE_EXPENSIVE_READ_RATE",
            "600/min",
        ),
        "staff_ingest": os.environ.get(
            "API_THROTTLE_STAFF_INGEST_RATE",
            "30/min",
        ),
        # Anonymous `/api/pub/cluster-dashboard/` pre-warmed bundle (see ``public_api.py``).
        "public_cluster_dashboard": os.environ.get(
            "API_THROTTLE_PUBLIC_CLUSTER_DASHBOARD_RATE",
            os.environ.get("API_THROTTLE_PUBLIC_MONTHLY_METRICS_RATE", "240/min"),
        ),
    },
}
SPECTACULAR_SETTINGS = {
    "TITLE": "HPCPerfStats API",
    "DESCRIPTION": "REST API for the HPCPerfStats machine SPA and public dashboards.",
    "VERSION": "3.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/",
    "COMPONENT_SPLIT_REQUEST": True,
}
# CORS: same-origin plus explicit trusted frontends only.
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = _parse_cors_allowed_origins()
_validate_cors_allowed_origins(CORS_ALLOWED_ORIGINS)
CSRF_TRUSTED_ORIGINS = list(CORS_ALLOWED_ORIGINS)

SESSION_SERIALIZER = 'django.contrib.sessions.serializers.JSONSerializer'
SESSION_ENGINE = 'django.contrib.sessions.backends.file'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format': '[DJANGO] %(levelname)s %(asctime)s %(module)s '
                      '%(name)s.%(funcName)s:%(lineno)s: %(message)s'
        },
        'agave': {
            'format': '[AGAVE] %(levelname)s %(asctime)s %(module)s '
                      '%(name)s.%(funcName)s:%(lineno)s: %(message)s'
        },
        'metrics': {
            'format':
                '[METRICS] %(levelname)s %(module)s %(name)s.%(funcName)s:%(lineno)s:'
                ' %(message)s user=%(user)s sessionId=%(sessionId)s op=%(operation)s'
                ' info=%(info)s'
        },
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'default',
            'stream': sys.stdout,
        },
        'opbeat': {
            'level': 'ERROR',
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
        },
        'metrics': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'metrics',
            'stream': sys.stdout,
        },
        'logfile': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
        },
        # Swallow noisy DisallowedHost errors (e.g., bad 0.0.0.0 probes)
        'null': {
            'class': 'logging.NullHandler',
        },
    },
    'loggers': {
        'hpcperfstats_site': {
            'handlers': ['logfile',],
            'level': 'INFO',
        },
        'django': {
            'handlers': ['console', 'opbeat'],
            'level': 'INFO',
            'propagate': True,
        },
        'celery': {
            'handlers': ['console', 'opbeat'],
            'level': 'DEBUG',
            'propagate': True
        },
        'opbeat': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'metrics': {
            'handlers': ['metrics'],
            'level': 'INFO',
        },
    },
}

# Only suppress DisallowedHost logging in non-DEBUG environments
if not DEBUG:
    LOGGING['loggers']['django.security.DisallowedHost'] = {
        'handlers': ['null'],
        'level': 'ERROR',
        'propagate': False,
    }
