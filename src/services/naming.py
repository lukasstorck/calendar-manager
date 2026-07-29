import re
import secrets
from urllib.parse import unquote, urlparse

# ASCII control character: codes 0-31 and the DEL character (code 127)
_CONTROL_CHARS = ''.join(chr(code_point) for code_point in list(range(0x20)) + [0x7F])
_CONTROL_CHARS_WITHOUT_NEWLINE = _CONTROL_CHARS.replace('\n', '')
_LINK_CHAR_PATTERN = r'A-Za-z0-9\-_+@'

ILLEGAL_NAME_CHARACTERS = re.compile(rf'[{re.escape(_CONTROL_CHARS)}]')
ILLEGAL_DESCRIPTION_CHARACTERS = re.compile(rf'[{re.escape(_CONTROL_CHARS_WITHOUT_NEWLINE)}]')
ILLEGAL_LINK_CHARACTERS = re.compile(rf'[^{_LINK_CHAR_PATTERN}]')

REGEX_NAME_VALIDATION = re.compile(rf'^[^{re.escape(_CONTROL_CHARS)}]{{1,200}}(?<! )$')
REGEX_DESCRIPTION_VALIDATION = re.compile(rf'^[^{re.escape(_CONTROL_CHARS_WITHOUT_NEWLINE)}]*$', re.DOTALL)
REGEX_LINK_VALIDATION = re.compile(rf'^[{_LINK_CHAR_PATTERN}]{{1,100}}$')

NAME_ERROR = 'must be 1-200 normal characters'
DESCRIPTION_ERROR = 'must only contain normal characters and newlines'
LINK_ERROR = "must be 1-100 alphanumeric characters or '-', '_', '+', '@'"


def sanitize_text(text: str, disallowed_characters: re.Pattern, collapse_whitespaces: bool = False) -> str:
  """Strip disallowed characters from text"""
  sanitized = disallowed_characters.sub('', text or '')
  if collapse_whitespaces:
    sanitized = ' '.join(sanitized.split())
  return sanitized


# Used for anything that becomes part of a public URL path (board/export public
# links). Deliberately restrictive: URL-safe characters only.
REGEX_LINK_NAME = re.compile(r'^[a-zA-Z0-9.\-]{1,100}$')

# Used for user-facing display names (import name, source label, board/export
# name). More permissive than LINK_NAME -- these are never placed directly into
# a URL path, so spaces and most punctuation are fine.
REGEX_DISPLAY_NAME = re.compile(rf'^[^{re.escape(_CONTROL_CHARS)}]{{1,200}}$')

# Used to validate a web calendar subscription URL before we try to fetch it.
# Accepts the two http(s) schemes plus 'webcal', a scheme some calendar apps
# use interchangeably with https for subscription links.
URL = re.compile(r'^(http|https|webcal)://', re.IGNORECASE)

LINK_NAME_ERROR = "must be 1-100 chars of a-z, A-Z, 0-9, '.' and '-'"
DISPLAY_NAME_ERROR = 'must be 1-200 characters, and may not contain control characters'
URL_ERROR = 'must be a valid http(s):// or webcal:// address'

DEFAULT_BOARD_NAME = 'New Board'
DEFAULT_EXPORT_NAME = 'New Export'
DEFAULT_IMPORT_NAME = 'New Import'
DEFAULT_SOURCE_LABEL = 'New Source'


# '_' and '-' in a URL path segment are almost always word separators someone
# used in place of a space (e.g. 'team_calendar.ics'), so both become an
# actual space before guessing a display name from them.
_WORD_SEPARATOR_TRANSLATION = str.maketrans('_-', '  ')


def generate_link_token() -> str:
  """Generate a random token for link protection"""
  return secrets.token_urlsafe(24)


def next_available_name(desired: str, existing_names: set[str]) -> str:
  """Get next available name based on `desired`

  If 'desired' is already taken, append (1), (2), ... .
  If desired already ends with a (n) suffix and is taken, keep incrementing from there.

  Args:
      desired (str): Desired name
      existing_names (set[str]): Existing names

  Returns:
      str: Next available name
  """
  if desired not in existing_names:
    return desired

  base = desired
  match = re.match(r'^(.*) \((\d+)\)$', desired)
  if match:
    base = match.group(1)

  n = 1
  while True:
    candidate = f'{base} ({n})'
    if candidate not in existing_names:
      return candidate
    n += 1


def sanitize_display_text(text: str) -> str:
  """Strip control characters and collapse surrounding whitespace from
  user-provided display text (names, labels). Does not enforce length or
  charset beyond that -- pair with DISPLAY_NAME for full validation."""
  cleaned = ILLEGAL_NAME_CHARACTERS.sub('', text or '')
  return ' '.join(cleaned.split()).strip()


def strip_file_extension(filename: str) -> str:
  """'birthdays.ics' -> 'birthdays'. Leaves names without a recognizable
  extension untouched."""
  if '.' in filename:
    base, _, ext = filename.rpartition('.')
    if base and 1 <= len(ext) <= 6:
      return base
  return filename


def normalize_url(url: str) -> str:
  """Silently add a scheme if the user typed a bare host/path with none
  (e.g. 'example.com/cal.ics' -> 'https://example.com/cal.ics'). If a scheme
  is already present -- including a non-http one -- it's left untouched, so
  URL still catches an actually-wrong scheme instead of masking it."""
  if '://' not in url:
    return f'https://{url}'
  return url


def guess_name_from_url(url: str) -> str:
  """Best-effort sensible display name for a web calendar URL: prefer the last
  path segment if it looks like a filename (e.g. 'team.ics' -> 'team'),
  otherwise fall back to the domain with any 'www.' prefix stripped.

  Used both as a placeholder hint client-side and as the server-side fallback
  when no label/name was supplied at all.
  """
  try:
    parsed = urlparse(url)
  except ValueError:
    return ''

  # A trailing slash means there's no actual filename segment to use --
  # 'example.com/cal/' should fall through to the domain, not to '' or 'cal'.
  path_segment = '' if parsed.path.endswith('/') else parsed.path.rsplit('/', 1)[-1]
  if path_segment:
    guess = unquote(path_segment)
    guess = strip_file_extension(guess)
    guess = guess.translate(_WORD_SEPARATOR_TRANSLATION)
    guess = sanitize_display_text(guess)
    if guess:
      return guess

  host = parsed.netloc.split('@')[-1].split(':')[0]
  host = host.removeprefix('www.')
  return sanitize_display_text(host)
