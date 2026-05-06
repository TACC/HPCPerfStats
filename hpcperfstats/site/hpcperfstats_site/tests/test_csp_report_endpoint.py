"""CSP report-uri endpoint: must accept browser POSTs without CSRF tokens."""

import pytest
from django.test import Client


@pytest.mark.django_db(databases=[])
def test_csp_report_accepts_post_without_csrf_token():
    """Browsers do not send Django CSRF cookies with CSP violation reports."""
    client = Client(enforce_csrf_checks=True)
    response = client.post(
        "/csp-report/",
        data='{"csp-report":{"document-uri":"https://example.test"}}',
        content_type="application/csp-report",
    )
    assert response.status_code == 204


@pytest.mark.django_db(databases=[])
def test_csp_report_rejects_oversized_body_with_204():
    client = Client(enforce_csrf_checks=True)
    huge = b'{"x":"' + (b"a" * 70000) + b'"}'
    response = client.post(
        "/csp-report/",
        data=huge,
        content_type="application/csp-report",
    )
    assert response.status_code == 204
