"""Security and contract tests for user API key JSON endpoints."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client

from hpcperfstats.site.machine.models import ApiKey

from .csrf_test_utils import csrf_headers

pytestmark = pytest.mark.django_db(databases=[])


def _csrf_headers(client):
  from django.middleware.csrf import get_token
  from django.test import RequestFactory

  request = RequestFactory().get("/")
  request.session = client.session
  return {"HTTP_X_CSRFTOKEN": get_token(request)}


class TestUserApiKeyApiSecurity:
  def test_rotate_requires_csrf_token_when_enforced(self):
    client = Client(enforce_csrf_checks=True)
    session = client.session
    session["access_token"] = "token"
    session["username"] = "alice"
    session["is_staff"] = False
    session.save()

    response = client.post("/api/user-api-key/rotate/")

    assert response.status_code == 403

  def test_status_requires_session(self):
    client = Client()
    response = client.get("/api/user-api-key/")
    assert response.status_code == 401
    payload = response.json()
    assert payload.get("login_url") == "/login_prompt"

  def test_status_returns_prefix_when_key_exists(self):
    client = Client()
    session = client.session
    session["access_token"] = "token"
    session["username"] = "alice"
    session["is_staff"] = False
    session.save()

    active_key = SimpleNamespace(key_prefix="abc123def456")
    list_queryset = MagicMock()
    list_queryset.order_by.return_value.first.return_value = active_key

    with patch("hpcperfstats.site.machine.api.ApiKey.objects.filter", return_value=list_queryset):
      response = client.get("/api/user-api-key/")

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert body["raw_key"] is None
    assert body["key_prefix"] == "abc123def456"

  def test_status_mints_key_when_missing(self):
    client = Client()
    session = client.session
    session["access_token"] = "token"
    session["username"] = "alice"
    session["is_staff"] = False
    session.save()

    empty_qs = MagicMock()
    empty_qs.order_by.return_value.first.return_value = None
    new_obj = SimpleNamespace(key_prefix="deadbeef1234")

    with patch("hpcperfstats.site.machine.api.ApiKey.objects.filter", return_value=empty_qs), patch(
        "hpcperfstats.site.machine.api.ApiKey.create_from_raw_key",
        return_value=(new_obj, "fresh-raw-key-hex"),
    ):
      response = client.get("/api/user-api-key/")

    assert response.status_code == 200
    body = response.json()
    assert body["raw_key"] == "fresh-raw-key-hex"
    assert body["key_prefix"] == "deadbeef1234"

  def test_rotate_returns_new_raw_key(self):
    client = Client()
    session = client.session
    session["access_token"] = "token"
    session["username"] = "alice"
    session["is_staff"] = False
    session.save()

    filtered = MagicMock()
    filtered.update.return_value = 1
    rotated = SimpleNamespace(key_prefix="rot456")

    with patch("hpcperfstats.site.machine.api.ApiKey.objects.filter", return_value=filtered), patch(
        "hpcperfstats.site.machine.api.ApiKey.create_from_raw_key",
        return_value=(rotated, "raw-new-api-key"),
    ):
      response = client.post(
          "/api/user-api-key/rotate/",
          **_csrf_headers(client),
      )

    assert response.status_code == 200
    assert response.json()["raw_key"] == "raw-new-api-key"
    assert response.json()["key_prefix"] == "rot456"
    filtered.update.assert_called_once_with(is_active=False)

  def test_key_is_stored_hashed_not_plaintext_response_field(self):
    """Raw key in JSON is the one-time display value; DB stores hash via model helper."""
    raw = "a" * 64
    assert ApiKey.hash_raw_key(raw) != raw


class TestSessionMutatingPostCsrf:
  """Regression: all browser-mutable session POST endpoints require X-CSRFToken."""

  def _staff_session(self, client):
    session = client.session
    session["access_token"] = "token"
    session["username"] = "alice"
    session["is_staff"] = True
    session.save()
    return session

  def test_drop_staff_requires_csrf_header(self):
    client = Client()
    self._staff_session(client)
    response = client.post("/api/session/drop-staff/")
    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token missing"

  def test_drop_staff_succeeds_with_csrf_header(self):
    client = Client()
    self._staff_session(client)
    response = client.post("/api/session/drop-staff/", **csrf_headers())
    assert response.status_code == 200
    assert response.json()["is_staff"] is False

  def test_invalidate_cache_requires_csrf_header(self):
    client = Client()
    self._staff_session(client)
    response = client.post(
        "/api/cache/invalidate-page/",
        data='{"page_path": "/machine/jobs/"}',
        content_type="application/json",
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token missing"

  def test_sacct_ingest_requires_csrf_header(self):
    client = Client()
    self._staff_session(client)
    response = client.post(
        "/api/sacct/ingest/?date=2024-01-01",
        data="jid|user|acct",
        content_type="text/plain",
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token missing"

  def test_sacct_ingest_accepts_staff_api_key_without_csrf(self):
    """hpcperfstats-tools sacct_gen posts with X-API-Key only (no CSRF cookie)."""
    from hpcperfstats.site.machine import api

    client = Client()
    key_obj = SimpleNamespace(username="pipeline", is_staff=True)
    with patch.object(api.ApiKey, "hash_raw_key", return_value="hashed"), patch.object(
        api.ApiKey.objects, "get", return_value=key_obj
    ), patch.object(api, "persist_accounting_daily_file"), patch.object(
        api.job_data.objects, "filter"
    ) as mock_filter:
      mock_filter.return_value.values_list.return_value.iterator.return_value = iter([])
      response = client.post(
          "/api/sacct/ingest/?date=2024-01-01",
          data="",
          content_type="text/plain",
          HTTP_X_API_KEY="a" * 64,
      )
    assert response.status_code == 200
    assert response.json()["inserted"] == 0

  def test_sacct_ingest_rejects_non_staff_api_key_without_csrf(self):
    from hpcperfstats.site.machine import api

    client = Client()
    key_obj = SimpleNamespace(username="reader", is_staff=False)
    with patch.object(api.ApiKey, "hash_raw_key", return_value="hashed"), patch.object(
        api.ApiKey.objects, "get", return_value=key_obj
    ):
      response = client.post(
          "/api/sacct/ingest/?date=2024-01-01",
          data="body",
          content_type="text/plain",
          HTTP_X_API_KEY="b" * 64,
      )
    assert response.status_code == 403
    assert response.json()["error"] == "Staff access required"
