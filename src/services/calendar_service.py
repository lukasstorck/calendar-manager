import datetime
import hashlib

import httpx
import icalendar

import src.logging
from src.core.config import MAX_WEB_CALENDAR_FILE_SIZE

logger = src.logging.logger


def hash_content(content: bytes) -> str:
  return hashlib.sha256(content).hexdigest()


class CalendarFetchError(Exception):
  """Raised when a web calendar subscription cannot be fetched. str(e) is a short, loggable one-liner."""


def _truncate_url(url: str, max_len: int = 60) -> str:
  return url if len(url) <= max_len else url[:max_len] + '...'


async def fetch_url(url: str) -> bytes:
  """Downloads web calendar from url. Raises CalendarFetchError on failure, timeout or when content too large."""
  try:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
      response = await client.get(url)
      response.raise_for_status()
      content = response.content
      if len(content) > MAX_WEB_CALENDAR_FILE_SIZE:
        raise ValueError('file too large')
      return content
  except httpx.HTTPStatusError as e:
    reason = f'HTTP {e.response.status_code}'
  except httpx.UnsupportedProtocol:
    reason = 'missing http(s):// scheme'
  except httpx.ConnectError:
    reason = 'could not resolve/connect to host'
  except httpx.TimeoutException:
    reason = 'request timed out'
  except ValueError as e:
    reason = str(e)
  except httpx.HTTPError as e:
    reason = e.__class__.__name__

  raise CalendarFetchError(f'Could not load calendar {_truncate_url(url)}: {reason}') from None


def _to_datetime(value) -> datetime.datetime | None:
  if value is None:
    return None
  if isinstance(value, datetime.datetime):
    if value.tzinfo is None:
      return value.replace(tzinfo=datetime.timezone.utc)
    return value
  if isinstance(value, datetime.date):
    return datetime.datetime(value.year, value.month, value.day, tzinfo=datetime.timezone.utc)
  return None


def parse_calendar(content: bytes, calendar_name: str = '') -> tuple[list[dict], datetime.datetime | None, datetime.datetime | None]:
  """Returns (events, range_start, range_end). Raises on parse failure."""
  calendar = icalendar.Calendar.from_ical(content)
  events = []
  range_start = None
  range_end = None

  for component in calendar.walk('VEVENT'):
    start = _to_datetime(component.get('dtstart').dt) if component.get('dtstart') else None
    end = _to_datetime(component.get('dtend').dt) if component.get('dtend') else start
    title = str(component.get('summary', ''))
    location = str(component.get('location', ''))
    description = str(component.get('description', ''))
    uid = str(component.get('uid', ''))

    events.append(
      {
        'uid': uid,
        'title': title,
        'location': location,
        'description': description,
        'start': start,
        'end': end,
        'calendar_name': calendar_name,
        'raw': component,
      }
    )
    if start is not None:
      range_start = start if range_start is None or start < range_start else range_start
      range_end = end if end and (range_end is None or end > range_end) else range_end

  return events, range_start, range_end


def build_calendar(events: list[dict], calendar_name: str = 'Filtered Calendar') -> str:
  logger.info(f'Building calendar with {len(events)} events')
  calendar = icalendar.Calendar()
  calendar.add('prodid', '-//Calendar Manager//calmgr//')  # TODO: check correct format
  calendar.add('version', '2.0')
  calendar.add('x-wr-calname', calendar_name)  # TODO: check use, calendar gateway uses similar field for original calendar name

  for event in events:
    raw = event.get('raw')
    if raw is not None:
      calendar.add_component(raw)
    else:
      vevent = icalendar.Event()
      vevent.add('summary', event.get('title', ''))
      if event.get('start'):
        vevent.add('dtstart', event['start'])
      if event.get('end'):
        vevent.add('dtend', event['end'])
      if event.get('location'):
        vevent.add('location', event['location'])
      if event.get('description'):
        vevent.add('description', event['description'])
      vevent.add('uid', event.get('uid') or event.get('title', 'event'))
      calendar.add_component(vevent)

  return calendar.to_ical().decode('utf-8')
