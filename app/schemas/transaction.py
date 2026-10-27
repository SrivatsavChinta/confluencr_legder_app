from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, WithJsonSchema

from app.models import TransactionType
from app.models.base import MAX_MONEY_AMOUNT
from app.schemas.account import RupeeAmount

REFERENCE_ID_MAX_LENGTH = 100  # Matches String(100) on Transaction.reference_id.
DESCRIPTION_MAX_LENGTH = 255  # Matches String(255) on Transaction.description.

ReferenceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=REFERENCE_ID_MAX_LENGTH),
]

# A blank description is stored as null rather than as an empty string.
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX_LENGTH),
    AfterValidator(lambda text: text or None),
]


def require_decimal_string(value: object) -> object:
    # JSON numbers are parsed as binary floats before reaching Decimal, which can change the value.
    if not isinstance(value, str):
        raise ValueError('amount must be a decimal string, for example "500.00"')
    return value


# A positive rupee amount with at most two decimal places that the database can store.
PositiveAmount = Annotated[
    Decimal,
    BeforeValidator(require_decimal_string),
    Field(gt=0, le=MAX_MONEY_AMOUNT, decimal_places=2),
    # The default schema advertises JSON numbers too, which require_decimal_string rejects.
    WithJsonSchema(
        {
            "type": "string",
            "description": (
                "Rupee amount as a decimal string: greater than 0, at most two decimal places, "
                f"at most {MAX_MONEY_AMOUNT}. JSON numbers are rejected."
            ),
            "examples": ["500.00"],
        }
    ),
]


class TransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: TransactionType
    amount: PositiveAmount
    description: Description | None = None
    reference_id: ReferenceId


class TransactionRecord(BaseModel):
    """A stored ledger entry, as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    type: TransactionType
    amount: RupeeAmount
    description: str | None
    reference_id: str
    created_at: datetime


class TransactionResponse(TransactionRecord):
    """Response to a credit or debit: the new entry plus the balance it produced."""

    balance_after: RupeeAmount


class PaginationMeta(BaseModel):
    current_page: int
    page_size: int
    total_records: int
    total_pages: int


class TransactionHistoryResponse(BaseModel):
    data: list[TransactionRecord]
    pagination: PaginationMeta
