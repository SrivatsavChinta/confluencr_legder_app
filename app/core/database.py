import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "ledger.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DATABASE_PATH}")


def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    # SQLite ignores foreign keys unless this is enabled on every new connection.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def create_db_engine(database_url: str) -> Engine:
    """Create an engine for the given URL, with SQLite foreign keys enforced."""
    url = make_url(database_url)
    connect_args = {}

    if url.get_backend_name() == "sqlite":
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        # FastAPI may use a session in a different thread from the one that opened it.
        connect_args["check_same_thread"] = False

    engine = create_engine(database_url, connect_args=connect_args)

    if url.get_backend_name() == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)

    return engine


def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create any missing tables. Existing tables and data are left untouched."""
    Base.metadata.create_all(bind=engine)


engine = create_db_engine(DATABASE_URL)
SessionLocal = create_session_factory(engine)
