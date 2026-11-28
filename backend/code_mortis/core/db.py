import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import DeclarativeBase, Session
from starlette.exceptions import HTTPException

from code_mortis.core.config import settings

mcp_request_path: ContextVar[str | None] = ContextVar("mcp_request_path", default=None)
log = logging.getLogger(__name__)


class McpPoolBusy(HTTPException):
    def __init__(self):
        super().__init__(503, "MCP database capacity is busy. Retry shortly.", headers={"Retry-After": "2"})


def now() -> datetime:
    return datetime.now(UTC)


def uid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


@lru_cache
def engine():
    return create_engine(settings().database_url, pool_pre_ping=True)


@lru_cache
def mcp_engine():
    config = settings()
    return create_engine(config.database_url, pool_pre_ping=True, pool_size=config.mcp_db_pool_size,
                         max_overflow=0, pool_timeout=1)


@contextmanager
def _session():
    path = mcp_request_path.get()
    with Session(mcp_engine() if path else engine(), expire_on_commit=False) as session:
        try:
            yield session
        except PoolTimeout as exc:
            if path is None:
                raise
            log.warning("MCP database checkout timed out endpoint=%s", path)
            raise McpPoolBusy() from exc


@contextmanager
def transaction():
    with _session() as session:
        with session.begin():
            yield session


def get_db():
    with _session() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
