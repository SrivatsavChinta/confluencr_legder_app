from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Money, UTCDateTime, not_blank, utc_now

if TYPE_CHECKING:
    from app.models.transaction import Transaction


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(not_blank("name"), name="name_not_blank"),
        CheckConstraint("balance >= 0", name="balance_non_negative"),
        # SQLite columns accept any value type, so integer paise must be checked explicitly.
        CheckConstraint("typeof(balance) = 'integer'", name="balance_is_integer"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)

    # passive_deletes="all" stops the ORM from detaching entries on delete,
    # so the database's RESTRICT rule decides (and blocks deleting accounts with history).
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account",
        passive_deletes="all",
    )

    def __repr__(self) -> str:
        return f"Account(id={self.id!r}, name={self.name!r}, balance={self.balance!r})"
