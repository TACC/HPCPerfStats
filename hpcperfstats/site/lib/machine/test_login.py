"""
Hidden Django HTML login for the INI-gated development test user.

Attributes:
  INVALID_CREDENTIALS_MESSAGE: Form error when username or password is wrong.
  MAX_TEST_LOGIN_PASSWORD_LEN: Upper bound for a staff-chosen password.
  MAX_TEST_LOGIN_USERNAME_LEN: Upper bound for a staff-chosen username.
  MIN_TEST_LOGIN_PASSWORD_LEN: Minimum accepted password length.
  TEST_LOGIN_EMAIL_DOMAIN: Synthetic session email domain.
  TEST_LOGIN_PATH: Hidden Django login URL (not under ``/machine/``).
  TEST_LOGIN_TOKEN_PREFIX: Session ``access_token`` prefix that skips Tapis.
"""
from __future__ import annotations

from typing import Any

import time

from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

import hpcperfstats.dbload.lib.conf_parser as cfg

from .models import TestLoginUser

TEST_LOGIN_TOKEN_PREFIX = "test-login:"
TEST_LOGIN_EMAIL_DOMAIN = "test-login.local"
TEST_LOGIN_PATH = "/test-login/"
INVALID_CREDENTIALS_MESSAGE = "Invalid username or password."
MAX_TEST_LOGIN_USERNAME_LEN = 128
MIN_TEST_LOGIN_PASSWORD_LEN = 8
MAX_TEST_LOGIN_PASSWORD_LEN = 256


def validate_test_login_credentials(username: str, password: str) -> str:
  """
  Return a validation error, or empty string when username and password are ok.

  Args:
    username (str): Staff-chosen test-login username.
    password (str): Staff-chosen plaintext password.

  Returns:
    str: Empty on success; otherwise a generic operator-facing error.

  Examples:
    >>> validate_test_login_credentials("qa", "secret12")
    ''
    >>> validate_test_login_credentials("", "secret12")
    'Username and password are required.'
  """
  name = username.strip()
  if not name or not password:
    return "Username and password are required."
  if len(name) > MAX_TEST_LOGIN_USERNAME_LEN:
    return "Username is too long."
  if any(ch.isspace() for ch in name):
    return "Username cannot contain spaces."
  if len(password) < MIN_TEST_LOGIN_PASSWORD_LEN:
    return "Password must be at least 8 characters."
  if len(password) > MAX_TEST_LOGIN_PASSWORD_LEN:
    return "Password is too long."
  return ""


def test_login_user_payload() -> dict[str, bool | str | None]:
  """
  Return the staff API JSON shape without password material.

  Returns:
    dict[str, bool | str | None]: ``configured``, ``username``, ``login_url``.

  Examples:
    >>> {"configured": False, "username": None, "login_url": TEST_LOGIN_PATH}["login_url"]
    '/test-login/'
  """
  user = TestLoginUser.get_singleton()
  return {
      "configured": user is not None,
      "username": user.username if user is not None else None,
      "login_url": TEST_LOGIN_PATH,
  }


def require_separate_test_login() -> None:
  """
  Raise Http404 when the development-only test-login flag is off.

  Returns:
    None

  Raises:
    Http404: Raised when ``get_separate_test_login()`` is False.

  Examples:
    >>> from unittest.mock import patch
    >>> with patch.object(cfg, "get_separate_test_login", return_value=False):
    ...     try:
    ...         require_separate_test_login()
    ...     except Http404:
    ...         "hidden"
    'hidden'
  """
  if not cfg.get_separate_test_login():
    raise Http404()


def mint_test_login_session(request: Any, username: str) -> None:
  """
  Store a synthetic staff session for a successful test-login POST.

  Args:
    request (Any): Django request whose session is mutated.
    username (str): Configured test-login username.

  Returns:
    None

  Examples:
    >>> class _Req:
    ...     session = {}
    >>> req = _Req()
    >>> mint_test_login_session(req, "qa")
    >>> req.session["access_token"]
    'test-login:qa'
  """
  session = request.session
  cycle_key = getattr(session, "cycle_key", None)
  if callable(cycle_key):
    cycle_key()
  now_epoch = int(time.time())
  session["username"] = username
  session["email"] = f"{username}@{TEST_LOGIN_EMAIL_DOMAIN}"
  session["is_staff"] = True
  session["access_token"] = f"{TEST_LOGIN_TOKEN_PREFIX}{username}"
  session["oauth_login_epoch"] = now_epoch
  session["oauth_last_seen_epoch"] = now_epoch
  session["oauth_last_validated_epoch"] = now_epoch
  session["oauth_access_token_expiry_epoch"] = now_epoch + 86400
  if hasattr(session, "modified"):
    session.modified = True


@require_http_methods(["GET", "POST"])
def test_login_page(request: Any) -> Any:
  """
  Render or authenticate the hidden test-login form.

  Args:
    request (Any): Django GET or POST for ``/test-login/``.

  Returns:
    Any: Redirect to ``/machine/`` on success, or an HTML form response.

  Raises:
    Http404: Raised when the INI flag is off.

  Examples:
    >>> from unittest.mock import patch
    >>> from django.test import RequestFactory
    >>> req = RequestFactory().get(TEST_LOGIN_PATH)
    >>> req.session = {}
    >>> with patch.object(cfg, "get_separate_test_login", return_value=False):
    ...     try:
    ...         test_login_page(req)
    ...     except Http404:
    ...         404
    404
  """
  require_separate_test_login()
  error = ""
  if request.method == "POST":
    username = str(request.POST.get("username") or "").strip()
    password = str(request.POST.get("password") or "")
    user = TestLoginUser.get_singleton()
    if (
        user is not None
        and username
        and username == user.username
        and user.check_password(password)
    ):
      mint_test_login_session(request, user.username)
      return HttpResponseRedirect("/machine/")
    error = INVALID_CREDENTIALS_MESSAGE
  return render(
      request,
      "machine/test_login.html",
      {"error": error},
  )
