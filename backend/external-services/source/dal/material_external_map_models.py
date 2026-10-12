from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class MaterialExternalMapModel(Base):
    __tablename__ = "material_external_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    service_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entity_id: Mapped[int | None] = mapped_column(BigInteger)
    source_entity_uuid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    external_item_id: Mapped[str | None] = mapped_column(String(128))
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    external_display_name: Mapped[str | None] = mapped_column(String(255))
    external_item_type: Mapped[str | None] = mapped_column(String(64))
    sync_status: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_token: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(Text)
    additional_info: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaterialModel(Base):
    __tablename__ = "material"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alias_name: Mapped[str | None] = mapped_column(String(1024))
    codes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_startdate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grade: Mapped[str | None] = mapped_column(String(64))
    manufacturer: Mapped[str | None] = mapped_column(String(64))
    material_uid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    name: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str | None] = mapped_column(String(16))
    tags: Mapped[str | None] = mapped_column(String(512))
    unit: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(String(3072))
    base_uom_id: Mapped[int | None] = mapped_column(Integer)
    conversion_factor: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    allow_decimal: Mapped[bool | None] = mapped_column(Boolean)
    gross_uom_id: Mapped[int | None] = mapped_column(Integer)
    purchase_to_gross_factor: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    gross_to_base_factor: Mapped[Decimal | None] = mapped_column(Numeric(15, 8))
    service_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[str | None] = mapped_column(String(255))


class VendorMaterialMapModel(Base):
    __tablename__ = "vendor_material_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_stock: Mapped[bool | None] = mapped_column(Boolean)
    lead_time: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    material_id: Mapped[int | None] = mapped_column(BigInteger)
    vendor_id: Mapped[int | None] = mapped_column(BigInteger)


class MaterialCostModel(Base):
    __tablename__ = "material_cost"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(15, 3))
    cost_type: Mapped[str | None] = mapped_column(String(64))
    effective_enddate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_startdate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grade: Mapped[str | None] = mapped_column(String(255))
    item_code: Mapped[str | None] = mapped_column(String(255))
    unit: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int | None] = mapped_column(Integer)
    version_enddate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_startdate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vendor_material_map_id: Mapped[int | None] = mapped_column(BigInteger)
    service_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[str | None] = mapped_column(String(255))
