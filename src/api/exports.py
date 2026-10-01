import uuid

import fastapi
import sqlalchemy.ext.asyncio
from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import (
  CurrentUser,
  DatabaseSession,
  DeleteResponse,
  ExportSourceData,
  ExportSourceValidationError,
  ExportSummaryResponse,
  ExportUpdateRequest,
)
from src.models import CalendarExport, CalendarExportSource, User
from src.services import calendar_service, database_requests, naming

router = APIRouter(prefix='/api/exports', tags=['exports'])


async def _validate_export_name(db: sqlalchemy.ext.asyncio.AsyncSession, name: str, user: User, previous_name: str) -> str:
  name = naming.sanitize_text(name, naming.ILLEGAL_NAME_CHARACTERS, collapse_whitespaces=True)

  if not naming.REGEX_NAME_VALIDATION.match(name):
    raise fastapi.HTTPException(status_code=422, detail=f'Calendar export name {naming.NAME_ERROR}')

  existing_names = await database_requests.get_export_names_for_user(db, user.id) - {previous_name}

  if name in existing_names:
    raise HTTPException(status_code=409, detail='Calendar export name already in use')

  return name


async def _validate_link_name(db: sqlalchemy.ext.asyncio.AsyncSession, link_name: str | None, export_id: uuid.UUID | None = None) -> str | None:
  link_name = naming.sanitize_text(link_name, naming.ILLEGAL_LINK_CHARACTERS)

  if not naming.REGEX_LINK_VALIDATION.match(link_name):
    raise fastapi.HTTPException(status_code=422, detail=f'Public export link name {naming.LINK_ERROR}')

  existing_export = await database_requests.find_export_by_link_name(db, link_name, export_id)

  if existing_export:
    raise fastapi.HTTPException(status_code=409, detail='Public export link name already in use')

  return link_name


async def _validate_sources(db: sqlalchemy.ext.asyncio.AsyncSession, user_id: str, sources: list[ExportSourceData]):
  validated_sources: list[ExportSourceData] = []
  validation_errors: list[ExportSourceValidationError] = []

  import_ids = [source_data.import_id for source_data in sources]
  valid_ids = await database_requests.get_valid_import_ids_for_user(db, user_id, import_ids)

  for source in sources:
    if source.import_id not in valid_ids:
      # TODO add to validation_errors
      continue

    filter_error = calendar_service.CalendarPipeline.validate_filter(source.filter)
    transform_error = calendar_service.CalendarPipeline.validate_transform(source.transform)

    if filter_error or transform_error:
      errors = {}
      if filter_error:
        errors['filter_error'] = filter_error
      if transform_error:
        errors['transform_error'] = transform_error

      validation_errors.append(ExportSourceValidationError(import_id=source.import_id, **errors).model_dump_json())
    else:
      validated_sources.append(source)

  return validated_sources, validation_errors


async def _get_owned_export_by_id(db: sqlalchemy.ext.asyncio.AsyncSession, export_id: uuid.UUID, user_id: str) -> CalendarExport:
  export = await database_requests.get_export_by_id(db, export_id)

  is_export_found = export is not None
  is_user_owner = is_export_found and export.user_id == user_id

  if not is_export_found or not is_user_owner:
    raise HTTPException(status_code=404, detail='Export not found')

  return export


async def _serialize_export_summary(db: AsyncSession, export: CalendarExport):
  sources = await database_requests.get_export_sources_ordered(db, export.id)
  sources_data = [ExportSourceData(import_id=source.import_id, filter=source.filter, transform=source.transform) for source in sources]

  summary_response = ExportSummaryResponse(
    id=export.id,
    name=export.name,
    description=export.description,
    link_name=export.link_name,
    published=export.published,
    protected=export.protected,
    token=export.token,
    event_count=export.event_count,
    updated_at=export.updated_at,
    sources=sources_data,
  )
  return summary_response


@router.get(
  '',
  response_model=list[ExportSummaryResponse],
  summary='List user calendar exports',
  description='List data of all calendar exports owned by the current user.',
)
async def list_exports(user: CurrentUser, db: DatabaseSession):
  exports = await database_requests.get_exports_for_user(db, user.id)

  serialized_boards = [await _serialize_export_summary(db, export) for export in exports]
  return serialized_boards


@router.post(
  '',
  response_model=ExportSummaryResponse,
  summary='Create a new calendar export',
  description='Create a new calendar export with default values.',
)
async def create_export(user: CurrentUser, db: DatabaseSession):
  existing_export_names = await database_requests.get_export_names_for_user(db, user.id)
  name = naming.next_available_name(naming.DEFAULT_EXPORT_NAME, existing_export_names)

  existing_link_names = await database_requests.get_export_link_names(db)
  sanitized_name = naming.sanitize_text(name, naming.ILLEGAL_LINK_CHARACTERS)
  link_name = naming.next_available_name(sanitized_name, existing_link_names)

  export = CalendarExport(
    user_id=user.id,
    name=name,
    link_name=link_name,
    token=naming.generate_link_token(),
  )
  db.add(export)
  await db.commit()
  await db.refresh(export)
  return await _serialize_export_summary(db, export)


@router.get('/{export_id}')
async def get_export(export_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  export = await _get_owned_export_by_id(db, export_id, user.id)
  return await _serialize_export_summary(db, export)


@router.post(
  '/{export_id}/reroll-token',
  response_model=ExportSummaryResponse,
  summary='Reroll calendar export token',
  description='Generate a new access token for a protected calendar export, invalidating the previous one.',
)
async def reroll_token(export_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  export = await _get_owned_export_by_id(db, export_id, user.id)

  export.token = naming.generate_link_token()

  await db.commit()
  await db.refresh(export)

  return await _serialize_export_summary(db, export)


@router.patch(
  '/{export_id}',
  response_model=ExportSummaryResponse,
  summary='Update calendar export',
  description='Partially update a calendar export owned by the current user. Omitting a field leaves it untouched.',
)
async def update_export(export_id: uuid.UUID, payload: ExportUpdateRequest, user: CurrentUser, db: DatabaseSession):
  calendar_export = await _get_owned_export_by_id(db, export_id, user.id)
  fields_set = payload.model_fields_set

  # TODO
  # update error responses to accumulate errors across all fields
  # then raise a single exception with all errors, so that all errors can be displayed at the same time
  # curently only the first error is reported back to the client

  if 'name' in fields_set:
    name = (payload.name or '').strip()
    calendar_export.name = await _validate_export_name(db, name, user, calendar_export.name)

  if 'description' in fields_set:
    calendar_export.description = (payload.description or '').strip() or None

  if 'link_name' in fields_set:
    link_name = (payload.link_name or '').strip() or None
    calendar_export.link_name = await _validate_link_name(db, link_name, calendar_export.id)

  if 'protected' in fields_set:
    calendar_export.protected = bool(payload.protected)

  if 'published' in fields_set:
    calendar_export.published = bool(payload.published)

  if 'sources' in fields_set:
    unvalidated_sources = payload.sources or []

    validated_sources, validation_errors = await _validate_sources(db, user_id=user.id, sources=unvalidated_sources)

    if validation_errors:
      raise HTTPException(status_code=422, detail=validation_errors)

    await database_requests.delete_export_sources_for_export(db, calendar_export.id)

    for position, source_data in enumerate(validated_sources):
      source_data = CalendarExportSource(
        export_id=calendar_export.id,
        import_id=source_data.import_id,
        filter=source_data.filter,
        transform=source_data.transform,
        position=position,
      )
      db.add(source_data)

  await db.flush()

  await calendar_service.update_export_data(db, calendar_export)

  await db.commit()
  await db.refresh(calendar_export)
  return await _serialize_export_summary(db, calendar_export)


@router.delete(
  '/{export_id}',
  response_model=DeleteResponse,
  summary='Delete calendar export',
  description='Delete a calendar export owned by the current user.',
)
async def delete_export(export_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  export = await _get_owned_export_by_id(db, export_id, user.id)

  await db.delete(export)
  await db.commit()

  return DeleteResponse(ok=True)
