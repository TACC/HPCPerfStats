"""Security and contract tests for user API key JSON endpoints."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import Client

from hpcperfstats.site.machine.models import ApiKey


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
