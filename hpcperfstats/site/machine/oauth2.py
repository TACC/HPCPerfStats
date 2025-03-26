from django.http import HttpResponseRedirect, HttpResponseBadRequest
from django.shortcuts import render
from django.urls import reverse
from django.conf import settings
import os, json, requests
import logging
from requests.auth import HTTPBasicAuth

logging.basicConfig()
logger = logging.getLogger('logger')

client_id = getattr(settings, "TAPIS_CLIENT_ID")
client_key = getattr(settings, "TAPIS_CLIENT_KEY")

if not client_id or not client_key:
    with open("/home1/01623/sharrell/.tapis/current", 'r') as fd:
        data = json.load(fd)
    client_id = data["result"]["client_id"]
    client_key = data["result"]["client_key"]

tenant_base_url = "https://tacc.tapis.io/v3"


def login_oauth(request):
    session = request.session
    session['auth_state'] = os.urandom(24).hex()

    redirect_uri = 'https://{}{}'.format(request.get_host(), reverse('agave_oauth_callback'))
    if redirect_uri.endswith('/'):
      redirect_uri = redirect_uri[:-1]

    authorization_url = (
        '%s/oauth2/authorize?client_id=%s&redirect_uri=%s&response_type=code&state=%s' %(
            tenant_base_url,
            client_id,
            redirect_uri,
            session['auth_state']
        )
    )
    print(authorization_url)
    return HttpResponseRedirect(authorization_url)


def agave_oauth_callback(request):
    state = request.GET.get('state')

    if request.session['auth_state'] != state:
        return HttpResponseBadRequest('Authorization state failed.')

    if 'code' in request.GET:
        redirect_uri = 'https://{}{}'.format(request.get_host(),
            reverse('agave_oauth_callback'))
        code = request.GET['code']
        if redirect_uri.endswith('/'):
          redirect_uri = redirect_uri[:-1]
        body = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri
            }

        response = requests.post('%s/oauth2/tokens' % tenant_base_url,
            json=body,
            auth=HTTPBasicAuth(client_id, client_key))
        token_data = response.json()

        logger.error(token_data["result"]["access_token"]["access_token"])
        # logger.error(token_data.keys())

        headers = {'x-tapis-token': token_data["result"]["access_token"]["access_token"]}
        user_response = requests.get('%s/oauth2/userinfo' %tenant_base_url, headers=headers)
        user_data = user_response.json()

        request.session['access_token'] = token_data["result"]["access_token"]["access_token"]
        request.session['refresh_token'] = token_data["result"]["refresh_token"]["refresh_token"]
        request.session['username'] = user_data['result']['username']
        logger.error(request.session['access_token'])
        # For now we determine whether a user is staff by seeing if hey have an @tacc.utexas.edu email.
        request.session['email'] = user_data['result']['email']
        request.session['is_staff'] = user_data['result']['email'].split('@')[-1] == 'tacc.utexas.edu'
        #request.session['is_staff'] = False
        return HttpResponseRedirect("/")


def logout(request):
    body = {
        'token': request.session['access_token']
    }

    response = requests.post('%s/oauth2/tokens/revoke' % tenant_base_url, json=body)

    request.session.flush()
    return HttpResponseRedirect("/")


def login_prompt(request):
    if check_for_tokens(request):
        return HttpResponseRedirect("/")
    return render(request, "registration/login_prompt.html", {"logged_in": False})


def check_for_tokens(request):
    # return True if request.session.get("access_token") else False
    try:
        access_token = request.session.get("access_token")
        if access_token:
            return True
    except Exception:
        return False
