"""Regression: collectstatic must not copy source maps into STATIC_ROOT."""

from __future__ import annotations

from pathlib import Path

from django.apps import apps
from django.core.management import call_command

from hpcperfstats.site.hpcperfstats_site.staticfiles_config import (
    HPCStaticFilesConfig,
)


def test_staticfiles_app_config_ignores_map_globs():
  site_cfg = apps.get_app_config("hpcperfstats_site")
  cfg = apps.get_app_config("staticfiles")
  assert site_cfg.name == "hpcperfstats.site.hpcperfstats_site"
  assert isinstance(cfg, HPCStaticFilesConfig)
  assert cfg.name == "django.contrib.staticfiles"
  assert "*.map" in cfg.ignore_patterns


def test_collectstatic_skips_js_map_files(tmp_path: Path, settings):
  src = tmp_path / "static_src"
  src.mkdir()
  (src / "app.js").write_text("console.log(1);\n", encoding="utf-8")
  (src / "app.js.map").write_text('{"version":3}\n', encoding="utf-8")
  nested = src / "chunks"
  nested.mkdir()
  (nested / "chunk.js").write_text("export {};\n", encoding="utf-8")
  (nested / "chunk.js.map").write_text("{}\n", encoding="utf-8")
  dest = tmp_path / "collected"
  dest.mkdir()
  settings.STATICFILES_DIRS = [str(src)]
  settings.STATIC_ROOT = str(dest)
  settings.STATICFILES_FINDERS = (
      "django.contrib.staticfiles.finders.FileSystemFinder",
  )
  call_command("collectstatic", interactive=False, verbosity=0, clear=True)
  assert (dest / "app.js").is_file()
  assert (dest / "chunks" / "chunk.js").is_file()
  assert not (dest / "app.js.map").exists()
  assert not (dest / "chunks" / "chunk.js.map").exists()
