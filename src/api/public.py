import fastapi

from src.api.deps import CalendarDownloadResponse, DatabaseSession
from src.services import database_requests

router = fastapi.APIRouter(tags=['public'])


@router.get('/calendar/{link_name}', status_code=200, response_class=CalendarDownloadResponse)
async def get_public_calendar(db: DatabaseSession, link_name: str, token: str | None = None):
  """Download calendar as ICS file.

  If the calendar is protected, the token is required.
  """
  calendar = await database_requests.find_export_by_link_name(db, link_name)

  is_calendar_found = calendar is not None
  is_calendar_published = is_calendar_found and calendar.published
  is_protected_invalid_token = is_calendar_found and calendar.protected and (not token or token != calendar.token)

  if not is_calendar_found or not is_calendar_published or is_protected_invalid_token:
    raise fastapi.HTTPException(status_code=404, detail='Calendar is unavailable or protected')

  if not calendar.output_ics:
    raise fastapi.HTTPException(status_code=500, detail='Calendar data is empty')

  return CalendarDownloadResponse(content=calendar.output_ics, filename=f'{link_name}', inline=True)


@router.get('/api/public/boards/{board_name}')
async def get_public_board(db: DatabaseSession, board_name: str, token: str | None = None):
  board = await database_requests.find_board_by_link_name(db, board_name)

  is_board_found = board is not None
  is_board_published = is_board_found and board.published
  is_token_invalid = is_board_found and (not token or token != board.token)

  if not is_board_found or not is_board_published or (board.protected and is_token_invalid):
    raise fastapi.HTTPException(status_code=404, detail='Board is unavailable or protected')

  exports = await database_requests.get_board_calendar_exports(db, board.id)

  calendars = []
  for calendar in exports:
    download_link = None
    if calendar.link_name:
      download_link = f'/calendar/{calendar.link_name}'
      if calendar.protected and calendar.token:
        download_link += f'?token={calendar.token}'

    calendars.append(
      {
        'name': calendar.name,
        'description': calendar.description,
        'download_link': download_link,
        'event_count': calendar.event_count,
        'updated_at': calendar.updated_at.isoformat(),
      }
    )

  return {
    'name': board.name,
    'description': board.description,
    'calendars': calendars,
  }
