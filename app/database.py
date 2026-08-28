import os
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.time import KST


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


@lru_cache
def get_engine() -> Engine:
    return create_db_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


def create_db_engine(url: str, *, pool_pre_ping: bool = False) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=pool_pre_ping,
        connect_args={"options": f"-c timezone={KST.key}"},
    )


def get_db() -> Generator[Session]:
    with Session(get_engine()) as session:
        yield session
