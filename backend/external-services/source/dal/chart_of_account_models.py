from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class ChartOfAccountModel(Base):
    __tablename__ = "chart_of_account"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_type: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool | None] = mapped_column(Boolean)
    builtin: Mapped[bool | None] = mapped_column(Boolean)
    code: Mapped[str | None] = mapped_column(String(255))
    code_provider: Mapped[str | None] = mapped_column(String(255))
    currency_code: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(String(255))
    account_no: Mapped[str | None] = mapped_column(String(255))
    detail_type: Mapped[str | None] = mapped_column(String(255))
    external_id: Mapped[str | None] = mapped_column(String(255))
    sync_token: Mapped[str | None] = mapped_column(String(255))
    service_id: Mapped[int | None] = mapped_column(BigInteger)
    external_acc_id: Mapped[str | None] = mapped_column(String(255))
    invalid: Mapped[bool | None] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
