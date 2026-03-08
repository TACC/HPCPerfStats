"""Django test configuration. Only loaded when collecting/running site.machine.tests. Sets DJANGO_SETTINGS_MODULE and runs django.setup().

"""
import os

import pytest


def pytest_configure(config):
  """Set Django settings and run django.setup() when this package's tests are run.

    """
  os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                        "hpcperfstats.site.hpcperfstats_site.settings")
  import django
  django.setup()
