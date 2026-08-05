"""
WSGI config for hpcperfstats_site. Sets path, MPLCONFIGDIR,
DJANGO_SETTINGS_MODULE, and exposes application.

Attributes:
  application: Attribute.
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '../'))
sys.path.append(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../../'))
os.environ.setdefault("MPLCONFIGDIR", "/tmp/")
os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                      "hpcperfstats.site.hpcperfstats_site.settings")
from django.conf import settings

# Limit OpenBLAS threads per web worker to avoid resource exhaustion.
# Value comes from Django settings while still allowing env override.
os.environ.setdefault(
    "OPENBLAS_NUM_THREADS",
    str(getattr(settings, "OPENBLAS_NUM_THREADS", 4)),
)

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
