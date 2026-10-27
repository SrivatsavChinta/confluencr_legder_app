from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.models import Account, Transaction, TransactionType, Transfer
from app.models.base import utc_now
from app.routers.accounts import DbSession
from app.routers.transactions import INSUFFICIENT_BALANCE, balance_update, reference_exists
from app.schemas.error import ErrorResponse
from app.schemas.transaction import TransactionRecord
from app.schemas.transfer import TransferCreate, TransferResponse

router = APIRouter(prefix="/api/transfers", tags=["transfers"])

DESTINATION_LIMIT_EXCEEDED = "Destination balance would exceed the maximum supported amount"
DUPLICATE_REFERENCE = "reference_id has already been used on the source or destination account"


@router.post(
    "",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Source or destination account not found",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "reference_id already used on the source or destination account, "
            "insufficient source balance, or the destination balance would exceed the maximum",
        },
    },
)
def create_transfer(payload: TransferCreate, db: DbSession) -> TransferResponse:
    if db.get(Account, payload.source_account_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source account not found")
    if db.get(Account, payload.destination_account_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destination account not found")

    # One timestamp for the transfer and both of its entries: they record a single event.
    created_at = utc_now()
    transfer = Transfer(
        source_account_id=payload.source_account_id,
        destination_account_id=payload.destination_account_id,
        amount=payload.amount,
        created_at=created_at,
    )
    # Both entries reuse the transfer's reference, so UNIQUE (account_id, reference_id)
    # on transactions rejects a repeated transfer from either account.
    debit = Transaction(
        account_id=payload.source_account_id,
        type=TransactionType.DEBIT,
        amount=payload.amount,
        description=payload.description,
        reference_id=payload.reference_id,
        transfer=transfer,
        created_at=created_at,
    )
    credit = Transaction(
        account_id=payload.destination_account_id,
        type=TransactionType.CREDIT,
        amount=payload.amount,
        description=payload.description,
        reference_id=payload.reference_id,
        transfer=transfer,
        created_at=created_at,
    )

    try:
        # Insert the transfer and both entries first: a duplicate reference fails here,
        # before either balance is changed.
        db.add(transfer)
        db.flush()

        source_balance = db.execute(
            balance_update(payload.source_account_id, TransactionType.DEBIT, payload.amount)
        ).scalar_one_or_none()
        if source_balance is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=INSUFFICIENT_BALANCE)

        destination_balance = db.execute(
            balance_update(payload.destination_account_id, TransactionType.CREDIT, payload.amount)
        ).scalar_one_or_none()
        if destination_balance is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DESTINATION_LIMIT_EXCEEDED)

        db.commit()
    except IntegrityError:
        db.rollback()
        # The unique constraint is the real guard; these lookups only decide which error to report.
        if reference_exists(db, payload.source_account_id, payload.reference_id) or reference_exists(
            db, payload.destination_account_id, payload.reference_id
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_REFERENCE)
        raise
    except Exception:
        db.rollback()
        raise

    return TransferResponse(
        id=transfer.id,
        source_account_id=transfer.source_account_id,
        destination_account_id=transfer.destination_account_id,
        amount=transfer.amount,
        reference_id=payload.reference_id,
        description=payload.description,
        created_at=transfer.created_at,
        debit_transaction=TransactionRecord.model_validate(debit),
        credit_transaction=TransactionRecord.model_validate(credit),
        source_balance_after=source_balance,
    )
