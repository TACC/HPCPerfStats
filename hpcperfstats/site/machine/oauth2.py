"""OAuth2 login, callback, logout, and token check for Tapis. Session stores access_token, refresh_token, username, email, is_staff.

"""
import logging
import os

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


def login_oauth(request):
  """Redirect to OAuth2 authorize URL with state; store state in session.

    """
  session = request.session
  session['auth_state'] = os.urandom(24).hex()

  redirect_uri = 'https://{}{}'.format(server_name, reverse('oauth_callback'))
  if redirect_uri.endswith('/'):
    redirect_uri = redirect_uri[:-1]

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
    redirect_uri = 'https://{}{}'.format(server_name, reverse('oauth_callback'))
    code = request.GET['code']
    if redirect_uri.endswith('/'):
      redirect_uri = redirect_uri[:-1]
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
    return HttpResponseRedirect("/")


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
  """Render login prompt template unless already authenticated, then redirect to /.

    """
  if check_for_tokens(request):
    return HttpResponseRedirect("/")
  return render(request, "registration/login_prompt.html", {"logged_in": False})


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
