from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class CustomerPaymentReceiveModel(Base):
    __tablename__ = "payment_receives"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chart_of_account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    customer_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_id: Mapped[int | None] = mapped_column(BigInteger)
    payment_reference_no: Mapped[str | None] = mapped_column(String(255))
    fieldcraft_transaction_id: Mapped[str | None] = mapped_column(String(255))
    payment_receipt_id: Mapped[str | None] = mapped_column(String(255))
    payment_transaction_id: Mapped[str | None] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 3), nullable=False)
    suspense_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 3))
    currency_code: Mapped[str | None] = mapped_column(String(255))
    exchange_rate: Mapped[str | None] = mapped_column(String(255))
    pay_method: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(128))
    additional_info: Mapped[str | None] = mapped_column(Text)
    codes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(16))
    received_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerPaymentEntryModel(Base):
    __tablename__ = "payment_receives_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payment_receives_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(15, 8), nullable=False)
    interest_paid: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    late_fee_paid: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    reference_no: Mapped[str | None] = mapped_column(String(255))
    codes: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
