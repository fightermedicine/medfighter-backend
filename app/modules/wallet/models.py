"""Wallet and double-entry ledger ORM models (§7, §8, §9, §43).

Tables owned:
- wallet_accounts
- ledger_transactions
- ledger_entries
- deposits

Invariants:
- Balanced double-entry: For any POSTED transaction, sum(DEBIT) == sum(CREDIT).
- Authoritative balance is dynamically derived from posted ledger entries:
  balance = sum(CREDIT) - sum(DEBIT) for user asset accounts.
- No direct mutable balance column exists anywhere.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class WalletAccount(Base):
    __tablename__ = "wallet_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Special system accounts have user_id = None (e.g. system cash clearing account)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    account_type: Mapped[str] = mapped_column(String(32), default="USER_WALLET", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EGP", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    entries: Mapped[list[LedgerEntry]] = relationship("LedgerEntry", back_populates="account")

    __table_args__ = (
        Index("ix_wallet_accounts_user_currency", "user_id", "currency", unique=True),
    )


class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), unique=True, index=True, nullable=True
    )
    reference: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    # Status: PENDING, POSTED, REVERSED, REJECTED
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    entries: Mapped[list[LedgerEntry]] = relationship(
        "LedgerEntry", back_populates="transaction", cascade="all, delete-orphan"
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("ledger_transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("wallet_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Direction: DEBIT (outflow from user wallet) or CREDIT (inflow to user wallet)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_piastres: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    transaction: Mapped[LedgerTransaction] = relationship(
        "LedgerTransaction", back_populates="entries"
    )
    account: Mapped[WalletAccount] = relationship("WalletAccount", back_populates="entries")


class Deposit(Base):
    __tablename__ = "deposits"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount_piastres: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(32), default="vodafone_cash", nullable=False)
    sender_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_code: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    # Status: PENDING, APPROVED, REJECTED
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("ledger_transactions.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
