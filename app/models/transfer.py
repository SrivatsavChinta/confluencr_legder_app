from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Money, UTCDateTime, utc_now

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.transaction import Transaction


class Transfer(Base):
    """A logical movement of money between two accounts.

    The money movement itself is recorded as a debit and a credit Transaction,
    each pointing back here through Transaction.transfer_id.
    """

    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("typeof(amount) = 'integer'", name="amount_is_integer"),
        CheckConstraint(
            "source_account_id <> destination_account_id",
            name="distinct_accounts",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    destination_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)

    # Two foreign keys point at accounts, so each relationship names the column it uses.
    source_account: Mapped["Account"] = relationship(foreign_keys=[source_account_id])
    destination_account: Mapped["Account"] = relationship(foreign_keys=[destination_account_id])
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="transfer",
        passive_deletes="all",
    )

    def __repr__(self) -> str:
        return (
            f"Transfer(id={self.id!r}, source_account_id={self.source_account_id!r}, "
            f"destination_account_id={self.destination_account_id!r}, amount={self.amount!r})"
        )
