from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class ProjectModel(Base):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assigned_to: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(255))
    customer: Mapped[dict | None] = mapped_column(JSONB)
    customer_asset: Mapped[dict | None] = mapped_column(JSONB)
    customer_uid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    description: Mapped[str | None] = mapped_column(Text)
    invalid: Mapped[bool | None] = mapped_column(Boolean)
    location: Mapped[str | None] = mapped_column(String(255))
    order_no: Mapped[str | None] = mapped_column(String(255))
    project_uid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    status: Mapped[str | None] = mapped_column(String(255))
    trade: Mapped[str | None] = mapped_column(String(255))
    trade_type: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int | None] = mapped_column(Integer)
    zipcode: Mapped[str | None] = mapped_column(String(255))
    # Existing tenant-schema columns populated by project geocoding. These are
    # read by the QuickBooks daily-time exporter; no schema migration is needed.
    jobsite_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    jobsite_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    geocode_status: Mapped[str | None] = mapped_column(String(20))
    metro_code: Mapped[str | None] = mapped_column(String(50))


class ProjectExternalMapModel(Base):
    __tablename__ = "project_external_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    service_id: Mapped[int | None] = mapped_column(BigInteger)
    external_project_id: Mapped[str | None] = mapped_column(String(128))
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    additional_info: Mapped[str | None] = mapped_column(Text)
    sync_status: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    project_uuid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    sync_token: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(Text)
