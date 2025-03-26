from agavepy.agave import Agave
from django.http import HttpResponseRedirect, HttpResponseBadRequest
from django.shortcuts import render
from django.urls import reverse
import os, json, requests
import logging

logging.basicConfig()
logger = logging.getLogger('logger')

try:
    with open("/home1/02561/sharrell/.agave/current", 'r') as fd:
        data = json.load(fd)

    client_key = data["result"]["consumerKey"]
    client_secret = data["result"]["consumerSecret"]
    tenant_base_url = "https://api.tacc.utexas.edu"
except:
    pass

def get_request():
    """Walk up the stack, return the nearest first argument named "request"."""
    frame = None
    try:
        for f in inspect.stack()[1:]:
            frame = f[0]
            code = frame.f_code
            if code.co_varnames and code.co_varnames[0] == "request":
                request = frame.f_locals['request']
    finally:
        del frame
    return request

def check_for_tokens(request):
    try:
        access_token = request.session.get("access_token")
        if access_token: return True
    except: return False

def update_session_tokens(**kwargs):
    """Update the request's session with the latest tokens since the client may have
    automatically refreshed them."""

    request = get_request()
    request.session['access_token'] = kwargs['access_token']
    request.session['refresh_token'] = kwargs['refresh_token']

def get_agave_client(username, password):
    
    if not client_key or not client_secret:
        raise Exception("Missing OAuth client credentials.")
    return Agave(api_server=tenant_base_url, username=username, password=password, client_name="tacc-stats",
     api_key=client_key, api_secret=client_secret, token_callback=update_session_tokens)

# login view with Agave functionality
def login(request):
    if check_for_tokens(request):
        return HttpResponseRedirect('/')

    if request.method=='POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        if not username:
            context = {"error": "Username cannot be blank"}
            return render(request, 'registration/login.html', context, content_type='text/html')

        if not password:
            context = {"error": "Password cannot be blank"}
            return render(request, 'registration/login.html', context, content_type='text/html')

        try:
            ag = get_agave_client(username, password)
        except Exception as e:
            context = {"error": "Invalid username or password: {}".format(e)}
            return render(request, 'registration/login.html', context, content_type='text/html')

        # at this point the Agave client has been generated.
        access_token = ag.token.token_info['access_token']
        refresh_token = ag.token.token_info['refresh_token']
        token_exp = ag.token.token_info['expires_at']
        
        request.session['username'] = username
        request.session['access_token'] = access_token
        request.session['refresh_token'] = refresh_token

        return HttpResponseRedirect("/")

    elif request.method == 'GET':
        return render(request, 'registration/login.html')

    return render(request, 'registration/login.html')

def logout(request):

    redirect_uri = 'https://{}{}'.format(request.get_host(), reverse('agave_oauth_callback'))

    body = {
        'token': request.session['access_token'],
        'token_type_hint': 'access_token'
    }

    response = requests.post('%s/revoke' % tenant_base_url,
        data=body,
        auth=(client_key, client_secret))
    request.session.flush()
    return HttpResponseRedirect("/")

def login_prompt(request):
    if check_for_tokens(request):
        return HttpResponseRedirect("/")
    return render(request, "registration/login_prompt.html", {"logged_in": False})


def login_oauth(request):
    session = request.session
    session['auth_state'] = os.urandom(24).hex()

    host = "stats.frontera.tacc.utexas.edu"
    redirect_uri = 'https://{}{}'.format(request.get_host(), reverse('agave_oauth_callback'))

    authorization_url = (
        '%s/authorize?client_id=%s&response_type=code&redirect_uri=%s&state=%s' %(
            tenant_base_url,
            client_key,
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
        redirect_uri = redirect_uri
        body = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri
            }

        response = requests.post('%s/token' % tenant_base_url,
            data=body,
            auth=(client_key, client_secret))
        token_data = response.json()

        logger.error(token_data.keys())

        headers = {'Authorization': 'Bearer %s' % token_data['access_token']}
        user_response = requests.get('%s/profiles/v2/me?pretty=true' %tenant_base_url, headers=headers)
        user_data = user_response.json()

        request.session['access_token'] = token_data['access_token']
        request.session['refresh_token'] = token_data['refresh_token']
        request.session['username'] = user_data['result']['username']
        logger.error(request.session['access_token'])
        # For now we determine whether a user is staff by seeing if hey have an @tacc.utexas.edu email.
        request.session['email'] = user_data['result']['email']
        request.session['is_staff'] = user_data['result']['email'].split('@')[-1] == 'tacc.utexas.edu'
        #request.session['is_staff'] = False
        return HttpResponseRedirect("/")
