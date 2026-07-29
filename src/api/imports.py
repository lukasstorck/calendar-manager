import asyncio
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

URL_FETCH_TIMEOUT_SECONDS = 10

import src.logging
from src.api.deps import CurrentUser, DatabaseSession
from src.models import (
  CalendarImport,
  CalendarImportSource,
  CalendarImportSourceKind,
  Snapshot,
  StaticFile,
  WebSubscription,
)
from src.services import calendar_service, database_requests, naming

logger = src.logging.logger

router = APIRouter(prefix='/api/imports', tags=['imports'])


async def serialize_source(db: AsyncSession, source: CalendarImportSource, active_source_id: uuid.UUID | None = None) -> dict:
  """Async, and deliberately avoids touching source.web_subscription / source.static_file --
  those (and sub.snapshots) are lazy relationship attributes, and accessing them from a
  plain sync function crashes with sqlalchemy.exc.MissingGreenlet because the implicit
  lazy-load then runs outside the greenlet context AsyncSession sets up around awaited
  calls. Querying by the FK columns explicitly sidesteps that entirely."""
  is_active = active_source_id is not None and source.id == active_source_id
  if source.kind == CalendarImportSourceKind.WEB:
    sub = await database_requests.get_web_subscription_by_id(db, source.web_subscription_id)
    latest = await database_requests.get_latest_snapshot_for_web_subscription(db, source.web_subscription_id)
    last_success = bool(sub.last_pulled_at and sub.last_success_at and sub.last_pulled_at == sub.last_success_at)
    return {
      'id': str(source.id),
      'kind': source.kind,
      'label': source.label,
      'is_active': is_active,
      'url': sub.url,
      'contribute_backup': source.contribute_backup,
      'created_at': source.created_at.isoformat(),
      'last_pulled_at': sub.last_pulled_at.isoformat() if sub.last_pulled_at else None,
      'last_success': last_success,
      'last_error': sub.last_error,
      'event_count': latest.event_count if latest else 0,
      'range_start': latest.range_start.isoformat() if latest and latest.range_start else None,
      'range_end': latest.range_end.isoformat() if latest and latest.range_end else None,
      'latest_snapshot_id': str(latest.id) if latest else None,
    }
  else:
    sf = await database_requests.get_static_file_by_id(db, source.static_file_id)
    return {
      'id': str(source.id),
      'kind': source.kind,
      'label': source.label,
      'is_active': is_active,
      'url': None,
      'created_at': source.created_at.isoformat(),
      'last_pulled_at': None,
      'last_success': True,
      'last_error': None,
      'event_count': sf.event_count,
      'range_start': sf.range_start.isoformat() if sf.range_start else None,
      'range_end': sf.range_end.isoformat() if sf.range_end else None,
    }


async def serialize_import(db: AsyncSession, calendar_import: CalendarImport) -> dict:
  # Queried explicitly rather than via calendar_import.sources, so this also works right
  # after creating a brand-new import (where the relationship has never been loaded and
  # calendar_import isn't guaranteed to come from a selectinload-eager query).
  sources = await database_requests.get_import_sources_for_import(db, calendar_import.id)
  # Web sources first, then static files; within each kind, oldest first.
  sources = sorted(sources, key=lambda s: (s.kind != CalendarImportSourceKind.WEB, s.created_at))
  return {
    'id': str(calendar_import.id),
    'name': calendar_import.name,
    'active_source_id': str(calendar_import.active_source_id) if calendar_import.active_source_id else None,
    'sources': [await serialize_source(db, s, calendar_import.active_source_id) for s in sources],
  }


async def _existing_import_names(db: AsyncSession, user_id: str) -> set[str]:
  return await database_requests.get_import_names_for_user(db, user_id)


def _existing_source_labels(calendar_import: CalendarImport) -> set[str]:
  return {s.label for s in calendar_import.sources if s.label}


async def _get_owned_import(db: AsyncSession, import_id: uuid.UUID, user_id: str) -> CalendarImport:
  # sources is eager-loaded here (rather than via db.get) since callers use imp.sources
  # directly for duplicate checks -- lazy-loading it later from a sync context would hit
  # the same MissingGreenlet trap serialize_import used to.
  imp = await database_requests.get_import_by_id_with_sources(db, import_id)
  if imp is None or imp.user_id != user_id:
    raise HTTPException(status_code=404, detail='Import not found')
  return imp


def _ics_attachment(raw_ics: str, filename: str) -> Response:
  """A plain .ics download response, shared by every "download this calendar
  content" endpoint below. filename is put in a quoted Content-Disposition
  param, so any quotes in it (labels are free text) are stripped rather than
  left to break the header."""
  safe_filename = filename.replace('"', "'")
  return Response(
    content=raw_ics,
    media_type='text/calendar',
    headers={'Content-Disposition': f'attachment; filename="{safe_filename}"'},
  )


async def cleanup_orphan_resources(db: AsyncSession) -> dict:
  """Delete pooled StaticFile/WebSubscription rows that no CalendarImportSource
  references anymore, across the whole table -- not scoped to one import or one
  request. Deleting a WebSubscription cascades to its Snapshot rows, which is
  safe: a StaticFile created from a snapshot copies the snapshot's data rather
  than referencing it, so it has no dependency on the WebSubscription surviving.

  Not called from any API path. This is intentionally request-independent so a
  delete doesn't force an unrelated user's request to pay for a table-wide scan.

  TODO: wire this up to a periodic scheduled job (see scheduler.py) instead of
  calling it ad hoc, so pooled resources actually get swept regularly.
  TODO: snapshots are never referenced directly by anything once contributed, so
  they need their own pruning pass on a backup retention strategy (e.g. keep
  latest/7-day/4-week/12-month tiers) -- this function only removes resources
  with zero CalendarImportSource references, it does not prune snapshot history.
  """
  static_file_ids = await database_requests.get_unreferenced_static_file_ids(db)
  for static_file_id in static_file_ids:
    static_file = await database_requests.get_static_file_by_id(db, static_file_id)
    if static_file is not None:
      await db.delete(static_file)

  web_subscription_ids = await database_requests.get_unreferenced_web_subscription_ids(db)
  for web_subscription_id in web_subscription_ids:
    web_subscription = await database_requests.get_web_subscription_by_id(db, web_subscription_id)
    if web_subscription is not None:
      await db.delete(web_subscription)

  await db.commit()
  return {'static_files_deleted': len(static_file_ids), 'web_subscriptions_deleted': len(web_subscription_ids)}


@router.get('')
async def list_imports(user: CurrentUser, db: DatabaseSession):
  imports = await database_requests.get_imports_for_user(db, user.id)
  return [await serialize_import(db, i) for i in imports]


@router.post('')
async def create_import(user: CurrentUser, db: DatabaseSession):
  existing_names = await _existing_import_names(db, user.id)
  final_name = naming.next_available_name(naming.DEFAULT_IMPORT_NAME, existing_names)

  imp = CalendarImport(user_id=user.id, name=final_name)
  db.add(imp)
  await db.commit()
  await db.refresh(imp)
  return await serialize_import(db, imp)


@router.patch('/{import_id}')
async def rename_import(import_id: uuid.UUID, payload: dict, user: CurrentUser, db: DatabaseSession):
  imp = await _get_owned_import(db, import_id, user.id)

  new_name = naming.sanitize_display_text(payload.get('name') or '')
  if not new_name:
    raise HTTPException(status_code=422, detail='name is required')
  if not naming.REGEX_DISPLAY_NAME.match(new_name):
    raise HTTPException(status_code=422, detail=f'name {naming.DISPLAY_NAME_ERROR}')

  existing_names = await _existing_import_names(db, user.id) - {imp.name}
  if new_name in existing_names:
    raise HTTPException(status_code=409, detail='Name already in use')

  imp.name = new_name
  await db.commit()
  return await serialize_import(db, imp)


@router.delete('/{import_id}')
async def delete_import(import_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  imp = await _get_owned_import(db, import_id, user.id)
  # active_source_id points at one of this import's own sources, and sources cascade
  # delete-orphan -- deleting both in the same flush is a cycle SQLAlchemy can't order
  # (CircularDependencyError). Null the FK first so the cascade has nothing to conflict
  # with.
  imp.active_source_id = None
  await db.flush()
  await db.delete(imp)
  await db.commit()
  return {'ok': True}


@router.post('/{import_id}/urls')
async def add_url_source(import_id: uuid.UUID, payload: dict, user: CurrentUser, db: DatabaseSession):
  imp = await _get_owned_import(db, import_id, user.id)

  url = (payload.get('url') or '').strip()
  if not url:
    raise HTTPException(status_code=422, detail='url is required')
  url = naming.normalize_url(url)
  if not naming.URL.match(url):
    raise HTTPException(status_code=422, detail=f'url {naming.URL_ERROR}')
  label = naming.sanitize_display_text(payload.get('label') or '')

  # Resolve the pooled WebSubscription first so the duplicate check can compare FK ids
  # (web_subscription_id) instead of walking source.web_subscription -- that relationship
  # attribute isn't safe to touch lazily from here (see serialize_source).
  subscription = await database_requests.find_web_subscription_by_url(db, url)

  if subscription is not None:
    duplicate = next((s for s in imp.sources if s.kind == CalendarImportSourceKind.WEB and s.web_subscription_id == subscription.id), None)
    if duplicate is not None:
      raise HTTPException(status_code=409, detail='This URL is already added to this import.')
  else:
    subscription = WebSubscription(url=url)
    db.add(subscription)
    await db.flush()

  pull_succeeded = False
  try:
    content = await asyncio.wait_for(calendar_service.fetch_url(url), timeout=URL_FETCH_TIMEOUT_SECONDS)
    events, range_start, range_end = calendar_service.parse_calendar(content)
    file_hash = calendar_service.hash_content(content)
    now = datetime.now(timezone.utc)

    snapshot = await database_requests.find_snapshot_by_hash(db, subscription.id, file_hash)
    if snapshot is None:
      snapshot = Snapshot(
        web_subscription_id=subscription.id,
        file_hash=file_hash,
        raw_ics=content.decode('utf-8', errors='replace'),
        event_count=len(events),
        range_start=range_start,
        range_end=range_end,
      )
      db.add(snapshot)
      await db.flush()

    subscription.last_pulled_at = now
    subscription.last_success_at = now
    subscription.last_error = None
    pull_succeeded = True
  except asyncio.TimeoutError:
    subscription.last_pulled_at = datetime.now(timezone.utc)
    subscription.last_error = f'Timed out after {URL_FETCH_TIMEOUT_SECONDS}s'
    logger.warning(f'add_url_source: timed out fetching {url} after {URL_FETCH_TIMEOUT_SECONDS}s')
  except calendar_service.CalendarFetchError as exception:
    subscription.last_pulled_at = datetime.now(timezone.utc)
    subscription.last_error = str(exception)[:200]
    logger.warning(f'add_url_source: {exception}')
  except Exception as exception:
    subscription.last_pulled_at = datetime.now(timezone.utc)
    subscription.last_error = str(exception)[:200]
    logger.exception(f'add_url_source: unexpected error fetching {url}')

  default_label = label or naming.guess_name_from_url(url) or naming.DEFAULT_SOURCE_LABEL
  final_label = naming.next_available_name(default_label, _existing_source_labels(imp))

  source = CalendarImportSource(
    import_id=imp.id,
    kind=CalendarImportSourceKind.WEB,
    web_subscription_id=subscription.id,
    label=final_label,
  )
  db.add(source)
  await db.flush()

  # Only switch the active source if this pull succeeded; on failure or timeout,
  # keep whatever was active before (the source is still attached either way,
  # and the user can retry or activate it manually later).
  if pull_succeeded:
    imp.active_source_id = source.id

  await db.commit()
  await db.refresh(source)
  return await serialize_source(db, source, imp.active_source_id)


@router.post('/{import_id}/files')
async def add_file_source(
  import_id: uuid.UUID,
  user: CurrentUser,
  db: DatabaseSession,
  file: Annotated[UploadFile, File(...)],
  label: Annotated[str, Form()] = '',
):
  imp = await _get_owned_import(db, import_id, user.id)

  content = await file.read()
  file_hash = calendar_service.hash_content(content)

  # Resolve the pooled StaticFile first so the duplicate check can compare FK ids
  # (static_file_id) instead of walking source.static_file -- see add_url_source.
  static_file = await database_requests.find_static_file_by_hash(db, file_hash)

  if static_file is not None:
    duplicate = next((s for s in imp.sources if s.kind == CalendarImportSourceKind.STATIC and s.static_file_id == static_file.id), None)
    if duplicate is not None:
      raise HTTPException(status_code=409, detail='This exact file is already added to this import.')
  else:
    try:
      events, range_start, range_end = calendar_service.parse_calendar(content)
    except Exception as exception:  # noqa: BLE001
      raise HTTPException(status_code=422, detail=f'Invalid calendar file: {exception}')
    static_file = StaticFile(
      file_hash=file_hash,
      raw_ics=content.decode('utf-8', errors='replace'),
      event_count=len(events),
      range_start=range_start,
      range_end=range_end,
    )
    db.add(static_file)
    await db.flush()

  default_label = label.strip() or naming.strip_file_extension(file.filename or '') or naming.DEFAULT_SOURCE_LABEL
  final_label = naming.next_available_name(default_label, _existing_source_labels(imp))

  source = CalendarImportSource(
    import_id=imp.id,
    kind=CalendarImportSourceKind.STATIC,
    static_file_id=static_file.id,
    label=final_label,
  )
  db.add(source)
  await db.flush()
  imp.active_source_id = source.id

  await db.commit()
  await db.refresh(source)
  return await serialize_source(db, source, imp.active_source_id)


@router.post('/{import_id}/snapshot')
async def add_snapshot_as_file_source(import_id: uuid.UUID, payload: dict, user: CurrentUser, db: DatabaseSession):
  """Take a snapshot previously pulled from one of the user's URL sources and
  attach it as a new static-file source on the given import (which may be a
  different import than the one the URL source lives on)."""
  imp = await _get_owned_import(db, import_id, user.id)

  snapshot_id = payload.get('snapshot_id')
  if not snapshot_id:
    raise HTTPException(status_code=422, detail='snapshot_id is required')
  snapshot = await database_requests.get_snapshot_by_id(db, uuid.UUID(str(snapshot_id)))
  if snapshot is None:
    raise HTTPException(status_code=404, detail='Snapshot not found')

  # Permission check: the user must own some URL source subscribed to the
  # web subscription this snapshot belongs to -- not necessarily on this import.
  owning_web_source = await database_requests.find_owned_web_import_source_for_subscription(db, user.id, snapshot.web_subscription_id)
  if owning_web_source is None:
    raise HTTPException(status_code=403, detail='You do not have access to this snapshot')

  static_file = await database_requests.find_static_file_by_hash(db, snapshot.file_hash)
  if static_file is None:
    static_file = StaticFile(
      file_hash=snapshot.file_hash,
      raw_ics=snapshot.raw_ics,
      event_count=snapshot.event_count,
      range_start=snapshot.range_start,
      range_end=snapshot.range_end,
    )
    db.add(static_file)
    await db.flush()

  duplicate = next((s for s in imp.sources if s.kind == CalendarImportSourceKind.STATIC and s.static_file_id == static_file.id), None)
  if duplicate is not None:
    raise HTTPException(status_code=409, detail='This snapshot is already added to this import.')

  default_label = f'{owning_web_source.label} Snapshot from {snapshot.fetched_at.isoformat(timespec="minutes")}'
  label = naming.sanitize_display_text(payload.get('label') or '') or default_label
  final_label = naming.next_available_name(label, _existing_source_labels(imp))

  source = CalendarImportSource(
    import_id=imp.id,
    kind=CalendarImportSourceKind.STATIC,
    static_file_id=static_file.id,
    label=final_label,
  )
  db.add(source)
  await db.commit()
  await db.refresh(source)
  return await serialize_source(db, source, imp.active_source_id)


@router.post('/{target_import_id}/sources/{source_id}/copy')
async def copy_file_source(target_import_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  """Attach an existing uploaded-file source's underlying StaticFile onto a
  different import (owned by the same user) as a new source -- the file-source
  equivalent of add_snapshot_as_file_source above, minus the "pick which
  snapshot" step since a file source only ever has the one file."""
  target_imp = await _get_owned_import(db, target_import_id, user.id)

  source = await database_requests.get_import_source_by_id(db, source_id)
  if source is None or source.kind != CalendarImportSourceKind.STATIC:
    raise HTTPException(status_code=404, detail='Import source not found')

  # Permission check: the user must own the import this source currently
  # lives on. Deliberately stricter than add_snapshot_as_file_source's
  # "any subscription you have access to" -- file sources aren't shared the
  # way web subscriptions/snapshots are, so only their current owner can copy them.
  source_import = await database_requests.get_import_by_id(db, source.import_id)
  if source_import is None or source_import.user_id != user.id:
    raise HTTPException(status_code=404, detail='Import source not found')

  duplicate = next((s for s in target_imp.sources if s.kind == CalendarImportSourceKind.STATIC and s.static_file_id == source.static_file_id), None)
  if duplicate is not None:
    raise HTTPException(status_code=409, detail='This file is already added to this import.')

  final_label = naming.next_available_name(source.label or naming.DEFAULT_SOURCE_LABEL, _existing_source_labels(target_imp))

  new_source = CalendarImportSource(
    import_id=target_imp.id,
    kind=CalendarImportSourceKind.STATIC,
    static_file_id=source.static_file_id,
    label=final_label,
  )
  db.add(new_source)
  await db.commit()
  await db.refresh(new_source)
  return await serialize_source(db, new_source, target_imp.active_source_id)


@router.get('/{import_id}/sources/{source_id}/snapshots')
async def list_source_snapshots(import_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  """All backup snapshots ever taken for this web source's underlying
  subscription (shared across every import/user attached to the same URL),
  newest first. Powers the backup-inspection modal."""
  imp = await _get_owned_import(db, import_id, user.id)

  source = await database_requests.get_import_source_by_id(db, source_id)
  if source is None or source.import_id != imp.id:
    raise HTTPException(status_code=404, detail='Import source not found')
  if source.kind != CalendarImportSourceKind.WEB:
    raise HTTPException(status_code=400, detail='Only web sources have snapshots')

  snapshots = await database_requests.get_snapshots_for_web_subscription(db, source.web_subscription_id)
  return [
    {
      'id': str(snap.id),
      'fetched_at': snap.fetched_at.isoformat(),
      'event_count': snap.event_count,
      'range_start': snap.range_start.isoformat() if snap.range_start else None,
      'range_end': snap.range_end.isoformat() if snap.range_end else None,
    }
    for snap in snapshots
  ]


@router.get('/{import_id}/sources/{source_id}/download')
async def download_source(import_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  """Download this source's calendar content directly -- the uploaded file
  as-is for a static source, or the most recently pulled snapshot for a web
  source. Same ownership check as every other per-source endpoint."""
  imp = await _get_owned_import(db, import_id, user.id)

  source = await database_requests.get_import_source_by_id(db, source_id)
  if source is None or source.import_id != imp.id:
    raise HTTPException(status_code=404, detail='Import source not found')

  if source.kind == CalendarImportSourceKind.STATIC:
    sf = await database_requests.get_static_file_by_id(db, source.static_file_id)
    if sf is None:
      raise HTTPException(status_code=404, detail='File not found')
    raw_ics = sf.raw_ics
  else:
    latest = await database_requests.get_latest_snapshot_for_web_subscription(db, source.web_subscription_id)
    if latest is None:
      raise HTTPException(status_code=404, detail='No backup available for this source yet')
    raw_ics = latest.raw_ics

  return _ics_attachment(raw_ics, f'{source.label or "calendar"}.ics')


@router.get('/{import_id}/sources/{source_id}/snapshots/{snapshot_id}/download')
async def download_source_snapshot(import_id: uuid.UUID, source_id: uuid.UUID, snapshot_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  """Download one specific backup snapshot of a web source's content."""
  imp = await _get_owned_import(db, import_id, user.id)

  source = await database_requests.get_import_source_by_id(db, source_id)
  if source is None or source.import_id != imp.id:
    raise HTTPException(status_code=404, detail='Import source not found')
  if source.kind != CalendarImportSourceKind.WEB:
    raise HTTPException(status_code=400, detail='Only web sources have snapshots')

  snapshot = await database_requests.get_snapshot_by_id(db, snapshot_id)
  if snapshot is None or snapshot.web_subscription_id != source.web_subscription_id:
    raise HTTPException(status_code=404, detail='Snapshot not found')

  timestamp = snapshot.fetched_at.strftime('%Y%m%dT%H%M%S')
  return _ics_attachment(snapshot.raw_ics, f'{source.label or "calendar"}-{timestamp}.ics')


@router.post('/{import_id}/sources/{source_id}/activate')
async def activate_import_source(import_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  """Explicitly select which attached source (a specific static file, or the
  live web subscription) is the one that feeds this import's exports."""
  imp = await _get_owned_import(db, import_id, user.id)

  source = await database_requests.get_import_source_by_id(db, source_id)
  if source is None or source.import_id != imp.id:
    raise HTTPException(status_code=404, detail='Import source not found')

  imp.active_source_id = source.id
  await db.commit()
  await db.refresh(imp)
  return await serialize_import(db, imp)


@router.patch('/{import_id}/sources/{source_id}')
async def update_source(import_id: uuid.UUID, source_id: uuid.UUID, payload: dict, user: CurrentUser, db: DatabaseSession):
  """label is modifiable for both static and web sources -- it's just this
  import's own display name for the source, never the URL/file content itself
  (that's changed by adding a new source and removing the old one).
  contribute_backup is only meaningful for web sources."""
  imp = await _get_owned_import(db, import_id, user.id)

  source = await database_requests.get_import_source_by_id(db, source_id)
  if source is None or source.import_id != imp.id:
    raise HTTPException(status_code=404, detail='Import source not found')

  if 'label' in payload:
    new_label = naming.sanitize_display_text(payload.get('label') or '')
    if not new_label:
      raise HTTPException(status_code=422, detail='label is required')
    if not naming.REGEX_DISPLAY_NAME.match(new_label):
      raise HTTPException(status_code=422, detail=f'label {naming.DISPLAY_NAME_ERROR}')

    existing_labels = _existing_source_labels(imp) - {source.label}
    if new_label in existing_labels:
      raise HTTPException(status_code=409, detail='Label already in use')

    source.label = new_label

  if 'contribute_backup' in payload:
    if source.kind != CalendarImportSourceKind.WEB:
      raise HTTPException(status_code=400, detail='contribute_backup only applies to web sources')
    source.contribute_backup = bool(payload['contribute_backup'])

  await db.commit()
  return await serialize_source(db, source, imp.active_source_id)


@router.delete('/{import_id}/sources/{source_id}')
async def delete_import_source(import_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, db: DatabaseSession):
  """Remove one CalendarImportSource reference from an import (a URL or static
  file attached to it). The underlying pooled StaticFile/WebSubscription is left
  in place even if this was the last reference to it -- see cleanup_orphan_resources,
  which sweeps unreferenced pooled resources on its own schedule instead of here."""
  imp = await _get_owned_import(db, import_id, user.id)

  source = await database_requests.get_import_source_by_id(db, source_id)
  if source is None or source.import_id != imp.id:
    raise HTTPException(status_code=404, detail='Import source not found')

  was_active = imp.active_source_id == source.id

  # If this is the active source, repoint (or clear) the FK and flush *before* deleting
  # the row it currently points to -- otherwise the DELETE flushes while imp.active_source_id
  # still references the row being removed, which is the same kind of FK ordering problem
  # as delete_import (there it's a cascade cycle; here it's a stale reference at delete time).
  if was_active:
    fallback = await database_requests.get_fallback_import_source(db, imp.id, source.id)
    imp.active_source_id = fallback.id if fallback else None
    await db.flush()

  await db.delete(source)
  await db.commit()
  await db.refresh(imp)
  return await serialize_import(db, imp)
