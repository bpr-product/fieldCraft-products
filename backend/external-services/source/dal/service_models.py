from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class ServiceMasterModel(Base):
    __tablename__ = "service_master"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    invalid: Mapped[bool | None] = mapped_column(Boolean)


class RoleModel(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    active: Mapped[bool | None] = mapped_column(Boolean)
    builtin: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoleExternalMapModel(Base):
    __tablename__ = "role_external_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    service_id: Mapped[int | None] = mapped_column(BigInteger)
    external_service_id: Mapped[str | None] = mapped_column(String(128))
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    external_display_name: Mapped[str | None] = mapped_column(String(255))
    sync_status: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int | None] = mapped_column(Integer, default=1)
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_token: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(Text)
    additional_info: Mapped[str | None] = mapped_column(Text)
    external_time_customfield_item_id: Mapped[str | None] = mapped_column(String(128))
