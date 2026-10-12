from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class InvoiceModel(Base):
    __tablename__ = "invoice"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(15, 3))
    codes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    customer_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invoice_message: Mapped[str | None] = mapped_column(String(255))
    invoice_no: Mapped[str] = mapped_column(String(255), nullable=False)
    invoiced_amount: Mapped[Decimal] = mapped_column(Numeric(15, 3), nullable=False)
    items: Mapped[str] = mapped_column(Text, nullable=False)
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reminder_status: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 3))
    tax_info: Mapped[str | None] = mapped_column(Text)
    term: Mapped[str | None] = mapped_column(String(255))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 3), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    additional_info: Mapped[str | None] = mapped_column(Text)
    invalid: Mapped[bool | None] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issue_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    type: Mapped[str | None] = mapped_column(String)
    document_id: Mapped[int | None] = mapped_column(BigInteger)


class InvoiceExternalMapModel(Base):
    __tablename__ = "invoice_external_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    service_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    external_invoice_id: Mapped[str | None] = mapped_column(String(128))
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_PUSH")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    sync_token: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    additional_info: Mapped[str | None] = mapped_column(Text)
    invalid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
