from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from source.dal.db_session import get_customer_session_factory
from source.dal.lead_models import LeadModel

logger = logging.getLogger(__name__)


class LeadRepository:
    """Direct lead inbox repository.

    Mirrors the Tenant API LeadService.receiveLead behavior locally so Roof Maxx
    webhooks/fetches do not need a Tenant API bearer token. Ingestion only
    creates/returns lead inbox rows; customer creation remains the tenant side's
    scheduled responsibility.
    """

    STATUS_PENDING = "PENDING"

    def receive_leads(self, lead_payloads: list[dict[str, Any]]) -> list[Optional[str]]:
        results: list[Optional[str]] = []
        for lead_payload in lead_payloads or []:
            try:
                results.append(self.receive_lead(lead_payload))
            except Exception as exc:
                logger.exception(
                    "[LeadRepository] failed to ingest lead | external_deal_id=%s error=%s",
                    (lead_payload or {}).get("externalDealId"),
                    exc,
                )
                results.append(None)
        return results

    def receive_lead(self, lead_payload: dict[str, Any]) -> str:
        normalized = self._normalize_payload(lead_payload)
        session_factory = get_customer_session_factory()
        session = session_factory()
        try:
            existing = self._find_existing(
                session,
                tenant_id=normalized["tenant_id"],
                service_id=normalized["service_id"],
                external_deal_id=normalized["external_deal_id"],
            )
            if existing:
                logger.info(
                    "[LeadRepository] duplicate lead ignored | tenant_id=%s service_id=%s external_deal_id=%s lead_uid=%s",
                    normalized["tenant_id"],
                    normalized["service_id"],
                    normalized["external_deal_id"],
                    existing.lead_uid,
                )
                return str(existing.lead_uid)

            now = datetime.now(timezone.utc)
            lead = LeadModel(
                lead_uid=uuid4(),
                tenant_id=normalized["tenant_id"],
                service_id=normalized["service_id"],
                external_deal_id=normalized["external_deal_id"],
                external_contact_id=normalized["external_contact_id"],
                first_name=normalized["first_name"],
                last_name=normalized["last_name"],
                email=normalized["email"],
                phone=normalized["phone"],
                data=normalized["data"],
                status=self.STATUS_PENDING,
                retry_count=0,
                created_at=now,
                updated_at=now,
            )
            session.add(lead)
            session.commit()
            logger.info(
                "[LeadRepository] lead received | lead_uid=%s service_id=%s tenant_id=%s external_deal_id=%s",
                lead.lead_uid,
                normalized["service_id"],
                normalized["tenant_id"],
                normalized["external_deal_id"],
            )
            return str(lead.lead_uid)
        except IntegrityError:
            session.rollback()
            existing = self._find_existing(
                session,
                tenant_id=normalized["tenant_id"],
                service_id=normalized["service_id"],
                external_deal_id=normalized["external_deal_id"],
            )
            if existing:
                return str(existing.lead_uid)
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _find_existing(
        session,
        *,
        tenant_id: str,
        service_id: int,
        external_deal_id: Optional[str],
    ) -> Optional[LeadModel]:
        if external_deal_id is None:
            return None
        stmt = select(LeadModel).where(
            LeadModel.tenant_id == tenant_id,
            LeadModel.service_id == service_id,
            LeadModel.external_deal_id == external_deal_id,
        )
        return session.execute(stmt).scalars().first()

    def _normalize_payload(self, lead_payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(lead_payload, dict):
            raise ValueError("Lead payload must be a JSON object")

        tenant_id = self._required_text(lead_payload.get("tenantId"), "tenantId")
        service_id = self._required_int(lead_payload.get("serviceId"), "serviceId")
        data = self._required_text(lead_payload.get("data"), "data")

        return {
            "tenant_id": self._truncate(tenant_id, 64),
            "service_id": service_id,
            "external_deal_id": self._optional_text(lead_payload.get("externalDealId"), 128),
            "external_contact_id": self._optional_text(lead_payload.get("externalContactId"), 128),
            "first_name": self._optional_text(lead_payload.get("firstName"), 128),
            "last_name": self._optional_text(lead_payload.get("lastName"), 128),
            "email": self._optional_text(lead_payload.get("email"), 128),
            "phone": self._optional_text(lead_payload.get("phone"), 32),
            "data": data,
        }

    @staticmethod
    def _required_text(value: Any, field_name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        return text

    @staticmethod
    def _required_int(value: Any, field_name: str) -> int:
        try:
            return int(str(value).strip())
        except Exception as exc:
            raise ValueError(f"{field_name} must be a valid number") from exc

    @classmethod
    def _optional_text(cls, value: Any, max_length: int) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        return cls._truncate(text, max_length)

    @staticmethod
    def _truncate(value: str, max_length: int) -> str:
        return value[:max_length] if max_length > 0 else value
