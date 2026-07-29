import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import (
  Board,
  BoardCalendar,
  CalendarExport,
  CalendarExportCache,
  CalendarExportSource,
  CalendarImport,
  CalendarImportSource,
  CalendarImportSourceKind,
  Snapshot,
  StaticFile,
  User,
  WebSubscription,
)

# -------------
# region: Users
# -------------


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
  """Fetch a user by primary key."""
  return await db.get(User, user_id)


# ---------------
# region: Imports
# ---------------


async def get_import_by_id(db: AsyncSession, import_id: uuid.UUID) -> CalendarImport | None:
  """Fetch a calendar import by primary key."""
  return await db.get(CalendarImport, import_id)


async def get_import_by_id_with_sources(db: AsyncSession, import_id: uuid.UUID) -> CalendarImport | None:
  """Fetch a calendar import by primary key, with its sources eager-loaded."""
  query = select(CalendarImport) \
          .where(CalendarImport.id == import_id) \
          .options(selectinload(CalendarImport.sources))  # fmt: skip

  result = await db.execute(query)
  return result.scalar_one_or_none()


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


# ----------------------
# region: Import sources
# ----------------------


async def get_import_source_by_id(db: AsyncSession, import_source_id: uuid.UUID) -> CalendarImportSource | None:
  """Fetch a calendar import source by primary key."""
  return await db.get(CalendarImportSource, import_source_id)


async def get_import_sources_for_import(db: AsyncSession, import_id: uuid.UUID) -> list[CalendarImportSource]:
  """List all sources attached to a given import."""
  query = select(CalendarImportSource) \
          .where(CalendarImportSource.import_id == import_id)  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def get_fallback_import_source(db: AsyncSession, import_id: uuid.UUID, excluded_source_id: uuid.UUID) -> CalendarImportSource | None:
  """Most recently created source on an import, excluding one specific source id."""
  query = select(CalendarImportSource) \
          .where(
            CalendarImportSource.import_id == import_id,
            CalendarImportSource.id != excluded_source_id
          ) \
          .order_by(CalendarImportSource.created_at.desc()) \
          .limit(1)  # fmt: skip

  result = await db.execute(query)
  return result.scalar_one_or_none()


# --------------------
# region: Static Files
# --------------------


async def get_unreferenced_static_file_ids(db: AsyncSession) -> list[uuid.UUID]:
  """Ids of StaticFile rows no CalendarImportSource references anymore."""
  query = select(StaticFile.id) \
          .where(StaticFile.id.not_in(
            select(CalendarImportSource.static_file_id)
            .where(CalendarImportSource.static_file_id.is_not(None))
          ))  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def get_static_file_by_id(db: AsyncSession, static_file_id: uuid.UUID) -> StaticFile | None:
  """Fetch a static file by primary key."""
  return await db.get(StaticFile, static_file_id)


async def find_static_file_by_hash(db: AsyncSession, file_hash: str) -> StaticFile | None:
  """Find a pooled static file by its content hash."""
  query = select(StaticFile) \
          .where(StaticFile.file_hash == file_hash)  # fmt: skip

  result = await db.execute(query)
  return result.scalar_one_or_none()


# -------------------------
# region: Web Subscriptions
# -------------------------


async def find_owned_web_import_source_for_subscription(db: AsyncSession, user_id: str, web_subscription_id: uuid.UUID) -> CalendarImportSource | None:
  """Find any web import source the user owns that is subscribed to the given web subscription."""
  query = select(CalendarImportSource) \
          .join(CalendarImport, CalendarImport.id == CalendarImportSource.import_id) \
          .where(
            CalendarImport.user_id == user_id,
            CalendarImportSource.kind == CalendarImportSourceKind.WEB,
            CalendarImportSource.web_subscription_id == web_subscription_id,
          ) \
          .limit(1)  # fmt: skip

  result = await db.execute(query)
  return result.scalar_one_or_none()


async def get_unreferenced_web_subscription_ids(db: AsyncSession) -> list[uuid.UUID]:
  """Ids of WebSubscription rows no CalendarImportSource references anymore."""
  query = select(WebSubscription.id) \
          .where(
            WebSubscription.id.not_in(
              select(CalendarImportSource.web_subscription_id)
              .where(CalendarImportSource.web_subscription_id.is_not(None)
          )))  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def get_web_subscription_by_id(db: AsyncSession, web_subscription_id: uuid.UUID) -> WebSubscription | None:
  """Fetch a web subscription by primary key."""
  return await db.get(WebSubscription, web_subscription_id)


async def find_web_subscription_by_url(db: AsyncSession, url: str) -> WebSubscription | None:
  """Find a pooled web subscription by its URL."""
  query = select(WebSubscription) \
          .where(WebSubscription.url == url)  # fmt: skip

  result = await db.execute(query)
  return result.scalar_one_or_none()


async def get_all_web_subscriptions(db: AsyncSession) -> list[WebSubscription]:
  """All web subscriptions across all users."""
  query = select(WebSubscription)
  result = await db.execute(query)
  return list(result.scalars().all())


# -------------------------
# region: Snapshots
# -------------------------


async def get_snapshot_by_id(db: AsyncSession, snapshot_id: uuid.UUID) -> Snapshot | None:
  """Fetch a snapshot by primary key."""
  return await db.get(Snapshot, snapshot_id)


async def find_snapshot_by_hash(db: AsyncSession, web_subscription_id: uuid.UUID, file_hash: str) -> Snapshot | None:
  """Find a subscription's snapshot with a given content hash."""
  query = select(Snapshot) \
          .where(
            Snapshot.web_subscription_id == web_subscription_id,
            Snapshot.file_hash == file_hash
          )  # fmt: skip

  result = await db.execute(query)
  return result.scalar_one_or_none()


async def get_latest_snapshot_for_web_subscription(db: AsyncSession, web_subscription_id: uuid.UUID) -> Snapshot | None:
  """Most recently fetched snapshot for a web subscription, if any."""
  query = select(Snapshot) \
          .where(Snapshot.web_subscription_id == web_subscription_id) \
          .order_by(Snapshot.fetched_at.desc()) \
          .limit(1)  # fmt: skip

  result = await db.execute(query)
  return result.scalar_one_or_none()


async def get_snapshots_for_web_subscription(db: AsyncSession, web_subscription_id: uuid.UUID) -> list[Snapshot]:
  """All snapshots for a web subscription, newest first."""
  query = select(Snapshot) \
          .where(Snapshot.web_subscription_id == web_subscription_id) \
          .order_by(Snapshot.fetched_at.desc())  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


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


async def get_export_source_import_ids(db: AsyncSession, export_id: uuid.UUID) -> list[uuid.UUID]:
  """List of import ids feeding a given export."""
  query = select(CalendarExportSource.import_id) \
          .where(CalendarExportSource.export_id == export_id)  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def delete_export_sources_for_export(db: AsyncSession, export_id: uuid.UUID) -> None:
  """Remove all CalendarExportSource rows for an export (bulk delete).

  Must be committed.
  """
  query = delete(CalendarExportSource) \
          .where(CalendarExportSource.export_id == export_id)  # fmt: skip

  await db.execute(query)


async def get_active_source_ids_for_export(db: AsyncSession, export_id: uuid.UUID) -> list[uuid.UUID | None]:
  """Active import-source ids of every import feeding a given export."""
  query = select(CalendarImport.active_source_id) \
          .join(CalendarExportSource, CalendarExportSource.import_id == CalendarImport.id) \
          .where(CalendarExportSource.export_id == export_id)  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def get_imports_for_export(db: AsyncSession, export_id: uuid.UUID) -> list[CalendarImport]:
  """Imports feeding a given export."""
  query = select(CalendarImport) \
          .where(CalendarImport.id.in_(
            select(CalendarExportSource.import_id)
            .where(CalendarExportSource.export_id == export_id)
          ))  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


async def get_duplicate_exports(db: AsyncSession, dedup_key: str, excluded_export_id: uuid.UUID) -> list[CalendarExport]:
  """Other exports sharing the same dedup key as the given export."""
  query = select(CalendarExport) \
          .where(
            CalendarExport.dedup_key == dedup_key,
            CalendarExport.id != excluded_export_id
          )  # fmt: skip

  result = await db.execute(query)
  return list(result.scalars().all())


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


async def get_export_cache_by_dedup_key(db: AsyncSession, dedup_key: str) -> CalendarExportCache | None:
  """Fetch a cached export result by its dedup key."""
  return await db.get(CalendarExportCache, dedup_key)


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


async def clear_calendar_references_for_board(db: AsyncSession, board_id: uuid.UUID) -> None:
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
