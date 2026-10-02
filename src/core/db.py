import sqlalchemy.ext.asyncio
import sqlalchemy.orm

import src.core.config

AsyncSession = sqlalchemy.ext.asyncio.AsyncSession
engine = sqlalchemy.ext.asyncio.create_async_engine(src.core.config.settings.database_url, echo=False, pool_pre_ping=True)
async_session = sqlalchemy.ext.asyncio.async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(sqlalchemy.orm.DeclarativeBase):
  pass


async def get_db():
  async with async_session() as session:
    yield session
