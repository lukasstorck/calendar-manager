import sqlalchemy.ext.asyncio
import sqlalchemy.orm

import src.core.config

engine = sqlalchemy.ext.asyncio.create_async_engine(src.core.config.DATABASE_URL, echo=False, pool_pre_ping=True)
async_session = sqlalchemy.ext.asyncio.async_sessionmaker(engine, expire_on_commit=False, class_=sqlalchemy.ext.asyncio.AsyncSession)


class Base(sqlalchemy.orm.DeclarativeBase):
  pass


async def get_db():
  async with async_session() as session:
    yield session
