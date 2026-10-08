import uuid
from datetime import datetime

from sqlalchemy import (
  Boolean,
  DateTime,
  ForeignKey,
  Integer,
  String,
  Text,
  UniqueConstraint,
  func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
  pass


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


class CalendarFile(Base):
  """Immutable ICS content, stored exactly once per content hash."""

  __tablename__ = 'calendar_files'
  __table_args__ = (UniqueConstraint('hash', name='uq_calendar_file_hash'),)

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  hash: Mapped[str] = mapped_column(String(64))
  data: Mapped[str] = mapped_column(Text)
  event_count: Mapped[int] = mapped_column(Integer, default=0)
  range_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CalendarImportFileReference(Base):
  """Calendar import reference for connecting an import to their files.

  One calendar import may have multiple referenced files and one file may be referenced
  by multiple imports (one to many to one). Each reference has a creation time attached,
  which is the time of upload for a static file upload or the fetch time for a web calendar.
  """

  __tablename__ = 'calendar_import_file_references'
  __table_args__ = (UniqueConstraint('import_id', 'created_at', name='uq_import_file_reference_import_id_created_at'),)

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('calendar_imports.id', ondelete='CASCADE'))
  file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('calendar_files.id', ondelete='RESTRICT'), index=True)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

  calendar_import: Mapped['CalendarImport'] = relationship(back_populates='file_references')
  file: Mapped['CalendarFile'] = relationship()


class CalendarImport(Base):
  """A user's named data import, either from a static file or a web calendar subscription.

  An import is always owned by a user. For an uploaded file, the URL is None and only a
  single file is referenced. For a web calendar, the URL is set and multiple files may be
  referenced if `backup` is enabled.
  """

  __tablename__ = 'calendar_imports'
  __table_args__ = (UniqueConstraint('user_id', 'name', name='uq_import_user_name'),)

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  user_id: Mapped[str] = mapped_column(String(300), ForeignKey('users.id', ondelete='CASCADE'))
  name: Mapped[str] = mapped_column(String(200))
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  active_file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('calendar_files.id', ondelete='RESTRICT'), index=True)

  # web calendars only (url is None -> uploaded file)
  url: Mapped[str | None] = mapped_column(Text, nullable=True)
  backup: Mapped[bool] = mapped_column(Boolean, default=False)
  # NOTE: last_fetched_at describes the last time that a fetch was attempted,
  #       last_success_at is the last time that a fetch was successful
  #       if last_fetched_at <= last_success_at, then the last fetch was successful
  #       if last_fetched_at > last_success_at, then the last fetch failed and
  #         an error message is expected to be stored in last_error
  last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
  last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

  user: Mapped['User'] = relationship(back_populates='imports')
  file_references: Mapped[list['CalendarImportFileReference']] = relationship(
    back_populates='calendar_import',
    cascade='all, delete-orphan',
    passive_deletes=True,
    order_by='CalendarImportFileReference.created_at.desc()',
  )


class CalendarExport(Base):
  """A generated calendar output from a set of import sources and an applied filter rule.

  An export can be published via the unique public name with or without token protection.
  Stats and the generated output are cached.
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
  link_name: Mapped[str] = mapped_column(String(100))

  published: Mapped[bool] = mapped_column(Boolean, default=True)
  protected: Mapped[bool] = mapped_column(Boolean, default=True)
  token: Mapped[str] = mapped_column(String(64))

  output_ics: Mapped[str] = mapped_column(Text, default='')
  event_count: Mapped[int] = mapped_column(Integer, default=0)
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

  user: Mapped['User'] = relationship(back_populates='exports')
  sources: Mapped[list['CalendarExportSource']] = relationship(back_populates='calendar_export', cascade='all, delete-orphan')
  board_links: Mapped[list['BoardCalendar']] = relationship(back_populates='calendar_export', cascade='all, delete-orphan')


class CalendarExportSource(Base):
  """Which calendar imports feed a given export and what filters and transforms are applied.

  An export may have multiple sources, each with different filters and transforms.
  Position (based on the UI) determines the priority for resulting events of a filtered source.
  In case of duplicate UIDs, the source with the higher position wins and overwrites the others.
  """

  __tablename__ = 'calendar_export_sources'

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  export_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('calendar_exports.id', ondelete='CASCADE'))
  import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('calendar_imports.id', ondelete='CASCADE'))
  position: Mapped[int] = mapped_column(Integer, default=0)

  filter: Mapped[str] = mapped_column(Text, default='')
  transform: Mapped[str] = mapped_column(Text, default='')

  calendar_export: Mapped['CalendarExport'] = relationship(back_populates='sources')
  calendar_import: Mapped['CalendarImport'] = relationship()


class Board(Base):
  """A public page listing a collection of exports.

  The public name is globally unique since it's used in the public link.
  A token is always generated at creation time, even if the board starts
  unprotected, so flipping protection back on later doesn't require a
  fresh token unless the owner explicitly rerolls one.
  """

  __tablename__ = 'boards'
  __table_args__ = (
    UniqueConstraint('user_id', 'name', name='uq_board_user_name'),
    UniqueConstraint('link_name', name='uq_board_link_name'),
  )

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  user_id: Mapped[str] = mapped_column(String(300), ForeignKey('users.id', ondelete='CASCADE'))
  name: Mapped[str] = mapped_column(String(100))
  description: Mapped[str | None] = mapped_column(Text, nullable=True)
  link_name: Mapped[str] = mapped_column(String(100))

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
