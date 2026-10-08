import collections
import datetime
import itertools
import logging
import typing

import pydantic
import pydantic_settings

# TODO might consolidate config and logging
logger = logging.getLogger(__name__)


class RetentionTier(typing.NamedTuple):
  max_age: datetime.timedelta | None
  interval: datetime.timedelta


class Settings(pydantic_settings.BaseSettings):
  log_level: str = 'WARNING'
  database_url: str

  refresh_interval: datetime.timedelta = datetime.timedelta(minutes=5)
  cleanup_time: datetime.time = datetime.time(hour=3)  # 03:00 UTC

  # backoff duration applied for increasing consecutive failures, 0 means no backoff
  fetch_backoff_durations: list[datetime.timedelta] = pydantic.Field(
    default=[
      datetime.timedelta(0),
      datetime.timedelta(0),
      datetime.timedelta(hours=1),
      datetime.timedelta(hours=6),
      datetime.timedelta(hours=12),
      datetime.timedelta(hours=24),
      datetime.timedelta(hours=36),
    ],
    min_length=1,
  )

  # backup intervals rules, each rule has to be obeyed:
  # for all backups between `now - max_age` and `now`:
  #   if max_age is None: max_age = now - oldest_backup.created_at
  #   slots = max_age // interval + 1
  #   for i in range(slots):
  #     cut_off = now - i * interval - interval * tolerance_ratio
  #     we want the backup that is created newer than the cut-off
  #     get oldest backup with created_at >= cut_off
  #     if any: mark for preservation
  # after all rules are checked: remove all backups that are not marked
  # for preservation
  # the order of the rules does not matter, they are sorted when validated
  retention_tiers: list[RetentionTier] = [
    RetentionTier(max_age=datetime.timedelta(days=3), interval=datetime.timedelta(days=1)),
    RetentionTier(max_age=datetime.timedelta(days=14), interval=datetime.timedelta(weeks=1)),
    RetentionTier(max_age=datetime.timedelta(days=365), interval=datetime.timedelta(days=30)),
    RetentionTier(max_age=None, interval=datetime.timedelta(days=365)),
  ]

  # relative tolerance for retention tier intervals
  relative_retention_interval_tolerance: float = pydantic.Field(default=0.1, ge=0, lt=1)

  # TODO: rename to max_calendar_import_file_size
  # max file size for web calendar imports
  max_web_calendar_file_size: int = 20 * 1024 * 1024  # 20 MB
  max_error_url_length: int = 60

  # ----------------------
  # region: authentication
  # ----------------------

  base_url: str
  session_secret: str

  skip_authentication: bool = False

  github_client_id: str | None = None
  github_client_secret: str | None = None
  google_client_id: str | None = None
  google_client_secret: str | None = None
  pocketid_client_id: str | None = None
  pocketid_client_secret: str | None = None
  pocketid_server_metadata_url: str | None = None

  # ------------------
  # region: validators
  # ------------------

  @pydantic.field_validator('retention_tiers')
  @classmethod
  def validate_retention_tiers(cls, tiers: list[RetentionTier]) -> list[RetentionTier]:
    unique_tiers = list(dict.fromkeys(tiers))
    if len(unique_tiers) < len(tiers):
      logger.warning(f'Removed {len(tiers) - len(unique_tiers)} duplicate retention tier(s)')

    sorted_tiers = sorted(unique_tiers, key=lambda tier: (tier.max_age is None, tier.max_age or datetime.timedelta(0), tier.interval))

    for max_age, count in collections.Counter(tier.max_age for tier in sorted_tiers).items():
      if count > 1:
        logger.warning(f'{count} retention tiers share the max age {"unbounded (None)" if max_age is None else max_age}')

    # a retention tier with greater max age, covers more time and backups, so
    # if this has a lower interval, it makes the lower tier redundant
    if any(older.interval <= younger.interval for younger, older in itertools.pairwise(sorted_tiers)):
      logger.warning('Retention tier intervals do not increase with the max age, some tiers are redundant')

    return sorted_tiers

  @pydantic.field_validator('log_level')
  @classmethod
  def validate_log_level(cls, value: str) -> str:
    value = value.upper()
    if value not in logging.getLevelNamesMapping():
      raise ValueError(f'Invalid LOG_LEVEL: {value}')
    return value


settings = Settings()
