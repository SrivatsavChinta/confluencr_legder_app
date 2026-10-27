from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.models.base import SQLITE_MAX_INTEGER
from app.schemas.account import RupeeAmount
from app.schemas.transaction import Description, PositiveAmount, ReferenceId, TransactionRecord

# Strict so that "1", 1.0, or true are not silently accepted as account IDs.
BodyAccountId = Annotated[StrictInt, Field(ge=1, le=SQLITE_MAX_INTEGER)]


class TransferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_account_id: BodyAccountId
    destination_account_id: BodyAccountId
    amount: PositiveAmount
    reference_id: ReferenceId
    description: Description | None = None

    @model_validator(mode="after")
    def require_different_accounts(self) -> "TransferCreate":
        if self.source_account_id == self.destination_account_id:
            raise ValueError("source_account_id and destination_account_id must be different")
        return self


class TransferResponse(BaseModel):
    id: int
    source_account_id: int
    destination_account_id: int
    amount: RupeeAmount
    reference_id: str
    description: str | None
    created_at: datetime
    debit_transaction: TransactionRecord
    credit_transaction: TransactionRecord
    source_balance_after: RupeeAmount
