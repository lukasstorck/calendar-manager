import asyncio
import collections
import dataclasses
import datetime
import uuid

import src.config
import src.logging
from src.models import CalendarImport, CalendarImportFileReference
from src.services import calendar_service, database_requests
from src.services.database_requests import AsyncSession, async_session

logger = src.logging.logger


# -----------------------------
# region: import/export refresh
# -----------------------------


@dataclasses.dataclass
class FetchBackoffEntry:
  consecutive_failures: int = 0
  backoff_until: datetime.datetime | None = None


class FetchBackoffTracker:
  """In-memory backoff state for repeatedly failing web calendar URLs.

  The state is stored per URL and does not persist across restarts. The n-th
  consecutive failure of a URL backs it off for `duration =
  backoff_durations[min(n, len(backoff_durations)) - 1]`.
  """

  def __init__(self, backoff_durations: list[datetime.timedelta]):
    self._backoff_durations = backoff_durations
    self._entries: dict[str, FetchBackoffEntry] = {}

  def is_backed_off(self, url: str, now: datetime.datetime) -> bool:
    """Whether the URL is tracked and is currently backed off."""
    entry = self._entries.get(url)
    return entry is not None and entry.backoff_until is not None and entry.backoff_until >= now

  def record_success(self, url: str):
    self._entries.pop(url, None)

  def record_failure(self, url: str, now: datetime.datetime) -> datetime.datetime | None:
    """Count a failed fetch. Returns the end of the backoff if the URL is now backed off.

    The counter is only reset by a success, so a single failed retry after an expired
    backoff immediately starts the next, longer backoff.
    """
    entry = self._entries.setdefault(url, FetchBackoffEntry())
    entry.consecutive_failures += 1

    duration_index = min(entry.consecutive_failures, len(self._backoff_durations)) - 1
    backoff_duration = self._backoff_durations[duration_index]
    entry.backoff_until = now + backoff_duration if backoff_duration else None

    return entry.backoff_until

  def prune_from_active(self, active_urls: set[str]):
    """Drop tracked URLs that no web import uses anymore."""
    for url in set(self._entries) - active_urls:
      del self._entries[url]


async def refresh_web_imports(db: AsyncSession, now: datetime.datetime, backoff_tracker: FetchBackoffTracker):
  """Fetch every distinct URL once, then store results for all web calendar
  imports using that URL.

  URLs that failed repeatedly are skipped until their backoff has expired.
  """

  # collect all imports by their URLs

  web_imports = await database_requests.get_web_imports(db)

  imports_by_url: dict[str, list[CalendarImport]] = collections.defaultdict(list)
  for calendar_import in web_imports:
    imports_by_url[calendar_import.url].append(calendar_import)

  for url, calendar_imports in imports_by_url.items():
    if backoff_tracker.is_backed_off(url, now):
      continue

    error = None

    try:
      calendar_bytes, fetch_error = await calendar_service.fetch_url(url)
      if fetch_error:
        raise ValueError(fetch_error)

      calendar = calendar_service.CalendarPipeline(calendar_bytes, lazy=True)
      calendar_file = await calendar_service.get_or_create_calendar_file(db, calendar)
    except Exception as exception:  # noqa: BLE001
      error = str(exception)

    if error:
      backoff_until = backoff_tracker.record_failure(url, now)
      if backoff_until:
        backoff_until_text = backoff_until.strftime('%Y-%m-%dT%H:%M:%SZ')
        error = f'{error} (repeated failures, backing off until {backoff_until_text})'
    else:
      backoff_tracker.record_success(url)

    for calendar_import in calendar_imports:
      # NOTE: fetch attempted, might not have succeeded -> still update fetched_at
      calendar_import.last_fetched_at = now

      if error:
        calendar_import.last_error = error
        continue

      calendar_import.last_success_at = now
      calendar_import.last_error = None

      if calendar_import.active_file_id != calendar_file.id:
        await database_requests.add_file_reference(db, calendar_import.id, calendar_file.id, now)
        calendar_import.active_file_id = calendar_file.id

  active_urls = set(imports_by_url)
  backoff_tracker.prune_from_active(active_urls)


async def run_refresh(backoff_tracker: FetchBackoffTracker):
  """Fetch subscribed web calendars and update all calendar exports."""
  now = datetime.datetime.now(datetime.timezone.utc)

  # Fetch subscribed web calendars

  async with async_session() as db:
    await refresh_web_imports(db, now, backoff_tracker)
    await db.commit()

  # Update calendar exports

  async with async_session() as db:
    calendar_exports = await database_requests.get_all_exports(db)

    for calendar_export in calendar_exports:
      try:
        await calendar_service.update_export_data(db, calendar_export, now)
      except Exception:
        logger.exception(f'Failed to update export {calendar_export.id}')

    await db.commit()


# ------------------------
# region: database cleanup
# ------------------------


def find_essential_reference_ids(
  file_references: list[CalendarImportFileReference],
  now: datetime.datetime,
  retention_tier: src.config.RetentionTier,
  tolerance_ratio: float,
) -> set[uuid.UUID]:
  """Ids of import file references that are essential for the specified backup retention tier.

  First the backup timeline is divided into slots of potential or desired backup timestamps.
  These are spaced out by the retention tier interval and reach until `max_age` or the oldest
  file reference is reached. Then, for every slot, the oldest reference that is not older than
  the slot cut-off is marked as essential so that the backup rule is satisfied.
  """

  window_start = file_references[0].created_at if retention_tier.max_age is None else now - retention_tier.max_age
  window = collections.deque(file_reference for file_reference in file_references if file_reference.created_at >= window_start)
  slot_count = (now - window_start) // retention_tier.interval + 1

  essential_reference_ids: set[uuid.UUID] = set()
  tolerance = retention_tier.interval * tolerance_ratio

  for slot in reversed(range(slot_count)):
    cut_off = now - slot * retention_tier.interval - tolerance

    while window and window[0].created_at < cut_off:
      window.popleft()

    if not window:
      break

    essential_reference_ids.add(window[0].id)

  return essential_reference_ids


def find_disposable_reference_ids(
  file_references: list[CalendarImportFileReference],
  keep_history: bool,
  now: datetime.datetime,
  retention_tiers: list[src.config.RetentionTier],
  tolerance_ratio: float,
) -> list[uuid.UUID]:
  """Check all retention tier rules and find disposable file reference ids."""
  if not file_references:
    return []

  # the newest reference matches the active file and is always kept
  essential_ids = {file_references[-1].id}

  if keep_history:
    for retention_tier in retention_tiers:
      essential_ids |= find_essential_reference_ids(file_references, now, retention_tier, tolerance_ratio)

  return [file_reference.id for file_reference in file_references if file_reference.id not in essential_ids]


async def run_database_cleanup():
  """Prune no longer needed web calendar backups."""
  now = datetime.datetime.now(datetime.timezone.utc)

  logger.info('Running daily cleanup')

  # prune file references

  async with async_session() as db:
    web_imports = await database_requests.get_web_imports(db)

    for calendar_import in web_imports:
      file_references = await database_requests.get_file_references_oldest_first(db, calendar_import.id)
      disposable_ids = find_disposable_reference_ids(
        file_references,
        keep_history=calendar_import.backup,
        now=now,
        retention_tiers=src.config.settings.retention_tiers,
        tolerance_ratio=src.config.settings.relative_retention_interval_tolerance,
      )
      deleted_count = await database_requests.delete_file_references_by_ids(db, disposable_ids)

      if deleted_count:
        logger.info(f'Deleted {deleted_count} file reference(s) for calendar import {calendar_import.id}')

    await db.commit()

  # prune calendar files

  async with async_session() as db:
    deleted_count = await database_requests.delete_unreferenced_calendar_files(db)
    await db.commit()

    if deleted_count:
      logger.info(f'Deleted {deleted_count} unreferenced calendar file(s)')


# -----------------------
# region: scheduler loops
# -----------------------


async def scheduler_loop(stop_event: asyncio.Event):
  backoff_tracker = FetchBackoffTracker(src.config.settings.fetch_backoff_durations)

  while not stop_event.is_set():
    try:
      await run_refresh(backoff_tracker)
    except Exception:
      logger.exception('Refresh pass failed')
    else:
      logger.debug('Refresh pass completed')

    try:
      await asyncio.wait_for(stop_event.wait(), timeout=src.config.settings.refresh_interval.total_seconds())
    except asyncio.TimeoutError:
      pass


async def cleanup_loop(stop_event: asyncio.Event):
  """Run the daily cleanup once at startup, then every day at the configured cleanup time."""
  while not stop_event.is_set():
    try:
      await run_database_cleanup()
    except Exception:
      logger.exception('Daily cleanup failed')

    now = datetime.datetime.now(datetime.timezone.utc)
    next_run = datetime.datetime.combine(now.date(), src.config.settings.cleanup_time, tzinfo=datetime.timezone.utc)
    if next_run <= now:
      next_run += datetime.timedelta(days=1)

    try:
      await asyncio.wait_for(stop_event.wait(), timeout=(next_run - now).total_seconds())
    except asyncio.TimeoutError:
      pass
