import copy
import dataclasses
import datetime
import enum
import hashlib
import re
import shlex
import sqlite3

import httpx
import icalendar
from sqlalchemy.ext.asyncio import AsyncSession

import src.logging
from src.core.config import MAX_ERROR_URL_LENGTH, MAX_WEB_CALENDAR_FILE_SIZE
from src.models import CalendarExport, CalendarImportSourceKind
from src.services import database_requests

logger = src.logging.logger


VEVENT_SQL_COLUMNS = {
  'dtstamp': 'TEXT',
  'uid': 'TEXT',
  'dtstart': 'TEXT',
  'dtend': 'TEXT',  # filled in if missing
  'duration': 'INTEGER',  # seconds, filled in if missing
  'summary': 'TEXT',
  'description': 'TEXT',
  'location': 'TEXT',
  'class': 'TEXT COLLATE NOCASE',
  'status': 'TEXT COLLATE NOCASE',
  'transp': 'TEXT COLLATE NOCASE',
  'sequence': 'INTEGER',
  'created': 'TEXT',
  'last-modified': 'TEXT',
  'url': 'TEXT',
  'all-day': 'INTEGER',  # 1 if DTSTART is a DATE value (VALUE=DATE), else 0
}


@dataclasses.dataclass
class CalendarProperties:
  prodid: str
  uid: str | None = None
  last_modified: datetime.datetime | None = None
  url: str | None = None
  refresh_interval: datetime.timedelta | None = None
  color: str | None = None


@dataclasses.dataclass
class CalendarStats:
  event_count: int
  range_start: datetime.datetime | None
  range_end: datetime.datetime | None


class MergeMode(enum.StrEnum):
  """Behavior for handling events with the same UID.

  Modes:
    `OVERWRITE`: overwrite an event with the same UID
    `SKIP`: skip any further events with the same UID
  """

  OVERWRITE = 'overwrite'
  SKIP = 'skip'


class CalendarPipeline:
  def __init__(self, data: bytes):
    self.calendar: icalendar.Calendar = icalendar.Calendar.from_ical(data)

  @property
  def hash(self):
    return hashlib.sha256(self.calendar.to_ical()).hexdigest()

  def copy(self, deepcopy: bool = True):
    return copy.deepcopy(self.calendar) if deepcopy else self.calendar

  def stats(self):
    event_count = 0
    range_start = None
    range_end = None

    for component in self.calendar.walk():
      if component.name != 'VEVENT':
        continue

      event_count += 1
      event_start, event_end = CalendarPipeline._event_bounds(component)

      if event_start is None:
        continue

      if range_start is None or event_start < range_start:
        range_start = event_start
      if range_end is None or event_end > range_end:
        range_end = event_end

    return CalendarStats(event_count=event_count, range_start=range_start, range_end=range_end)

  def export_calendar_properties(self) -> CalendarProperties:
    """Return extracted calendar properties."""
    properties = CalendarProperties(
      prodid=self.calendar.prodid,
      uid=self.calendar.uid,
      last_modified=self.calendar.last_modified,
      url=self.calendar.url,
      refresh_interval=self.calendar.refresh_interval,
      color=self.calendar.color,
    )
    return properties

  def apply_calendar_properties(self, properties: CalendarProperties, common_vendor_fallbacks: bool = True, deepcopy: bool = True):
    """Overwrite calendar properties.

    If `common_vendor_fallbacks` is True, set these additional properties:
      uid -> X-WR-RELCALID
      refresh_interval -> X-PUBLISHED-TTL
    """

    calendar = self.copy(deepcopy)

    calendar.prodid = properties.prodid
    calendar.version = '2.0'
    calendar.uid = properties.uid
    calendar.last_modified = properties.last_modified
    calendar.url = properties.url
    calendar.color = properties.color

    calendar.pop('REFRESH-INTERVAL', None)
    if properties.refresh_interval is not None:
      calendar.add('REFRESH-INTERVAL', properties.refresh_interval, parameters={'VALUE': 'DURATION'})

    if common_vendor_fallbacks:
      if calendar.uid:
        calendar.pop('X-WR-RELCALID', None)
        calendar.add('X-WR-RELCALID', calendar.uid)

      if calendar.refresh_interval:
        calendar.pop('X-PUBLISHED-TTL', None)
        calendar.add('X-PUBLISHED-TTL', icalendar.vDuration(calendar.refresh_interval).to_ical().decode())

    return calendar

  @staticmethod
  def validate_filter(filter: str) -> str | None:
    """Check if `filter` is a valid SQL WHERE clause.

    Return None if valid, an error message otherwise.
    """
    filter = filter.strip()
    if not filter:
      return None

    # TODO: add dummy data to also validate functions
    session = CalendarPipeline._create_session()
    try:
      session.execute(f'SELECT id FROM events WHERE {filter}').fetchall()
      return None
    except sqlite3.Error as error:
      return str(error)
    finally:
      session.close()

  def apply_filter(self, filter: str, deepcopy: bool = True) -> icalendar.Calendar:
    """Get calendar with filtered events."""
    calendar = self.copy(deepcopy)

    filter = filter.strip()
    if not filter:
      return calendar

    events = [component for component in calendar.subcomponents if component.name == 'VEVENT']

    session = self._create_session([(index, *self._create_row_data_from_event(event)) for index, event in enumerate(events)])
    try:
      # TODO maybe use uid instead of index, but must ensure uid is unique and not null beforehand
      keep_event_rows = {row[0] for row in session.execute(f'SELECT id FROM events WHERE {filter}')}
    finally:
      session.close()

    kept_components = []
    event_index = 0
    for component in calendar.subcomponents:
      if component.name != 'VEVENT':
        kept_components.append(component)
        continue

      if event_index in keep_event_rows:
        kept_components.append(component)
      event_index += 1

    calendar.subcomponents[:] = kept_components
    return calendar

  @staticmethod
  def parse_transform(transform: str) -> tuple[list[tuple], str | None]:
    """Parse a transform string into operations."""
    try:
      tokens = shlex.split(transform)
    except ValueError as error:
      return [], f'Invalid quoting: {error}'

    operations: list[tuple] = []
    for token in tokens:
      name, _, value = token.partition(':')
      name = name.lower()

      # TODO: base predicates on regexes, provide list of allowed predicates (with regexes) for web-client

      if name in ('clip-min-duration', 'clip-max-duration', 'shift'):
        try:
          duration = icalendar.vDuration.from_ical(value)
        except ValueError:
          return [], f'{name}: invalid ISO 8601 duration "{value}"'
        if name != 'shift' and duration < datetime.timedelta(0):
          continue  # negative clip values are ignored
        operations.append((name, duration))

      elif name.startswith('set-'):
        key = name[len('set-') :].upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9-]*', key):
          return [], f'{name}: invalid property name'
        if key == 'UID':
          return [], 'set-uid: uid can not be overwritten'
        if key == 'EXTRA-PROPERTIES':
          return [], 'set-extra-properties is not allowed'
        if key in ('DTSTART', 'DTEND', 'DURATION'):
          return [], f'set-{key.lower()}: use shift/clip-* to change event times'
        operations.append(('set', (key, value)))

      elif name == 'remove':
        key = value.upper()
        if not key:
          return [], 'remove: missing property name'
        if key in ('UID', 'DTSTAMP', 'DTSTART', 'DTEND', 'DURATION'):
          return [], f'remove: {key.lower()} is protected and can not be removed'
        operations.append(('remove', key))

      elif name in ('overlap-trim-start', 'overlap-trim-end', 'combine-all-day'):
        if value:
          return [], f'{name} does not take a value'
        operations.append((name, None))

      else:
        return [], f'Unknown transform predicate: {name}'

    return operations, None

  @staticmethod
  def validate_transform(transform: str) -> str | None:
    """Check if `transform` is a valid.

    Return None if valid, an error message otherwise.
    """
    # TODO: check for invalid key / value pairs for set-* predicate
    #       -> test against icalendar.Event().add(key, value)
    _, error = CalendarPipeline.parse_transform(transform)
    return error

  def apply_transform(self, transform: str, deepcopy: bool = True):
    calendar = self.copy(deepcopy)

    operations, error = CalendarPipeline.parse_transform(transform)
    if error:
      raise ValueError(error)

    for name, argument in operations:
      events = [component for component in calendar.subcomponents if component.name == 'VEVENT']

      if name in ('clip-min-duration', 'clip-max-duration', 'shift'):
        for event in events:
          if CalendarPipeline._is_all_day(event):
            # shift/clip are not meaningful for all-day (date) events
            # TODO maybe allow shift if duration > 24h, shift all-day events by whole days
            continue

          start, end = CalendarPipeline._event_bounds(event)
          if start is None:
            continue

          if name == 'shift':
            new_start, new_end = start + argument, end + argument
          else:
            new_start = start
            too_short = name == 'clip-min-duration' and end - start < argument
            too_long = name == 'clip-max-duration' and end - start > argument
            new_end = start + argument if too_short or too_long else end
          if (new_start, new_end) != (start, end):
            CalendarPipeline._write_bounds(event, new_start, new_end)

      elif name == 'set':
        key, value = argument
        for event in events:
          event.pop(key, None)
          event.add(key, value)

      elif name == 'remove':
        for event in events:
          if argument == 'EXTRA-PROPERTIES':
            for key in [key for key in event if key.upper().startswith('X-')]:
              del event[key]
          else:
            event.pop(argument, None)

      elif name in ('overlap-trim-start', 'overlap-trim-end'):
        CalendarPipeline._trim_overlaps(events, trim_start=name == 'overlap-trim-start')

      elif name == 'combine-all-day':
        CalendarPipeline._combine_all_day(calendar, events)

    return calendar

  def merge_events(self, other: 'CalendarPipeline', mode: str = MergeMode.OVERWRITE, deepcopy: bool = True):
    """Merge events with other calendar.

    Events with duplicate UID are handled according to `mode`.
    """
    if mode not in (MergeMode.OVERWRITE, MergeMode.SKIP):
      raise ValueError(f'Unknown merge mode: {mode}')

    calendar = self.copy(deepcopy)
    existing_uids = {str(component.uid) for component in calendar.subcomponents if component.name == 'VEVENT'}

    # collect events to insert (uid -> event), one pass over `other`
    incoming: dict[str, icalendar.Component] = {}
    for component in other.calendar.subcomponents:
      if component.name != 'VEVENT':
        continue

      uid = str(component.uid)
      if mode == MergeMode.SKIP and (uid in existing_uids or uid in incoming):
        continue
      incoming[uid] = component

    # delete overwritten events with a single pass over the calendar
    if mode == MergeMode.OVERWRITE:
      replaced_uids = existing_uids & incoming.keys()
      if replaced_uids:
        calendar.subcomponents[:] = [
          component for component in calendar.subcomponents if not (component.name == 'VEVENT' and str(component.uid) in replaced_uids)
        ]

    # insert
    for component in incoming.values():
      calendar.add_component(copy.deepcopy(component))

    calendar.add_missing_timezones()
    return calendar

  def sort_components(self):
    """Soft sort icalendar components.

    Components are sorted by:
    1. whether they are an event
    2. event start
    3. event end
    4. event UID
    """

    _MIN_DT = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

    def build_sort_key(component: icalendar.Component):
      if not isinstance(component, icalendar.Event):
        return (0, _MIN_DT, _MIN_DT, '')
      start, end = CalendarPipeline._event_bounds(component)
      return (1, start or _MIN_DT, end or _MIN_DT, str(component.get('UID', '')))

    self.calendar.subcomponents.sort(key=build_sort_key)

  @staticmethod
  def _create_session(rows=()) -> sqlite3.Connection:
    """Prepare new sqlite3 session."""
    column_definitions = ', '.join(f'"{name}" {column_type}' for name, column_type in VEVENT_SQL_COLUMNS.items())
    column_names = ', '.join(f'"{name}"' for name in VEVENT_SQL_COLUMNS)
    placeholders = ', '.join('?' * (len(VEVENT_SQL_COLUMNS) + 1))

    session = sqlite3.connect(':memory:')
    session.create_function('REGEXP', 2, CalendarPipeline._regexp, deterministic=True)
    session.create_function('DURATION', 1, CalendarPipeline._duration, deterministic=True)
    session.execute(f'CREATE TABLE events (id INTEGER PRIMARY KEY, {column_definitions})')
    session.executemany(f'INSERT INTO events (id, {column_names}) VALUES ({placeholders})', rows)
    session.set_authorizer(CalendarPipeline._read_only_authorizer)
    return session

  @staticmethod
  def _to_utc(value: datetime.date | datetime.datetime | None) -> datetime.datetime | None:
    """Normalize timezone to UTC for date and datetime values."""
    if value is None:
      return None

    if isinstance(value, datetime.datetime):
      if value.tzinfo is None:
        # floating time: assume UTC
        return value.replace(tzinfo=datetime.timezone.utc)
      # convert to UTC (if not already UTC)
      return value.astimezone(datetime.timezone.utc)
    # plain date (all-day event) -> midnight UTC
    return datetime.datetime.combine(value, datetime.time.min, tzinfo=datetime.timezone.utc)

  @staticmethod
  def _is_all_day(event: icalendar.Event) -> bool:
    """True if DTSTART is a DATE value (all-day event), False for date-times."""
    dtstart = event.decoded('DTSTART', None)
    return isinstance(dtstart, datetime.date) and not isinstance(dtstart, datetime.datetime)

  @staticmethod
  def _event_bounds(component: icalendar.Event) -> tuple[datetime.datetime, datetime.datetime] | tuple[None, None]:
    """Return start, end as a UTC datetime for a VEVENT."""
    raw_start = component.decoded('DTSTART', None)
    if raw_start is None:
      return None, None

    start = CalendarPipeline._to_utc(raw_start)
    raw_end = component.decoded('DTEND', None)
    duration = component.decoded('DURATION', None)

    if raw_end is not None:
      end = CalendarPipeline._to_utc(raw_end)
    elif duration is not None:
      end = start + duration
    elif not isinstance(raw_start, datetime.datetime):
      # all-day with no end lasts one day
      end = start + datetime.timedelta(days=1)
    else:
      # timed with no end is zero-length
      end = start

    return start, max(start, end)

  @staticmethod
  def _to_sql_value(value):
    """Convert a decoded icalendar value into something sqlite can store."""
    if value is None:
      return None
    if isinstance(value, (datetime.datetime, datetime.date)):
      return value.isoformat()
    if isinstance(value, datetime.timedelta):
      return int(value.total_seconds())
    if isinstance(value, (int, str, bytes)):
      return value
    return str(value)

  @staticmethod
  def _create_row_data_from_event(event: icalendar.Event) -> tuple:
    """Values for one VEVENT for a SQL table row.

    A missing dtend/duration is calculated (only in the table; the VEVENT
    itself is not modified):
      DTSTART + DTEND     -> duration = dtend - dtstart
      DTSTART + DURATION  -> dtend = dtstart + duration
      DTSTART (date-time) -> dtend = dtstart, duration = 0
      DTSTART (date)      -> dtend = dtstart + 1 day, duration = 86400
    """
    values = {name: CalendarPipeline._to_sql_value(event.decoded(name, None)) for name in VEVENT_SQL_COLUMNS}

    values['dtstamp'] = CalendarPipeline._to_sql_value(CalendarPipeline._to_utc(event.decoded('DTSTAMP', None)))
    values['created'] = CalendarPipeline._to_sql_value(CalendarPipeline._to_utc(event.decoded('CREATED', None)))
    values['last-modified'] = CalendarPipeline._to_sql_value(CalendarPipeline._to_utc(event.decoded('LAST-MODIFIED', None)))

    values['all-day'] = int(CalendarPipeline._is_all_day(event))

    start, end = CalendarPipeline._event_bounds(event)
    values['dtstart'] = CalendarPipeline._to_sql_value(start)
    values['dtend'] = CalendarPipeline._to_sql_value(end)
    values['duration'] = CalendarPipeline._to_sql_value(end - start) if start is not None and end is not None else None

    return tuple(values.values())

  @staticmethod
  def _duration(value):
    """Implements DURATION('PT24H') -> 86400 (ISO 8601 to seconds)."""
    if value is None:
      return None
    return int(icalendar.vDuration.from_ical(value).total_seconds())

  @staticmethod
  def _regexp(pattern, value):
    """Implements `value REGEXP pattern` (re.search)."""
    if pattern is None or value is None:
      return False
    return re.search(pattern, str(value)) is not None

  @staticmethod
  def _read_only_authorizer(action, *_):
    """Only allow SELECT (blocks DROP/INSERT/ATTACH/etc. inside the clause)."""
    allowed = (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION)
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

  @staticmethod
  def _write_bounds(event: icalendar.Event, start: datetime.datetime, end: datetime.datetime):
    """Write UTC start/end back as DTSTART/DTEND, keeping the original value type."""
    raw_start = event.decoded('DTSTART')

    def convert(value: datetime.datetime):
      if isinstance(raw_start, datetime.datetime):
        if raw_start.tzinfo is None:
          return value.replace(tzinfo=None)
        return value.astimezone(raw_start.tzinfo)
      return value.date()

    for key in ('DTSTART', 'DTEND', 'DURATION'):
      event.pop(key, None)
    event.add('DTSTART', convert(start))
    event.add('DTEND', convert(end))

  @staticmethod
  def _trim_overlaps(events: list[icalendar.Event], trim_start: bool):
    """Resolve overlaps between consecutive events (ordered by start, then end).

    trim_start=False: end of the earlier event is set to start of the later one.
    trim_start=True: start of the later event is set to end of the earlier one,
    its end is raised to at least the new start (zero length).
    """
    items: list[tuple[icalendar.Event, datetime.datetime, datetime.datetime]] = []
    for event in events:
      if CalendarPipeline._is_all_day(event):
        # all-day events legitimately overlap timed events
        continue

      start, end = CalendarPipeline._event_bounds(event)
      if start is not None:
        items.append([event, start, end])
    items.sort(key=lambda item: (item[1], item[2]))

    changed: set[int] = set()
    for index in range(1, len(items)):
      previous, current = items[index - 1], items[index]
      if previous[2] <= current[1]:
        continue
      if trim_start:
        current[1] = previous[2]
        current[2] = max(current[2], current[1])
        changed.add(index)
      else:
        previous[2] = current[1]
        changed.add(index - 1)

    for index in changed:
      event, start, end = items[index]
      CalendarPipeline._write_bounds(event, start, end)

  @staticmethod
  def _combine_all_day(calendar: icalendar.Calendar, events: list[icalendar.Component]):
    """Merge consecutive all-day events into multi-day event.

    Select consecutive all-day events with the same summary, merge them into one
    multi-day event. If they contain different descriptions, include them in the
    new event with per-day headings. All other properties are copied from the
    first event of a consecutive all-day event chain.
    """

    @dataclasses.dataclass
    class Event:
      start: datetime.datetime
      end: datetime.datetime
      event: icalendar.Event

    @dataclasses.dataclass
    class Chain:
      items: list[Event]
      end: datetime.datetime

    # find all all-day events with the same summary

    buckets: dict[str, list[Event]] = {}
    for event in events:
      if not CalendarPipeline._is_all_day(event):
        continue

      summary = event.get('SUMMARY')
      if summary is None:
        continue

      start, end = CalendarPipeline._event_bounds(event)
      buckets.setdefault(str(summary), []).append(Event(start, end, event))

    # merge consecutive all-day events into first event, mark others for removal

    removed = set()
    for bucket_events in buckets.values():
      bucket_events.sort(key=lambda item: (item.start, item.end))

      chains: list[Chain] = []
      for bucket_event in bucket_events:
        if chains and bucket_event.start <= chains[-1].end:
          # a previous event is already in the chain and overlaps or is contiguous with the current event
          # -> append to chain and update end
          chains[-1].items.append(bucket_event)
          chains[-1].end = max(chains[-1].end, bucket_event.end)
        else:
          chains.append(Chain(items=[bucket_event], end=bucket_event.end))

      for chain in chains:
        if len(chain.items) < 2:
          # single event: nothing to merge
          continue

        first = chain.items[0]

        # read descriptions from all events

        descriptions: list[tuple[int, str]] = []
        for chain_event in chain.items:
          text = str(chain_event.event.get('DESCRIPTION', '')).strip()
          if text:
            descriptions.append(((chain_event.start - first.start).days + 1, text))

        # create combined event description if different or take first description

        if descriptions:
          event_texts = {text for _, text in descriptions}
          if len(event_texts) == 1 and len(descriptions) == len(chain.items):
            combined = descriptions[0][1]  # all identical: keep as is
          else:
            combined = '\n\n'.join(f'Day {day}\n{text}' for day, text in descriptions)
          first.event.pop('DESCRIPTION', None)
          first.event.add('DESCRIPTION', combined)

        # update first event for new multi-day event length
        # mark other events of the chain for removal

        CalendarPipeline._write_bounds(first.event, first.start, chain.end)
        removed.update(id(item.event) for item in chain.items[1:])

    if removed:
      calendar.subcomponents[:] = [component for component in calendar.subcomponents if id(component) not in removed]


class CalendarFetchError(Exception):
  """Raised when a web calendar subscription cannot be fetched."""


async def fetch_url(url: str) -> bytes:
  """Downloads web calendar from url."""
  try:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
      response = await client.get(url)
      response.raise_for_status()
      content = response.content
      if len(content) > MAX_WEB_CALENDAR_FILE_SIZE:
        raise ValueError('file too large')
      return content

  except httpx.HTTPStatusError as exception:
    reason = f'HTTP {exception.response.status_code}'
  except httpx.UnsupportedProtocol:
    reason = 'missing http(s):// scheme'
  except httpx.ConnectError:
    reason = 'could not resolve/connect to host'
  except httpx.TimeoutException:
    reason = 'request timed out'
  except ValueError as exception:
    reason = str(exception)
  except httpx.HTTPError as exception:
    reason = exception.__class__.__name__

  truncated_url = url if len(url) <= MAX_ERROR_URL_LENGTH else url[:MAX_ERROR_URL_LENGTH] + '...'
  raise CalendarFetchError(f'Could not load calendar {truncated_url}: {reason}') from None


async def update_export_data(db: AsyncSession, export: CalendarExport, now: datetime.datetime | None = None):
  """Recompute an export's output"""
  now = now or datetime.datetime.now(datetime.timezone.utc)

  old_hash = CalendarPipeline(export.output_ics.encode('utf-8')).hash if export.output_ics else None

  merged_calendar = None
  sources = await database_requests.get_export_sources_ordered(db, export.id)
  for source in sources:
    # get ics data from source

    calendar_import = await database_requests.get_import_by_id(db, source.import_id)

    if calendar_import is None or calendar_import.active_source_id is None:
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

    # load and apply pipeline

    try:
      source_calendar = CalendarPipeline(raw_ics.encode('utf-8'))
      source_calendar.apply_filter(source.filter, deepcopy=False)
      source_calendar.apply_transform(source.transform, deepcopy=False)
    except Exception:
      logger.exception(f'Could not load, filter or transform calendar (import {source.import_id})')
      continue

    if merged_calendar is None:
      merged_calendar = source_calendar
    else:
      merged_calendar.merge_events(source_calendar, deepcopy=False)

  if merged_calendar is None:
    return

  # TODO apply calendar fields
  # merged_calendar.apply_calendar_properties(export.calendar_properties, deepcopy=False)

  # TODO remove events that are in deleted_uids, add delete as transform predicate (ignore all other ops)

  merged_calendar.sort_components()

  calendar_stats = merged_calendar.stats()
  export.output_ics = merged_calendar.calendar.to_ical().decode('utf-8')
  export.event_count = calendar_stats.event_count

  await db.flush()

  content_changed = old_hash is None or old_hash != merged_calendar.hash
  if content_changed:
    export.updated_at = now

  export.last_checked_at = now
