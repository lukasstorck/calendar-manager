import logging

import pydantic
import pydantic_settings


class Settings(pydantic_settings.BaseSettings):
  log_level: str = 'WARNING'
  database_url: str
  refresh_interval_seconds: int
  max_web_calendar_file_size: int = 20 * 1024 * 1024  # 20 MB
  max_error_url_length: int = 60

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

  @pydantic.field_validator('log_level')
  @classmethod
  def validate_log_level(cls, value: str) -> str:
    value = value.upper()
    if value not in logging.getLevelNamesMapping():
      raise ValueError(f'Invalid LOG_LEVEL: {value}')
    return value


settings = Settings()
