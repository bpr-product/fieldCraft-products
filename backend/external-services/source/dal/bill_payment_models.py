from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class PaymentExpenseModel(Base):
    __tablename__ = "payment_expense"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    amount: Mapped[float | None] = mapped_column(Numeric)
    codes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    currency_code: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(255))
    exchange_rate: Mapped[str | None] = mapped_column(String(64))
    payee_account_no: Mapped[str | None] = mapped_column(String(255))
    payee_id: Mapped[int | None] = mapped_column(BigInteger)
    reference_id: Mapped[int | None] = mapped_column(BigInteger)
    reference_no: Mapped[str | None] = mapped_column(String(255))
    reference_type: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[str | None] = mapped_column(String(255))
    chart_of_account_id: Mapped[int | None] = mapped_column(BigInteger)
    project_id: Mapped[int | None] = mapped_column(BigInteger)


class FinalPaymentExternalMapModel(Base):
    __tablename__ = "payment_external_map"
    __table_args__ = (
        CheckConstraint(
            "((CASE WHEN payment_receives_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN payment_expense_id IS NOT NULL THEN 1 ELSE 0 END) = 1)",
            name="chk_payment_external_map_exactly_one_ref",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger)
    tenant_id: Mapped[str] = mapped_column(String(255))
    external_payment_id: Mapped[str | None] = mapped_column(String(128))
    external_invoice_id: Mapped[str | None] = mapped_column(String(128))
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    additional_info: Mapped[str | None] = mapped_column(Text)
    sync_status: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_receives_id: Mapped[int | None] = mapped_column(BigInteger)
    payment_expense_id: Mapped[int | None] = mapped_column(BigInteger)
    service_id: Mapped[int | None] = mapped_column(Integer)
    endorsement_effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    sync_token: Mapped[str | None] = mapped_column(Text)
    invalid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
