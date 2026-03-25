from django.test import Client


def test_robots_txt_has_default_cache_and_security_headers_for_https():
  client = Client()
  response = client.get("/robots.txt", secure=True)

  assert response.status_code == 200
  assert response["Cache-Control"] == "no-store, no-cache"
  assert response["X-Frame-Options"] == "SAMEORIGIN"
  assert response["Permissions-Policy"] == "geolocation=(), microphone=(), camera=()"
  assert response["Cross-Origin-Opener-Policy"] == "same-origin"

  hsts = response["Strict-Transport-Security"]
  assert "max-age=31536000" in hsts
  assert "includeSubDomains" in hsts


def test_robots_txt_security_headers_for_http_do_not_include_hsts():
  client = Client()
  response = client.get("/robots.txt")

  assert response.status_code == 200
  assert response["Cache-Control"] == "no-store, no-cache"
  assert response["X-Frame-Options"] == "SAMEORIGIN"
  assert response["Permissions-Policy"] == "geolocation=(), microphone=(), camera=()"
  assert response["Cross-Origin-Opener-Policy"] == "same-origin"
  assert "Strict-Transport-Security" not in response


def test_spa_index_keeps_explicit_cache_control_from_view():
  client = Client()
  response = client.get("/machine/", secure=True)

  assert response.status_code == 200
  assert response["Cache-Control"] == "public, max-age=300"
  assert response["X-Frame-Options"] == "SAMEORIGIN"
  assert "Strict-Transport-Security" in response

