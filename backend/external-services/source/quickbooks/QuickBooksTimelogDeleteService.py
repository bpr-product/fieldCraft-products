from __future__ import annotations

from typing import Any, Dict

from source.Utils import constants as const
from source.quickbooks.QuickBooksTimeActivityDeleteService import (
    QuickBooksTimeActivityDeleteService,
)


class QuickBooksTimelogDeleteService:
    """Developer cleanup for FIELDCRAFT-mapped QBO Accounting TimeActivity records only."""

    def __init__(self) -> None:
        self.activity_delete = QuickBooksTimeActivityDeleteService()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def delete_all_mapped_time_activities(
        self,
        *,
        tenant_id: str,
        service_id: str,
        confirm_delete: bool,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        tenant_id = self._clean(tenant_id)
        service_id = self._clean(service_id)
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not service_id:
            raise ValueError("service_id is required")

        result = self.activity_delete.delete_all_mapped_time_activities(
            tenant_id=tenant_id,
            service_id=service_id,
            confirm_delete=confirm_delete,
            dry_run=dry_run,
        )
        result["scope"] = "FIELDCRAFT_MAPPED_QBO_TIME_ACTIVITIES_ONLY"
        if dry_run:
            result[const.KEY_MESSAGE] = "Dry run completed for mapped QuickBooks TimeActivity records."
        elif int(result.get("failed") or 0):
            result[const.KEY_MESSAGE] = (
                "Some mapped QuickBooks TimeActivity records could not be deleted."
            )
        elif int(result.get("deleted") or 0) or int(result.get("alreadyMissing") or 0):
            result[const.KEY_MESSAGE] = (
                "Mapped QuickBooks TimeActivity records were removed and queued for re-export."
            )
        else:
            result[const.KEY_MESSAGE] = "No mapped QuickBooks TimeActivity records were found."
        return result
