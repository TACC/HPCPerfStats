"""Unit tests for SPA CSP meta embedding and private nginx includes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from hpcperfstats.site.lib.spa_csp_meta import (
  inject_csp_meta_into_frontend_tree,
  inject_csp_meta_into_html,
  sha256_csp_hash,
  write_spa_csp_includes,
)


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def test_inject_csp_meta_covers_inline_script_hash(tmp_path: Path):
  frontend = tmp_path / "frontend"
  machine = frontend / "machine"
  pub = frontend / "pub"
  machine.mkdir(parents=True)
  pub.mkdir(parents=True)
  body = 'self.__next_f.push([1,"x"])'
  (machine / "index.html").write_text(
      f"<html><head></head><body><script>{body}</script></body></html>",
      encoding="utf-8",
  )
  (pub / "index.html").write_text(
      f"<html><head></head><body><script>{body}</script></body></html>",
      encoding="utf-8",
  )
  n = inject_csp_meta_into_frontend_tree(frontend)
  assert n == 2
  html = (machine / "index.html").read_text(encoding="utf-8")
  assert "http-equiv=\"Content-Security-Policy\"" in html
  assert sha256_csp_hash(body) in html
  assert "unsafe-eval" in html
  assert "style-src 'self' 'unsafe-inline'" in html
  assert "unsafe-inline" not in html.split("script-src")[1].split(";")[0]
  pub_html = (pub / "index.html").read_text(encoding="utf-8")
  assert "style-src 'self' 'unsafe-inline'" in pub_html
  assert "unsafe-eval" not in pub_html


def test_build_csp_policy_bokeh_style_inline_omits_style_hashes():
  from hpcperfstats.site.lib.spa_csp_meta import build_csp_policy

  policy = build_csp_policy(
      script_hashes=["'sha256-abc='"],
      style_hashes=["'sha256-style='"],
      style_attr_hashes=["'sha256-attr='"],
      allow_unsafe_eval=True,
      allow_bokeh_style_inline=True,
  )
  assert "style-src 'self' 'unsafe-inline'" in policy
  assert "'sha256-style='" not in policy
  assert "'unsafe-hashes'" not in policy
  assert "'sha256-abc='" in policy
  assert "unsafe-eval" in policy
  script_src = policy.split("script-src ")[1].split(";")[0]
  assert "unsafe-inline" not in script_src


def test_write_spa_csp_includes_writes_only_to_out_dir(tmp_path: Path):
  frontend = tmp_path / "frontend"
  out_dir = tmp_path / "etc_nginx"
  machine = frontend / "machine"
  pub = frontend / "pub"
  machine.mkdir(parents=True)
  pub.mkdir(parents=True)
  script_body = "self.__next_f=self.__next_f||[];"
  (machine / "index.html").write_text(
      f"<html><body><script>{script_body}</script></body></html>",
      encoding="utf-8",
  )
  (pub / "index.html").write_text(
      f"<html><body><script>{script_body}</script></body></html>",
      encoding="utf-8",
  )
  machine_out, pub_out = write_spa_csp_includes(frontend, out_dir)
  assert machine_out.parent == out_dir
  assert not (frontend / "nginx-csp-machine.inc").exists()
  machine_text = machine_out.read_text(encoding="utf-8")
  pub_text = pub_out.read_text(encoding="utf-8")
  assert sha256_csp_hash(script_body) in machine_text
  assert "style-src 'self' 'unsafe-inline'" in machine_text
  assert "style-src 'self' 'unsafe-inline'" in pub_text
  assert "unsafe-eval" in machine_text
  assert "unsafe-eval" not in pub_text
  assert "'unsafe-hashes'" not in machine_text


def test_inject_replaces_prior_meta():
  first = inject_csp_meta_into_html(
      "<html><head></head><body></body></html>", "default-src 'self'"
  )
  second = inject_csp_meta_into_html(first, "default-src 'none'")
  assert second.count("Content-Security-Policy") == 1
  assert "default-src 'none'" in second


def test_proxy_dockerfile_ships_spa_csp_meta_module():
  dockerfile = (_repo_root() / "services-conf" / "proxy.Dockerfile").read_text(
      encoding="utf-8"
  )
  assert "spa_csp_meta.py" in dockerfile
  conf = (_repo_root() / "services-conf" / "nginx-static-files.conf").read_text(
      encoding="utf-8"
  )
  # SPA locations must not send a stale hash CSP header (document meta is authority).
  assert "include /etc/nginx/nginx-csp-machine.inc" not in conf
  assert "include /etc/nginx/nginx-csp-pub.inc" not in conf
  assert "include /srv/static/frontend/nginx-csp" not in conf


def test_cli_module_loads():
  path = _repo_root() / "services-conf" / "write_nginx_spa_csp_includes.py"
  spec = importlib.util.spec_from_file_location("write_nginx_spa_csp_includes", path)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  assert mod._load_spa_csp_meta() is not None


def test_extract_inline_csp_hashes_forgiving_script_end_tag():
  """Browsers accept ``</script attrs>``; CSP hashing must match that body too."""
  from hpcperfstats.site.lib.spa_csp_meta import extract_inline_csp_hashes_from_html

  body = 'self.__next_f.push([1,"x"])'
  expected = sha256_csp_hash(body)
  normal = extract_inline_csp_hashes_from_html(
      f"<html><body><script>{body}</script></body></html>"
  )
  forgiving = extract_inline_csp_hashes_from_html(
      f'<html><body><script>{body}</script unused="">'
      f"</body></html>"
  )
  spaced = extract_inline_csp_hashes_from_html(
      f"<html><body><script>{body}</script ></body></html>"
  )
  assert normal["script_hashes"] == [expected]
  assert forgiving["script_hashes"] == [expected]
  assert spaced["script_hashes"] == [expected]
