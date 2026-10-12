from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class ReceiptModel(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ai_result: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(255))
    customer_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    file_name: Mapped[str | None] = mapped_column(String(1024))
    invoice_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invoice_number: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(255))
    order_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order_number: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(128))
    vendor_id: Mapped[str | None] = mapped_column(String)
    document_id: Mapped[int | None] = mapped_column(BigInteger)
    project_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    purchase_order_id: Mapped[int | None] = mapped_column(BigInteger)
    receipt_additional_info: Mapped[str | None] = mapped_column(Text)
    invalid: Mapped[bool | None] = mapped_column(Boolean)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int | None] = mapped_column(Integer)
    chart_of_account_id: Mapped[int | None] = mapped_column(BigInteger)
    receipt_uuid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    stock_reference: Mapped[str | None] = mapped_column(String(255))
    paid_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReceiptExternalMapModel(Base):
    __tablename__ = "receipt_external_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    receipt_id: Mapped[int | None] = mapped_column(BigInteger)
    receipt_uuid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    tenant_id: Mapped[str | None] = mapped_column(String(255))
    service_id: Mapped[int | None] = mapped_column(BigInteger)
    external_receipt_id: Mapped[str | None] = mapped_column(String(128))
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    sync_status: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_token: Mapped[str | None] = mapped_column(String)
