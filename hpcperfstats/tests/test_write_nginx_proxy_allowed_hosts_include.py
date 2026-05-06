from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _load_parse():
  path = _repo_root() / "services-conf" / "parse_hpcperfstats_proxy_hosts.py"
  spec = importlib.util.spec_from_file_location("parse_hpcperfstats_proxy_hosts", path)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  sys.modules["parse_hpcperfstats_proxy_hosts"] = mod
  spec.loader.exec_module(mod)
  return mod


def _load_write_after_parse():
  _load_parse()
  path = _repo_root() / "services-conf" / "write_nginx_proxy_allowed_hosts_include.py"
  spec = importlib.util.spec_from_file_location("write_nginx_proxy_allowed_hosts_include", path)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_parse_reads_ini_example():
  mod = _load_parse()
  names = mod.load_allowed_server_names(_repo_root() / "hpcperfstats.ini.example")
  assert "servername.domain.edu" in names
  assert "stats.cluster.domain.edu" in names


def test_write_allowed_hosts_include_emits_server_name_line(tmp_path):
  mod_w = _load_write_after_parse()
  ini = tmp_path / "t.ini"
  ini.write_text(
      "[DEFAULT]\nserver = a.example.com, b.example.org\n",
      encoding="utf-8",
  )
  out = tmp_path / "hps-proxy-allowed-hosts.inc"
  mod_w.write_allowed_hosts_include(ini_path=ini, out_path=out)
  text = out.read_text(encoding="utf-8")
  assert "server_name a.example.com b.example.org;" in text


def test_committed_nginx_conf_example_includes_generated_fragment():
  example = (_repo_root() / "services-conf" / "nginx.conf.example").read_text(encoding="utf-8")
  assert "include /etc/nginx/hps-proxy-allowed-hosts.inc;" in example
  assert "ssl_certificate " in example


def test_parse_rejects_invalid_hostname(tmp_path):
  mod = _load_parse()
  ini = tmp_path / "bad.ini"
  ini.write_text("[DEFAULT]\nserver = not_a_valid_host!\n", encoding="utf-8")
  try:
    mod.load_allowed_server_names(ini)
  except ValueError as exc:
    assert "invalid hostname" in str(exc).lower()
  else:
    raise AssertionError("expected ValueError")
