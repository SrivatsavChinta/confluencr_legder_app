from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer, StringConstraints

ACCOUNT_NAME_MAX_LENGTH = 100  # Matches String(100) on Account.name.

# Rupees as a fixed two-decimal string ("0.00"), avoiding JSON floating-point numbers.
RupeeAmount = Annotated[
    Decimal,
    PlainSerializer(lambda amount: str(amount.quantize(Decimal("0.01"))), return_type=str),
]

AccountName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=ACCOUNT_NAME_MAX_LENGTH),
]


class AccountCreate(BaseModel):
    # Reject unknown fields so a client sending "balance" is told it was not accepted.
    model_config = ConfigDict(extra="forbid")

    name: AccountName


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    balance: RupeeAmount
    created_at: datetime


class AccountListResponse(BaseModel):
    items: list[AccountResponse]


class AccountBalanceResponse(BaseModel):
    account_id: int
    balance: RupeeAmount
    currency: Literal["INR"] = "INR"
