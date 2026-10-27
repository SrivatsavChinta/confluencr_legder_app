import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.main import app
from app.models import Account, Transaction, Transfer
from app.models.base import MAX_MONEY_AMOUNT


def create_account(client, name="Account", opening_balance=None):
    response = client.post("/api/accounts", json={"name": name})
    assert response.status_code == 201
    account_id = response.json()["id"]
    if opening_balance is not None:
        funded = client.post(
            f"/api/accounts/{account_id}/transactions",
            json={"type": "credit", "amount": opening_balance, "reference_id": "OPENING"},
        )
        assert funded.status_code == 201
    return account_id


def post_transfer(client, source_id, destination_id, /, **fields):
    payload = {
        "source_account_id": source_id,
        "destination_account_id": destination_id,
        "amount": "100.00",
        "reference_id": "TRANSFER-001",
    }
    payload.update(fields)
    return client.post("/api/transfers", json=payload)


def balance_of(client, account_id):
    return client.get(f"/api/accounts/{account_id}/balance").json()["balance"]


def count_rows(session_factory, model):
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(model))


@pytest.fixture
def two_accounts(client):
    """Account A with ₹5,000.00 and account B with ₹1,000.00, each funded by one opening credit."""
    return create_account(client, "A", "5000.00"), create_account(client, "B", "1000.00")


def assert_nothing_transferred(client, session_factory, source_id, destination_id):
    assert balance_of(client, source_id) == "5000.00"
    assert balance_of(client, destination_id) == "1000.00"
    assert count_rows(session_factory, Transfer) == 0
    assert count_rows(session_factory, Transaction) == 2  # Only the two opening credits.


# Successful transfers


def test_transfer_moves_money_between_accounts(client, two_accounts):
    source_id, destination_id = two_accounts

    response = post_transfer(client, source_id, destination_id, amount="2000.00")

    assert response.status_code == 201
    assert balance_of(client, source_id) == "3000.00"
    assert balance_of(client, destination_id) == "3000.00"


def test_transfer_creates_one_transfer_and_two_linked_entries(client, session_factory, two_accounts):
    source_id, destination_id = two_accounts

    transfer_id = post_transfer(client, source_id, destination_id, amount="250.50").json()["id"]

    assert count_rows(session_factory, Transfer) == 1
    with session_factory() as session:
        transfer = session.get(Transfer, transfer_id)
        assert (transfer.source_account_id, transfer.destination_account_id) == (source_id, destination_id)
        assert transfer.amount == Decimal("250.50")

        entries = {entry.type.value: entry for entry in transfer.transactions}
        assert set(entries) == {"debit", "credit"}
        assert entries["debit"].account_id == source_id
        assert entries["credit"].account_id == destination_id
        for entry in entries.values():
            assert entry.amount == Decimal("250.50")
            assert entry.transfer_id == transfer_id
            assert entry.reference_id == "TRANSFER-001"


def test_transfer_response_contains_transfer_and_both_entries(client, two_accounts):
    source_id, destination_id = two_accounts

    response = post_transfer(
        client,
        source_id,
        destination_id,
        amount="1000.00",
        reference_id="  TRANSFER-001 ",
        description="  Payment ",
    )

    body = response.json()
    assert set(body) == {
        "id",
        "source_account_id",
        "destination_account_id",
        "amount",
        "reference_id",
        "description",
        "created_at",
        "debit_transaction",
        "credit_transaction",
        "source_balance_after",
    }
    assert body["source_account_id"] == source_id
    assert body["destination_account_id"] == destination_id
    assert body["amount"] == "1000.00"
    assert body["reference_id"] == "TRANSFER-001"
    assert body["description"] == "Payment"
    assert body["source_balance_after"] == "4000.00"
    assert body["created_at"].endswith("Z")
    datetime.fromisoformat(body["created_at"].replace("Z", "+00:00"))

    debit, credit = body["debit_transaction"], body["credit_transaction"]
    assert (debit["type"], debit["account_id"], debit["amount"]) == ("debit", source_id, "1000.00")
    assert (credit["type"], credit["account_id"], credit["amount"]) == ("credit", destination_id, "1000.00")
    for entry in (debit, credit):
        assert entry["reference_id"] == "TRANSFER-001"
        assert entry["description"] == "Payment"
        assert entry["created_at"] == body["created_at"]
        assert "balance_after" not in entry


def test_transfer_of_entire_balance_leaves_source_at_zero(client, two_accounts):
    source_id, destination_id = two_accounts

    response = post_transfer(client, source_id, destination_id, amount="5000.00")

    assert response.status_code == 201
    assert response.json()["source_balance_after"] == "0.00"
    assert balance_of(client, destination_id) == "6000.00"


# Validation


def test_missing_source_account_returns_404(client, session_factory):
    destination_id = create_account(client, "B")

    response = post_transfer(client, 9999, destination_id)

    assert response.status_code == 404
    assert response.json() == {"detail": "Source account not found"}
    assert count_rows(session_factory, Transfer) == 0


def test_missing_destination_account_returns_404(client, session_factory):
    source_id = create_account(client, "A", "500.00")

    response = post_transfer(client, source_id, 9999)

    assert response.status_code == 404
    assert response.json() == {"detail": "Destination account not found"}
    assert balance_of(client, source_id) == "500.00"
    assert count_rows(session_factory, Transfer) == 0


def test_transfer_to_same_account_returns_422(client, session_factory):
    account_id = create_account(client, "A", "500.00")

    response = post_transfer(client, account_id, account_id)

    assert response.status_code == 422
    assert "must be different" in response.json()["detail"][0]["msg"]
    assert balance_of(client, account_id) == "500.00"
    assert count_rows(session_factory, Transfer) == 0


@pytest.mark.parametrize(
    ("fields", "error_field"),
    [
        ({"amount": "0.00"}, "amount"),
        ({"amount": "-10.00"}, "amount"),
        ({"amount": "10.001"}, "amount"),
        ({"amount": "NaN"}, "amount"),
        ({"amount": "Infinity"}, "amount"),
        ({"amount": "92233720368547758.08"}, "amount"),
        ({"amount": 100}, "amount"),
        ({"reference_id": None}, "reference_id"),
        ({"reference_id": "   "}, "reference_id"),
        ({"reference_id": "R" * 101}, "reference_id"),
        ({"source_account_id": "1"}, "source_account_id"),
        ({"source_account_id": 0}, "source_account_id"),
        ({"destination_account_id": True}, "destination_account_id"),
        ({"destination_account_id": 2**63}, "destination_account_id"),
        ({"id": 5}, "id"),
        ({"created_at": "2020-01-01T00:00:00Z"}, "created_at"),
        ({"source_balance_after": "0.00"}, "source_balance_after"),
    ],
)
def test_invalid_transfer_payload_returns_422(client, session_factory, two_accounts, fields, error_field):
    source_id, destination_id = two_accounts

    response = post_transfer(client, source_id, destination_id, **fields)

    assert response.status_code == 422
    assert any(error["loc"][:2] == ["body", error_field] for error in response.json()["detail"])
    assert_nothing_transferred(client, session_factory, source_id, destination_id)


@pytest.mark.parametrize("missing_field", ["source_account_id", "destination_account_id", "amount", "reference_id"])
def test_missing_required_field_returns_422(client, two_accounts, missing_field):
    payload = {"source_account_id": 1, "destination_account_id": 2, "amount": "1.00", "reference_id": "R"}
    del payload[missing_field]

    response = client.post("/api/transfers", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", missing_field]


# Insufficient funds and balance limits


def test_insufficient_source_balance_returns_409_and_changes_nothing(client, session_factory, two_accounts):
    source_id, destination_id = two_accounts

    response = post_transfer(client, source_id, destination_id, amount="5000.01")

    assert response.status_code == 409
    assert response.json() == {"detail": "Insufficient balance"}
    assert_nothing_transferred(client, session_factory, source_id, destination_id)


def test_destination_credit_failure_rolls_back_source_debit(client, session_factory):
    source_id = create_account(client, "A", "500.00")
    with session_factory() as session:
        destination = Account(name="Full", balance=MAX_MONEY_AMOUNT)
        session.add(destination)
        session.commit()
        destination_id = destination.id

    # The source debit succeeds inside the transaction; the destination credit then matches no row.
    response = post_transfer(client, source_id, destination_id, amount="1.00")

    assert response.status_code == 409
    assert response.json() == {"detail": "Destination balance would exceed the maximum supported amount"}
    assert balance_of(client, source_id) == "500.00"
    assert balance_of(client, destination_id) == str(MAX_MONEY_AMOUNT)
    assert count_rows(session_factory, Transfer) == 0
    assert count_rows(session_factory, Transaction) == 1


# Duplicate references


def test_duplicate_transfer_reference_returns_409_and_changes_nothing(client, session_factory, two_accounts):
    source_id, destination_id = two_accounts
    assert post_transfer(client, source_id, destination_id, amount="100.00").status_code == 201

    response = post_transfer(client, source_id, destination_id, amount="100.00")

    assert response.status_code == 409
    assert response.json() == {"detail": "reference_id has already been used on the source or destination account"}
    assert balance_of(client, source_id) == "4900.00"
    assert balance_of(client, destination_id) == "1100.00"
    assert count_rows(session_factory, Transfer) == 1
    assert count_rows(session_factory, Transaction) == 2 + 2


def test_duplicate_is_reported_before_insufficient_balance(client, two_accounts):
    source_id, destination_id = two_accounts
    post_transfer(client, source_id, destination_id, amount="5000.00")

    response = post_transfer(client, source_id, destination_id, amount="5000.00")

    assert response.status_code == 409
    assert "reference_id" in response.json()["detail"]


def test_reference_used_by_a_destination_entry_is_rejected(client, session_factory, two_accounts):
    source_id, destination_id = two_accounts
    # "OPENING" is already the reference of the destination's own opening credit.
    response = post_transfer(client, source_id, destination_id, reference_id="OPENING")

    assert response.status_code == 409
    assert_nothing_transferred(client, session_factory, source_id, destination_id)


def test_same_reference_is_allowed_between_unrelated_accounts(client, two_accounts):
    source_id, destination_id = two_accounts
    other_source = create_account(client, "C", "100.00")
    other_destination = create_account(client, "D")

    assert post_transfer(client, source_id, destination_id, reference_id="SHARED").status_code == 201
    assert post_transfer(client, other_source, other_destination, reference_id="SHARED").status_code == 201


# Atomicity: deterministic failure injection


def inject_failure(engine, should_fail):
    """Raise inside the first SQL statement for which should_fail(statement) is true."""
    executed = []

    def listener(connection, cursor, statement, parameters, context, executemany):
        executed.append(" ".join(statement.split()[:3]))
        if should_fail(statement):
            raise SQLAlchemyError("Injected failure")

    event.listen(engine, "before_cursor_execute", listener)
    return executed, lambda: event.remove(engine, "before_cursor_execute", listener)


def test_failure_after_source_debit_rolls_back_entire_transfer(client, engine, session_factory, two_accounts):
    source_id, destination_id = two_accounts
    updates = []

    def fail_on_second_balance_update(statement):
        if statement.startswith("UPDATE accounts"):
            updates.append(statement)
            return len(updates) == 2
        return False

    executed, remove = inject_failure(engine, fail_on_second_balance_update)
    response = post_transfer(client, source_id, destination_id, amount="2000.00")
    remove()

    # Transfer row, both entries, and the source debit all ran before the destination credit failed.
    assert {"INSERT INTO transfers", "INSERT INTO transactions"} <= set(executed)
    assert executed[-2:] == ["UPDATE accounts SET", "UPDATE accounts SET"]
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert_nothing_transferred(client, session_factory, source_id, destination_id)


def test_failure_while_inserting_entries_rolls_back_transfer_row(client, engine, session_factory, two_accounts):
    source_id, destination_id = two_accounts

    executed, remove = inject_failure(engine, lambda statement: statement.startswith("INSERT INTO transactions"))
    response = post_transfer(client, source_id, destination_id, amount="2000.00")
    remove()

    assert executed[-2:] == ["INSERT INTO transfers", "INSERT INTO transactions"]
    assert response.status_code == 500
    assert_nothing_transferred(client, session_factory, source_id, destination_id)


def test_failure_at_commit_rolls_back_entire_transfer(client, engine, session_factory, two_accounts):
    source_id, destination_id = two_accounts

    def fail_on_commit(connection):
        raise SQLAlchemyError("Injected commit failure")

    event.listen(engine, "commit", fail_on_commit)
    response = post_transfer(client, source_id, destination_id, amount="2000.00")
    event.remove(engine, "commit", fail_on_commit)

    assert response.status_code == 500
    assert_nothing_transferred(client, session_factory, source_id, destination_id)


# Concurrency


def post_transfers_simultaneously(payloads):
    barrier = threading.Barrier(len(payloads))

    def send(payload):
        barrier.wait()
        return TestClient(app).post("/api/transfers", json=payload).status_code

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return list(pool.map(send, payloads))


def test_simultaneous_transfers_cannot_overdraw_source(client, session_factory):
    source_id = create_account(client, "A", "500.00")
    destination_id = create_account(client, "B")
    payloads = [
        {"source_account_id": source_id, "destination_account_id": destination_id,
         "amount": "100.00", "reference_id": f"T-{n}"}
        for n in range(10)
    ]

    statuses = post_transfers_simultaneously(payloads)

    assert sorted(statuses) == [201] * 5 + [409] * 5
    assert balance_of(client, source_id) == "0.00"
    assert balance_of(client, destination_id) == "500.00"
    assert count_rows(session_factory, Transfer) == 5
    assert count_rows(session_factory, Transaction) == 1 + 5 * 2


def test_simultaneous_duplicate_transfers_are_processed_once(client, session_factory, two_accounts):
    source_id, destination_id = two_accounts
    payload = {"source_account_id": source_id, "destination_account_id": destination_id,
               "amount": "100.00", "reference_id": "SAME"}

    statuses = post_transfers_simultaneously([payload] * 5)

    assert sorted(statuses) == [201] + [409] * 4
    assert balance_of(client, source_id) == "4900.00"
    assert balance_of(client, destination_id) == "1100.00"
    assert count_rows(session_factory, Transfer) == 1


def test_simultaneous_opposite_transfers_conserve_money(client, session_factory, two_accounts):
    first_id, second_id = two_accounts
    payloads = [
        {"source_account_id": source, "destination_account_id": destination,
         "amount": "700.00", "reference_id": f"SWAP-{n}"}
        for n, (source, destination) in enumerate([(first_id, second_id), (second_id, first_id)] * 5)
    ]

    statuses = post_transfers_simultaneously(payloads)

    assert set(statuses) <= {201, 409}
    balances = [Decimal(balance_of(client, account_id)) for account_id in (first_id, second_id)]
    assert all(balance >= 0 for balance in balances)
    assert sum(balances) == Decimal("6000.00")
    assert count_rows(session_factory, Transfer) == statuses.count(201)
    assert count_rows(session_factory, Transaction) == 2 + 2 * statuses.count(201)


# Compatibility with transactions and history


def test_transfer_appears_in_both_account_histories(client, two_accounts):
    source_id, destination_id = two_accounts
    body = post_transfer(client, source_id, destination_id, amount="2000.00", description="Payment").json()

    source_history = client.get(f"/api/accounts/{source_id}/transactions").json()
    destination_history = client.get(f"/api/accounts/{destination_id}/transactions").json()

    assert source_history["data"][0] == body["debit_transaction"]
    assert destination_history["data"][0] == body["credit_transaction"]
    assert source_history["pagination"]["total_records"] == 2
    assert destination_history["pagination"]["total_records"] == 2


def test_history_filters_and_pagination_include_transfer_entries(client, two_accounts):
    source_id, destination_id = two_accounts
    for n in range(3):
        post_transfer(client, source_id, destination_id, amount="10.00", reference_id=f"T-{n}")

    debits = client.get(f"/api/accounts/{source_id}/transactions", params={"type": "debit", "limit": 2}).json()
    source_credits = client.get(f"/api/accounts/{source_id}/transactions", params={"type": "credit"}).json()
    destination_credits = client.get(
        f"/api/accounts/{destination_id}/transactions", params={"type": "credit", "page": 2, "limit": 2}
    ).json()

    assert [entry["reference_id"] for entry in debits["data"]] == ["T-2", "T-1"]
    assert debits["pagination"] == {"current_page": 1, "page_size": 2, "total_records": 3, "total_pages": 2}
    assert [entry["reference_id"] for entry in source_credits["data"]] == ["OPENING"]
    assert [entry["reference_id"] for entry in destination_credits["data"]] == ["T-0", "OPENING"]
    assert destination_credits["pagination"]["total_records"] == 4


def test_credit_and_debit_still_work_alongside_transfers(client, two_accounts):
    source_id, destination_id = two_accounts
    post_transfer(client, source_id, destination_id, amount="1000.00")

    debit = client.post(
        f"/api/accounts/{destination_id}/transactions",
        json={"type": "debit", "amount": "1500.00", "reference_id": "SPEND"},
    )

    assert debit.status_code == 201
    assert debit.json()["balance_after"] == "500.00"
    assert balance_of(client, source_id) == "4000.00"
