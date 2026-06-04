"""Regression: WSGI must not answer STATIC_URL; nginx serves /static/ in production."""

from pathlib import Path

from django.test import Client
from django.urls import reverse


def test_wsgi_client_does_not_serve_static_url_prefix():
  """If Django adds static() routes or static middleware, this contract breaks."""
  client = Client()
  response = client.get("/static/frontend/__nginx_only_wsgi_contract__.txt")
  assert response.status_code == 404


def test_wsgi_client_does_not_serve_spa_shell_routes():
  """The SPA shell is nginx-owned; Django WSGI must not answer /machine/*."""
  client = Client()
  assert client.get("/machine/").status_code == 404
  assert client.get("/machine/jobs/").status_code == 404
  assert client.get("/pub/").status_code == 404


def test_wsgi_resolves_known_app_route():
  """Sanity: Client reaches urlpatterns (avoid false pass on generic 404)."""
  client = Client()
  response = client.get(reverse("csp_report"))
  assert response.status_code == 405


def test_wsgi_robots_txt_is_owned_by_nginx_not_wsgi():
  """Production serves /robots.txt from static files at the edge; Gunicorn must not answer it."""
  client = Client()
  assert client.get("/robots.txt").status_code == 404


def test_nginx_static_files_conf_robots_txt_is_static_with_edge_headers():
  repo_root = Path(__file__).resolve().parents[4]
  conf = (repo_root / "services-conf" / "nginx-static-files.conf").read_text(encoding="utf-8")
  assert "location = /robots.txt" in conf
  assert "alias /srv/static/frontend/robots.txt" in conf
  assert "include /etc/nginx/nginx-edge-security-headers.inc" in conf
  edge = (repo_root / "services-conf" / "nginx-edge-security-headers.inc").read_text(encoding="utf-8")
  assert "add_header Cache-Control" in edge
  assert "add_header X-Content-Type-Options" in edge
  assert "nosniff" in edge


def test_nginx_static_files_conf_returns_favicon_at_edge():
  """Production proxy answers /favicon.ico without involving Django (currently 404)."""
  repo_root = Path(__file__).resolve().parents[4]
  conf = (repo_root / "services-conf" / "nginx-static-files.conf").read_text(encoding="utf-8")
  assert "location = /favicon.ico" in conf
  assert "return 404" in conf


def test_nginx_static_files_conf_sets_nosniff_and_html_types_on_static_locations():
  """Nginx-served paths get nosniff; SPA shells get explicit HTML typing (not proxied to Django)."""
  repo_root = Path(__file__).resolve().parents[4]
  conf = (repo_root / "services-conf" / "nginx-static-files.conf").read_text(encoding="utf-8")
  assert conf.count('add_header X-Content-Type-Options "nosniff" always') >= 6
  assert conf.count("default_type text/html") == 2
  assert conf.count("charset utf-8") == 2


def test_nginx_static_files_conf_allowlists_django_prefixes_and_default_404():
  """Proxy must not forward unknown paths; Django routes are enumerated explicitly."""
  repo_root = Path(__file__).resolve().parents[4]
  conf = (repo_root / "services-conf" / "nginx-static-files.conf").read_text(encoding="utf-8")
  proxy_inc = "/etc/nginx/nginx-django-proxy-common.inc"
  assert proxy_inc in conf
  assert "try_files /frontend/pub.html =503" in conf
  for needle in (
      "\nlocation = / {\n",
      "location ^~ /api/",
      "location = /robots.txt",
      "location ^~ /csp-report/",
      "location ^~ /api-key/",
      "location ^~ /admin_monitor/",
      "location ^~ /login/",
      "location = /login_prompt",
      "location ^~ /logout/",
      "location ^~ /oauth_callback/",
      "location / {",
      "return 404;",
  ):
    assert needle in conf
  common = (repo_root / "services-conf" / "nginx-django-proxy-common.inc").read_text(encoding="utf-8")
  assert "proxy_pass" not in common
  assert "proxy_set_header Host $host;" in common
