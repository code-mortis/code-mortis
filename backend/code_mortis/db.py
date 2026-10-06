from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from .config import settings


def now() -> datetime:
    return datetime.now(UTC)


def uid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


@lru_cache
def engine():
    return create_engine(settings().database_url, pool_pre_ping=True)


@contextmanager
def transaction():
    with Session(engine(), expire_on_commit=False) as session:
        with session.begin():
            yield session


def get_db():
    with Session(engine(), expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
