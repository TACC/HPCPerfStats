"""Tests for api_key_cache helpers."""

from pathlib import Path
from unittest.mock import patch

from hpcperfstats_tools import api_key_cache


def test_load_cached_api_key_single_line(tmp_path):
  p = tmp_path / "keys"
  p.write_text("secret-key-only\n", encoding="utf-8")
  with patch.object(api_key_cache, "API_KEY_CACHE", p):
    assert api_key_cache.load_cached_api_key("http://any/api/") == "secret-key-only"


def test_load_cached_api_key_url_mapping(tmp_path):
  p = tmp_path / "keys"
  p.write_text(
      "# comment\n"
      "http://localhost:8000/api/ key-one\n"
      "https://other/api/ key-two\n",
      encoding="utf-8",
  )
  with patch.object(api_key_cache, "API_KEY_CACHE", p):
    assert api_key_cache.load_cached_api_key("http://localhost:8000/api/") == "key-one"
    assert api_key_cache.load_cached_api_key("https://other/api") == "key-two"


def test_save_cached_api_key_writes_mapping(tmp_path):
  p = Path(tmp_path) / "keys"
  with patch.object(api_key_cache, "API_KEY_CACHE", p):
    api_key_cache.save_cached_api_key("http://host/api/", "k99")
    text = p.read_text(encoding="utf-8")
    assert "http://host/api" in text
    assert "k99" in text


def test_api_key_help_url_strips_api_suffix(monkeypatch):
  monkeypatch.delenv("HPCPERF_API_KEY_URL", raising=False)
  url = api_key_cache.api_key_help_url("http://localhost:8000/api/")
  assert url == "http://localhost:8000/api-key/"


def test_api_key_help_url_env_override(monkeypatch):
  monkeypatch.setenv("HPCPERF_API_KEY_URL", "https://custom/help")
  assert api_key_cache.api_key_help_url("http://x/api/") == "https://custom/help"
