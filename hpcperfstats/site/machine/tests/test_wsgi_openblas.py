import importlib
import os
import sys

from django.conf import settings


def _reload_wsgi_module():
  module_name = "hpcperfstats.site.hpcperfstats_site.wsgi"
  sys.modules.pop(module_name, None)
  return importlib.import_module(module_name)


def test_wsgi_sets_openblas_threads_from_django_settings(monkeypatch):
  monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)
  monkeypatch.setattr(settings, "OPENBLAS_NUM_THREADS", 7, raising=False)

  _reload_wsgi_module()

  assert settings.OPENBLAS_NUM_THREADS == 7
  assert os.environ["OPENBLAS_NUM_THREADS"] == "7"


def test_wsgi_preserves_openblas_threads_env_override(monkeypatch):
  monkeypatch.setenv("OPENBLAS_NUM_THREADS", "11")
  monkeypatch.setattr(settings, "OPENBLAS_NUM_THREADS", 3, raising=False)

  _reload_wsgi_module()

  assert os.environ["OPENBLAS_NUM_THREADS"] == "11"
