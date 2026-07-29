import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import CurrentUser, DatabaseSession
from src.models import CalendarExport, CalendarExportSource
from src.services import database_requests, filter_engine, filter_parser, naming

router = APIRouter(prefix='/api/exports', tags=['exports'])


async def _existing_export_names(db: AsyncSession, user_id: str) -> set[str]:
  return await database_requests.get_export_names_for_user(db, user_id)


async def _validate_link_name(db: AsyncSession, link_name: str | None, export_id=None) -> str | None:
  if not link_name:
    return None
  if not naming.REGEX_LINK_NAME.match(link_name):
    raise HTTPException(status_code=422, detail=f'Public export link name {naming.LINK_NAME_ERROR}')
  existing = await database_requests.find_export_by_link_name(db, link_name, export_id)
  if existing:
    raise HTTPException(status_code=409, detail='Public export link name already in use')
  return link_name


async def _get_owned_export_by_id(db: AsyncSession, export_id: uuid.UUID, user_id: str) -> CalendarExport:
  export = await database_requests.get_export_by_id(db, export_id)

  is_export_found = export is not None
  is_user_owner = is_export_found and export.user_id == user_id

  if not is_export_found or not is_user_owner:
    raise HTTPException(status_code=404, detail='Export not found')

  return export


async def _input_count(db: AsyncSession, export_id) -> int:
  return await database_requests.count_export_sources(db, export_id)


async def serialize_summary(db: AsyncSession, export: CalendarExport) -> dict:
  duplicates = await filter_engine.find_duplicate_exports(db, export)
  public_link = f'/calendar/{export.link_name}' if export.link_name else None
  copy_link = None
  if public_link:
    copy_link = f'{public_link}?token={export.token}' if export.protected and export.token else public_link
  return {
    'id': str(export.id),
    'name': export.name,
    'description': export.description,
    'link_name': export.link_name,
    'token': export.token,
    'public_link': public_link,  # TODO: remove
    'copy_link': copy_link,  # TODO: remove
    'published': export.published,
    'protected': export.protected,
    'input_count': await _input_count(db, export.id),
    'output_count': export.last_output_count,
    'last_output_change_at': export.last_output_change_at.isoformat() if export.last_output_change_at else None,
    'duplicate_warning': [{'id': str(d.id), 'name': d.name} for d in duplicates] or None,
  }


# TODO: reuse serialize_summary with function parameter, e.g.
async def serialize_detail(db: AsyncSession, calendar_export: CalendarExport) -> dict:
  base = await serialize_summary(db, calendar_export)
  import_ids_as_uuids = await database_requests.get_export_source_import_ids(db, calendar_export.id)
  import_ids = [str(r) for r in import_ids_as_uuids]
  parsed = filter_parser.parse(calendar_export.rule_text)
  base.update(
    {
      'rule_text': calendar_export.rule_text,
      'import_ids': import_ids,
      'rule_errors': [{'message': e.message, 'position': e.position} for e in parsed.errors],
    }
  )
  return base


@router.get('')
async def list_exports(user: CurrentUser, db: DatabaseSession):
  exports = await database_requests.get_exports_for_user(db, user.id)
  return [await serialize_summary(db, export) for export in exports]


@router.post('')
async def create_export(user: CurrentUser, db: DatabaseSession):
  existing_names = await _existing_export_names(db, user.id)
  name = naming.next_available_name(naming.DEFAULT_EXPORT_NAME, existing_names)

  # Mirrors create_board's link_name generation exactly (see boards.py).
  # NOTE: get_export_link_names does not exist yet in database_requests.py --
  # only get_board_link_names does. It needs to be added there: same shape,
  # just selecting distinct non-null CalendarExport.link_name instead of
  # Board.link_name (link names are globally unique across all users).
  existing_link_names = await database_requests.get_export_link_names(db)
  sanitized_name = naming.sanitize_text(name, naming.ILLEGAL_LINK_CHARACTERS)
  link_name = naming.next_available_name(sanitized_name, existing_link_names)

  export = CalendarExport(
    user_id=user.id,
    name=name,
    link_name=link_name,
    rule_text='',
    published=True,
    protected=True,
    token=naming.generate_link_token(),
  )
  db.add(export)
  await db.commit()
  await db.refresh(export)
  return await serialize_detail(db, export)


@router.get('/{export_id}')
async def get_export(export_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  export = await _get_owned_export_by_id(db, export_id, user.id)
  return await serialize_detail(db, export)


@router.post('/{export_id}/reroll-token')
async def reroll_token(export_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  """Generate a new access token for a protected export, invalidating the
  previous one. Mirrors boards.py's reroll_token exactly -- this route was
  missing entirely, even though the frontend already had a button for it."""
  export = await _get_owned_export_by_id(db, export_id, user.id)

  export.token = naming.generate_link_token()

  await db.commit()
  await db.refresh(export)
  return await serialize_detail(db, export)


@router.patch('/{export_id}')
async def update_export(export_id: uuid.UUID, payload: dict, user: CurrentUser, db: DatabaseSession):
  calendar_export = await _get_owned_export_by_id(db, export_id, user.id)

  if 'name' in payload:
    new_name = (payload['name'] or '').strip()
    if not new_name:
      raise HTTPException(status_code=422, detail='Export name cannot be empty')
    existing_names = await _existing_export_names(db, user.id) - {calendar_export.name}
    if new_name in existing_names:
      raise HTTPException(status_code=409, detail='Export name already in use')
    calendar_export.name = new_name

  if 'description' in payload:
    calendar_export.description = (payload['description'] or '').strip() or None

  if 'link_name' in payload:
    calendar_export.link_name = await _validate_link_name(db, (payload['link_name'] or '').strip() or None, calendar_export.id)

  if 'protected' in payload:
    want_protected = bool(payload['protected'])
    if want_protected and not calendar_export.protected:
      calendar_export.token = naming.generate_link_token()
    if not want_protected:
      calendar_export.token = None
    calendar_export.protected = want_protected

  if 'published' in payload:
    calendar_export.published = bool(payload['published'])

  if 'rule_text' in payload:
    rule_text = payload['rule_text'] or ''
    parsed = filter_parser.parse(rule_text)
    calendar_export.rule_text = rule_text
    if not parsed.valid:
      await db.commit()
      detail = await serialize_detail(db, calendar_export)
      return detail

  if 'import_ids' in payload:
    raw_ids = payload['import_ids'] or []
    try:
      ids = [uuid.UUID(i) for i in raw_ids]
    except ValueError:
      raise HTTPException(status_code=422, detail='Invalid import id')
    valid_ids = await database_requests.get_valid_import_ids_for_user(db, user.id, ids)
    await database_requests.delete_export_sources_for_export(db, calendar_export.id)
    for iid in ids:
      if iid in valid_ids:
        db.add(CalendarExportSource(export_id=calendar_export.id, import_id=iid))

  await db.flush()
  parsed = filter_parser.parse(calendar_export.rule_text)
  if parsed.valid:
    await filter_engine.run_filter(db, calendar_export)

  await db.commit()
  await db.refresh(calendar_export)
  return await serialize_detail(db, calendar_export)


@router.delete('/{export_id}')
async def delete_export(export_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  export = await _get_owned_export_by_id(db, export_id, user.id)
  await db.delete(export)
  await db.commit()
  return {'ok': True}
