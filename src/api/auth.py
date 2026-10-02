import authlib.integrations.base_client.errors
import authlib.integrations.starlette_client
import authlib.integrations.starlette_client.apps
import fastapi
import fastapi.responses
import starlette.middleware.sessions

import src.core.config
import src.logging

OAuthClient = authlib.integrations.starlette_client.apps.StarletteOAuth2App

logger = src.logging.logger
settings = src.core.config.settings

DEFAULT_USER = {'id': 'default_user', 'provider': 'no_auth'}
OAUTH_CALLBACK_TEMPLATE_PATH = 'src/templates/oauth-callback.html'


router = fastapi.APIRouter(prefix='/api/auth', tags=['auth'])
oauth = authlib.integrations.starlette_client.OAuth()

if not settings.skip_authentication and settings.github_client_id and settings.github_client_secret:
  oauth.register(
    name='github',
    client_id=settings.github_client_id,
    client_secret=settings.github_client_secret,
    authorize_url='https://github.com/login/oauth/authorize',
    access_token_url='https://github.com/login/oauth/access_token',
    api_base_url='https://api.github.com/',
    client_kwargs={'scope': 'user:email'},
  )

if not settings.skip_authentication and settings.google_client_id and settings.google_client_secret:
  oauth.register(
    name='google',
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url=('https://accounts.google.com/.well-known/openid-configuration'),
    client_kwargs={'scope': 'openid email profile'},
  )

if not settings.skip_authentication and settings.pocketid_client_id and settings.pocketid_client_secret and settings.pocketid_server_metadata_url:
  oauth.register(
    name='pocketid',
    client_id=settings.pocketid_client_id,
    client_secret=settings.pocketid_client_secret,
    server_metadata_url=settings.pocketid_server_metadata_url,
    client_kwargs={'scope': 'openid'},
  )

PROVIDERS = sorted(oauth._registry.keys(), key=str.lower)


def configure(app: fastapi.FastAPI):
  app.add_middleware(
    starlette.middleware.sessions.SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie='session',
    max_age=60 * 60 * 24 * 14,
    same_site='lax',
    https_only=True,
  )


def get_session_user(request: fastapi.Request) -> dict[str, str] | None:
  if settings.skip_authentication:
    return DEFAULT_USER

  user = request.session.get('user')

  # authentication is is required, so the default user is not allowed
  if user == DEFAULT_USER:
    request.session.clear()
    return None

  return user


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

  redirect_uri = f'{settings.base_url}/api/auth/callback/{provider}'
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
