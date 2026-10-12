from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from source.dal.db_session import Base

OPEN_END_AT = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


class CustomerModel(Base):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_uuid: Mapped[str] = mapped_column(String(255), nullable=False)
    firstname: Mapped[str | None] = mapped_column(String(255))
    lastname: Mapped[str | None] = mapped_column(String(255))
    middlename: Mapped[str | None] = mapped_column(String(255))
    account_no: Mapped[str | None] = mapped_column(String(255))
    birthdate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gender: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(255))
    mobile_no: Mapped[str | None] = mapped_column(String(64))
    alternate_mobile: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(255))
    eula_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    version_effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version_expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    endorsement_effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    endorsement_expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service_id: Mapped[int | None] = mapped_column(BigInteger)

    mappings: Mapped[list["CustomerExternalMapModel"]] = relationship(back_populates="customer")


class CustomerExternalMapModel(Base):
    __tablename__ = "customer_external_map"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customer.id"), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    service_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    customer_uuid: Mapped[str | None] = mapped_column(String(255))
    external_customer_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    sync_status: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)
    additional_info: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    version_effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version_expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    endorsement_effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    endorsement_expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    customer: Mapped[CustomerModel | None] = relationship(back_populates="mappings")


class CustomerTenantModel(Base):
    __tablename__ = "customer_tenant"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_uid: Mapped[str | None] = mapped_column(Text)
    customer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schema_version: Mapped[int | None] = mapped_column(Integer)
    tenant_id: Mapped[str | None] = mapped_column(String(255))
