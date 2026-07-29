import datetime
import uuid
from typing import Annotated

import fastapi
import pydantic
import sqlalchemy.ext.asyncio

import src.auth
import src.core.db
import src.models
import src.services.database_requests


async def get_current_user(request: fastapi.Request, db: 'DatabaseSession') -> src.models.User:
  """Get current user database entry.

  The user id is read from the current session, which is populated by the callback
  from the OAuth provider. If the user is not yet in the database, it is created.
  """
  session_user = src.auth.get_session_user(request)
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


DatabaseSession = Annotated[sqlalchemy.ext.asyncio.AsyncSession, fastapi.Depends(src.core.db.get_db)]
CurrentUser = Annotated[src.models.User, fastapi.Depends(get_current_user)]


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
