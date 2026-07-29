import uuid

import fastapi
import sqlalchemy
import sqlalchemy.ext.asyncio

from src.api.deps import BoardSummaryResponse, BoardUpdateRequest, CurrentUser, DatabaseSession, DeleteResponse
from src.models import Board, BoardCalendar, User
from src.services import database_requests, naming

router = fastapi.APIRouter(prefix='/api/boards', tags=['boards'])


async def _validate_board_name(db: sqlalchemy.ext.asyncio.AsyncSession, name: str, user: User, board_id: uuid.UUID | None = None) -> str:
  name = naming.sanitize_text(name, naming.ILLEGAL_NAME_CHARACTERS, collapse_whitespaces=True)

  if not naming.REGEX_NAME_VALIDATION.match(name):
    raise fastapi.HTTPException(status_code=422, detail=f'Board name {naming.NAME_ERROR}')

  existing_board = await database_requests.find_user_owned_board_by_name(db, name, user, board_id)

  if existing_board:
    raise fastapi.HTTPException(status_code=409, detail='Board name already in use')

  return name


async def _validate_link_name(db: sqlalchemy.ext.asyncio.AsyncSession, link_name: str | None, board_id: uuid.UUID | None = None) -> str | None:
  link_name = naming.sanitize_text(link_name, naming.ILLEGAL_LINK_CHARACTERS)

  if not naming.REGEX_LINK_VALIDATION.match(link_name):
    raise fastapi.HTTPException(status_code=422, detail=f'Public board link name {naming.LINK_ERROR}')

  existing_board = await database_requests.find_board_by_link_name(db, link_name, board_id)

  if existing_board:
    raise fastapi.HTTPException(status_code=409, detail='Public board link name already in use')

  return link_name


async def _get_owned_board_by_id(db: sqlalchemy.ext.asyncio.AsyncSession, board_id: uuid.UUID, user_id: str):
  board = await database_requests.get_board_by_id(db, board_id)

  is_board_found = board is not None
  is_user_owner = is_board_found and board.user_id == user_id

  if not is_board_found or not is_user_owner:
    raise fastapi.HTTPException(status_code=404, detail='Board not found')

  return board


async def _serialize_board_summary(db: sqlalchemy.ext.asyncio.AsyncSession, board: Board):
  calendar_ids = await database_requests.get_board_calendar_export_ids(db, board.id)

  return BoardSummaryResponse(
    id=board.id,
    name=board.name,
    description=board.description,
    link_name=board.link_name,
    published=board.published,
    protected=board.protected,
    token=board.token,
    calendar_ids=calendar_ids,
  )


@router.get(
  '',
  response_model=list[BoardSummaryResponse],
  summary='List user boards',
  description='List data of all calendar boards owned by the current user, ordered by name.',
)
async def list_boards(user: CurrentUser, db: DatabaseSession):
  boards = await database_requests.get_boards_for_user(db, user.id)

  serialized_boards = [await _serialize_board_summary(db, board) for board in boards]
  return serialized_boards


@router.post(
  '',
  response_model=BoardSummaryResponse,
  summary='Create a new calendar board',
  description='Create a new calendar board with default values.',
)
async def create_board(user: CurrentUser, db: DatabaseSession):
  existing_board_names = await database_requests.get_board_names_for_user(db, user.id)
  name = naming.next_available_name(naming.DEFAULT_BOARD_NAME, existing_board_names)

  existing_board_link_names = await database_requests.get_board_link_names(db)
  sanitized_name = naming.sanitize_text(name, naming.ILLEGAL_LINK_CHARACTERS)
  link_name = naming.next_available_name(sanitized_name, existing_board_link_names)

  board = Board(
    user_id=user.id,
    name=name,
    description=None,
    link_name=link_name,
    published=True,  # TODO: add in model, PATCH and UI
    protected=True,
    token=naming.generate_link_token(),
  )

  db.add(board)
  await db.commit()
  await db.refresh(board)

  return await _serialize_board_summary(db, board)


@router.get(
  '/{board_id}',
  response_model=BoardSummaryResponse,
  summary='Get board',
  description='Fetch a single calendar board owned by the current user by its ID.',
)
async def get_board(board_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  board = await _get_owned_board_by_id(db, board_id, user.id)
  return await _serialize_board_summary(db, board)


@router.patch(
  '/{board_id}',
  response_model=BoardSummaryResponse,
  summary='Update board',
  description='Partially update a calendar board owned by the current user. Omitting a field leaves it untouched.',
)
async def update_board(board_id: uuid.UUID, payload: BoardUpdateRequest, user: CurrentUser, db: DatabaseSession):
  """Partially update a board owned by the current user. Only fields present in the request body are changed. Setting `link_name`, `description`, or `calendar_ids` explicitly to `null` clears/unpublishes them; omitting a field leaves it untouched."""
  board = await _get_owned_board_by_id(db, board_id, user.id)
  fields_set = payload.model_dump(exclude_unset=True)

  if 'name' in fields_set:
    name = (payload.name or '').strip()
    board.name = await _validate_board_name(db, name, user, board.id)  # TODO

  if 'link_name' in fields_set:
    link_name = (payload.link_name or '').strip() or None
    board.link_name = await _validate_link_name(db, link_name, board.id)  # TODO

  if 'description' in fields_set:
    description = (payload.description or '').strip()
    board.description = description or None

  if 'protected' in fields_set:
    board.protected = bool(payload.protected)

  if 'published' in fields_set:
    board.published = bool(payload.published)

  if 'calendar_ids' in fields_set:
    ids = payload.calendar_ids or []

    # get new set of calendar ids
    valid_ids = await database_requests.verify_export_ids_are_owned_by_user(db, user, ids)

    # clear associated calendars for this board
    await database_requests.clear_calendar_references_for_board(db, board.id)

    # associate new calendars with this board
    for calendar_id in valid_ids:
      db.add(BoardCalendar(board_id=board.id, export_id=calendar_id))

  await db.commit()
  await db.refresh(board)

  return await _serialize_board_summary(db, board)


@router.post(
  '/{board_id}/reroll-token',
  response_model=BoardSummaryResponse,
  summary='Reroll board token',
  description='Generate a new access token for a protected board, invalidating the previous one.',
)
async def reroll_token(board_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  board = await _get_owned_board_by_id(db, board_id, user.id)

  board.token = naming.generate_link_token()

  await db.commit()
  await db.refresh(board)

  return await _serialize_board_summary(db, board)


@router.delete(
  '/{board_id}',
  response_model=DeleteResponse,
  summary='Delete board',
  description='Delete a board owned by the current user.',
)
async def delete_board(board_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  board = await _get_owned_board_by_id(db, board_id, user.id)

  await db.delete(board)
  await db.commit()

  return {'ok': True}
