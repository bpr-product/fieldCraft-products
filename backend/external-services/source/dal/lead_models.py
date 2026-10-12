from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class LeadModel(Base):
    """SQLAlchemy mapping for the customer lead inbox table."""

    __tablename__ = "lead"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lead_uid: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    service_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    external_deal_id: Mapped[str | None] = mapped_column(String(128))
    external_contact_id: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    data: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    customer_id: Mapped[int | None] = mapped_column(BigInteger)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
