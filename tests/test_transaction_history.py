from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Transaction, TransactionType

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
RECORD_FIELDS = {"id", "account_id", "type", "amount", "description", "reference_id", "created_at"}


def create_account(client, name="Wallet"):
    response = client.post("/api/accounts", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def post_transaction(client, account_id, transaction_type, amount, reference_id, description=None):
    payload = {"type": transaction_type, "amount": amount, "reference_id": reference_id}
    if description is not None:
        payload["description"] = description
    response = client.post(f"/api/accounts/{account_id}/transactions", json=payload)
    assert response.status_code == 201
    return response.json()


def seed_transactions(session_factory, account_id, types, timestamps=None):
    """Insert ledger rows directly with controlled timestamps (one minute apart by default).

    Balances are not touched; these rows exist only to exercise history queries.
    Returns the new transaction IDs in insertion order.
    """
    if timestamps is None:
        timestamps = [BASE_TIME + timedelta(minutes=n) for n in range(len(types))]
    with session_factory() as session:
        entries = [
            Transaction(
                account_id=account_id,
                type=TransactionType(transaction_type),
                amount=Decimal("10.00") + n,
                reference_id=f"SEED-{n}",
                created_at=created_at,
            )
            for n, (transaction_type, created_at) in enumerate(zip(types, timestamps))
        ]
        session.add_all(entries)
        session.commit()
        return [entry.id for entry in entries]


def get_history(client, account_id, **params):
    return client.get(f"/api/accounts/{account_id}/transactions", params=params)


def history_ids(client, account_id, **params):
    response = get_history(client, account_id, **params)
    assert response.status_code == 200
    return [entry["id"] for entry in response.json()["data"]]


# Basic behavior


def test_history_returns_account_transactions(client):
    account_id = create_account(client)
    credit = post_transaction(client, account_id, "credit", "1500.00", "TXN-001", description="Salary")
    debit = post_transaction(client, account_id, "debit", "500.00", "TXN-002", description="Purchase")

    response = get_history(client, account_id)

    assert response.status_code == 200
    expected = [
        {key: value for key, value in created.items() if key != "balance_after"} for created in (debit, credit)
    ]
    assert response.json()["data"] == expected


def test_history_excludes_other_accounts(client, session_factory):
    account_id = create_account(client, name="Mine")
    other_id = create_account(client, name="Other")
    own_ids = seed_transactions(session_factory, account_id, ["credit", "debit"])
    seed_transactions(session_factory, other_id, ["credit", "credit", "debit"])

    body = get_history(client, account_id).json()

    assert sorted(entry["id"] for entry in body["data"]) == sorted(own_ids)
    assert body["pagination"]["total_records"] == 2


def test_history_of_account_without_transactions_is_empty(client):
    account_id = create_account(client)

    response = get_history(client, account_id)

    assert response.status_code == 200
    assert response.json() == {
        "data": [],
        "pagination": {"current_page": 1, "page_size": 10, "total_records": 0, "total_pages": 0},
    }


def test_history_of_nonexistent_account_returns_404(client):
    response = get_history(client, 9999)

    assert response.status_code == 404
    assert response.json() == {"detail": "Account not found"}


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", str(2**63)])
def test_history_with_invalid_account_id_returns_422(client, bad_id):
    response = get_history(client, bad_id)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "account_id"]


def test_history_response_structure_and_field_types(client):
    account_id = create_account(client)
    post_transaction(client, account_id, "credit", "100.00", "TXN-001", description="Salary")
    post_transaction(client, account_id, "credit", "50.00", "TXN-002")

    body = get_history(client, account_id).json()

    assert set(body) == {"data", "pagination"}
    assert set(body["pagination"]) == {"current_page", "page_size", "total_records", "total_pages"}
    assert all(isinstance(value, int) for value in body["pagination"].values())
    for entry in body["data"]:
        assert set(entry) == RECORD_FIELDS
        assert isinstance(entry["id"], int)
        assert isinstance(entry["account_id"], int)
        assert entry["type"] in {"credit", "debit"}
        assert isinstance(entry["amount"], str)
        assert entry["description"] is None or isinstance(entry["description"], str)
        assert isinstance(entry["reference_id"], str)
        assert isinstance(entry["created_at"], str)
    assert [entry["description"] for entry in body["data"]] == [None, "Salary"]


def test_history_amounts_are_two_decimal_strings(client):
    account_id = create_account(client)
    for reference_id, amount in [("A", "100"), ("B", "0.5"), ("C", "1234.56")]:
        post_transaction(client, account_id, "credit", amount, reference_id)

    amounts = [entry["amount"] for entry in get_history(client, account_id).json()["data"]]

    assert amounts == ["1234.56", "0.50", "100.00"]


def test_history_timestamps_are_utc_iso_8601(client, session_factory):
    account_id = create_account(client)
    created_at = datetime(2026, 10, 27, 10, 0, 0, 123456, tzinfo=timezone.utc)
    seed_transactions(session_factory, account_id, ["credit"], timestamps=[created_at])

    entry = get_history(client, account_id).json()["data"][0]

    assert entry["created_at"] == "2026-10-27T10:00:00.123456Z"


# Filtering


@pytest.mark.parametrize(
    ("type_filter", "expected_types"),
    [("credit", {"credit"}), ("debit", {"debit"}), (None, {"credit", "debit"})],
)
def test_type_filter_selects_matching_transactions(client, session_factory, type_filter, expected_types):
    account_id = create_account(client)
    types = ["credit", "debit", "credit", "credit", "debit"]
    seed_transactions(session_factory, account_id, types)
    params = {"type": type_filter} if type_filter else {}

    body = get_history(client, account_id, **params).json()

    returned_types = {entry["type"] for entry in body["data"]}
    expected_count = sum(1 for t in types if t in expected_types)
    assert returned_types == expected_types
    assert len(body["data"]) == expected_count
    assert body["pagination"]["total_records"] == expected_count


@pytest.mark.parametrize("bad_type", ["refund", "CREDIT", "Debit", ""])
def test_invalid_type_filter_returns_422(client, bad_type):
    account_id = create_account(client)

    response = get_history(client, account_id, type=bad_type)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "type"]


# Pagination


def test_default_page_and_page_size(client, session_factory):
    account_id = create_account(client)
    ids = seed_transactions(session_factory, account_id, ["credit"] * 15)

    body = get_history(client, account_id).json()

    assert [entry["id"] for entry in body["data"]] == list(reversed(ids))[:10]
    assert body["pagination"] == {"current_page": 1, "page_size": 10, "total_records": 15, "total_pages": 2}


def test_custom_page_and_limit(client, session_factory):
    account_id = create_account(client)
    ids = seed_transactions(session_factory, account_id, ["credit"] * 12)
    newest_first = list(reversed(ids))

    body = get_history(client, account_id, page=2, limit=5).json()

    assert [entry["id"] for entry in body["data"]] == newest_first[5:10]
    assert body["pagination"] == {"current_page": 2, "page_size": 5, "total_records": 12, "total_pages": 3}


def test_pages_cover_all_records_without_overlap(client, session_factory):
    account_id = create_account(client)
    ids = seed_transactions(session_factory, account_id, ["credit", "debit"] * 11 + ["credit"])

    pages = [history_ids(client, account_id, page=page, limit=10) for page in (1, 2, 3)]

    assert [len(page) for page in pages] == [10, 10, 3]
    assert pages[0] + pages[1] + pages[2] == list(reversed(ids))


@pytest.mark.parametrize(
    ("record_count", "limit", "expected_pages"),
    [(1, 10, 1), (10, 10, 1), (11, 10, 2), (23, 10, 3), (23, 100, 1), (5, 1, 5)],
)
def test_total_pages_is_ceiling_of_records_over_limit(client, session_factory, record_count, limit, expected_pages):
    account_id = create_account(client)
    seed_transactions(session_factory, account_id, ["credit"] * record_count)

    pagination = get_history(client, account_id, limit=limit).json()["pagination"]

    assert pagination["total_records"] == record_count
    assert pagination["total_pages"] == expected_pages


def test_maximum_limit_is_accepted(client, session_factory):
    account_id = create_account(client)
    seed_transactions(session_factory, account_id, ["credit"] * 101)

    body = get_history(client, account_id, limit=100).json()

    assert len(body["data"]) == 100
    assert body["pagination"]["total_pages"] == 2


@pytest.mark.parametrize(
    ("param", "bad_value"),
    [
        ("page", "0"),
        ("page", "-1"),
        ("page", "abc"),
        ("page", "1.5"),
        ("page", str(2**63)),
        ("limit", "0"),
        ("limit", "-1"),
        ("limit", "101"),
        ("limit", "abc"),
    ],
)
def test_invalid_page_or_limit_returns_422(client, param, bad_value):
    account_id = create_account(client)

    response = get_history(client, account_id, **{param: bad_value})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", param]


def test_page_beyond_results_returns_empty_data_with_metadata(client, session_factory):
    account_id = create_account(client)
    seed_transactions(session_factory, account_id, ["credit"] * 3)

    response = get_history(client, account_id, page=5)

    assert response.status_code == 200
    assert response.json() == {
        "data": [],
        "pagination": {"current_page": 5, "page_size": 10, "total_records": 3, "total_pages": 1},
    }


def test_very_large_page_number_is_handled(client, session_factory):
    account_id = create_account(client)
    seed_transactions(session_factory, account_id, ["credit"])

    response = get_history(client, account_id, page=(2**63 - 1) // 100, limit=100)

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_filter_without_matches_reports_zero_pages(client, session_factory):
    account_id = create_account(client)
    seed_transactions(session_factory, account_id, ["credit"] * 4)

    body = get_history(client, account_id, type="debit").json()

    assert body == {
        "data": [],
        "pagination": {"current_page": 1, "page_size": 10, "total_records": 0, "total_pages": 0},
    }


# Ordering


def test_history_is_ordered_newest_first_by_timestamp(client, session_factory):
    account_id = create_account(client)
    # Insertion order (and therefore id order) deliberately differs from timestamp order.
    timestamps = [BASE_TIME + timedelta(days=offset) for offset in (2, 0, 3, 1)]
    ids = seed_transactions(session_factory, account_id, ["credit"] * 4, timestamps=timestamps)

    returned = history_ids(client, account_id)

    assert returned == [ids[2], ids[0], ids[3], ids[1]]


def test_equal_timestamps_are_ordered_by_newest_id_across_pages(client, session_factory):
    account_id = create_account(client)
    ids = seed_transactions(session_factory, account_id, ["credit"] * 7, timestamps=[BASE_TIME] * 7)

    pages = [history_ids(client, account_id, page=page, limit=3) for page in (1, 2, 3)]

    assert pages[0] + pages[1] + pages[2] == sorted(ids, reverse=True)
    assert history_ids(client, account_id, page=2, limit=3) == pages[1]


# Combined behavior


def test_filter_and_pagination_work_together(client, session_factory):
    account_id = create_account(client)
    types = ["credit", "debit"] * 6  # 6 credits and 6 debits, alternating.
    ids = seed_transactions(session_factory, account_id, types)
    debit_ids_newest_first = [entry_id for entry_id, t in reversed(list(zip(ids, types))) if t == "debit"]

    body = get_history(client, account_id, type="debit", page=2, limit=4).json()

    assert [entry["id"] for entry in body["data"]] == debit_ids_newest_first[4:6]
    assert {entry["type"] for entry in body["data"]} == {"debit"}
    assert body["pagination"] == {"current_page": 2, "page_size": 4, "total_records": 6, "total_pages": 2}


def test_history_queries_do_not_change_balance_or_transactions(client, session_factory):
    account_id = create_account(client)
    post_transaction(client, account_id, "credit", "300.00", "TXN-001")
    post_transaction(client, account_id, "debit", "120.50", "TXN-002")

    for params in ({}, {"type": "credit"}, {"type": "debit", "page": 2, "limit": 1}, {"page": 9}):
        assert get_history(client, account_id, **params).status_code == 200

    assert client.get(f"/api/accounts/{account_id}/balance").json()["balance"] == "179.50"
    with session_factory() as session:
        query = select(func.count()).select_from(Transaction).where(Transaction.account_id == account_id)
        assert session.scalar(query) == 2
