import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import (
  CalendarDownloadResponse,
  CurrentUser,
  DatabaseSession,
  DeleteResponse,
  ImportCreateRequest,
  ImportFileReferenceResponse,
  ImportSummaryResponse,
  ImportUpdateRequest,
)
from src.models import CalendarImport
from src.services import calendar_service, database_requests, naming

URL_FETCH_TIMEOUT_SECONDS = 10

router = APIRouter(prefix='/api/imports', tags=['imports'])


async def _serialize_import_summary(db: AsyncSession, calendar_import: CalendarImport) -> ImportSummaryResponse:
  file_stats = await database_requests.get_calendar_file_stats(db, calendar_import.active_file_id)

  return ImportSummaryResponse(
    id=calendar_import.id,
    name=calendar_import.name,
    created_at=calendar_import.created_at,
    event_count=file_stats.event_count,
    range_start=file_stats.range_start,
    range_end=file_stats.range_end,
    url=calendar_import.url,
    backup=calendar_import.backup,
    last_fetched_at=calendar_import.last_fetched_at,
    last_success_at=calendar_import.last_success_at,
    last_error=calendar_import.last_error,
  )


async def _validate_import_name(db: AsyncSession, name: str | None, user_id: str, current_name: str | None = None) -> str:
  name = naming.sanitize_text(name, naming.ILLEGAL_NAME_CHARACTERS, collapse_whitespaces=True)

  if not naming.REGEX_NAME_VALIDATION.match(name):
    raise HTTPException(status_code=422, detail=f'Calendar import name {naming.NAME_ERROR}')

  existing_names = await database_requests.get_import_names_for_user(db, user_id) - {current_name}

  if name in existing_names:
    raise HTTPException(status_code=409, detail='Calendar import name already in use')

  return name


async def _choose_new_import_name(db: AsyncSession, name: str, user_id: str) -> str:
  name = naming.sanitize_text(name, naming.ILLEGAL_NAME_CHARACTERS, collapse_whitespaces=True)

  existing_names = await database_requests.get_import_names_for_user(db, user_id)
  name = naming.next_available_name(name, existing_names)

  try:
    name = await _validate_import_name(db, name, user_id)
  except HTTPException:
    name = naming.next_available_name(naming.DEFAULT_IMPORT_NAME, existing_names)

  return name


async def _get_owned_import_by_id(db: AsyncSession, import_id: uuid.UUID, user_id: str) -> CalendarImport:
  calendar_import = await database_requests.get_import_by_id(db, import_id)

  is_import_found = calendar_import is not None
  is_user_owner = is_import_found and calendar_import.user_id == user_id

  if not is_import_found or not is_user_owner:
    raise HTTPException(status_code=404, detail='Import not found')

  return calendar_import


@router.get(
  '',
  response_model=list[ImportSummaryResponse],
  summary='List user calendar imports',
  description='List all calendar imports owned by the current user, oldest first.',
)
async def list_imports(user: CurrentUser, db: DatabaseSession):
  calendar_imports = await database_requests.get_imports_for_user(db, user.id)

  serialized_imports = [await _serialize_import_summary(db, calendar_import) for calendar_import in calendar_imports]
  return serialized_imports


@router.post(
  '',
  response_model=ImportSummaryResponse,
  summary='Create a new calendar import',
  description='Create an import from either a web calendar `url` or an uploaded ICS `file`.',
)
async def create_import(payload: Annotated[ImportCreateRequest, Form()], user: CurrentUser, db: DatabaseSession):
  if payload.url is not None:
    url = payload.url
    calendar_bytes, error = await calendar_service.fetch_url(payload.url)

    if error is not None:
      # TODO: might allow currently not working file, but then have to place dummy file
      raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=error)
  else:
    url = None
    calendar_bytes = await payload.file.read()

  calendar = calendar_service.CalendarPipeline(calendar_bytes, lazy=True)

  name = await _choose_new_import_name(db, payload.name, user.id)

  calendar_file = await calendar_service.get_or_create_calendar_file(db, calendar)
  calendar_import = await database_requests.create_calendar_import(db, user.id, name, calendar_file, datetime.now(timezone.utc), url=url)

  await db.commit()
  await db.refresh(calendar_import)
  return await _serialize_import_summary(db, calendar_import)


@router.patch(
  '/{import_id}',
  response_model=ImportSummaryResponse,
  summary='Update import',
  description='Partially update a calendar import owned by the current user. Omitting a field leaves it untouched',
)
async def update_import(import_id: uuid.UUID, payload: ImportUpdateRequest, user: CurrentUser, db: DatabaseSession):
  calendar_import = await _get_owned_import_by_id(db, import_id, user.id)
  fields_set = payload.model_fields_set

  if 'name' in fields_set:
    name = (payload.name or '').strip()
    calendar_import.name = await _validate_import_name(db, name, user.id, calendar_import.name)

  if 'backup' in fields_set:
    is_web_calendar = calendar_import.url is not None

    if not is_web_calendar:
      raise HTTPException(status_code=422, detail='Backup only applies to web calendar imports')

    calendar_import.backup = bool(payload.backup)

  await db.commit()
  await db.refresh(calendar_import)
  return await _serialize_import_summary(db, calendar_import)


@router.get(
  '/{import_id}/file-references',
  response_model=list[ImportFileReferenceResponse],
  summary='List import file references',
  description='List of file reference summary information with their respective calendar file stats over all file references of an import.',
)
async def list_file_references(import_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  calendar_import = await _get_owned_import_by_id(db, import_id, user.id)

  file_references = await database_requests.get_file_references_for_import(db, calendar_import.id)

  serialized_file_references = [
    ImportFileReferenceResponse(
      id=file_reference.id,
      created_at=file_reference.created_at,
      event_count=file_reference.event_count,
      range_start=file_reference.range_start,
      range_end=file_reference.range_end,
    )
    for file_reference in file_references
  ]
  return serialized_file_references


@router.get(
  '/{import_id}/download',
  status_code=200,
  response_class=CalendarDownloadResponse,
  summary='Download import',
  description='Download the active calendar file of an import.',
)
async def download_import(import_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  calendar_import = await _get_owned_import_by_id(db, import_id, user.id)
  calendar_file = await database_requests.get_calendar_file_by_id(db, calendar_import.active_file_id)

  return CalendarDownloadResponse(content=calendar_file.data, filename=f'{calendar_import.name}')


@router.get(
  '/{import_id}/file-references/{file_reference_id}/download',
  status_code=200,
  response_class=CalendarDownloadResponse,
  summary='Download a calendar file of an import',
  description='Download the calendar file of an import, specified by its file reference id.',
)
async def download_file_reference(import_id: uuid.UUID, file_reference_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  calendar_import = await _get_owned_import_by_id(db, import_id, user.id)
  file_reference = await database_requests.get_file_reference_by_id(db, file_reference_id)
  if file_reference is None or file_reference.import_id != calendar_import.id:
    raise HTTPException(status_code=404, detail='File reference not found')

  calendar_file = await database_requests.get_calendar_file_by_id(db, file_reference.file_id)

  timestamp = file_reference.created_at.strftime('%Y%m%dT%H%M%S')
  file_name = naming.generate_affixed_name(calendar_import.name, suffix=f'{timestamp}', separator='-')
  return CalendarDownloadResponse(content=calendar_file.data, filename=file_name)


@router.post(
  '/{import_id}/file-references/{file_reference_id}/copy',
  response_model=ImportSummaryResponse,
  summary='Create an import from a file of an existing import',
  description='Create a new calendar import from the file of an existing import.',
)
async def copy_file_reference_to_new_import(import_id: uuid.UUID, file_reference_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  source_import = await _get_owned_import_by_id(db, import_id, user.id)
  file_reference = await database_requests.get_file_reference_by_id(db, file_reference_id)
  if file_reference is None or file_reference.import_id != source_import.id:
    raise HTTPException(status_code=404, detail='File reference not found')

  prefix = 'Copy of'
  suffix = f'from {file_reference.created_at.strftime("%b %d, %Y, %H:%M")}'
  suggested_name = naming.generate_affixed_name(source_import.name, prefix=prefix, suffix=suffix, max_length=naming._MAX_NAME_LENGTH)
  name = await _choose_new_import_name(db, suggested_name, user.id)

  calendar_file = await database_requests.get_calendar_file_by_id(db, file_reference.file_id)
  calendar_import = await database_requests.create_calendar_import(db, user.id, name, calendar_file, datetime.now(timezone.utc))

  await db.commit()
  await db.refresh(calendar_import)
  return await _serialize_import_summary(db, calendar_import)


@router.delete(
  '/{import_id}',
  response_model=DeleteResponse,
  summary='Delete import',
  description='Delete an import owned by the current user.',
)
async def delete_import(import_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  calendar_import = await _get_owned_import_by_id(db, import_id, user.id)

  await db.delete(calendar_import)
  await db.commit()

  return DeleteResponse(ok=True)
