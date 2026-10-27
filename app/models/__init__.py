"""Importing this package registers every model on Base.metadata."""

from app.models.account import Account
from app.models.base import Base
from app.models.transaction import Transaction, TransactionType
from app.models.transfer import Transfer

__all__ = ["Account", "Base", "Transaction", "TransactionType", "Transfer"]
