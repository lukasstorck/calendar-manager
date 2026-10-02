import asyncio
from datetime import datetime, timezone

import src.config
import src.logging
from src.core.db import AsyncSession, async_session
from src.models import Snapshot, WebSubscription
from src.services import calendar_service, database_requests

logger = src.logging.logger


async def refresh_web_subscription(db: AsyncSession, subscription: WebSubscription) -> None:
  now = datetime.now(timezone.utc)
  try:
    content = await calendar_service.fetch_url(subscription.url)
    calendar = calendar_service.CalendarPipeline(content)
    calendar_stats = calendar.stats()

    snapshot = await database_requests.find_snapshot_by_hash(db, subscription.id, calendar.hash)
    if snapshot is None:
      snapshot = Snapshot(
        web_subscription_id=subscription.id,
        file_hash=calendar.hash,
        raw_ics=content.decode('utf-8', errors='replace'),
        event_count=calendar_stats.event_count,
        range_start=calendar_stats.range_start,
        range_end=calendar_stats.range_end,
      )
      db.add(snapshot)

    subscription.last_pulled_at = now
    subscription.last_success_at = now
    subscription.last_error = None
  except calendar_service.CalendarFetchError as exception:
    subscription.last_pulled_at = now
    subscription.last_error = str(exception)[:2000]
    logger.warning(str(exception))
  except Exception as exception:
    subscription.last_pulled_at = now
    subscription.last_error = str(exception)[:2000]
    logger.exception(f'Failed to refresh web subscription {subscription.id}')


async def run_refresh_pass() -> None:
  """1) pull all web subscriptions, 2) run all export rules against
  current data. Doing it in this order avoids recursion within one pass,
  since exports never write back into imports."""
  async with async_session() as db:
    subscriptions = await database_requests.get_all_web_subscriptions(db)
    # TODO: remove subscriptions that are not referenced in any CalendarImportSource
    # (see imports.cleanup_orphan_resources -- wire that up as its own periodic job
    # rather than doing the removal inline in this refresh pass)
    for subscription in subscriptions:
      await refresh_web_subscription(db, subscription)
    await db.commit()

  async with async_session() as db:
    calendar_exports = await database_requests.get_all_exports(db)
    now = datetime.now(timezone.utc)
    for calendar_export in calendar_exports:
      try:
        await calendar_service.update_export_data(db, calendar_export, now)
      except Exception:
        logger.exception(f'Failed to run export {calendar_export.id}')
    await db.commit()


async def scheduler_loop(stop_event: asyncio.Event) -> None:
  while not stop_event.is_set():
    try:
      await run_refresh_pass()
    except Exception:
      logger.exception('Refresh pass failed')
    try:
      await asyncio.wait_for(stop_event.wait(), timeout=src.config.settings.refresh_interval_seconds)
    except asyncio.TimeoutError:
      pass
