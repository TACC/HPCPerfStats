"""Unit tests for oauth2 helpers (no live Tapis calls)."""

from unittest.mock import MagicMock, patch

from django.http import HttpResponseRedirect
from django.test import RequestFactory
from django.test import override_settings


class TestSafeRedirectPath:
  def test_rejects_empty_or_unsafe(self):
    from hpcperfstats.site.machine import oauth2

    assert oauth2._safe_redirect_path("") is None
    assert oauth2._safe_redirect_path("//evil.com") is None
    assert oauth2._safe_redirect_path("/ok\\x") is None

  def test_accepts_same_origin_path(self):
    from hpcperfstats.site.machine import oauth2

    assert oauth2._safe_redirect_path("/machine/") == "/machine/"


class TestCheckForTokens:
  def test_false_without_token(self):
    from hpcperfstats.site.machine import oauth2

    request = RequestFactory().get("/")
    request.session = {}
    assert oauth2.check_for_tokens(request) is False

  def test_true_with_access_token(self):
    from hpcperfstats.site.machine import oauth2

    request = RequestFactory().get("/")
    request.session = {"access_token": "t"}
    assert oauth2.check_for_tokens(request) is True

  @override_settings(SESSION_IDLE_TIMEOUT_SECONDS=1, SESSION_ABSOLUTE_TIMEOUT_SECONDS=3600)
  def test_false_when_idle_timeout_exceeded(self):
    from hpcperfstats.site.machine import oauth2

    request = RequestFactory().get("/")
    request.session = {
        "access_token": "tok",
        "oauth_login_epoch": 100,
        "oauth_last_seen_epoch": 100,
        "oauth_last_validated_epoch": 100,
    }
    with patch("hpcperfstats.site.machine.oauth2.time.time", return_value=103):
      assert oauth2.check_for_tokens(request) is False

  def test_refreshes_access_token_when_expiring(self):
    from hpcperfstats.site.machine import oauth2

    request = RequestFactory().get("/")
    request.session = {
        "access_token": "old",
        "refresh_token": "r1",
        "oauth_login_epoch": 100,
        "oauth_last_seen_epoch": 100,
        "oauth_last_validated_epoch": 100,
        "oauth_access_token_expiry_epoch": 110,
    }
    mock_post = MagicMock()
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "result": {
            "access_token": {"access_token": "new", "expires_in": 3600},
            "refresh_token": {"refresh_token": "r2"},
        }
    }
    with patch.object(oauth2, "_http_session", MagicMock(post=mock_post)), patch(
        "hpcperfstats.site.machine.oauth2.time.time", return_value=109
    ):
      assert oauth2.check_for_tokens(request) is True
    assert request.session["access_token"] == "new"
    assert request.session["refresh_token"] == "r2"


class TestLoginOauth:
  def test_redirects_and_sets_state(self):
    from hpcperfstats.site.machine import oauth2

    request = RequestFactory().get("/login?next=/jobs")
    request.session = {}
    with patch.object(oauth2, "reverse", return_value="/oauth_callback/"), patch.object(
        oauth2.cfg, "get_server_name", return_value="example.com"
    ), patch.object(
        oauth2.cfg, "get_oauth_authorize_url", return_value="https://idp/authorize?r=%s&s=%s"
    ):
      response = oauth2.login_oauth(request)
    assert isinstance(response, HttpResponseRedirect)
    assert "https://idp/authorize" in response.url
    assert "auth_state" in request.session
    assert request.session.get("auth_next") == "/jobs"


class TestOauthCallback:
  def test_state_mismatch_redirects_logout(self):
    from hpcperfstats.site.machine import oauth2

    request = RequestFactory().get("/cb?state=bad&code=x")
    request.session = {"auth_state": "good"}
    response = oauth2.oauth_callback(request)
    assert response.url == "/logout"

  def test_success_sets_session_and_redirects(self):
    from hpcperfstats.site.machine import oauth2

    request = RequestFactory().get("/cb?state=abc&code=ccc")
    request.session = {"auth_state": "abc", "auth_next": "/host/"}
    token_json = {
        "result": {
            "access_token": {"access_token": "atok"},
            "refresh_token": {"refresh_token": "rtok"},
        }
    }
    user_json = {
        "result": {
            "username": "alice",
            "email": "alice@staff.example.edu",
        }
    }
    mock_post = MagicMock()
    mock_post.return_value.json.return_value = token_json
    mock_get = MagicMock()
    mock_get.return_value.json.return_value = user_json
    session = MagicMock()
    session.post = mock_post
    session.get = mock_get
    with patch.object(oauth2, "reverse", return_value="/oauth_callback/"), patch.object(
        oauth2.cfg, "get_server_name", return_value="example.com"
    ), patch.object(
        oauth2, "staff_email_domain", "staff.example.edu"
    ), patch.object(
        oauth2, "_http_session", session
    ):
      response = oauth2.oauth_callback(request)
    assert isinstance(response, HttpResponseRedirect)
    assert response.url == "/host/"
    assert request.session["username"] == "alice"
    assert request.session["is_staff"] is True


class TestLoginPrompt:
  def test_redirects_to_login_when_not_authenticated(self):
    from hpcperfstats.site.machine import oauth2

    request = RequestFactory().get("/login_prompt")
    request.session = {}
    with patch.object(oauth2, "check_for_tokens", return_value=False), patch.object(
        oauth2, "reverse", return_value="/login/"
    ):
      response = oauth2.login_prompt(request)
    assert isinstance(response, HttpResponseRedirect)
    assert response.url.startswith("/login/")
