import hashlib
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

import src.logging
from src.models import (
  CalendarExport,
  CalendarExportCache,
  CalendarImportSourceKind,
)
from src.services import calendar_service, database_requests, filter_parser

logger = src.logging.logger


def compute_dedup_key(source_ids: list[str], rule_text: str) -> str:
  normalized_sources = ','.join(sorted(str(s) for s in source_ids))
  normalized_rule = '_'.join(rule_text.split())
  payload = f'{normalized_sources}|{normalized_rule}'
  return hashlib.sha256(payload.encode('utf-8')).hexdigest()


async def get_export_source_ids(db: AsyncSession, export: CalendarExport) -> list[str]:
  """Dedup key is keyed on the active *import source* (static file / snapshot lineage),
  not the import itself, since that's what actually determines the output."""
  active_source_ids = await database_requests.get_active_source_ids_for_export(db, export.id)
  return [str(source_id) for source_id in active_source_ids if source_id is not None]


async def run_filter(db: AsyncSession, export: CalendarExport, now: datetime | None = None) -> None:
  """Recompute an export's output, using/populating the shared CalendarExportCache."""
  now = now or datetime.now(timezone.utc)

  parsed = filter_parser.parse(export.rule_text)
  source_ids = await get_export_source_ids(db, export)
  dedup_key = compute_dedup_key(source_ids, export.rule_text)

  cache = await database_requests.get_export_cache_by_dedup_key(db, dedup_key)
  if cache is not None:
    export.dedup_key = dedup_key
    export.last_output_count = cache.event_count
    export.last_output_change_at = cache.updated_at
    export.last_checked_at = now
    return

  all_events: list[dict] = []
  if source_ids and parsed.valid:
    calendar_imports = await database_requests.get_imports_for_export(db, export.id)
    for calendar_import in calendar_imports:
      # Not calendar_import.active_source / active_source.static_file -- those are lazy
      # relationship attributes, and touching them outside an explicit awaited query
      # crashes with sqlalchemy.exc.MissingGreenlet even from inside an async function
      # (being async doesn't make a bare attribute access awaitable by itself). Look
      # them up by FK id instead, same as the fixes in imports.py.
      if calendar_import.active_source_id is None:
        continue
      active_source = await database_requests.get_import_source_by_id(db, calendar_import.active_source_id)
      if active_source is None:
        continue

      if active_source.kind == CalendarImportSourceKind.STATIC:
        static_file = await database_requests.get_static_file_by_id(db, active_source.static_file_id) if active_source.static_file_id else None
        raw_ics = static_file.raw_ics if static_file else None
      else:
        latest = await database_requests.get_latest_snapshot_for_web_subscription(db, active_source.web_subscription_id)
        raw_ics = latest.raw_ics if latest else None

      if not raw_ics:
        continue
      try:
        events, _, _ = calendar_service.parse_calendar(raw_ics.encode('utf-8'), calendar_name=calendar_import.name)
      except Exception as exc:  # noqa: BLE001
        logger.error(f'Invalid calendar file: {exc}')
        continue
      for ev in events:
        if filter_parser.matches(ev, parsed.predicates, now):
          all_events.append(ev)

  output_ics = calendar_service.build_calendar(all_events, calendar_name=export.name)
  event_count = len(all_events)

  cache = CalendarExportCache(dedup_key=dedup_key, output_ics=output_ics, event_count=event_count)
  db.add(cache)
  await db.flush()

  export.dedup_key = dedup_key
  export.last_output_count = event_count
  export.last_output_change_at = now
  export.last_checked_at = now


async def find_duplicate_exports(db: AsyncSession, export: CalendarExport) -> list[CalendarExport]:
  if not export.dedup_key:
    return []
  return await database_requests.get_duplicate_exports(db, export.dedup_key, export.id)
