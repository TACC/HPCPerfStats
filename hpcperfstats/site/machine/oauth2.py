"""OAuth2 login, callback, logout, and token check for Tapis. Session stores access_token, refresh_token, username, email, is_staff.

"""
import logging
import os
from urllib.parse import quote

import requests
from django.http import HttpResponseRedirect
from django.shortcuts import render
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
  """Render login prompt unless already authenticated; then redirect to next or /.

    """
  next_url = request.GET.get('next', '')
  if check_for_tokens(request):
    redirect_to = next_url if _safe_redirect_path(next_url) else '/'
    return HttpResponseRedirect(redirect_to)
  login_url = reverse('login') + ('?next=' + quote(next_url) if next_url else '')
  return render(request, "registration/login_prompt.html", {
      "logged_in": False,
      "next": next_url,
      "login_url": login_url,
      "machine_name": cfg.get_host_name_ext(),
  })


def check_for_tokens(request):
  """Return True if session has access_token, else False.

    """
  # return True if request.session.get("access_token") else False
  try:
    access_token = request.session.get("access_token")
    if access_token:
      return True
  except Exception:
    return False
