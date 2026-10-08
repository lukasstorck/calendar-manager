import datetime
import typing
import urllib.parse
import uuid

import fastapi
import pydantic

import src.api.auth
import src.models
import src.services.database_requests
import src.services.naming


async def get_current_user(request: fastapi.Request, db: 'DatabaseSession') -> src.models.User:
  """Get current user database entry.

  The user id is read from the current session, which is populated by the callback
  from the OAuth provider. If the user is not yet in the database, it is created.
  """
  session_user = src.api.auth.get_session_user(request)
  if not session_user:
    raise fastapi.HTTPException(status_code=401, detail='Not authenticated')

  user_id = f'{session_user["provider"]}:{session_user["id"]}'
  user = await src.services.database_requests.get_user_by_id(db, user_id)
  if user is None:
    user = src.models.User(id=user_id, provider=session_user['provider'], external_id=session_user['id'])
    db.add(user)
    await db.commit()
    await db.refresh(user)
  return user


DatabaseSession = typing.Annotated[src.services.database_requests.AsyncSession, fastapi.Depends(src.services.database_requests.get_db)]
CurrentUser = typing.Annotated[src.models.User, fastapi.Depends(get_current_user)]


class BoardUpdateRequest(pydantic.BaseModel):
  """Update request for a calendar board. Only the fields provided are changed."""

  name: str | None = None
  link_name: str | None = None
  description: str | None = None
  published: bool | None = None
  protected: bool | None = None
  calendar_ids: list[uuid.UUID] | None = None


class BoardSummaryResponse(pydantic.BaseModel):
  """Summary response for a calendar board"""

  id: uuid.UUID
  name: str
  description: str | None
  link_name: str | None
  published: bool
  protected: bool
  token: str | None
  calendar_ids: list[uuid.UUID]


class DeleteResponse(pydantic.BaseModel):
  """Delete response when successfully deleting a database object"""

  ok: bool


class ExportSourceData(pydantic.BaseModel):
  """Data describing a single calendar export source."""

  import_id: uuid.UUID
  filter: str = ''
  transform: str = ''


class ExportSourceValidationError(pydantic.BaseModel):
  """Validation error data for a single calendar export source."""

  import_id: uuid.UUID
  filter_error: str = ''
  transform_error: str = ''


class ExportUpdateRequest(pydantic.BaseModel):
  """Update request for a calendar export. Only the fields provided are changed."""

  name: str | None = None
  description: str | None = None
  link_name: str | None = None
  protected: bool | None = None
  published: bool | None = None
  sources: list[ExportSourceData] | None = None


class ExportSummaryResponse(pydantic.BaseModel):
  """Summary response for a calendar export."""

  id: uuid.UUID
  name: str
  description: str | None
  link_name: str
  published: bool
  protected: bool
  token: str
  event_count: int
  updated_at: datetime.datetime
  sources: list[ExportSourceData] = pydantic.Field(default_factory=list)


class ImportSummaryResponse(pydantic.BaseModel):
  """Summary response for a calendar import."""

  id: uuid.UUID
  name: str
  created_at: datetime.datetime
  event_count: int
  range_start: datetime.datetime | None
  range_end: datetime.datetime | None
  # web calendar only, otherwise None
  url: str | None
  backup: bool
  last_fetched_at: datetime.datetime | None
  last_success_at: datetime.datetime | None
  last_error: str | None


class ImportFileReferenceResponse(pydantic.BaseModel):
  """Summary response for a calendar import file reference."""

  id: uuid.UUID
  created_at: datetime.datetime
  event_count: int
  range_start: datetime.datetime | None
  range_end: datetime.datetime | None


_ALLOWED_SCHEMES_FOR_CREATE_IMPORT_REQUEST_URL = {'http', 'https', 'webcal', 'webcals'}


class ImportCreateRequest(pydantic.BaseModel):
  """Create request for a calendar import, sent as multipart form data (a file
  upload needs it). Exactly one of `url` or `file` is required.
  """

  name: str | None = None
  url: str | None = None
  file: fastapi.UploadFile | None = None    # TODO: limit file size to settings.max_web_calendar_file_size

  @pydantic.field_validator('url')
  @classmethod
  def _validate_url(cls, value: str | None) -> str | None:
    if value is None:
      return None

    value = value.strip()

    if not value:
      raise ValueError('URL must not be empty')

    # accept URLs without scheme
    if '://' not in value and not value.startswith('//'):
      value = f'https://{value}'
    elif value.startswith('//'):
      value = f'https:{value}'

    try:
      url = pydantic.TypeAdapter(pydantic.AnyUrl).validate_python(value)
    except pydantic.ValidationError as exception:
      raise ValueError('Invalid calendar URL') from exception

    if url.scheme not in _ALLOWED_SCHEMES_FOR_CREATE_IMPORT_REQUEST_URL:
      schemes = ', '.join(sorted(_ALLOWED_SCHEMES_FOR_CREATE_IMPORT_REQUEST_URL))
      raise ValueError(f'URL scheme must be one of: {schemes}')

    if not url.host:
      raise ValueError('URL must contain a hostname')

    return str(url)

  @pydantic.model_validator(mode='after')
  def _require_exactly_one_source(self):
    if (self.url is None) == (self.file is None):
      raise ValueError('exactly one of url or file is required')
    return self


class ImportUpdateRequest(pydantic.BaseModel):
  """Update request for a calendar import. Only the fields provided are changed."""

  name: str | None = None
  backup: bool | None = None


class CalendarDownloadResponse(fastapi.Response):
  """Response for every calendar file download."""

  media_type = 'text/calendar'
  EXTENSION = '.ics'

  def __init__(self, content: str, filename: str, inline: bool = False, **kwargs):
    disposition = 'inline' if inline else 'attachment'

    ascii_sanitized_name = src.services.naming.sanitize_text(filename, src.services.naming.ILLEGAL_LINK_CHARACTERS) or 'calendar'
    ascii_filename = ascii_sanitized_name + self.EXTENSION
    utf8_filename = urllib.parse.quote(filename, safe='') + self.EXTENSION

    headers = {'Content-Disposition': (f'{disposition}; filename="{ascii_filename}"; filename*=UTF-8\'\'{utf8_filename}')}

    super().__init__(content=content, headers=headers, **kwargs)
