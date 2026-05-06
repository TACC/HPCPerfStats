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
  response = client.get(reverse("robots_txt"))
  assert response.status_code == 200


def test_nginx_static_files_conf_returns_favicon_at_edge():
  """Production proxy answers /favicon.ico without involving Django (currently 404)."""
  repo_root = Path(__file__).resolve().parents[4]
  conf = (repo_root / "services-conf" / "nginx-static-files.conf").read_text(encoding="utf-8")
  assert "location = /favicon.ico" in conf
  assert "return 404" in conf
