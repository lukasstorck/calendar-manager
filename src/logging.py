import logging
import sys

import src.core.config

logger = logging.getLogger('calendar-manager')
logger.setLevel(src.core.config.settings.log_level)
logger.propagate = False


class Formatter(logging.Formatter):
  def format(self, record: logging.LogRecord) -> str:
    return f'{record.levelname + ":":<9} {record.getMessage()}'


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(Formatter())
logger.addHandler(handler)

logger.debug(f'Logging initialized with level {src.core.config.settings.log_level}')
