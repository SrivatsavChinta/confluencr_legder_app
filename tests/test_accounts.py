from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.database import DEFAULT_DATABASE_PATH, create_db_engine, create_session_factory, get_db, init_db
from app.main import app
from app.models import Account


@pytest.fixture
def session_factory(tmp_path):
    database_path = tmp_path / "test_api.db"
    assert database_path != DEFAULT_DATABASE_PATH

    engine = create_db_engine(f"sqlite:///{database_path}")
    init_db(engine)
    yield create_session_factory(engine)
    engine.dispose()


def use_database(session_factory):
    def get_test_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db


@pytest.fixture
def client(session_factory):
    use_database(session_factory)
    # Not used as a context manager: that would run app startup, which initialises the default database.
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def create_account(client, name="Savings Account"):
    response = client.post("/api/accounts", json={"name": name})
    assert response.status_code == 201
    return response.json()


def parse_utc(timestamp):
    assert timestamp.endswith("Z")
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def store_account(session_factory, **values):
    with session_factory() as session:
        account = Account(**values)
        session.add(account)
        session.commit()
        return account.id


# Create account


def test_create_account_returns_201_with_generated_fields(client):
    response = client.post("/api/accounts", json={"name": "Savings Account"})

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "name", "balance", "created_at"}
    assert isinstance(body["id"], int) and body["id"] >= 1
    assert body["name"] == "Savings Account"
    assert body["balance"] == "0.00"

    created_at = parse_utc(body["created_at"])
    assert created_at.utcoffset().total_seconds() == 0
    assert abs(datetime.now(timezone.utc) - created_at).total_seconds() < 60


def test_create_account_persists_to_database(client, session_factory):
    body = create_account(client)

    with session_factory() as session:
        stored = session.get(Account, body["id"])
        assert stored.name == "Savings Account"
        assert stored.balance == Decimal("0.00")


def test_create_account_trims_name(client):
    assert create_account(client, name="  Savings Account \t")["name"] == "Savings Account"


def test_create_account_accepts_name_at_max_length(client):
    assert create_account(client, name="a" * 100)["name"] == "a" * 100


@pytest.mark.parametrize(
    ("payload", "error_type", "error_location"),
    [
        ({}, "missing", ["body", "name"]),
        ({"name": None}, "string_type", ["body", "name"]),
        ({"name": 123}, "string_type", ["body", "name"]),
        ({"name": ""}, "string_too_short", ["body", "name"]),
        ({"name": "   \t\n"}, "string_too_short", ["body", "name"]),
        ({"name": "a" * 101}, "string_too_long", ["body", "name"]),
        ({"name": "  " + "a" * 101 + "  "}, "string_too_long", ["body", "name"]),
        ({"name": "Rich", "balance": "1000.00"}, "extra_forbidden", ["body", "balance"]),
        ({"name": "Chosen", "id": 42}, "extra_forbidden", ["body", "id"]),
        ({"name": "Dated", "created_at": "2020-01-01T00:00:00Z"}, "extra_forbidden", ["body", "created_at"]),
    ],
)
def test_create_account_rejects_invalid_payload(client, payload, error_type, error_location):
    response = client.post("/api/accounts", json=payload)

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(error["type"] == error_type and error["loc"] == error_location for error in errors)
    assert client.get("/api/accounts").json() == {"items": []}


def test_create_account_rejects_malformed_json(client):
    response = client.post(
        "/api/accounts",
        content="{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422


# List accounts


def test_list_accounts_returns_empty_list_when_no_accounts(client):
    response = client.get("/api/accounts")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_list_accounts_returns_newest_first(client):
    created = [create_account(client, name=name) for name in ("First", "Second", "Third")]

    items = client.get("/api/accounts").json()["items"]

    assert [item["name"] for item in items] == ["Third", "Second", "First"]
    assert items == list(reversed(created))


def test_list_accounts_breaks_timestamp_ties_by_newest_id(client, session_factory):
    same_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ids = [store_account(session_factory, name=f"Tied {n}", created_at=same_time) for n in range(3)]

    items = client.get("/api/accounts").json()["items"]

    assert [item["id"] for item in items] == sorted(ids, reverse=True)


def test_list_accounts_matches_database(client, session_factory):
    store_account(session_factory, name="Funded", balance=Decimal("1500.75"))
    create_account(client, name="Fresh")

    items = client.get("/api/accounts").json()["items"]

    with session_factory() as session:
        for item in items:
            stored = session.get(Account, item["id"])
            assert item["name"] == stored.name
            assert Decimal(item["balance"]) == stored.balance
            assert parse_utc(item["created_at"]) == stored.created_at


# Account details


def test_get_account_returns_account(client):
    created = create_account(client)

    response = client.get(f"/api/accounts/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_missing_account_returns_404(client):
    response = client.get("/api/accounts/9999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Account not found"}


@pytest.mark.parametrize("bad_id", ["abc", "1.5", "0", "-1", str(2**63)])
@pytest.mark.parametrize("suffix", ["", "/balance"])
def test_invalid_account_id_returns_422(client, bad_id, suffix):
    response = client.get(f"/api/accounts/{bad_id}{suffix}")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "account_id"]


# Account balance


def test_new_account_balance_is_zero(client):
    created = create_account(client)

    response = client.get(f"/api/accounts/{created['id']}/balance")

    assert response.status_code == 200
    assert response.json() == {"account_id": created["id"], "balance": "0.00", "currency": "INR"}


@pytest.mark.parametrize(
    ("stored_balance", "expected"),
    [
        (Decimal("1234.5"), "1234.50"),
        (Decimal("0.05"), "0.05"),
        (Decimal("100"), "100.00"),
        (Decimal("92233720368547758.07"), "92233720368547758.07"),
    ],
)
def test_existing_balance_is_returned_as_two_decimal_string(client, session_factory, stored_balance, expected):
    account_id = store_account(session_factory, name="Funded", balance=stored_balance)

    response = client.get(f"/api/accounts/{account_id}/balance")

    assert response.status_code == 200
    assert response.json() == {"account_id": account_id, "balance": expected, "currency": "INR"}


def test_balance_of_missing_account_returns_404(client):
    response = client.get("/api/accounts/9999/balance")

    assert response.status_code == 404
    assert response.json() == {"detail": "Account not found"}


# Unexpected failures


def test_database_failure_returns_safe_500(client, tmp_path):
    # A database with no tables makes every account query fail inside SQLAlchemy.
    broken_engine = create_db_engine(f"sqlite:///{tmp_path / 'no_tables.db'}")
    use_database(create_session_factory(broken_engine))

    response = client.get("/api/accounts")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    broken_engine.dispose()
