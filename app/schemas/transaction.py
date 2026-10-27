from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator

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


class TransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: TransactionType
    amount: Annotated[Decimal, Field(gt=0, le=MAX_MONEY_AMOUNT, decimal_places=2)]
    description: Description | None = None
    reference_id: ReferenceId

    @field_validator("amount", mode="before")
    @classmethod
    def require_string_amount(cls, value: object) -> object:
        # JSON numbers are parsed as binary floats before reaching Decimal, which can change the value.
        if not isinstance(value, str):
            raise ValueError('amount must be a decimal string, for example "500.00"')
        return value


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    type: TransactionType
    amount: RupeeAmount
    description: str | None
    reference_id: str
    created_at: datetime
    balance_after: RupeeAmount
