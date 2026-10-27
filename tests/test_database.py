from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, StatementError

from app.core.database import DEFAULT_DATABASE_PATH, create_db_engine, create_session_factory, init_db
from app.models import Account, Transaction, TransactionType, Transfer

EXPECTED_TABLES = {"accounts", "transactions", "transfers"}


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


@pytest.fixture
def session(session_factory):
    with session_factory() as db_session:
        yield db_session


def create_account(session, name="Alice"):
    account = Account(name=name)
    session.add(account)
    session.commit()
    return account


def make_transaction(account_id, **overrides):
    values = {
        "account_id": account_id,
        "type": TransactionType.CREDIT,
        "amount": Decimal("100.00"),
        "reference_id": "ref-001",
    }
    values.update(overrides)
    return Transaction(**values)


def assert_commit_fails(session, record, expected_error=IntegrityError):
    session.add(record)
    with pytest.raises(expected_error):
        session.commit()
    session.rollback()


# Database initialization


def test_init_db_creates_expected_tables(engine):
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_init_db_can_run_repeatedly_without_losing_data(engine, session_factory):
    with session_factory() as session:
        create_account(session)

    init_db(engine)
    init_db(engine)

    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    with session_factory() as session:
        assert session.query(Account).count() == 1


def test_data_persists_across_sessions(session_factory):
    with session_factory() as first_session:
        account_id = create_account(first_session, name="Persistent").id

    with session_factory() as second_session:
        stored = second_session.get(Account, account_id)
        assert stored is not None
        assert stored.name == "Persistent"


def test_foreign_keys_are_enforced(engine):
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


# Account constraints


def test_valid_account_is_persisted_with_generated_fields(session_factory):
    with session_factory() as session:
        account_id = create_account(session).id

    with session_factory() as session:
        account = session.get(Account, account_id)
        assert isinstance(account.id, int)
        assert account.balance == Decimal("0.00")
        assert account.created_at.tzinfo == timezone.utc
        assert abs(datetime.now(timezone.utc) - account.created_at).total_seconds() < 60


@pytest.mark.parametrize("blank_name", ["", "   ", "\t\n "])
def test_blank_account_name_is_rejected(session, blank_name):
    assert_commit_fails(session, Account(name=blank_name))


def test_missing_account_name_is_rejected(session):
    assert_commit_fails(session, Account(name=None))


def test_negative_balance_is_rejected(session):
    assert_commit_fails(session, Account(name="Overdrawn", balance=Decimal("-0.01")))


def test_balance_round_trips_exactly(session_factory):
    with session_factory() as session:
        account = Account(name="Precise", balance=Decimal("1234567.89"))
        session.add(account)
        session.commit()
        account_id = account.id

    with session_factory() as session:
        assert session.get(Account, account_id).balance == Decimal("1234567.89")


@pytest.mark.parametrize(
    ("bad_amount", "expected_error"),
    [
        (Decimal("10.001"), ValueError),
        (10.5, TypeError),
    ],
)
def test_money_rejects_sub_paise_and_float_values(session, bad_amount, expected_error):
    session.add(Account(name="Bad money", balance=bad_amount))
    with pytest.raises(StatementError) as error_info:
        session.commit()
    assert isinstance(error_info.value.orig, expected_error)
    session.rollback()


# Transaction constraints


def test_valid_transaction_is_persisted(session_factory):
    with session_factory() as session:
        account = create_account(session)
        entry = make_transaction(account.id, description="Opening deposit")
        session.add(entry)
        session.commit()
        entry_id = entry.id

    with session_factory() as session:
        stored = session.get(Transaction, entry_id)
        assert stored.type is TransactionType.CREDIT
        assert stored.amount == Decimal("100.00")
        assert stored.description == "Opening deposit"
        assert stored.transfer_id is None
        assert stored.created_at.tzinfo == timezone.utc
        assert stored.account.name == "Alice"


def test_invalid_transaction_type_is_rejected(session):
    account = create_account(session)
    assert_commit_fails(session, make_transaction(account.id, type="refund"))


@pytest.mark.parametrize("bad_amount", [Decimal("0"), Decimal("-5.00")])
def test_non_positive_transaction_amount_is_rejected(session, bad_amount):
    account = create_account(session)
    assert_commit_fails(session, make_transaction(account.id, amount=bad_amount))


def test_transaction_for_missing_account_is_rejected(session):
    assert_commit_fails(session, make_transaction(account_id=9999))


@pytest.mark.parametrize("bad_reference", [None, "", "   "])
def test_missing_or_blank_reference_id_is_rejected(session, bad_reference):
    account = create_account(session)
    assert_commit_fails(session, make_transaction(account.id, reference_id=bad_reference))


def test_duplicate_reference_id_on_same_account_is_rejected(session):
    account = create_account(session)
    session.add(make_transaction(account.id, reference_id="dup"))
    session.commit()

    assert_commit_fails(session, make_transaction(account.id, reference_id="dup"))


def test_same_reference_id_is_allowed_on_different_accounts(session):
    alice = create_account(session, name="Alice")
    bob = create_account(session, name="Bob")

    session.add_all(
        [
            make_transaction(alice.id, reference_id="shared"),
            make_transaction(bob.id, reference_id="shared"),
        ]
    )
    session.commit()

    assert session.query(Transaction).filter_by(reference_id="shared").count() == 2


def test_account_with_transactions_cannot_be_deleted(session):
    account = create_account(session)
    session.add(make_transaction(account.id))
    session.commit()

    session.delete(account)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    assert session.query(Transaction).count() == 1


def test_transactions_can_be_listed_newest_first(session):
    account = create_account(session)
    session.add_all(
        [
            make_transaction(
                account.id,
                reference_id=f"ref-{day}",
                created_at=datetime(2026, 1, day, tzinfo=timezone.utc),
            )
            for day in (1, 3, 2)
        ]
    )
    session.commit()

    newest_first = (
        session.query(Transaction)
        .filter_by(account_id=account.id)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .all()
    )
    assert [entry.reference_id for entry in newest_first] == ["ref-3", "ref-2", "ref-1"]


# Transfer constraints


def test_valid_transfer_is_persisted(session):
    alice = create_account(session, name="Alice")
    bob = create_account(session, name="Bob")

    transfer = Transfer(
        source_account_id=alice.id,
        destination_account_id=bob.id,
        amount=Decimal("250.50"),
    )
    session.add(transfer)
    session.commit()

    assert transfer.id is not None
    assert transfer.source_account.name == "Alice"
    assert transfer.destination_account.name == "Bob"
    assert transfer.created_at.tzinfo == timezone.utc


@pytest.mark.parametrize("missing_side", ["source_account_id", "destination_account_id"])
def test_transfer_with_missing_account_is_rejected(session, missing_side):
    alice = create_account(session, name="Alice")
    bob = create_account(session, name="Bob")
    values = {
        "source_account_id": alice.id,
        "destination_account_id": bob.id,
        "amount": Decimal("10.00"),
    }
    values[missing_side] = 9999

    assert_commit_fails(session, Transfer(**values))


def test_transfer_to_same_account_is_rejected(session):
    alice = create_account(session)
    assert_commit_fails(
        session,
        Transfer(source_account_id=alice.id, destination_account_id=alice.id, amount=Decimal("10.00")),
    )


@pytest.mark.parametrize("bad_amount", [Decimal("0"), Decimal("-10.00")])
def test_non_positive_transfer_amount_is_rejected(session, bad_amount):
    alice = create_account(session, name="Alice")
    bob = create_account(session, name="Bob")
    assert_commit_fails(
        session,
        Transfer(source_account_id=alice.id, destination_account_id=bob.id, amount=bad_amount),
    )


def test_transfer_can_be_linked_to_its_ledger_entries(session_factory):
    with session_factory() as session:
        alice = create_account(session, name="Alice")
        bob = create_account(session, name="Bob")
        transfer = Transfer(
            source_account_id=alice.id,
            destination_account_id=bob.id,
            amount=Decimal("75.00"),
        )
        transfer.transactions = [
            make_transaction(alice.id, type=TransactionType.DEBIT, amount=Decimal("75.00"), reference_id="xfer-1"),
            make_transaction(bob.id, type=TransactionType.CREDIT, amount=Decimal("75.00"), reference_id="xfer-1"),
        ]
        session.add(transfer)
        session.commit()
        transfer_id = transfer.id

    with session_factory() as session:
        stored = session.get(Transfer, transfer_id)
        entries = {entry.type: entry for entry in stored.transactions}
        assert entries[TransactionType.DEBIT].account_id == stored.source_account_id
        assert entries[TransactionType.CREDIT].account_id == stored.destination_account_id
        assert all(entry.transfer is stored for entry in stored.transactions)


def test_transaction_with_missing_transfer_is_rejected(session):
    account = create_account(session)
    assert_commit_fails(session, make_transaction(account.id, transfer_id=9999))


# Raw SQL storage constraints
# These inserts bypass the ORM and the Money type, so only the database schema is under test.
# Text such as 'abc' compares greater than any number in SQLite, so it would pass "amount > 0" alone.

RAW_CREATED_AT = "2026-01-01 00:00:00.000000"


def raw_insert(engine, table, **values):
    columns = ", ".join(values)
    placeholders = ", ".join(f":{column}" for column in values)
    with engine.begin() as connection:
        connection.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), values)


def assert_raw_insert_violates(engine, constraint_name, table, **values):
    with pytest.raises(IntegrityError) as error_info:
        raw_insert(engine, table, **values)
    assert f"CHECK constraint failed: {constraint_name}" in str(error_info.value.orig)


def raw_account(account_id, balance=0):
    return {"id": account_id, "name": f"Raw {account_id}", "balance": balance, "created_at": RAW_CREATED_AT}


def raw_transaction(amount):
    return {
        "account_id": 1,
        "type": "credit",
        "amount": amount,
        "reference_id": "raw-ref",
        "created_at": RAW_CREATED_AT,
    }


def raw_transfer(amount):
    return {
        "source_account_id": 1,
        "destination_account_id": 2,
        "amount": amount,
        "created_at": RAW_CREATED_AT,
    }


def stored_value_and_type(engine, table, column):
    with engine.connect() as connection:
        return connection.execute(text(f"SELECT {column}, typeof({column}) FROM {table}")).one()


@pytest.fixture
def raw_accounts(engine):
    raw_insert(engine, "accounts", **raw_account(1))
    raw_insert(engine, "accounts", **raw_account(2))


def test_raw_integer_balance_is_accepted(engine):
    raw_insert(engine, "accounts", **raw_account(1, balance=25050))

    assert tuple(stored_value_and_type(engine, "accounts", "balance")) == (25050, "integer")


@pytest.mark.parametrize("bad_balance", ["zzz", 10.5])
def test_raw_non_integer_balance_is_rejected(engine, bad_balance):
    assert_raw_insert_violates(
        engine, "ck_accounts_balance_is_integer", "accounts", **raw_account(1, balance=bad_balance)
    )


def test_raw_negative_balance_is_rejected(engine):
    assert_raw_insert_violates(
        engine, "ck_accounts_balance_non_negative", "accounts", **raw_account(1, balance=-100)
    )


def test_raw_integer_transaction_amount_is_accepted(engine, raw_accounts):
    raw_insert(engine, "transactions", **raw_transaction(10050))

    assert tuple(stored_value_and_type(engine, "transactions", "amount")) == (10050, "integer")


@pytest.mark.parametrize("bad_amount", ["abc", 10.5])
def test_raw_non_integer_transaction_amount_is_rejected(engine, raw_accounts, bad_amount):
    assert_raw_insert_violates(
        engine, "ck_transactions_amount_is_integer", "transactions", **raw_transaction(bad_amount)
    )


@pytest.mark.parametrize("bad_amount", [0, -100])
def test_raw_non_positive_transaction_amount_is_rejected(engine, raw_accounts, bad_amount):
    assert_raw_insert_violates(
        engine, "ck_transactions_amount_positive", "transactions", **raw_transaction(bad_amount)
    )


def test_raw_integer_transfer_amount_is_accepted(engine, raw_accounts):
    raw_insert(engine, "transfers", **raw_transfer(7500))

    assert tuple(stored_value_and_type(engine, "transfers", "amount")) == (7500, "integer")


@pytest.mark.parametrize("bad_amount", ["abc", 10.5])
def test_raw_non_integer_transfer_amount_is_rejected(engine, raw_accounts, bad_amount):
    assert_raw_insert_violates(
        engine, "ck_transfers_amount_is_integer", "transfers", **raw_transfer(bad_amount)
    )


@pytest.mark.parametrize("bad_amount", [0, -100])
def test_raw_non_positive_transfer_amount_is_rejected(engine, raw_accounts, bad_amount):
    assert_raw_insert_violates(
        engine, "ck_transfers_amount_positive", "transfers", **raw_transfer(bad_amount)
    )
