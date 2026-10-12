from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class UserAccountModel(Base):
    __tablename__ = "user_account"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    active: Mapped[bool | None] = mapped_column(Boolean)
    alternate_mobile_no: Mapped[str | None] = mapped_column(String(64))
    avatar: Mapped[str | None] = mapped_column(Text)
    birthdate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted: Mapped[bool | None] = mapped_column(Boolean)
    email: Mapped[str | None] = mapped_column(String(255))
    eula_accepted: Mapped[bool | None] = mapped_column(Boolean)
    firstname: Mapped[str | None] = mapped_column(String(255))
    gender: Mapped[str | None] = mapped_column(String(64))
    lastname: Mapped[str | None] = mapped_column(String(255))
    middlename: Mapped[str | None] = mapped_column(String(255))
    mobile_no: Mapped[str | None] = mapped_column(String(64))
    object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalid: Mapped[bool | None] = mapped_column(Boolean)
    schema_version: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_id: Mapped[int | None] = mapped_column(BigInteger)
    service_id: Mapped[int | None] = mapped_column(BigInteger)


class UserAccountExternalMapModel(Base):
    __tablename__ = "user_account_external_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_account_id: Mapped[int | None] = mapped_column(BigInteger)
    tenant_id: Mapped[str | None] = mapped_column(String(255))
    service_id: Mapped[int | None] = mapped_column(BigInteger)
    external_employee_id: Mapped[str | None] = mapped_column(String(128))
    external_vendor_id: Mapped[str | None] = mapped_column(String(128))
    external_entity_type: Mapped[str | None] = mapped_column(String(32))
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    external_display_name: Mapped[str | None] = mapped_column(String(255))
    external_time_user_id: Mapped[str | None] = mapped_column(String(128))
    sync_status: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int | None] = mapped_column(Integer, default=1)
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_account_uuid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    sync_token: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(Text)
