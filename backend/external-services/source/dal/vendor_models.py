from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class VendorModel(Base):
    __tablename__ = "vendor"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_no: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    check_name: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(255))
    company_name: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    is_email_verified: Mapped[bool | None] = mapped_column(Boolean)
    lead_time: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[str | None] = mapped_column(String(255))
    pay_terms: Mapped[int | None] = mapped_column(Integer)
    primary_mobile_number: Mapped[str | None] = mapped_column(String(64))
    routing_no: Mapped[str | None] = mapped_column(String(255))
    services: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vendor_uid: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(255))
    zip_code: Mapped[str | None] = mapped_column(String(64))
    additional_info: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(String(255))
    categories: Mapped[str | None] = mapped_column(Text)
    subcontractor_id: Mapped[int | None] = mapped_column(BigInteger)


class VendorExternalMapModel(Base):
    __tablename__ = "vendor_external_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vendor_id: Mapped[int | None] = mapped_column(BigInteger)
    tenant_id: Mapped[str | None] = mapped_column(String(255))
    service_id: Mapped[int | None] = mapped_column(BigInteger)
    external_vendor_id: Mapped[str | None] = mapped_column(String(128))
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    sync_status: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vendor_uuid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    sync_token: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
