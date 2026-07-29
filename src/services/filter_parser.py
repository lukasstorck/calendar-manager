"""
Parser for the filter-rule mini language, e.g.:

  from:2024-01-01 to:today() title-contains:standup,sync
  location-contains:office description-contains:urgent
  calendar-name-contains:work -calendar-name-contains:personal

Grammar:
  rule       := predicate*
  predicate  := ["-"] name ":" value ("," value)*
  value      := special_time | free_text
  special    := today() | tomorrow() | yesterday() | thisweek() | lastweek() |
                nextweek() | thismonth() | lastmonth() | nextmonth() |
                thisyear() | lastyear() | nextyear()

All predicates are conjunctive (AND). Comma-separated values within one
predicate are disjunctive (OR). Leading "-" negates the predicate.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

SINGLE_ONLY = {'from', 'to'}
TEXT_PREDICATES = {
  'title-contains',
  'location-contains',
  'description-contains',
  'calendar-name-contains',
}
DATE_PREDICATES = {'from', 'to'}
ALL_PREDICATES = TEXT_PREDICATES | DATE_PREDICATES

SPECIAL_TIME_VALUES = {
  'today()',
  'tomorrow()',
  'yesterday()',
  'thisweek()',
  'lastweek()',
  'nextweek()',
  'thismonth()',
  'lastmonth()',
  'nextmonth()',
  'thisyear()',
  'lastyear()',
  'nextyear()',
}

SPECIAL_TIME_EXPLANATIONS = {
  'today()': 'Start of today',
  'tomorrow()': 'Start of tomorrow',
  'yesterday()': 'Start of yesterday',
  'thisweek()': 'Start of the current week (Monday)',
  'lastweek()': 'Start of the previous week',
  'nextweek()': 'Start of the next week',
  'thismonth()': 'Start of the current month',
  'lastmonth()': 'Start of the previous month',
  'nextmonth()': 'Start of the next month',
  'thisyear()': 'Start of the current year',
  'lastyear()': 'Start of the previous year',
  'nextyear()': 'Start of the next year',
}


@dataclass
class Predicate:
  name: str
  negated: bool
  values: list[str]
  raw: str


@dataclass
class ParseError:
  message: str
  position: int


@dataclass
class ParseResult:
  predicates: list[Predicate] = field(default_factory=list)
  errors: list[ParseError] = field(default_factory=list)

  @property
  def valid(self) -> bool:
    return not self.errors


def parse(rule_text: str) -> ParseResult:
  result = ParseResult()
  if not rule_text or not rule_text.strip():
    return result

  pos = 0
  text = rule_text.strip()
  seen_single: dict[str, int] = {}

  chunks = []
  buf = ''
  for ch in text:
    if ch.isspace():
      if buf:
        chunks.append((pos - len(buf), buf))
      buf = ''
    else:
      buf += ch
    pos += 1
  if buf:
    chunks.append((pos - len(buf), buf))

  for start, chunk in chunks:
    m = re.fullmatch(r'(?P<neg>-)?(?P<name>[a-zA-Z][a-zA-Z-]*):(?P<value>.*)', chunk)
    if not m:
      result.errors.append(ParseError(f"Cannot parse token '{chunk}'", start))
      continue
    name = m.group('name')
    negated = bool(m.group('neg'))
    raw_value = m.group('value')

    if name not in ALL_PREDICATES:
      result.errors.append(ParseError(f"Unknown predicate '{name}'", start))
      continue
    if not raw_value:
      result.errors.append(ParseError(f"Predicate '{name}' needs a value", start))
      continue

    values = raw_value.split(',')
    if any(v == '' for v in values):
      result.errors.append(ParseError(f"Empty value in list for '{name}'", start))
      continue

    if name in SINGLE_ONLY:
      if name in seen_single:
        result.errors.append(ParseError(f"Predicate '{name}' may only be used once", start))
        continue
      seen_single[name] = start
      if len(values) != 1:
        result.errors.append(ParseError(f"Predicate '{name}' does not accept multiple values", start))
        continue

    if name in DATE_PREDICATES:
      for value in values:
        if value.lower() in SPECIAL_TIME_VALUES:
          continue
        if not _is_valid_datetime(value):
          result.errors.append(ParseError(f"'{value}' is not a valid date/time or special value for '{name}'", start))

    result.predicates.append(Predicate(name=name, negated=negated, values=values, raw=chunk))

  return result


def _is_valid_datetime(value: str) -> bool:
  try:
    resolve_special_time(value)
    return True
  except ValueError:
    return False


def resolve_special_time(value: str, now: datetime | None = None) -> datetime:
  now = now or datetime.now(UTC)

  if now.tzinfo is None:
    now = now.replace(tzinfo=UTC)

  today = now.replace(hour=0, minute=0, second=0, microsecond=0)
  weekday = today.weekday()
  week_start = today - timedelta(days=weekday)

  month_start = today.replace(day=1)
  year_start = today.replace(month=1, day=1)

  v = value.lower()

  mapping = {
    'today()': today,
    'tomorrow()': today + timedelta(days=1),
    'yesterday()': today - timedelta(days=1),
    'thisweek()': week_start,
    'lastweek()': week_start - timedelta(weeks=1),
    'nextweek()': week_start + timedelta(weeks=1),
    'thismonth()': month_start,
    'lastmonth()': _add_months(month_start, -1),
    'nextmonth()': _add_months(month_start, 1),
    'thisyear()': year_start,
    'lastyear()': year_start.replace(year=year_start.year - 1),
    'nextyear()': year_start.replace(year=year_start.year + 1),
  }

  if v in mapping:
    return mapping[v]

  for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M'):
    try:
      return datetime.strptime(value, fmt).replace(tzinfo=now.tzinfo)
    except ValueError:
      continue

  raise ValueError(f"Cannot resolve time value '{value}'")


def _add_months(d: datetime, delta: int) -> datetime:
  month = d.month - 1 + delta
  year = d.year + month // 12
  month = month % 12 + 1
  return d.replace(year=year, month=month)


def matches(event: dict, predicates: list[Predicate], now: datetime | None = None) -> bool:
  for p in predicates:
    ok = _predicate_matches(event, p, now)
    if p.negated:
      ok = not ok
    if not ok:
      return False
  return True


def _predicate_matches(event: dict, p: Predicate, now: datetime | None) -> bool:
  if p.name == 'from':
    threshold = resolve_special_time(p.values[0], now)
    return event['end'] is None or event['end'] >= threshold
  if p.name == 'to':
    threshold = resolve_special_time(p.values[0], now)
    return event['start'] is None or event['start'] <= threshold
  if p.name == 'title-contains':
    return any(v.lower() in (event.get('title') or '').lower() for v in p.values)
  if p.name == 'location-contains':
    return any(v.lower() in (event.get('location') or '').lower() for v in p.values)
  if p.name == 'description-contains':
    return any(v.lower() in (event.get('description') or '').lower() for v in p.values)
  if p.name == 'calendar-name-contains':
    return any(v.lower() in (event.get('calendar_name') or '').lower() for v in p.values)
  return True
