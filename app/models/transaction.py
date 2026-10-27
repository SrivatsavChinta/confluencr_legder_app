import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Money, UTCDateTime, not_blank, utc_now

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.transfer import Transfer


class TransactionType(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class Transaction(Base):
    """A single ledger entry against one account."""

    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("typeof(amount) = 'integer'", name="amount_is_integer"),
        CheckConstraint(not_blank("reference_id"), name="reference_id_not_blank"),
        UniqueConstraint("account_id", "reference_id"),
        # Supports listing an account's entries newest-first; id breaks timestamp ties.
        Index(None, "account_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    type: Mapped[TransactionType] = mapped_column(
        Enum(
            TransactionType,
            name="type",
            native_enum=False,
            create_constraint=True,
            length=6,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)
    transfer_id: Mapped[int | None] = mapped_column(
        ForeignKey("transfers.id", ondelete="RESTRICT"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)

    account: Mapped["Account"] = relationship(back_populates="transactions")
    transfer: Mapped["Transfer | None"] = relationship(back_populates="transactions")

    def __repr__(self) -> str:
        return (
            f"Transaction(id={self.id!r}, account_id={self.account_id!r}, "
            f"type={self.type.value if self.type else None!r}, amount={self.amount!r})"
        )
