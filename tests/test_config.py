"""Tests for hpcperfstats_tools.config."""

import importlib
from pathlib import Path


def test_get_api_base_url_reads_ini(tmp_path, monkeypatch):
  ini = tmp_path / "tools.ini"
  ini.write_text(
      "[API]\n"
      "base_url = https://stats.example.org/api/\n",
      encoding="utf-8",
  )
  monkeypatch.setenv("HPCPERFSTATS_TOOLS_INI", str(ini))
  import hpcperfstats_tools.config as cfg_mod

  importlib.reload(cfg_mod)
  try:
    assert cfg_mod.get_api_base_url() == "https://stats.example.org/api/"
  finally:
    monkeypatch.delenv("HPCPERFSTATS_TOOLS_INI", raising=False)
    importlib.reload(cfg_mod)


def test_get_api_base_url_falls_back_when_ini_missing(monkeypatch):
  monkeypatch.delenv("HPCPERFSTATS_TOOLS_INI", raising=False)
  import hpcperfstats_tools.config as cfg_mod

  importlib.reload(cfg_mod)
  assert cfg_mod.get_api_base_url() == "http://localhost:8000/api/"
