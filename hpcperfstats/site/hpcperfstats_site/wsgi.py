"""WSGI config for hpcperfstats_site. Sets path, MPLCONFIGDIR, DJANGO_SETTINGS_MODULE, and exposes application.

AI generated.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),'../'))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),'../../../'))
os.environ.setdefault("MPLCONFIGDIR","/tmp/")
os.environ.setdefault("DJANGO_SETTINGS_MODULE","hpcperfstats.site.hpcperfstats_site.settings")

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()