from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Account
from app.schemas.account import (
    AccountBalanceResponse,
    AccountCreate,
    AccountListResponse,
    AccountResponse,
)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

DbSession = Annotated[Session, Depends(get_db)]

# Upper bound is SQLite's largest integer; larger values would fail inside the driver.
AccountId = Annotated[int, Path(ge=1, le=2**63 - 1)]


def get_account_or_404(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, db: DbSession) -> Account:
    account = Account(name=payload.name)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("", response_model=AccountListResponse)
def list_accounts(db: DbSession) -> dict:
    accounts = db.scalars(select(Account).order_by(Account.created_at.desc(), Account.id.desc())).all()
    return {"items": accounts}


@router.get("/{account_id}", response_model=AccountResponse)
def get_account(account_id: AccountId, db: DbSession) -> Account:
    return get_account_or_404(db, account_id)


@router.get("/{account_id}/balance", response_model=AccountBalanceResponse)
def get_account_balance(account_id: AccountId, db: DbSession) -> AccountBalanceResponse:
    account = get_account_or_404(db, account_id)
    return AccountBalanceResponse(account_id=account.id, balance=account.balance)
