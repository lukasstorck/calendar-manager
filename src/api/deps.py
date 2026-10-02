import datetime
import typing
import uuid

import fastapi
import pydantic

import src.api.auth
import src.models
import src.services.database_requests


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
