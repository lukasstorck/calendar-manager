import asyncio
import contextlib

import fastapi
import fastapi.responses
import fastapi.staticfiles

import src.api.boards
import src.api.exports
import src.api.imports
import src.api.public
import src.auth
import src.core.db
import src.services.scheduler

STATIC_DIR_PATH = 'src/static'
HOMEPAGE_PATH = f'{STATIC_DIR_PATH}/index.html'
BOARD_PAGE_PATH = f'{STATIC_DIR_PATH}/board.html'

_stop_event: asyncio.Event | None = None
_scheduler_task: asyncio.Task | None = None


@contextlib.asynccontextmanager
async def lifespan(app: fastapi.FastAPI):
  global _stop_event, _scheduler_task
  async with src.core.db.engine.begin() as conn:
    await conn.run_sync(src.core.db.Base.metadata.create_all)

  _stop_event = asyncio.Event()
  _scheduler_task = asyncio.create_task(src.services.scheduler.scheduler_loop(_stop_event))

  yield

  _stop_event.set()
  if _scheduler_task:
    await _scheduler_task


app = fastapi.FastAPI(lifespan=lifespan)
src.auth.configure(app)

app.include_router(src.auth.router)
app.include_router(src.api.imports.router)
app.include_router(src.api.exports.router)
app.include_router(src.api.boards.router)
app.include_router(src.api.public.router)

app.mount('/static', fastapi.staticfiles.StaticFiles(directory=STATIC_DIR_PATH), name='static')


@app.get('/health', summary='Health check')
async def health():
  if src.auth.PROVIDERS:
    return {'status': 'ok'}

  raise fastapi.HTTPException(status_code=503, detail='Authentication providers not configured')


@app.get(
  '/',
  response_class=fastapi.responses.FileResponse,
  summary='Serve static main app page',
  description='Serve the main app page. It shows general information about what the app does when not logged in and when the user is logged in, it shows the user configured imports, exports and boards.',
  tags=['pages'],
)
async def index():
  return fastapi.responses.FileResponse(HOMEPAGE_PATH)


_board_page_docs = {
  'response_class': fastapi.responses.FileResponse,
  'summary': 'Serve static calendar board page',
  'description': 'Serves public calendar board pages. Token is only needed, when the board is set to protected, otherwise when supplied, it is ignored. While the FileResponse is static, the url path is parsed client-side and the board information is requested via the `/api/public/boards/{board_name}` endpoint.',
  'tags': ['pages'],
}


@app.get('/board/{board_name}', **_board_page_docs)
@app.get('/board/{board_name}/', **_board_page_docs)
@app.get('/board/{board_name}/{token}', **_board_page_docs)
async def board_page(board_name: str, token: str | None = None):
  return fastapi.responses.FileResponse(BOARD_PAGE_PATH)


@app.get('/favicon.ico')  # TODO: add favicon
@app.get('/installHook.js.map')  # TODO: remove
async def not_found():
  raise fastapi.HTTPException(status_code=404)


@app.get('/{catchall:path}')
def catchall_redirect(catchall: str):
  return fastapi.responses.RedirectResponse(url='/')
