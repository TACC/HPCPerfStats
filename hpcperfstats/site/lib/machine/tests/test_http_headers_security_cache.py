from django.test import Client

import pytest

from hpcperfstats.site.hpcperfstats_site.middleware import (
  DEFAULT_COOP,
  DEFAULT_CSP,
  DEFAULT_CSP_NO_ACTIVE,
  DEFAULT_CSP_REPORT_ONLY,
  DEFAULT_CSP_STRICT,
  DEFAULT_PERMISSIONS_POLICY,
)


pytestmark = pytest.mark.django_db(databases=[])


def test_spa_shell_not_served_by_wsgi():
  """Production serves /machine/ from nginx; Gunicorn must not answer the SPA shell."""
  client = Client()
  response = client.get("/machine/", secure=True)

  assert response.status_code == 404


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
  assert response["Content-Security-Policy"] == DEFAULT_CSP_NO_ACTIVE
  assert response["Content-Security-Policy-Report-Only"] == DEFAULT_CSP_REPORT_ONLY
  assert "unsafe-inline" not in response["Content-Security-Policy"]
  assert "unsafe-eval" not in DEFAULT_CSP_REPORT_ONLY


def test_api_json_routes_use_no_active_csp():
  client = Client()
  response = client.get("/api/jobs/", secure=True)
  assert response.status_code in (200, 302, 401, 403)
  assert response["Content-Security-Policy"] == DEFAULT_CSP_NO_ACTIVE
  assert "unsafe-inline" not in response["Content-Security-Policy"]
  assert "unsafe-eval" not in response["Content-Security-Policy"]


def test_middleware_csp_constants_drop_unsafe_inline():
  assert "unsafe-inline" not in DEFAULT_CSP
  assert "unsafe-inline" not in DEFAULT_CSP_STRICT
  assert "unsafe-inline" not in DEFAULT_CSP_NO_ACTIVE
  assert "unsafe-inline" not in DEFAULT_CSP_REPORT_ONLY
  assert "unsafe-eval" in DEFAULT_CSP
  assert "unsafe-eval" not in DEFAULT_CSP_STRICT
  assert "script-src 'none'" in DEFAULT_CSP_NO_ACTIVE
  assert "style-src 'none'" in DEFAULT_CSP_NO_ACTIVE
