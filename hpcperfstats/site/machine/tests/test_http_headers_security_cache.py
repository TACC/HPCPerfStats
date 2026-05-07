from django.test import Client

from hpcperfstats.site.hpcperfstats_site.middleware import (
  DEFAULT_COOP,
  DEFAULT_CSP,
  DEFAULT_CSP_REPORT_ONLY,
  DEFAULT_PERMISSIONS_POLICY,
)


def test_spa_index_keeps_explicit_cache_control_from_view():
  client = Client()
  response = client.get("/machine/", secure=True)

  assert response.status_code == 200
  assert response["Cache-Control"] == "public, max-age=300"
  assert response["X-Frame-Options"] == "SAMEORIGIN"
  assert "Strict-Transport-Security" in response
  assert response["Permissions-Policy"] == DEFAULT_PERMISSIONS_POLICY
  assert response["Cross-Origin-Opener-Policy"] == DEFAULT_COOP
  assert response["Content-Security-Policy"] == DEFAULT_CSP
  assert response["Content-Security-Policy-Report-Only"] == DEFAULT_CSP_REPORT_ONLY


def test_security_headers_are_not_overwritten_if_already_set_by_view():
  # Use the CSP report endpoint to verify middleware doesn't overwrite explicit values.
  client = Client()
  response = client.post(
    "/csp-report/",
    data="{}",
    content_type="application/csp-report",
    secure=True,
  )

  assert response.status_code == 204
  assert response["Permissions-Policy"] == DEFAULT_PERMISSIONS_POLICY
  assert response["Cross-Origin-Opener-Policy"] == DEFAULT_COOP
  assert response["Content-Security-Policy"] == DEFAULT_CSP
  assert response["Content-Security-Policy-Report-Only"] == DEFAULT_CSP_REPORT_ONLY

