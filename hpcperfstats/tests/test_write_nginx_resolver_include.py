"""Unit tests for proxy OCSP resolver include generation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _load_resolver_mod():
  path = _repo_root() / "services-conf" / "write_nginx_resolver_include.py"
  spec = importlib.util.spec_from_file_location("write_nginx_resolver_include", path)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_parse_resolv_nameservers_dedupes_and_skips_invalid():
  mod = _load_resolver_mod()
  text = "\n".join(
      [
          "# comment",
          "nameserver 127.0.0.11",
          "nameserver 127.0.0.11",
          "nameserver not-an-ip",
          "nameserver 1.1.1.1",
          "search example.test",
          "",
      ]
  )
  assert mod.parse_resolv_nameservers(text) == ["127.0.0.11", "1.1.1.1"]


def test_render_resolver_include_requires_nameserver():
  mod = _load_resolver_mod()
  with pytest.raises(ValueError, match="no usable nameserver"):
    mod.render_resolver_include([])
  body = mod.render_resolver_include(["127.0.0.11", "1.1.1.1"])
  assert "resolver 127.0.0.11 1.1.1.1 ipv6=off valid=300s;" in body
  assert "resolver_timeout 5s;" in body


def test_write_nginx_resolver_include_roundtrip(tmp_path: Path):
  mod = _load_resolver_mod()
  resolv = tmp_path / "resolv.conf"
  out = tmp_path / "nginx-resolver.inc"
  resolv.write_text("nameserver 127.0.0.11\n", encoding="utf-8")
  names = mod.write_nginx_resolver_include(resolv_path=resolv, out_path=out)
  assert names == ["127.0.0.11"]
  assert out.is_file()
  assert "resolver 127.0.0.11" in out.read_text(encoding="utf-8")


def test_nginx_conf_and_proxy_dockerfile_complete_ocsp_contract():
  example = (_repo_root() / "services-conf" / "nginx.conf").read_text(
      encoding="utf-8"
  )
  assert "ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;" in example
  assert "include /etc/nginx/nginx-resolver.inc;" in example
  dockerfile = (_repo_root() / "services-conf" / "proxy.Dockerfile").read_text(
      encoding="utf-8"
  )
  assert "ca-certificates" in dockerfile
  assert "write_nginx_resolver_include.py" in dockerfile
  assert "proxy_entrypoint.sh" in dockerfile
