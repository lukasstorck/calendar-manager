import logging
import sys

import src.core.config

if src.core.config.LOG_LEVEL not in logging.getLevelNamesMapping():
  raise ValueError(f'Invalid LOG_LEVEL: {src.core.config.LOG_LEVEL}')

logger = logging.getLogger('calendar-manager')
logger.setLevel(src.core.config.LOG_LEVEL)
logger.propagate = False


class Formatter(logging.Formatter):
  def format(self, record: logging.LogRecord) -> str:
    return f'{record.levelname + ":":<9} {record.getMessage()}'


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(Formatter())
logger.addHandler(handler)

logger.debug(f'Logging initialized with level {src.core.config.LOG_LEVEL}')
