"""OAuth2 login, callback, logout, and token check for Tapis. Session stores access_token, refresh_token, username, email, is_staff.

"""
import logging
import os
import time
from urllib.parse import quote

import requests
from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse
from requests.auth import HTTPBasicAuth

import hpcperfstats.conf_parser as cfg

logging.basicConfig()
logger = logging.getLogger('logger')

client_id = cfg.get_oauth_client_id()
client_key = cfg.get_oauth_client_key()
tenant_base_url = cfg.get_oauth_base_url()
staff_email_domain = cfg.get_staff_email_domain()
server_name = cfg.get_server_name().split(',')[0]

# Shared session for OAuth2 token and userinfo requests (connection reuse).
_http_session = requests.Session()
_TOKEN_VALIDATE_INTERVAL_SECONDS = 300
_TOKEN_REFRESH_SKEW_SECONDS = 60


def _get_redirect_uri():
  """Build OAuth2 redirect_uri (no trailing slash) for this server."""
  uri = 'https://{}{}'.format(server_name, reverse('oauth_callback'))
  return uri[:-1] if uri.endswith('/') else uri


def _safe_redirect_path(path):
  """Return path if it is a safe same-origin redirect (starts with /, not //), else None."""
  if not path or not path.startswith('/') or path.startswith('//') or '\\' in path:
    return None
  return path


def login_oauth(request):
  """Redirect to OAuth2 authorize URL with state; store state and optional next in session.

    """
  session = request.session
  session['auth_state'] = os.urandom(24).hex()
  next_url = request.GET.get('next', '')
  if _safe_redirect_path(next_url):
    session['auth_next'] = next_url

  redirect_uri = _get_redirect_uri()
  authorization_url = (cfg.get_oauth_authorize_url() %
                       (redirect_uri, session['auth_state']))
  return HttpResponseRedirect(authorization_url)


def oauth_callback(request):
  """Exchange code for tokens, fetch userinfo, set session (access_token, username, is_staff by email domain), redirect to /.

    """
  state = request.GET.get('state')
  saved_state = request.session.get('auth_state')

  if not saved_state or saved_state != state:
    return HttpResponseRedirect('/logout')

  if 'code' in request.GET:
    redirect_uri = _get_redirect_uri()
    code = request.GET['code']
    body = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri
    }

    response = _http_session.post('%s/oauth2/tokens' % tenant_base_url,
                                  json=body,
                                  auth=HTTPBasicAuth(client_id, client_key))
    token_data = response.json()

    headers = {
        'x-tapis-token': token_data["result"]["access_token"]["access_token"]
    }
    user_response = _http_session.get('%s/oauth2/userinfo' % tenant_base_url,
                                      headers=headers)
    user_data = user_response.json()

    request.session['access_token'] = token_data["result"]["access_token"][
        "access_token"]
    request.session['refresh_token'] = token_data["result"]["refresh_token"][
        "refresh_token"]
    now_epoch = int(time.time())
    request.session['oauth_login_epoch'] = now_epoch
    request.session['oauth_last_seen_epoch'] = now_epoch
    request.session['oauth_last_validated_epoch'] = now_epoch
    request.session['oauth_access_token_expiry_epoch'] = _extract_access_token_expiry_epoch(
        token_data, now_epoch
    )
    request.session['username'] = user_data['result']['username']

    # For now we determine whether a user is staff by seeing if hey have a specific email domain set in ini
    request.session['email'] = user_data['result']['email']
    request.session['is_staff'] = user_data['result']['email'].split(
        '@')[-1] == staff_email_domain
    next_url = request.session.pop('auth_next', None)
    redirect_to = next_url if _safe_redirect_path(next_url) else '/'
    return HttpResponseRedirect(redirect_to)


def logout(request):
  """Revoke token, flush session, redirect to /.

    """
  access_token = request.session.get('access_token')
  if access_token:
    _http_session.post('%s/oauth2/tokens/revoke' % tenant_base_url,
                       json={'token': access_token})
  request.session.flush()
  return HttpResponseRedirect("/")


def login_prompt(request):
  """Redirect to OAuth login unless already authenticated; then redirect to next or /.

    """
  next_url = request.GET.get('next', '')
  if check_for_tokens(request):
    redirect_to = next_url if _safe_redirect_path(next_url) else '/'
    return HttpResponseRedirect(redirect_to)
  login_url = reverse('login') + ('?next=' + quote(next_url) if next_url else '')
  return HttpResponseRedirect(login_url)


def check_for_tokens(request):
  """Return True if session has access_token, else False.

    """
  try:
    session = request.session
    access_token = request.session.get("access_token")
    if access_token and str(access_token).startswith("api-key:"):
      return True
    if access_token:
      now_epoch = int(time.time())
      if _session_expired(session, now_epoch):
        session.flush()
        return False
      if _token_needs_refresh(session, now_epoch):
        if not _refresh_access_token(session, now_epoch):
          session.flush()
          return False
        access_token = session.get("access_token")
      if _token_validation_due(session, now_epoch):
        if not _validate_access_token(access_token):
          if not _refresh_access_token(session, now_epoch):
            session.flush()
            return False
        session["oauth_last_validated_epoch"] = now_epoch
      session["oauth_last_seen_epoch"] = now_epoch
      return True
  except Exception:
    return False
  return False


def _extract_access_token_expiry_epoch(token_data, now_epoch):
  """Read token expiry from Tapis response; fallback to one hour."""
  token = ((token_data or {}).get("result") or {}).get("access_token") or {}
  expires_at = token.get("expires_at")
  if isinstance(expires_at, (int, float)) and int(expires_at) > now_epoch:
    return int(expires_at)
  expires_in = token.get("expires_in")
  if isinstance(expires_in, (int, float)) and int(expires_in) > 0:
    return now_epoch + int(expires_in)
  return now_epoch + 3600


def _session_expired(session, now_epoch):
  login_epoch = int(session.get("oauth_login_epoch") or now_epoch)
  last_seen_epoch = int(session.get("oauth_last_seen_epoch") or login_epoch)
  idle_timeout = int(getattr(settings, "SESSION_IDLE_TIMEOUT_SECONDS", 3600))
  absolute_timeout = int(
      getattr(settings, "SESSION_ABSOLUTE_TIMEOUT_SECONDS", settings.SESSION_COOKIE_AGE)
  )
  if idle_timeout > 0 and now_epoch - last_seen_epoch > idle_timeout:
    return True
  if absolute_timeout > 0 and now_epoch - login_epoch > absolute_timeout:
    return True
  return False


def _token_needs_refresh(session, now_epoch):
  expiry = int(session.get("oauth_access_token_expiry_epoch") or 0)
  if expiry <= 0:
    return False
  return now_epoch >= (expiry - _TOKEN_REFRESH_SKEW_SECONDS)


def _token_validation_due(session, now_epoch):
  if session.get("oauth_last_validated_epoch") is None:
    session["oauth_last_validated_epoch"] = now_epoch
    return False
  last_validated = int(session.get("oauth_last_validated_epoch") or 0)
  return (now_epoch - last_validated) >= _TOKEN_VALIDATE_INTERVAL_SECONDS


def _refresh_access_token(session, now_epoch):
  refresh_token = session.get("refresh_token")
  if not refresh_token:
    return False
  body = {
      "grant_type": "refresh_token",
      "refresh_token": refresh_token,
  }
  try:
    response = _http_session.post(
        '%s/oauth2/tokens' % tenant_base_url,
        json=body,
        auth=HTTPBasicAuth(client_id, client_key),
        timeout=5,
    )
    if response.status_code >= 400:
      return False
    token_data = response.json()
    result = token_data.get("result") or {}
    access_token = (result.get("access_token") or {}).get("access_token")
    if not access_token:
      return False
    session["access_token"] = access_token
    new_refresh = (result.get("refresh_token") or {}).get("refresh_token")
    if new_refresh:
      session["refresh_token"] = new_refresh
    session["oauth_access_token_expiry_epoch"] = _extract_access_token_expiry_epoch(
        token_data, now_epoch
    )
    session["oauth_last_validated_epoch"] = now_epoch
    return True
  except Exception:
    return False


def _validate_access_token(access_token):
  headers = {'x-tapis-token': access_token}
  try:
    response = _http_session.get(
        '%s/oauth2/userinfo' % tenant_base_url,
        headers=headers,
        timeout=5,
    )
    return response.status_code < 400
  except Exception:
    return False
