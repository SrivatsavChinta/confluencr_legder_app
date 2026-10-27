from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import Update, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Account, Transaction, TransactionType
from app.models.base import MAX_MONEY_AMOUNT
from app.routers.accounts import AccountId, DbSession, get_account_or_404
from app.schemas.transaction import TransactionCreate, TransactionResponse

router = APIRouter(prefix="/api/accounts", tags=["transactions"])

INSUFFICIENT_BALANCE = "Insufficient balance"
BALANCE_LIMIT_EXCEEDED = "Resulting balance would exceed the maximum supported amount"
DUPLICATE_REFERENCE = "A transaction with this reference_id already exists for this account"


def balance_update(account_id: int, transaction_type: TransactionType, amount: Decimal) -> Update:
    """Change the balance in one conditional UPDATE so the check and the write cannot be separated.

    No row is updated (and nothing is returned) if a debit would make the balance negative
    or a credit would push it past what the database can store.
    """
    if transaction_type is TransactionType.CREDIT:
        statement = (
            update(Account)
            .where(Account.id == account_id, Account.balance <= MAX_MONEY_AMOUNT - amount)
            .values(balance=Account.balance + amount)
        )
    else:
        statement = (
            update(Account)
            .where(Account.id == account_id, Account.balance >= amount)
            .values(balance=Account.balance - amount)
        )
    # The new balance is read from RETURNING, so the ORM need not sync loaded Account objects.
    return statement.returning(Account.balance).execution_options(synchronize_session=False)


def reference_exists(db: Session, account_id: int, reference_id: str) -> bool:
    query = select(Transaction.id).where(
        Transaction.account_id == account_id,
        Transaction.reference_id == reference_id,
    )
    return db.scalar(query) is not None


@router.post(
    "/{account_id}/transactions",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_transaction(account_id: AccountId, payload: TransactionCreate, db: DbSession) -> TransactionResponse:
    get_account_or_404(db, account_id)

    entry = Transaction(account_id=account_id, **payload.model_dump())
    try:
        # Insert first: a duplicate reference fails here, before any balance change is attempted.
        db.add(entry)
        db.flush()

        balance_after = db.execute(balance_update(account_id, payload.type, payload.amount)).scalar_one_or_none()
        if balance_after is None:
            detail = BALANCE_LIMIT_EXCEEDED if payload.type is TransactionType.CREDIT else INSUFFICIENT_BALANCE
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

        db.commit()
    except IntegrityError:
        db.rollback()
        # The unique constraint is the real guard; this lookup only decides which error to report.
        if reference_exists(db, account_id, payload.reference_id):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_REFERENCE)
        raise
    except Exception:
        db.rollback()
        raise

    return TransactionResponse(
        id=entry.id,
        account_id=entry.account_id,
        type=entry.type,
        amount=entry.amount,
        description=entry.description,
        reference_id=entry.reference_id,
        created_at=entry.created_at,
        balance_after=balance_after,
    )
