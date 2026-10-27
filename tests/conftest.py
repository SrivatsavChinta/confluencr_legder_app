import pytest
from fastapi.testclient import TestClient

from app.core.database import DEFAULT_DATABASE_PATH, create_db_engine, create_session_factory, get_db, init_db
from app.main import app


@pytest.fixture
def engine(tmp_path):
    database_path = tmp_path / "test_ledger.db"
    assert database_path != DEFAULT_DATABASE_PATH

    test_engine = create_db_engine(f"sqlite:///{database_path}")
    init_db(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def session_factory(engine):
    return create_session_factory(engine)


def _use_database(session_factory):
    def get_test_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db


@pytest.fixture
def use_database():
    """Point the API at a different session factory for the rest of the test."""
    return _use_database


@pytest.fixture
def client(session_factory):
    _use_database(session_factory)
    # Not used as a context manager: that would run app startup, which initialises the default database.
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
