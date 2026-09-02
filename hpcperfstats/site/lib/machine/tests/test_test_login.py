"""Unit tests for the INI-gated hidden test-login HTML page and staff API."""

from unittest.mock import MagicMock, patch

import pytest
from django.http import Http404
from django.test import RequestFactory
from rest_framework import status

from .csrf_test_utils import csrf_headers

pytestmark = pytest.mark.machine_unit_mock


class _Session(dict):
  """Dict session with Django-like cycle_key/flush for view tests."""

  def cycle_key(self):
    self["_cycled"] = True

  def flush(self):
    self.clear()


def test_test_login_404_when_disabled():
  from hpcperfstats.site.lib.machine import test_login

  request = RequestFactory().get("/test-login/")
  request.session = _Session()
  with patch.object(test_login.cfg, "get_separate_test_login", return_value=False):
    with pytest.raises(Http404):
      test_login.test_login_page(request)
  post = RequestFactory().post("/test-login/", {"username": "a", "password": "b"})
  post.session = _Session()
  with patch.object(test_login.cfg, "get_separate_test_login", return_value=False):
    with pytest.raises(Http404):
      test_login.test_login_page(post)


def test_test_login_success_sets_staff_session():
  from hpcperfstats.site.lib.machine import test_login

  user = MagicMock()
  user.username = "qa"
  user.check_password.return_value = True
  request = RequestFactory().post(
      "/test-login/", {"username": "qa", "password": "secret12"}
  )
  request.session = _Session()
  with patch.object(test_login.cfg, "get_separate_test_login", return_value=True), patch(
      "hpcperfstats.site.lib.machine.test_login.TestLoginUser.get_singleton",
      return_value=user,
  ):
    response = test_login.test_login_page(request)
  assert response.status_code == 302
  assert response.url == "/machine/"
  assert request.session["username"] == "qa"
  assert request.session["is_staff"] is True
  assert request.session["email"] == "qa@test-login.local"
  assert request.session["access_token"] == "test-login:qa"
  assert request.session.get("_cycled") is True


def test_test_login_rejects_bad_password():
  from hpcperfstats.site.lib.machine import test_login

  user = MagicMock()
  user.username = "qa"
  user.check_password.return_value = False
  request = RequestFactory().post(
      "/test-login/", {"username": "qa", "password": "wrong"}
  )
  request.session = _Session()
  with patch.object(test_login.cfg, "get_separate_test_login", return_value=True), patch(
      "hpcperfstats.site.lib.machine.test_login.TestLoginUser.get_singleton",
      return_value=user,
  ), patch(
      "hpcperfstats.site.lib.machine.test_login.render",
      return_value=MagicMock(status_code=200, content=b"Invalid username or password."),
  ) as mock_render:
    response = test_login.test_login_page(request)
  assert response.status_code == 200
  assert "access_token" not in request.session
  assert request.session.get("is_staff") is None
  context = mock_render.call_args.args[2]
  assert context["error"] == "Invalid username or password."


def test_test_login_api_404_when_disabled():
  from hpcperfstats.site.lib.machine import api

  request = RequestFactory().get("/api/test-login/user/")
  request.session = {"is_staff": True}
  with patch(
      "hpcperfstats.site.lib.machine.test_login.cfg.get_separate_test_login",
      return_value=False,
  ):
    response = api.test_login_user(request)
  assert response.status_code == 404


def test_test_login_api_requires_staff():
  from hpcperfstats.site.lib.machine import api

  request = RequestFactory().get("/api/test-login/user/")
  request.session = {"is_staff": False, "access_token": "tok"}
  with patch(
      "hpcperfstats.site.lib.machine.test_login.cfg.get_separate_test_login",
      return_value=True,
  ), patch(
      "hpcperfstats.site.lib.machine.api._require_staff",
      return_value=api.Response(
          {"error": "Staff access required"},
          status=status.HTTP_403_FORBIDDEN,
      ),
  ):
    response = api.test_login_user(request)
  assert response.status_code == 403


def test_test_login_api_create_replace_never_returns_hash():
  from hpcperfstats.site.lib.machine import api

  saved = MagicMock()
  saved.username = "qa"
  saved.password_hash = "pbkdf2_should_never_leak"
  request = RequestFactory().post(
      "/api/test-login/user/",
      {"username": "qa", "password": "secret12"},
      content_type="application/json",
      **csrf_headers(),
  )
  request.session = {"username": "staffer", "is_staff": True}
  request.data = {"username": "qa", "password": "secret12"}
  with patch(
      "hpcperfstats.site.lib.machine.test_login.cfg.get_separate_test_login",
      return_value=True,
  ), patch(
      "hpcperfstats.site.lib.machine.api._require_staff",
      return_value=None,
  ), patch(
      "hpcperfstats.site.lib.machine.api._require_csrf_for_session_post",
      return_value=None,
  ), patch(
      "hpcperfstats.site.lib.machine.api.TestLoginUser.replace_singleton",
      return_value=saved,
  ) as mock_replace, patch(
      "hpcperfstats.site.lib.machine.api.TestLoginUser.get_singleton",
      return_value=saved,
  ):
    response = api.test_login_user(request)
  mock_replace.assert_called_once_with("qa", "secret12", created_by="staffer")
  assert response.status_code == 200
  data = response.data
  assert data["configured"] is True
  assert data["username"] == "qa"
  assert data["login_url"] == "/test-login/"
  assert "password" not in data
  assert "password_hash" not in data
  dumped = str(data)
  assert "pbkdf2_should_never_leak" not in dumped
