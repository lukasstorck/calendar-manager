import enum
import uuid
from datetime import datetime

from sqlalchemy import (
  Boolean,
  DateTime,
  Enum,
  ForeignKey,
  Integer,
  String,
  Text,
  UniqueConstraint,
  func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.db import Base


class CalendarSourceKind(enum.StrEnum):
  WEB_CALENDAR_URL = 'web_calendar_url'
  STATIC_FILE = 'static_file'


class User(Base):
  """The user identity based on the OAuth provider.

  Identity comes entirely from the OAuth provider. The primary key is
  "<provider>:<external id>".
  User rows are created lazily the first time an authenticated request
  needs it.
  """

  __tablename__ = 'users'

  id: Mapped[str] = mapped_column(String(300), primary_key=True)
  provider: Mapped[str] = mapped_column(String(50))
  external_id: Mapped[str] = mapped_column(String(200))
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

  imports: Mapped[list['CalendarImport']] = relationship(back_populates='user', cascade='all, delete-orphan')
  exports: Mapped[list['CalendarExport']] = relationship(back_populates='user', cascade='all, delete-orphan')
  boards: Mapped[list['Board']] = relationship(back_populates='user', cascade='all, delete-orphan')


class StaticFile(Base):
  """Backend-managed shared static file, deduped by content hash"""

  __tablename__ = 'static_files'
  __table_args__ = (UniqueConstraint('file_hash', name='uq_static_file_hash'),)

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  file_hash: Mapped[str] = mapped_column(String(64))
  raw_ics: Mapped[str] = mapped_column(Text)
  event_count: Mapped[int] = mapped_column(Integer, default=0)
  range_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebSubscription(Base):
  """Backend-managed shared web calendar subscription identity, deduped by URL"""

  __tablename__ = 'web_subscriptions'
  __table_args__ = (UniqueConstraint('url', name='uq_web_url'),)

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  url: Mapped[str] = mapped_column(Text)
  # note: if last_pulled_at === last_success_at, then pull was successful
  last_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

  snapshots: Mapped[list['Snapshot']] = relationship(back_populates='web_subscription', cascade='all, delete-orphan')


class Snapshot(Base):
  """Data entry for one time file download of a web calendar subscription.

  The raw ICS is deduped by content hash.
  """

  __tablename__ = 'snapshots'
  __table_args__ = (UniqueConstraint('web_subscription_id', 'file_hash', name='uq_snapshot_web_subscription_id_file_hash'),)

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  web_subscription_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('web_subscriptions.id', ondelete='CASCADE'))
  file_hash: Mapped[str] = mapped_column(String(64))
  raw_ics: Mapped[str] = mapped_column(Text)
  event_count: Mapped[int] = mapped_column(Integer, default=0)
  range_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

  web_subscription: Mapped['WebSubscription'] = relationship(back_populates='snapshots')


class CalendarImportSourceKind(enum.StrEnum):
  STATIC = 'static'
  WEB = 'web'


class CalendarImportSource(Base):
  """A wrapper for a static file or web calendar subscription import source.

  An import source can either be a static file or a web calendar subscription
  based on an URL. Those instances are managed by the backend and can be shared
  across multiple imports. This wrapper forwards to either one of those two
  tables and proivdes import specific metadata (label, backup contribution).
  """

  __tablename__ = 'calendar_import_sources'

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('calendar_imports.id', ondelete='CASCADE'))

  kind: Mapped[CalendarImportSourceKind] = mapped_column(Enum(CalendarImportSourceKind))
  static_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('static_files.id', ondelete='RESTRICT'), nullable=True)
  web_subscription_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('web_subscriptions.id', ondelete='RESTRICT'), nullable=True)

  contribute_backup: Mapped[bool] = mapped_column(Boolean, default=False)  # only meaningful when kind == WEB
  label: Mapped[str | None] = mapped_column(String(200), nullable=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())  # upload time or first-subscribe time

  calendar_import: Mapped['CalendarImport'] = relationship(back_populates='sources', foreign_keys='CalendarImportSource.import_id')

  # CHECK constraint: kind='static' -> static_file_id set, web_subscription_id null
  #                   kind='web'    -> web_subscription_id set, static_file_id null


class CalendarImport(Base):
  """A user's named reference to a collection of CalendarImportSources

  A user can have multiple import sources attached to the same import with one of the
  sources selected as the active source.
  """

  __tablename__ = 'calendar_imports'
  __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_import_user_name'),)

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  user_id: Mapped[str] = mapped_column(String(300), ForeignKey('users.id', ondelete='CASCADE'))
  name: Mapped[str] = mapped_column(String(200))

  active_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('calendar_import_sources.id', ondelete='SET NULL'), nullable=True)

  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

  user: Mapped['User'] = relationship(back_populates='imports')
  sources: Mapped[list['CalendarImportSource']] = relationship(
    back_populates='calendar_import', foreign_keys='CalendarImportSource.import_id', cascade='all, delete-orphan'
  )
  active_source: Mapped['CalendarImportSource | None'] = relationship(foreign_keys=[active_source_id])


class CalendarExport(Base):
  """A generated calendar output from a set of import sources and an applied filter rule.

  An export can be published via the unique public name with or without token protection.
  Stats and the generated output are cached and saved via dedup_key, a hash of the sorted
  source ids and the normalized filter rule. It is used to generate calendars with the
  same inputs only once across all exports and users.
  """

  __tablename__ = 'calendar_exports'
  __table_args__ = (
    UniqueConstraint('user_id', 'name', name='uq_export_user_name'),
    UniqueConstraint('link_name', name='uq_export_link_name'),
  )

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  user_id: Mapped[str] = mapped_column(String(300), ForeignKey('users.id', ondelete='CASCADE'))
  name: Mapped[str] = mapped_column(String(200))
  description: Mapped[str | None] = mapped_column(Text, nullable=True)
  link_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
  rule_text: Mapped[str] = mapped_column(Text, default='')
  show_rule_in_description: Mapped[bool] = mapped_column(Boolean, default=True)

  published: Mapped[bool] = mapped_column(Boolean, default=True)
  protected: Mapped[bool] = mapped_column(Boolean, default=True)
  token: Mapped[str | None] = mapped_column(String(64), nullable=True)

  dedup_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

  last_output_count: Mapped[int] = mapped_column(Integer, default=0)
  last_output_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

  user: Mapped['User'] = relationship(back_populates='exports')
  sources: Mapped[list['CalendarExportSource']] = relationship(back_populates='calendar_export', cascade='all, delete-orphan')
  board_links: Mapped[list['BoardCalendar']] = relationship(back_populates='calendar_export', cascade='all, delete-orphan')


class CalendarExportSource(Base):
  """Which calendar imports feed a given export"""

  __tablename__ = 'calendar_export_sources'
  __table_args__ = (UniqueConstraint('export_id', 'import_id', name='uq_export_import'),)

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  export_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('calendar_exports.id', ondelete='CASCADE'))
  import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('calendar_imports.id', ondelete='CASCADE'))

  calendar_export: Mapped['CalendarExport'] = relationship(back_populates='sources')
  calendar_import: Mapped['CalendarImport'] = relationship()


class CalendarExportCache(Base):
  """Cached information about a generated calendar export.

  The dedup_key is a hash of the calendar export source ids and the normalized filter rule.
  Multiple CalendarExport rows with the same dedup_key share this result.
  """

  __tablename__ = 'calendar_export_cache'

  dedup_key: Mapped[str] = mapped_column(String(64), primary_key=True)
  output_ics: Mapped[str] = mapped_column(Text, default='')
  event_count: Mapped[int] = mapped_column(Integer, default=0)
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Board(Base):
  """A public page listing a collection of exports.

  The public name is globally unique since it's used in the public link.
  A token is always generated at creation time, even if the board starts
  unprotected, so flipping protection back on later doesn't require a
  fresh token unless the owner explicitly rerolls one.
  """

  __tablename__ = 'boards'
  __table_args__ = (
    UniqueConstraint('link_name', name='uq_board_link_name'),
    UniqueConstraint('user_id', 'name', name='uq_board_user_name'),
  )

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  user_id: Mapped[str] = mapped_column(String(300), ForeignKey('users.id', ondelete='CASCADE'))
  name: Mapped[str] = mapped_column(String(100))
  description: Mapped[str | None] = mapped_column(Text, nullable=True)
  link_name: Mapped[str | None] = mapped_column(String(100))

  published: Mapped[bool] = mapped_column(Boolean, default=True)
  protected: Mapped[bool] = mapped_column(Boolean, default=True)
  token: Mapped[str] = mapped_column(String(64))

  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

  user: Mapped['User'] = relationship(back_populates='boards')
  calendars: Mapped[list['BoardCalendar']] = relationship(back_populates='board', cascade='all, delete-orphan')


class BoardCalendar(Base):
  """Which exports are published on a given board"""

  __tablename__ = 'board_calendars'
  __table_args__ = (UniqueConstraint('board_id', 'export_id', name='uq_board_export'),)

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  board_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('boards.id', ondelete='CASCADE'))
  export_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('calendar_exports.id', ondelete='CASCADE'))
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

  board: Mapped['Board'] = relationship(back_populates='calendars')
  calendar_export: Mapped['CalendarExport'] = relationship(back_populates='board_links')
