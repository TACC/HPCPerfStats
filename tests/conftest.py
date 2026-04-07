"""Parent conftest for ``tests/`` (outside default ``hpcperfstats`` testpaths)."""
import importlib.util
import os

if importlib.util.find_spec("pytest_django") is not None:
  pytest_plugins = ["pytest_django.fixtures"]
else:
  pytest_plugins = []


def pytest_configure(config):
  """When ``pytest_django`` is absent, bootstrap Django so stress modules can import ORM."""
  if importlib.util.find_spec("pytest_django") is not None:
    return
  os.environ.setdefault(
      "DJANGO_SETTINGS_MODULE",
      "hpcperfstats.site.hpcperfstats_site.settings",
  )
  import django

  django.setup()
