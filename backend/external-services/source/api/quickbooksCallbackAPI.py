"""
QuickBooks callback/webhook API.
"""

from typing import Dict
import json
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from source.Utils import constants as const
from source.Utils.webhook import (
    _extract_webhook_realm_and_changes,
    _process_qbo_webhook,
    _verify_qbo_webhook_signature,
)

QBO_CALLBACK_API = APIRouter(tags=["QuickBooks"])


@QBO_CALLBACK_API.post("/extsvc/api/v1/quickbooks/webhook")
async def quickbooks_webhook(request: Request, background_tasks: BackgroundTasks):

    raw = await request.body()

    sig = request.headers.get("x-intuit-signature") or request.headers.get("X-Intuit-Signature")

    # verify signature
    _verify_qbo_webhook_signature(raw, sig)

    payload = json.loads(raw.decode())

    realm_id, changes = _extract_webhook_realm_and_changes(payload)
    from source.dal.QuickBooksRepository import QuickBooksRepository

    qb_repo = QuickBooksRepository()

    try:
        qbo_service_id = qb_repo.getServiceIdByName("QuickBooks")
    except Exception:
        qbo_service_id = None

    match = qb_repo.findSubscriberByRealmIdAndServiceId(str(realm_id), str(qbo_service_id or "")) if qbo_service_id else None
    if not match:
        match = qb_repo.findSubscriberByRealmId(str(realm_id))
    if not match:
        return {"ok": True, "message": "No subscriber found for QuickBooks realmId", "realmIdPresent": bool(realm_id)}

    tenant_id = match["subscriber_id"]
    service_id = match["service_id"]
    
    if not realm_id:
        raise HTTPException(status_code=400, detail="Missing realmId in webhook payload")

    background_tasks.add_task(
        _process_qbo_webhook,
        tenant_id,
        service_id,
        changes
    )

    entity_counts: Dict[str, int] = {}

    for ch in (changes or []):
        k = (ch.get("name") or "UNKNOWN").strip()
        entity_counts[k] = entity_counts.get(k, 0) + 1

    return {
        "ok": True,
        "realmIdPresent": bool(realm_id),
        "tenantId": tenant_id,
        "serviceId": service_id,
        "entityCounts": entity_counts,
        "changesReceived": len(changes or []),
        "scheduled": True,
    }