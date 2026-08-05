"""Unit tests for regenerating SPA CSP includes from on-volume HTML."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _load_mod():
  path = _repo_root() / "services-conf" / "write_nginx_spa_csp_includes.py"
  spec = importlib.util.spec_from_file_location("write_nginx_spa_csp_includes", path)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_write_spa_csp_includes_writes_only_to_out_dir(tmp_path: Path):
  mod = _load_mod()
  frontend = tmp_path / "frontend"
  out_dir = tmp_path / "etc_nginx"
  machine = frontend / "machine"
  pub = frontend / "pub"
  machine.mkdir(parents=True)
  pub.mkdir(parents=True)
  script_body = "self.__next_f=self.__next_f||[];"
  style_body = "body{color:red}"
  (machine / "index.html").write_text(
      f"<html><head><style>{style_body}</style></head>"
      f'<body style="display:none"><script>{script_body}</script></body></html>',
      encoding="utf-8",
  )
  (pub / "index.html").write_text(
      f"<html><body><script>{script_body}</script></body></html>",
      encoding="utf-8",
  )

  machine_out, pub_out = mod.write_spa_csp_includes(frontend, out_dir)
  assert machine_out.parent == out_dir
  assert pub_out.parent == out_dir
  assert not (frontend / "nginx-csp-machine.inc").exists()
  assert not (frontend / "nginx-csp-pub.inc").exists()
  machine_csp = machine_out.read_text(encoding="utf-8")
  pub_csp = pub_out.read_text(encoding="utf-8")
  expected = mod.sha256_csp_hash(script_body)
  assert expected in machine_csp
  assert expected in pub_csp
  assert "unsafe-eval" in machine_csp
  assert "unsafe-eval" not in pub_csp
  assert "unsafe-inline" not in machine_csp


def test_proxy_dockerfile_and_entrypoint_keep_csp_private():
  dockerfile = (_repo_root() / "services-conf" / "proxy.Dockerfile").read_text(
      encoding="utf-8"
  )
  assert "write_nginx_spa_csp_includes.py" in dockerfile
  entry = (_repo_root() / "services-conf" / "proxy_entrypoint.sh").read_text(
      encoding="utf-8"
  )
  assert '--out-dir "${CSP_OUT_DIR}"' in entry
  assert 'CSP_OUT_DIR="${HPCPERFSTATS_PROXY_CSP_OUT_DIR:-/etc/nginx}"' in entry
  conf = (_repo_root() / "services-conf" / "nginx-static-files.conf").read_text(
      encoding="utf-8"
  )
  assert "include /etc/nginx/nginx-csp-machine.inc" in conf
  assert "include /srv/static/frontend/nginx-csp" not in conf
