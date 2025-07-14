# Django settings for hpcperfstats_site project.
import os
import sys
import hpcperfstats.conf_parser as cfg

import hpcperfstats.site.hpcperfstats_site as hpcperfstats_site
DIR = os.path.dirname(os.path.abspath(__file__))

from django.contrib.messages import constants as messages
MESSAGE_TAGS = { 
    messages.ERROR: 'danger',
}

# For dockerization 
SECRET_KEY = os.environ.get("SECRET_KEY")
#DEBUG = bool(os.environ.get("DEBUG", default=0))
DEBUG = cfg.get_debug()

ADMINS = (
    ('Stephen Lien Harrell', 'sharrell@tacc.utexas.edu'),
)

MANAGERS = ADMINS

# Set cookies properly
SESSION_COOKIE_HTTPONLY = True



# Give a name that is unique for the computing platform
DATABASES = {
    'default': {
        'ENGINE': cfg.get_engine_name(),
        'NAME'  : '{0}'.format(cfg.get_db_name()),
        'USER'  : cfg.get_username(),
        'PASSWORD': cfg.get_password(),
        'HOST': cfg.get_host(),         
        'PORT': cfg.get_port(),               
        },
    # Uncomment this portion if an xalt database exists
    'xalt' : {
        #'ENGINE' : 'mysql.connector.django',
        'ENGINE' : cfg.get_xalt_engine(),
        'NAME' : cfg.get_xalt_name(),
        'USER' : cfg.get_xalt_user(),
        'PASSWORD' : cfg.get_xalt_password(),
        'HOST' : cfg.get_xalt_host()
        }        
    }

# Hosts/domain names that are valid for this site; required if DEBUG is False
# See https://docs.djangoproject.com/en/1.5/ref/settings/#allowed-hosts
ALLOWED_HOSTS = ['*']


# Local time zone for this installation. Choices can be found here:
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# although not all choices may be available on all operating systems.
# In a Windows environment this must be set to your system time zone.
TIME_ZONE = 'America/Chicago'

# Language code for this installation. All choices can be found here:
# http://www.i18nguy.com/unicode/language-identifiers.html
LANGUAGE_CODE = 'en-us'

SITE_ID = 1

# If you set this to False, Django will make some optimizations so as not
# to load the internationalization machinery.
USE_I18N = True

# If you set this to False, Django will not format dates, numbers and
# calendars according to the current locale.
USE_L10N = True

# If you set this to False, Django will not use timezone-aware datetimes.
USE_TZ = True

# Absolute filesystem path to the directory that will hold user-uploaded files.
# Example: "/var/www/example.com/media/"
MEDIA_ROOT = os.path.join(DIR,'media/')

# URL that handles the media served from MEDIA_ROOT. Make sure to use a
# trailing slash.
# Examples: "http://example.com/media/", "http://media.example.com/"
MEDIA_URL = '/media/'

# Absolute path to the directory static files should be collected to.
# Don't put anything in this directory yourself; store your static files
# in apps' "static/" subdirectories and in STATICFILES_DIRS.
# Example: "/var/www/example.com/static/"
STATIC_ROOT = ''

# URL prefix for static files.
# Example: "http://example.com/static/", "http://static.example.com/"
STATIC_URL = '/static/'

# Additional locations of static files
STATICFILES_DIRS = (
    os.path.join(DIR,'static/'),
)

# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
)

# Make this unique, and don't share it with anybody.
SECRET_KEY = 'dcute6k4o*0%=76t6!2q=wqv4lt20v32(m!c_ueed^)x8q2u#8'

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

MIDDLEWARE = (
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.cache.UpdateCacheMiddleware',
    'django.middleware.cache.FetchFromCacheMiddleware',
)

ROOT_URLCONF = 'hpcperfstats.site.hpcperfstats_site.urls'
# Python dotted path to the WSGI application used by Django's runserver.
WSGI_APPLICATION = 'hpcperfstats.site.hpcperfstats_site.wsgi'

INSTALLED_APPS = (
    'hpcperfstats.site.machine',
    'hpcperfstats.site.xalt',
    'hpcperfstats.site.hpcperfstats_site',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    #'django_extensions',
    # Uncomment the next line to enable the admin:
    'django.contrib.admin',
    #'debug_toolbar',
    #'django_pdf',
    # Uncomment the next line to enable admin documentation:
    # 'django.contrib.admindocs',
)
INTERNAL_IPS = ['127.0.0.1']
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
            'format': '[METRICS] %(levelname)s %(module)s %(name)s.%(funcName)s:%(lineno)s:'
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
            'level':'INFO',
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
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
