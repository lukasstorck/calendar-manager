import os

import authlib.integrations.base_client.errors
import authlib.integrations.starlette_client
import authlib.integrations.starlette_client.apps
import fastapi
import fastapi.responses
import starlette.middleware.sessions

import src.logging

OAuthClient = authlib.integrations.starlette_client.apps.StarletteOAuth2App

logger = src.logging.logger

BASE_URL = os.environ.get('BASE_URL')
SESSION_SECRET = os.environ.get('SESSION_SECRET')

GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID')
GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET')

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')

POCKETID_CLIENT_ID = os.environ.get('POCKETID_CLIENT_ID')
POCKETID_CLIENT_SECRET = os.environ.get('POCKETID_CLIENT_SECRET')
POCKETID_SERVER_METADATA_URL = os.environ.get('POCKETID_SERVER_METADATA_URL')

OAUTH_CALLBACK_TEMPLATE_PATH = 'src/templates/oauth-callback.html'


router = fastapi.APIRouter(prefix='/api/auth', tags=['auth'])
oauth = authlib.integrations.starlette_client.OAuth()

if GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET:
  oauth.register(
    name='github',
    client_id=GITHUB_CLIENT_ID,
    client_secret=GITHUB_CLIENT_SECRET,
    authorize_url='https://github.com/login/oauth/authorize',
    access_token_url='https://github.com/login/oauth/access_token',
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'user:email'},
  )

if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
  oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url=('https://accounts.google.com/.well-known/openid-configuration'),
    client_kwargs={'scope': 'openid email profile'},
  )

if POCKETID_CLIENT_ID and POCKETID_CLIENT_SECRET and POCKETID_SERVER_METADATA_URL:
  oauth.register(
    name='pocketid',
    client_id=POCKETID_CLIENT_ID,
    client_secret=POCKETID_CLIENT_SECRET,
    server_metadata_url=POCKETID_SERVER_METADATA_URL,
    client_kwargs={'scope': 'openid'},
  )

PROVIDERS = sorted(oauth._registry.keys(), key=str.lower)


def configure(app: fastapi.FastAPI):
  app.add_middleware(
    starlette.middleware.sessions.SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie='session',
    max_age=60 * 60 * 24 * 14,
    same_site='lax',
    https_only=True,
  )


def get_session_user(request: fastapi.Request) -> dict[str, str] | None:
  return request.session.get('user')


@router.get('/providers')
async def providers() -> set[str]:
  """List of available OAuth providers."""
  return PROVIDERS


@router.get('/me')
async def me(request: fastapi.Request):
  """Get user data stored in session (provider and provider specific user id).

  Note: this is not the same as the user id stored in the database.
  """
  user = get_session_user(request)

  if not user:
    raise fastapi.HTTPException(status_code=401, detail='Not authenticated')

  return user


@router.get('/login/{provider}')
async def login(request: fastapi.Request, provider: str):
  if provider not in PROVIDERS:
    raise fastapi.HTTPException(status_code=400, detail='Unsupported provider')

  redirect_uri = f'{BASE_URL}/api/auth/callback/{provider}'
  client: OAuthClient = oauth.create_client(provider)

  return await client.authorize_redirect(request, redirect_uri)


@router.get('/callback/{provider}')
async def callback(request: fastapi.Request, provider: str):
  if provider not in PROVIDERS:
    raise fastapi.HTTPException(status_code=400, detail='Unsupported provider')

  client: OAuthClient = oauth.create_client(provider)

  try:
    token = await client.authorize_access_token(request)
  except authlib.integrations.base_client.errors.MismatchingStateError:
    logger.warning('OAuth callback state mismatch for provider=%s', provider)
    return fastapi.Response(status_code=400, content='OAuth state mismatch')

  if provider == 'github':
    response = await client.get('user', token=token)
    profile = response.json()
    user = {'id': str(profile['id']), 'provider': 'github'}
  elif provider in ('google', 'pocketid'):
    userinfo = token['userinfo']
    user = {'id': userinfo['sub'], 'provider': provider}
  else:
    raise fastapi.HTTPException(status_code=400, detail='Unsupported provider')

  # set userdata in session
  request.session['user'] = user

  return fastapi.responses.FileResponse(OAUTH_CALLBACK_TEMPLATE_PATH)


@router.post('/logout')
async def logout(request: fastapi.Request):
  request.session.clear()

  return {'success': True}
