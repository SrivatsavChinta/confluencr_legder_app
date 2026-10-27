import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.main import app
from app.models import Account, Transaction
from app.models.base import MAX_MONEY_AMOUNT


def create_account(client, name="Wallet"):
    response = client.post("/api/accounts", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def store_account(session_factory, balance):
    with session_factory() as session:
        account = Account(name="Stored", balance=balance)
        session.add(account)
        session.commit()
        return account.id


def post_transaction(client, account_id, /, **fields):
    payload = {"type": "credit", "amount": "100.00", "reference_id": "TXN-001"}
    payload.update(fields)
    return client.post(f"/api/accounts/{account_id}/transactions", json=payload)


def credit(client, account_id, amount, reference_id):
    response = post_transaction(client, account_id, type="credit", amount=amount, reference_id=reference_id)
    assert response.status_code == 201
    return response.json()


def balance_of(client, account_id):
    return client.get(f"/api/accounts/{account_id}/balance").json()["balance"]


def count_transactions(session_factory, account_id):
    with session_factory() as session:
        query = select(func.count()).select_from(Transaction).where(Transaction.account_id == account_id)
        return session.scalar(query)


# Successful operations


def test_credit_returns_201_with_transaction_and_resulting_balance(client):
    account_id = create_account(client)

    response = post_transaction(
        client,
        account_id,
        type="credit",
        amount="500.00",
        description="Salary",
        reference_id="TXN-001",
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id",
        "account_id",
        "type",
        "amount",
        "description",
        "reference_id",
        "created_at",
        "balance_after",
    }
    assert isinstance(body["id"], int)
    assert body["account_id"] == account_id
    assert body["type"] == "credit"
    assert body["amount"] == "500.00"
    assert body["description"] == "Salary"
    assert body["reference_id"] == "TXN-001"
    assert body["balance_after"] == "500.00"

    assert body["created_at"].endswith("Z")
    created_at = datetime.fromisoformat(body["created_at"].replace("Z", "+00:00"))
    assert abs(datetime.now(timezone.utc) - created_at).total_seconds() < 60


def test_credit_persists_transaction_and_increases_balance(client, session_factory):
    account_id = create_account(client)

    body = credit(client, account_id, "250.75", "TXN-001")

    assert balance_of(client, account_id) == "250.75"
    with session_factory() as session:
        stored = session.get(Transaction, body["id"])
        assert stored.account_id == account_id
        assert stored.type.value == "credit"
        assert stored.amount == Decimal("250.75")
        assert stored.reference_id == "TXN-001"


def test_debit_with_sufficient_funds_decreases_balance(client, session_factory):
    account_id = create_account(client)
    credit(client, account_id, "1000.00", "TXN-001")

    response = post_transaction(
        client, account_id, type="debit", amount="500.00", description="Purchase", reference_id="TXN-002"
    )

    assert response.status_code == 201
    assert response.json()["type"] == "debit"
    assert response.json()["balance_after"] == "500.00"
    assert balance_of(client, account_id) == "500.00"
    assert count_transactions(session_factory, account_id) == 2


def test_debit_of_entire_balance_leaves_zero(client):
    account_id = create_account(client)
    credit(client, account_id, "75.50", "TXN-001")

    response = post_transaction(client, account_id, type="debit", amount="75.50", reference_id="TXN-002")

    assert response.status_code == 201
    assert response.json()["balance_after"] == "0.00"


def test_decimal_amounts_are_exact(client):
    account_id = create_account(client)

    credit(client, account_id, "0.10", "TXN-1")
    credit(client, account_id, "0.20", "TXN-2")
    debit = post_transaction(client, account_id, type="debit", amount="0.05", reference_id="TXN-3")

    assert debit.json()["balance_after"] == "0.25"
    assert balance_of(client, account_id) == "0.25"


@pytest.mark.parametrize(
    ("submitted", "returned"),
    [("100", "100.00"), ("100.5", "100.50"), ("0.01", "0.01"), ("1e2", "100.00"), ("500.000", "500.00")],
)
def test_amount_is_returned_with_two_decimal_places(client, submitted, returned):
    account_id = create_account(client)

    body = credit(client, account_id, submitted, "TXN-001")

    assert body["amount"] == returned
    assert body["balance_after"] == returned


def test_string_fields_are_trimmed_and_blank_description_is_null(client):
    account_id = create_account(client)

    first = post_transaction(client, account_id, reference_id="  TXN-001 ", description="  Salary  ").json()
    second = post_transaction(client, account_id, reference_id="TXN-002", description="   ").json()
    third = post_transaction(client, account_id, reference_id="TXN-003").json()

    assert first["reference_id"] == "TXN-001"
    assert first["description"] == "Salary"
    assert second["description"] is None
    assert third["description"] is None


def test_maximum_storable_amount_is_accepted(client):
    account_id = create_account(client)

    body = credit(client, account_id, str(MAX_MONEY_AMOUNT), "TXN-MAX")

    assert body["balance_after"] == "92233720368547758.07"


# Validation


@pytest.mark.parametrize(
    ("fields", "error_field"),
    [
        ({"amount": "0"}, "amount"),
        ({"amount": "0.00"}, "amount"),
        ({"amount": "-5.00"}, "amount"),
        ({"amount": "10.001"}, "amount"),
        ({"amount": "NaN"}, "amount"),
        ({"amount": "Infinity"}, "amount"),
        ({"amount": "-Infinity"}, "amount"),
        ({"amount": "abc"}, "amount"),
        ({"amount": ""}, "amount"),
        ({"amount": None}, "amount"),
        ({"amount": 100}, "amount"),
        ({"amount": 100.5}, "amount"),
        ({"amount": "92233720368547758.08"}, "amount"),
        ({"amount": "1e30"}, "amount"),
        ({"type": "refund"}, "type"),
        ({"type": "CREDIT"}, "type"),
        ({"type": None}, "type"),
        ({"reference_id": ""}, "reference_id"),
        ({"reference_id": "   \t"}, "reference_id"),
        ({"reference_id": None}, "reference_id"),
        ({"reference_id": "R" * 101}, "reference_id"),
        ({"description": "D" * 256}, "description"),
        ({"id": 99}, "id"),
        ({"account_id": 2}, "account_id"),
        ({"balance_after": "1000.00"}, "balance_after"),
        ({"created_at": "2020-01-01T00:00:00Z"}, "created_at"),
    ],
)
def test_invalid_transaction_payload_is_rejected(client, session_factory, fields, error_field):
    account_id = create_account(client)

    response = post_transaction(client, account_id, **fields)

    assert response.status_code == 422
    assert any(error["loc"][:2] == ["body", error_field] for error in response.json()["detail"])
    assert balance_of(client, account_id) == "0.00"
    assert count_transactions(session_factory, account_id) == 0


@pytest.mark.parametrize("missing_field", ["type", "amount", "reference_id"])
def test_missing_required_field_is_rejected(client, missing_field):
    account_id = create_account(client)
    payload = {"type": "credit", "amount": "100.00", "reference_id": "TXN-001"}
    del payload[missing_field]

    response = client.post(f"/api/accounts/{account_id}/transactions", json=payload)

    assert response.status_code == 422
    assert {"type": "missing", "loc": ["body", missing_field]}.items() <= response.json()["detail"][0].items()


@pytest.mark.parametrize("bad_id", ["abc", "1.5", "0", "-1", str(2**63)])
def test_invalid_account_id_returns_422(client, bad_id):
    response = post_transaction(client, bad_id)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "account_id"]


def test_nonexistent_account_returns_404(client, session_factory):
    response = post_transaction(client, 9999)

    assert response.status_code == 404
    assert response.json() == {"detail": "Account not found"}
    assert count_transactions(session_factory, 9999) == 0


# Duplicate references


def test_duplicate_reference_on_same_account_is_rejected(client, session_factory):
    account_id = create_account(client)
    credit(client, account_id, "100.00", "TXN-001")

    response = post_transaction(client, account_id, amount="100.00", reference_id="TXN-001")

    assert response.status_code == 409
    assert response.json() == {"detail": "A transaction with this reference_id already exists for this account"}
    assert balance_of(client, account_id) == "100.00"
    assert count_transactions(session_factory, account_id) == 1


def test_duplicate_reference_is_reported_before_insufficient_balance(client):
    account_id = create_account(client)
    credit(client, account_id, "100.00", "TXN-001")

    response = post_transaction(client, account_id, type="debit", amount="500.00", reference_id="TXN-001")

    assert response.status_code == 409
    assert "reference_id" in response.json()["detail"]
    assert balance_of(client, account_id) == "100.00"


def test_duplicate_reference_matches_after_trimming(client, session_factory):
    account_id = create_account(client)
    credit(client, account_id, "100.00", "TXN-001")

    response = post_transaction(client, account_id, reference_id="  TXN-001  ")

    assert response.status_code == 409
    assert count_transactions(session_factory, account_id) == 1


def test_same_reference_is_allowed_on_different_accounts(client):
    first_account = create_account(client, name="First")
    second_account = create_account(client, name="Second")

    credit(client, first_account, "10.00", "SHARED-REF")
    credit(client, second_account, "20.00", "SHARED-REF")

    assert balance_of(client, first_account) == "10.00"
    assert balance_of(client, second_account) == "20.00"


# Insufficient balance and balance limits


def test_debit_exceeding_balance_is_rejected(client, session_factory):
    account_id = create_account(client)
    credit(client, account_id, "100.00", "TXN-001")

    response = post_transaction(client, account_id, type="debit", amount="100.01", reference_id="TXN-002")

    assert response.status_code == 409
    assert response.json() == {"detail": "Insufficient balance"}
    assert balance_of(client, account_id) == "100.00"
    assert count_transactions(session_factory, account_id) == 1


def test_debit_on_empty_account_is_rejected(client, session_factory):
    account_id = create_account(client)

    response = post_transaction(client, account_id, type="debit", amount="0.01")

    assert response.status_code == 409
    assert count_transactions(session_factory, account_id) == 0


def test_rejected_debit_does_not_reserve_its_reference(client):
    account_id = create_account(client)
    rejected = post_transaction(client, account_id, type="debit", amount="50.00", reference_id="TXN-001")
    assert rejected.status_code == 409

    retried = post_transaction(client, account_id, type="credit", amount="50.00", reference_id="TXN-001")

    assert retried.status_code == 201


def test_credit_beyond_storable_balance_is_rejected(client, session_factory):
    account_id = store_account(session_factory, balance=MAX_MONEY_AMOUNT)

    response = post_transaction(client, account_id, amount="0.01")

    assert response.status_code == 409
    assert response.json() == {"detail": "Resulting balance would exceed the maximum supported amount"}
    assert balance_of(client, account_id) == "92233720368547758.07"
    assert count_transactions(session_factory, account_id) == 0


# Atomicity: deterministic failure injection


def test_failure_during_balance_update_rolls_back_inserted_transaction(client, engine, session_factory):
    account_id = create_account(client)
    credit(client, account_id, "100.00", "TXN-001")
    executed = []

    def fail_on_balance_update(connection, cursor, statement, parameters, context, executemany):
        executed.append(statement.split()[0])
        if statement.startswith("UPDATE accounts"):
            raise SQLAlchemyError("Injected failure")

    event.listen(engine, "before_cursor_execute", fail_on_balance_update)
    response = post_transaction(client, account_id, type="debit", amount="40.00", reference_id="TXN-002")
    event.remove(engine, "before_cursor_execute", fail_on_balance_update)

    assert executed[-2:] == ["INSERT", "UPDATE"]  # The ledger row was written before the failure.
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert balance_of(client, account_id) == "100.00"
    assert count_transactions(session_factory, account_id) == 1


def test_failure_at_commit_rolls_back_both_changes(client, engine, session_factory):
    account_id = create_account(client)
    credit(client, account_id, "100.00", "TXN-001")

    def fail_on_commit(connection):
        raise SQLAlchemyError("Injected commit failure")

    event.listen(engine, "commit", fail_on_commit)
    response = post_transaction(client, account_id, type="debit", amount="40.00", reference_id="TXN-002")
    event.remove(engine, "commit", fail_on_commit)

    assert response.status_code == 500
    assert balance_of(client, account_id) == "100.00"
    assert count_transactions(session_factory, account_id) == 1


# Concurrency


def post_simultaneously(account_id, payloads):
    """Send each payload from its own thread, released together by a barrier."""
    barrier = threading.Barrier(len(payloads))

    def send(payload):
        barrier.wait()
        return TestClient(app).post(f"/api/accounts/{account_id}/transactions", json=payload)

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return [response.status_code for response in pool.map(send, payloads)]


def test_simultaneous_debits_cannot_overdraw_account(client, session_factory):
    account_id = create_account(client)
    credit(client, account_id, "500.00", "FUND")
    payloads = [{"type": "debit", "amount": "100.00", "reference_id": f"DEBIT-{n}"} for n in range(10)]

    statuses = post_simultaneously(account_id, payloads)

    assert sorted(statuses) == [201] * 5 + [409] * 5
    assert balance_of(client, account_id) == "0.00"
    assert count_transactions(session_factory, account_id) == 1 + 5


def test_simultaneous_duplicate_requests_create_one_transaction(client, session_factory):
    account_id = create_account(client)
    payloads = [{"type": "credit", "amount": "100.00", "reference_id": "SAME-REF"} for _ in range(5)]

    statuses = post_simultaneously(account_id, payloads)

    assert sorted(statuses) == [201] + [409] * 4
    assert balance_of(client, account_id) == "100.00"
    assert count_transactions(session_factory, account_id) == 1
