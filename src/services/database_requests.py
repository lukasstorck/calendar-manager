import datetime
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import src.config
from src.models import (
  Board,
  BoardCalendar,
  CalendarExport,
  CalendarExportSource,
  CalendarFile,
  CalendarImport,
  CalendarImportFileReference,
  User,
)

engine = create_async_engine(src.config.settings.database_url, echo=False, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
  async with async_session() as session:
    yield session


# -------------
# region: Users
# -------------


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
  """Fetch a user by id."""
  return await db.get(User, user_id)


# ---------------
# region: Imports
# ---------------


async def get_import_by_id(db: AsyncSession, import_id: uuid.UUID) -> CalendarImport | None:
  """Fetch a calendar import by primary key."""
  return await db.get(CalendarImport, import_id)


async def get_imports_for_user(db: AsyncSession, user_id: str) -> list[CalendarImport]:
  """List all imports owned by a user, oldest first."""
  query = select(CalendarImport) \
          .where(CalendarImport.user_id == user_id) \
          .order_by(CalendarImport.created_at)  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def get_import_names_for_user(db: AsyncSession, user_id: str) -> set[str]:
  """Set of all import names owned by a user."""
  query = select(CalendarImport.name) \
          .where(CalendarImport.user_id == user_id)  # fmt: skip

  result = await db.execute(query)
  return {row[0] for row in result.all()}


async def get_valid_import_ids_for_user(db: AsyncSession, user_id: str, import_ids: list[uuid.UUID]) -> set[uuid.UUID]:
  """Subset of the given import ids that are owned by the user."""
  query = select(CalendarImport.id) \
          .where(
            CalendarImport.user_id == user_id,
            CalendarImport.id.in_(import_ids)
          )  # fmt: skip

  result = await db.execute(query)
  return set(result.scalars().all())


async def get_web_imports(db: AsyncSession) -> list[CalendarImport]:
  """All calendar imports of all users that are based on a web calendar."""
  query = select(CalendarImport) \
          .where(CalendarImport.url.is_not(None))  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def create_calendar_import(
  db: AsyncSession,
  user_id: str,
  name: str,
  calendar_file: CalendarFile,
  now: datetime.datetime,
  url: str | None = None,
) -> CalendarImport:
  """Create an import with its first file. A url makes it a web calendar, otherwise it is a static file."""
  is_web_calendar = url is not None

  calendar_import = CalendarImport(
    user_id=user_id,
    name=name,
    url=url,
    active_file_id=calendar_file.id,
    last_fetched_at=now if is_web_calendar else None,
    last_success_at=now if is_web_calendar else None,
  )

  db.add(calendar_import)
  await db.flush()

  await add_file_reference(db, calendar_import.id, calendar_file.id, now)
  return calendar_import


# ----------------------
# region: Calendar files
# ----------------------


async def get_calendar_file_by_id(db: AsyncSession, file_id: uuid.UUID) -> CalendarFile | None:
  """Fetch a calendar file by id."""
  return await db.get(CalendarFile, file_id)


async def get_calendar_file_by_hash(db: AsyncSession, file_hash: str) -> CalendarFile | None:
  """Find a calendar file by its content hash."""
  query = select(CalendarFile) \
          .where(CalendarFile.hash == file_hash)  # fmt: skip

  result = await db.execute(query)
  return result.scalar_one_or_none()


async def get_calendar_file_stats(db: AsyncSession, file_id: uuid.UUID):
  """Calendar file stats."""
  query = select(CalendarFile.event_count, CalendarFile.range_start, CalendarFile.range_end) \
          .where(CalendarFile.id == file_id)  # fmt: skip

  result = await db.execute(query)
  return result.one()


async def get_active_calendar_file_for_import(db: AsyncSession, import_id: uuid.UUID) -> CalendarFile | None:
  """The referenced active calendar file of an import, so the most recent backup or the uploaded file."""
  query = select(CalendarFile) \
          .join(CalendarImport, CalendarImport.active_file_id == CalendarFile.id) \
          .where(CalendarImport.id == import_id)  # fmt: skip

  result = await db.execute(query)
  return result.scalar_one_or_none()


async def delete_unreferenced_calendar_files(db: AsyncSession):
  """Delete CalendarFile rows that no file reference and no import's active file points at.

  Returns the number of deleted rows. Must be committed.
  """
  query = delete(CalendarFile) \
          .where(
            CalendarFile.id.not_in(select(CalendarImportFileReference.file_id)),
            CalendarFile.id.not_in(select(CalendarImport.active_file_id))
          )  # fmt: skip

  result: CursorResult = await db.execute(query)
  return result.rowcount


# -------------------------------
# region: Import file references
# -------------------------------


async def get_file_reference_by_id(db: AsyncSession, file_reference_id: uuid.UUID) -> CalendarImportFileReference | None:
  """Fetch an import file reference by primary key."""
  return await db.get(CalendarImportFileReference, file_reference_id)


async def get_file_references_for_import(db: AsyncSession, import_id: uuid.UUID):
  """File reference information with calendar file stats for all file references of an import, newest first."""
  query = select(
            CalendarImportFileReference.id,
            CalendarImportFileReference.created_at,
            CalendarFile.event_count,
            CalendarFile.range_start,
            CalendarFile.range_end,
          ) \
          .select_from(CalendarImportFileReference) \
          .join(CalendarFile, CalendarFile.id == CalendarImportFileReference.file_id) \
          .where(CalendarImportFileReference.import_id == import_id) \
          .order_by(CalendarImportFileReference.created_at.desc())  # fmt: skip

  result = await db.execute(query)
  return list(result.all())


async def add_file_reference(db: AsyncSession, import_id: uuid.UUID, file_id: uuid.UUID, created_at: datetime.datetime):
  """Register a file as a new file reference of an import."""
  file_reference = CalendarImportFileReference(import_id=import_id, file_id=file_id, created_at=created_at)
  db.add(file_reference)
  await db.flush()


async def get_file_references_oldest_first(db: AsyncSession, import_id: uuid.UUID) -> list[CalendarImportFileReference]:
  """All FileReferences of a calendar import, oldest first."""
  query = select(CalendarImportFileReference) \
          .where(CalendarImportFileReference.import_id == import_id) \
          .order_by(CalendarImportFileReference.created_at)  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def delete_file_references_by_ids(db: AsyncSession, file_reference_ids: list[uuid.UUID]):
  """Remove ImportFileReferences with the given ids.

  Must be committed.
  """
  if not file_reference_ids:
    return 0

  query = delete(CalendarImportFileReference) \
          .where(CalendarImportFileReference.id.in_(file_reference_ids))  # fmt: skip

  result: CursorResult = await db.execute(query)
  return result.rowcount


# ---------------
# region: Exports
# ---------------


async def get_export_by_id(db: AsyncSession, export_id: uuid.UUID) -> CalendarExport | None:
  """Fetch a calendar export by primary key."""
  return await db.get(CalendarExport, export_id)


async def find_export_by_link_name(db: AsyncSession, link_name: str, excluded_export_id: uuid.UUID | None = None) -> CalendarExport | None:
  """Find an export by its globally unique public link name, optionally excluding one export id."""
  query = select(CalendarExport).where(CalendarExport.link_name == link_name)

  if excluded_export_id:
    query = query.where(CalendarExport.id != excluded_export_id)

  result = await db.execute(query)
  return result.scalar_one_or_none()


async def get_export_names_for_user(db: AsyncSession, user_id: str) -> set[str]:
  """Set of all export names owned by a user."""
  query = select(CalendarExport.name) \
          .where(CalendarExport.user_id == user_id)  # fmt: skip

  result = await db.execute(query)
  return set(result.scalars().all())


async def get_export_link_names(db: AsyncSession) -> set[str]:
  """Set of all export link names in use, across all users (calendar export link names are globally unique)."""
  query = select(CalendarExport.link_name)
  result = await db.execute(query)
  return set(result.scalars().all())


async def count_export_sources(db: AsyncSession, export_id: uuid.UUID) -> int:
  """Number of import sources feeding a given export."""
  query = select(func.count()) \
          .select_from(CalendarExportSource) \
          .where(CalendarExportSource.export_id == export_id)  # fmt: skip

  result = await db.execute(query)
  return result.scalar_one()


async def get_export_sources_ordered(db: AsyncSession, export_id: uuid.UUID) -> list[CalendarExportSource]:
  """List of export sources given an export id, ordered by position."""
  query = select(CalendarExportSource) \
          .where(CalendarExportSource.export_id == export_id) \
          .order_by(CalendarExportSource.position)  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def delete_export_sources_for_export(db: AsyncSession, export_id: uuid.UUID):
  """Remove all CalendarExportSource links for an export.

  Must be committed.
  """
  query = delete(CalendarExportSource) \
          .where(CalendarExportSource.export_id == export_id)  # fmt: skip

  await db.execute(query)


async def get_all_exports(db: AsyncSession) -> list[CalendarExport]:
  """All calendar exports across all users."""
  query = select(CalendarExport)
  result = await db.execute(query)
  return list(result.scalars().all())


async def get_exports_for_user(db: AsyncSession, user_id: str) -> list[CalendarExport]:
  """List all exports owned by a user, oldest first."""
  query = select(CalendarExport) \
          .where(CalendarExport.user_id == user_id) \
          .order_by(CalendarExport.created_at)  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def verify_export_ids_are_owned_by_user(db: AsyncSession, user: User, export_ids: list[uuid.UUID]) -> set[uuid.UUID]:
  """Subset of the given export ids that are owned by the user."""
  query = select(CalendarExport.id) \
          .where(CalendarExport.user_id == user.id, CalendarExport.id.in_(export_ids))  # fmt: skip

  result = await db.execute(query)
  return set(result.scalars())


# --------------
# region: Boards
# --------------


async def get_board_by_id(db: AsyncSession, board_id: uuid.UUID) -> Board | None:
  """Fetch a board by primary key."""
  return await db.get(Board, board_id)


async def find_user_owned_board_by_name(db: AsyncSession, name: str, user: User, excluded_board_id: uuid.UUID | None = None) -> Board | None:
  """Find a board by its user scope unique name.

  Optionally excluding one board id to check for link name collisions with another board.
  """

  query = select(Board) \
          .where(Board.name == name, Board.user_id == user.id)  # fmt: skip

  if excluded_board_id:
    query = query.where(Board.id != excluded_board_id)

  result = await db.execute(query)
  return result.scalar_one_or_none()


async def find_board_by_link_name(db: AsyncSession, link_name: str, excluded_board_id: uuid.UUID | None = None) -> Board | None:
  """Find a board by its globally unique public link name.

  Optionally excluding one board id to check for name collisions with another board.
  """

  query = select(Board) \
          .where(Board.link_name == link_name)  # fmt: skip

  if excluded_board_id:
    query = query.where(Board.id != excluded_board_id)

  result = await db.execute(query)
  return result.scalar_one_or_none()


async def get_boards_for_user(db: AsyncSession, user_id: str) -> list[Board]:
  """List all boards owned by a user, oldest first."""
  query = select(Board) \
          .where(Board.user_id == user_id) \
          .order_by(func.lower(Board.name))  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def get_board_names_for_user(db: AsyncSession, user_id: str) -> set[str]:
  """Set of all board display names owned by a user."""
  query = select(Board.name) \
          .where(Board.user_id == user_id)  # fmt: skip

  result = await db.execute(query)
  return set(result.scalars())


async def get_board_link_names(db: AsyncSession) -> set[str]:
  """Set of all board link names in use, across all users (calendar board link names are globally unique)."""
  query = select(Board.link_name)
  result = await db.execute(query)
  return set(result.scalars().all())


async def get_board_calendar_export_ids(db: AsyncSession, board_id: uuid.UUID) -> list[uuid.UUID]:
  """List of export ids attached to a board."""
  query = select(BoardCalendar.export_id) \
          .where(BoardCalendar.board_id == board_id)  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def clear_calendar_references_for_board(db: AsyncSession, board_id: uuid.UUID):
  """Remove all BoardCalendar links for a board.

  Must be committed.
  """
  query = delete(BoardCalendar) \
          .where(BoardCalendar.board_id == board_id)  # fmt: skip

  await db.execute(query)


async def get_board_calendar_exports(db: AsyncSession, board_id: uuid.UUID) -> list[CalendarExport]:
  """List exports attached to a board, ordered by export name."""
  query = select(CalendarExport) \
          .join(BoardCalendar, BoardCalendar.export_id == CalendarExport.id) \
          .where(BoardCalendar.board_id == board_id) \
          .order_by(CalendarExport.name)  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())
