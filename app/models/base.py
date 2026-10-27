from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Integer, MetaData
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

# Predictable constraint names make integrity errors easier to read and test.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

PAISE_PER_RUPEE = 100
SQLITE_MAX_INTEGER = 2**63 - 1
# Largest rupee amount that fits in a SQLite INTEGER column as paise: 92233720368547758.07.
MAX_MONEY_AMOUNT = Decimal(SQLITE_MAX_INTEGER) / PAISE_PER_RUPEE


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def not_blank(column_name: str) -> str:
    """SQL expression that rejects empty strings and strings of only spaces, tabs, or newlines."""
    whitespace = "' ' || char(9) || char(10) || char(13)"
    return f"length(trim({column_name}, {whitespace})) > 0"


class Money(TypeDecorator):
    """Rupee amounts as Decimal in Python, stored exactly as integer paise in the database.

    SQLite has no exact decimal type, so integer storage avoids floating-point rounding.
    Amounts with more than two decimal places, or outside the SQLite integer range, are rejected.
    """

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, (Decimal, int)) or isinstance(value, bool):
            raise TypeError(f"Money values must be Decimal or int, got {type(value).__name__}")

        amount = Decimal(value)
        if not amount.is_finite():
            raise ValueError(f"Money value must be finite, got {amount}")
        if abs(amount) > MAX_MONEY_AMOUNT:
            raise ValueError(f"Money value is outside the storable range: {amount}")

        paise = amount * PAISE_PER_RUPEE
        if paise != paise.to_integral_value():
            raise ValueError(f"Money value has more than two decimal places: {amount}")
        return int(paise)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(value) / PAISE_PER_RUPEE).quantize(Decimal("0.01"))


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes in Python, stored as UTC in SQLite (which has no timezone support)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Datetime values must be timezone-aware")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)
