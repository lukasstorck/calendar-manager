import re
import secrets

# NOTE: These values should not be changed via a config. Changes to these
# values are changes to the regexesfor validation and might break existing
# database entries and assumptions.
_MAX_NAME_LENGTH = 200
_MAX_LINK_LENGTH = 100

# ASCII control character: codes 0-31 and the DEL character (code 127)
_CONTROL_CHARS = ''.join(chr(code_point) for code_point in list(range(0x20)) + [0x7F])
_CONTROL_CHARS_WITHOUT_NEWLINE = _CONTROL_CHARS.replace('\n', '')
_LINK_CHAR_PATTERN = r'A-Za-z0-9\-_+@'

ILLEGAL_NAME_CHARACTERS = re.compile(rf'[{re.escape(_CONTROL_CHARS)}]')
ILLEGAL_DESCRIPTION_CHARACTERS = re.compile(rf'[{re.escape(_CONTROL_CHARS_WITHOUT_NEWLINE)}]')
ILLEGAL_LINK_CHARACTERS = re.compile(rf'[^{_LINK_CHAR_PATTERN}]')

REGEX_NAME_VALIDATION = re.compile(rf'^[^{re.escape(_CONTROL_CHARS)}]{{1,{_MAX_NAME_LENGTH}}}(?<! )$')
REGEX_DESCRIPTION_VALIDATION = re.compile(rf'^[^{re.escape(_CONTROL_CHARS_WITHOUT_NEWLINE)}]*$', re.DOTALL)
REGEX_LINK_VALIDATION = re.compile(rf'^[{_LINK_CHAR_PATTERN}]{{1,{_MAX_LINK_LENGTH}}}$')

NAME_ERROR = f'must be 1-{_MAX_NAME_LENGTH} normal characters'
DESCRIPTION_ERROR = 'must only contain normal characters and newlines'
LINK_ERROR = f"must be 1-{_MAX_LINK_LENGTH} alphanumeric characters or '-', '_', '+', '@'"


# TODO: maybe add strip: bool = True, check all references
def sanitize_text(text: str, disallowed_characters: re.Pattern, collapse_whitespaces: bool = False) -> str:
  """Strip disallowed characters from text"""
  sanitized = disallowed_characters.sub('', text or '')
  if collapse_whitespaces:
    sanitized = ' '.join(sanitized.split())
  return sanitized


# Used to validate a web calendar subscription URL before we try to fetch it.
# Accepts the two http(s) schemes plus 'webcal', a scheme some calendar apps
# use interchangeably with https for subscription links.
URL = re.compile(r'^(http|https|webcal)://', re.IGNORECASE)

URL_ERROR = 'must be a valid http(s):// or webcal:// address'

DEFAULT_BOARD_NAME = 'New Board'
DEFAULT_EXPORT_NAME = 'New Export'
DEFAULT_IMPORT_NAME = 'New Import'


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


def generate_affixed_name(name: str, prefix: str = '', suffix: str = '', separator: str = ' ', max_length: int | None = None) -> str:
  if max_length is not None:
    affix_length = 0

    if prefix:
      affix_length += len(separator) + len(prefix)

    if suffix:
      affix_length += len(separator) + len(suffix)

    remaining_length = max_length - affix_length
    if remaining_length <= 0:
      return ''

    if len(name) > remaining_length:
      name = name[:remaining_length]

  if prefix:
    prefix = f'{prefix}{separator}'

  if suffix:
    suffix = f'{separator}{suffix}'

  generated_name = f'{prefix}{name}{suffix}'
  return generated_name
