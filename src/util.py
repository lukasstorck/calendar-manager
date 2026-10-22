import datetime
import hashlib
import itertools
import logging
import operator
import re
import typing

import icalendar

MINIMUM_DATETIME = datetime.datetime.min.replace(tzinfo=datetime.UTC)

# calendar byte string normalization regex patterns for calendar hash

REGEX_NEWLINE_WITH_INDENTATION = re.compile(rb'\n[ \t]')
REGEX_DTSTAMP_LINE = re.compile(rb'^DTSTAMP[;:][^\n]*\n?', re.MULTILINE)


def normalized_calendar_hash(calendar_bytes: bytes) -> str:
  """sha256 of the normalized serialization of `calendar_bytes`

  The hash ignores
  - CRLF vs. LF line endings
  - unfolded lines
  - DTSTAMP lines
  """

  calendar_bytes = calendar_bytes.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
  calendar_bytes = REGEX_NEWLINE_WITH_INDENTATION.sub(b'', calendar_bytes)
  calendar_bytes = REGEX_DTSTAMP_LINE.sub(b'', calendar_bytes)

  return hashlib.sha256(calendar_bytes).hexdigest()


def normalized_calendar_component_hash(component: icalendar.Component) -> str:
  """sha256 of the normalized serialization of `component`

  The hash ignores
  - CRLF vs. LF line endings
  - unfolded lines
  - DTSTAMP lines
  """

  component_bytes = component.to_ical()
  component_bytes = component_bytes.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
  component_bytes = REGEX_NEWLINE_WITH_INDENTATION.sub(b'', component_bytes)
  component_bytes = REGEX_DTSTAMP_LINE.sub(b'', component_bytes)

  return hashlib.blake2b(component_bytes, digest_size=32).hexdigest()


ITEM = typing.TypeVar('ITEM')


def lazy_multi_sort(
  items: typing.Iterable[ITEM],
  sort_key_builders: tuple[typing.Callable[[ITEM], typing.Any]],
  reverse: bool | typing.Sequence[bool] = False,
  logger: logging.Logger | None = None,
) -> list[ITEM]:
  """Stable multi-level sort; level k+1 keys are computed only for ties on level k.

  Each key is computed at most once per element. Keys must be orderable. `reverse`
  is one bool or one bool per level. If `logger` is given, a warning is logged for
  each group still tied after the last level.
  """
  n_levels = len(sort_key_builders)

  if isinstance(reverse, bool):
    reverse_flags = (reverse,) * n_levels
  else:
    reverse_flags = tuple(reverse)
    if len(reverse_flags) != n_levels:
      raise ValueError(f'lazy_multi_sort reverse parameter has {len(reverse_flags)} entries but there are {n_levels} key builders')

  def sort_group(group: list[ITEM], level: int) -> list[ITEM]:
    if len(group) <= 1:
      return group

    if level == n_levels:
      # more than one element in group after the last level -> unbreakable tie
      if logger is not None:
        sample = ', '.join(repr(x) for x in group[:3])
        more = ', ...' if len(group) > 3 else ''
        logger.warning(f'lazy_multi_sort: {len(group)} elements tied on all {n_levels} levels (original order kept): {sample}{more}')

      return group

    items_with_sort_key = [(sort_key_builders[level](x), x) for x in group]
    items_with_sort_key.sort(key=operator.itemgetter(0), reverse=reverse_flags[level])

    result: list[ITEM] = []
    for _, tied in itertools.groupby(items_with_sort_key, key=operator.itemgetter(0)):
      result.extend(sort_group([x for _, x in tied], level + 1))
    return result

  return sort_group(list(items), 0)
