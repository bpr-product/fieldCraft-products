from __future__ import annotations

from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, Boolean, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from source.dal.db_session import Base


class TimelogModel(Base):
    __tablename__ = "timelog"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    resource_schedule_id: Mapped[int | None] = mapped_column(BigInteger)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(255))
    approval_status: Mapped[str | None] = mapped_column(String(24))
    invalid: Mapped[bool | None] = mapped_column(Boolean)
    break_times: Mapped[str | None] = mapped_column(Text)
    entry_type: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResourceScheduleModel(Base):
    __tablename__ = "resource_schedule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    resource_id: Mapped[int | None] = mapped_column(BigInteger)
    task_id: Mapped[int | None] = mapped_column(BigInteger)
    invalid: Mapped[bool | None] = mapped_column(Boolean)


class ResourceModel(Base):
    __tablename__ = "resource"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    cost: Mapped[float | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(255))
    invalid: Mapped[bool | None] = mapped_column(Boolean)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))



class TaskModel(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_uid: Mapped[str | None] = mapped_column(PGUUID(as_uuid=False))
    # Legacy task columns remain mapped for the task-based exporter. Schema-v3
    # daily queries intentionally never select them.
    task_name: Mapped[str | None] = mapped_column(Text)
    assignees: Mapped[dict | list | str | None] = mapped_column(JSONB)


class AttributeModel(Base):
    """Task attribute catalogue used by the tenant schema v3 task model."""

    __tablename__ = "attributes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    attribute_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_type: Mapped[str | None] = mapped_column(String(32))
    data_type: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int | None] = mapped_column(Integer)
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_valid: Mapped[bool | None] = mapped_column(Boolean)


class TaskAttributeModel(Base):
    """Typed, versioned values for a task in the tenant schema v3 model."""

    __tablename__ = "task_attribute"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    attribute_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data_type: Mapped[str | None] = mapped_column(String(32))
    attr_value_text: Mapped[str | None] = mapped_column(Text)
    attr_value_integer: Mapped[int | None] = mapped_column(BigInteger)
    attr_value_float: Mapped[float | None] = mapped_column(Numeric)
    attr_value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    attr_value_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attr_value_jsonb: Mapped[dict | list | str | None] = mapped_column(JSONB)
    attr_value_uuid: Mapped[str | None] = mapped_column(PGUUID(as_uuid=False))
    version: Mapped[int | None] = mapped_column(Integer)
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_valid: Mapped[bool | None] = mapped_column(Boolean)


class TaskAdditionalInfoModel(Base):
    """Transition fallback for assignee data not yet moved into task attributes."""

    __tablename__ = "task_additional_info"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int | None] = mapped_column(BigInteger)
    assignees: Mapped[list[str] | None] = mapped_column(ARRAY(String(1024)))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TimelogExternalMapModel(Base):
    __tablename__ = "timelog_external_map"
    __table_args__ = (
        Index("idx_timelog_external_map_status", "tenant_id", "service_id", "sync_status"),
        Index("idx_timelog_external_map_timelog", "tenant_id", "service_id", "timelog_id"),
        Index("idx_timelog_external_map_qbo_id", "tenant_id", "service_id", "external_time_activity_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    timelog_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    service_id: Mapped[int | None] = mapped_column(BigInteger)
    external_time_activity_id: Mapped[str | None] = mapped_column(String(128))
    external_entity_type: Mapped[str | None] = mapped_column(String(32))
    external_time_timesheet_id: Mapped[str | None] = mapped_column(String(128))
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    external_display_name: Mapped[str | None] = mapped_column(String(255))
    sync_status: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    endorsement_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_token: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)
