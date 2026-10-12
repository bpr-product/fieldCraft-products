from typing import Any, Dict, Optional
from fastapi import Request


from source.Utils import constants as const


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except Exception:
        return default

def _safe_list(obj: Any) -> list:
    return obj if isinstance(obj, list) else []


def _compact_text(x: Any) -> str:
    """Lowercase + remove whitespace/underscores/dashes for robust contains checks."""
    if x is None:
        return ""
    s = str(x).strip().lower()
    return "".join(ch for ch in s if ch.isalnum())


def _is_already_mapped_action(x: Any) -> bool:
    c = _compact_text(x)
    return (
            "alreadymapped" in c
            or "alreadyexported" in c
            or "existing" in c
            or c == "mapped"
    )


def _is_missing_required(x: Any) -> bool:
    c = _compact_text(x)
    # Keep this fairly strict; we only want true validation issues.
    return (
            "missingrequired" in c
            or "missingrequiredfield" in c
            or "missingrequiredfields" in c
            or "missingemail" in c
            or "missingphone" in c
            or "missingaddress" in c
            or "missingcity" in c
            or "missingstate" in c
            or "missingzip" in c
            or "missingcountry" in c
    )


def _is_deleted_action(x: Any) -> bool:
    c = _compact_text(x)
    return "deleted" in c or "isdeleted" in c


def _finalize_status_and_direction(
        *,
        direction: str,
        raw_status: str,
        action: str,
        message: str,
) -> tuple[str, str]:
    """Normalize into UI buckets.

    Buckets:
      - success
      - sync (already mapped/existing)
      - skipped
      - failed (missing required etc.)
      - unknown
    """

    status = raw_status or "unknown"
    final_direction = direction

    # Missing required fields are failures (even if originally marked skipped)
    if _is_missing_required(action) or _is_missing_required(message):
        return "failed", "Sync"

    # Treat "already mapped / existing" as a Sync outcome (not skipped)
    if status == "skipped" and (_is_already_mapped_action(action) or _is_already_mapped_action(message)):
        return "sync", "Sync"

    # Deleted is a real skip (keep as skipped)
    if status == "skipped" and (_is_deleted_action(action) or _is_deleted_action(message)):
        return "skipped", final_direction

    return status, final_direction


def _get_first(row: Dict[str, Any], *keys: str) -> Any:
    """Return the first non-None value for the given keys (in order)."""
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def _vendor_ids_for_actions(results: list, actions: set) -> set:
    ids = set()
    for r in _safe_list(results):
        if not isinstance(r, dict):
            continue

        action = (r.get(const.KEY_ACTION) or "").strip()
        if action not in actions:
            continue

        qid = _get_first(r, const.KEY_QBO_VENDOR_ID, const.KEY_EXTERNAL_ID)
        if qid is None:
            continue

        qid = str(qid).strip()
        if qid:
            ids.add(qid)

    return ids


def build_vendor_sync_overall(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    export_results = _safe_list((export_summary or {}).get("results"))
    import_results = _safe_list((import_summary or {}).get("results"))

    already_actions = {"alreadyMapped"}
    export_already = _vendor_ids_for_actions(export_results, already_actions)
    import_already = _vendor_ids_for_actions(import_results, already_actions)
    already_unique = export_already | import_already

    changed_actions = {"createdInQbo", "mappedFromQbo", "linkedExistingInFieldCraft", "createdInFieldCraft"}
    export_changed = _vendor_ids_for_actions(export_results, changed_actions)
    import_changed = _vendor_ids_for_actions(import_results, changed_actions)
    changed_unique = export_changed | import_changed

    overall = {
        "alreadyInSyncUnique": len(already_unique),
        "changedUnique": len(changed_unique),
        "skippedMissingRequiredFields": _safe_int((import_summary or {}).get("skippedMissingRequiredFields"), 0),
        "failedTotal": _safe_int((export_summary or {}).get("failed"), 0) + _safe_int((import_summary or {}).get("failed"), 0),
        "exportSkippedAlreadyMapped": _safe_int((export_summary or {}).get("skippedAlreadyMapped"), 0),
        "importSkippedAlreadyMapped": _safe_int((import_summary or {}).get("skippedAlreadyMapped"), 0),
    }
    return overall


def build_vendor_sync_overall_results(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Build a de-duplicated list of per-vendor results across export+import.

    Primary key is the QBO vendor id (qboVendorId/external_id). If missing, fall back
    to FIELDCRAFT vendor id, then display name.

    When duplicates exist:
      failed > success > skipped > unknown
    """

    def _norm_status(val: Any) -> str:
        s = str(val or "").strip().lower()
        if s in ("success", "skipped", "failed"):
            return s
        return "unknown"

    def _rank(st: str) -> int:
        return {"failed": 3, "success": 2, "skipped": 1, "unknown": 0}.get(st, 0)

    def _vendor_key(r: Dict[str, Any]) -> str:
        qid = _get_first(r, const.KEY_QBO_VENDOR_ID, const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        qid = str(qid or "").strip()
        if qid:
            return f"qbo:{qid}"

        gid = _get_first(r, const.KEY_FIELDCRAFT_VENDOR_ID, const.KEY_VENDOR_ID, const.KEY_VENDOR_ID_CAMEL)
        gid = str(gid or "").strip()
        if gid:
            return f"fieldcraft:{gid}"

        name = r.get(const.KEY_DISPLAY_NAME) or r.get(const.KEY_VENDOR_NAME) or r.get(const.KEY_NAME) or ""
        name = str(name).strip().lower()
        if name:
            return f"name:{name}"

        return f"row:{id(r)}"

    def _score_row(r: Dict[str, Any]) -> int:
        # Prefer rows that have more fields populated.
        score = 0
        for k in (const.KEY_EMAIL, const.KEY_PHONE, const.KEY_DISPLAY_NAME, const.KEY_REASON, const.KEY_ERROR):
            v = r.get(k)
            if v is not None and str(v).strip() != "":
                score += 1
        return score

    def _project(r: Dict[str, Any], sources: set, picked_source: str) -> Dict[str, Any]:
        qid = _get_first(r, const.KEY_QBO_VENDOR_ID, const.KEY_EXTERNAL_ID)
        gid = _get_first(r, const.KEY_FIELDCRAFT_VENDOR_ID, const.KEY_VENDOR_ID)

        status = _norm_status(r.get(const.KEY_STATUS))

        raw_action = (r.get(const.KEY_ACTION) or "").strip()
        action = raw_action or None

        msg = (
                r.get(const.KEY_REASON)
                or r.get(const.KEY_ERROR)
                or r.get(const.KEY_MESSAGE)
                or ""
        )

        # Direction rules (UX):
        # - For skipped/alreadyMapped we show "Sync" (represents already-in-sync across directions)
        # - For success/failed we show the phase that produced the chosen row: Import or Export
        direction = "Overall"
        if picked_source in ("Export", "Import"):
            direction = picked_source

        status_final, direction_final = _finalize_status_and_direction(
            direction=direction,
            raw_status=status,
            action=raw_action,
            message=str(msg or ""),
        )

        return {
            "direction": direction_final,
            "status": status_final,
            "action": action,
            "reason": r.get(const.KEY_REASON),
            "error": r.get(const.KEY_ERROR),
            "message": r.get(const.KEY_MESSAGE),
            "displayName": r.get(const.KEY_DISPLAY_NAME) or r.get(const.KEY_VENDOR_NAME) or r.get(const.KEY_NAME),
            "email": r.get(const.KEY_EMAIL),
            "phone": r.get(const.KEY_PHONE),
            "qboVendorId": str(qid) if qid is not None else None,
            "fieldcraftVendorId": gid,
            "syncToken": r.get(const.KEY_SYNC_TOKEN) or r.get(const.KEY_SYNC_TOKEN_SNAKE),
            "sources": sorted(list(sources)),
        }

    dedup: Dict[str, Dict[str, Any]] = {}

    for source_name, summary in (("Export", export_summary), ("Import", import_summary)):
        for r in _safe_list((summary or {}).get("results")):
            if not isinstance(r, dict):
                continue
            st = _norm_status(r.get(const.KEY_STATUS))
            key = _vendor_key(r)
            if key not in dedup:
                dedup[key] = {"row": r, "status": st, "sources": {source_name}, "picked_source": source_name}
                continue

            cur = dedup[key]
            cur["sources"].add(source_name)

            if _rank(st) > _rank(cur.get("status")):
                cur["row"] = r
                cur["status"] = st
                cur["picked_source"] = source_name
            elif _rank(st) == _rank(cur.get("status")):
                if _score_row(r) > _score_row(cur.get("row") or {}):
                    cur["row"] = r
                    cur["status"] = st
                    cur["picked_source"] = source_name

    results = [_project(v["row"], v["sources"], v.get("picked_source") or "Overall") for v in dedup.values()]
    results.sort(
        key=lambda x: (_rank(x.get(const.KEY_STATUS) or "unknown"), str(x.get(const.KEY_DISPLAY_NAME) or "").lower()),
        reverse=True,
    )

    counts = {"total": len(results), "success": 0, "sync": 0, "skipped": 0, "failed": 0, "unknown": 0}
    for r in results:
        st = _norm_status(r.get(const.KEY_STATUS))
        if st in counts:
            counts[st] += 1
        else:
            counts["unknown"] += 1

    return {"results": results, "counts": counts}


# -------------------------
# Bills (export + import)
# -------------------------

def _bill_ids_for_actions(results: list, actions: set) -> set:
    ids = set()
    for r in _safe_list(results):
        if not isinstance(r, dict):
            continue
        action = (r.get(const.KEY_ACTION) or "").strip()
        if action not in actions:
            continue
        qid = _get_first(r, const.KEY_QBO_BILL_ID, "qboBillId", const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        qid = str(qid or "").strip()
        if qid:
            ids.add(qid)
            continue
        gid = _get_first(r, const.KEY_FIELDCRAFT_BILL_ID, "receiptId", "billId")
        gid = str(gid or "").strip()
        if gid:
            ids.add(f"fieldcraft:{gid}")
    return ids


def build_bill_sync_overall(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    export_results = _safe_list((export_summary or {}).get("results"))
    import_results = _safe_list((import_summary or {}).get("results"))

    already_actions = {"alreadyMapped", "alreadyCurrent"}
    export_already = _bill_ids_for_actions(export_results, already_actions)
    import_already = _bill_ids_for_actions(import_results, already_actions)
    already_unique = export_already | import_already

    changed_actions = {"created in QBO", "createdInQbo", "updated in QBO", "mapped from QBO", "linked existing in FIELDCRAFT", "linked existing pending push", "created in FIELDCRAFT", "updated in FIELDCRAFT", "resolved import failure"}
    export_changed = _bill_ids_for_actions(export_results, changed_actions)
    import_changed = _bill_ids_for_actions(import_results, changed_actions)
    changed_unique = export_changed | import_changed

    return {
        "alreadyInSyncUnique": len(already_unique),
        "changedUnique": len(changed_unique),
        "failedTotal": _safe_int((export_summary or {}).get("failed"), 0) + _safe_int((import_summary or {}).get("failed"), 0),
        "exportSkippedAlreadyMapped": _safe_int((export_summary or {}).get("skippedAlreadyMapped"), 0),
        "importSkippedAlreadyMapped": _safe_int((import_summary or {}).get("skippedAlreadyMapped"), 0),
    }


def build_bill_sync_overall_results(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    def _norm_status(val: Any) -> str:
        s = str(val or "").strip().lower()
        if s in ("success", "skipped", "failed"):
            return s
        return "unknown"

    def _rank(st: str) -> int:
        return {"failed": 3, "success": 2, "skipped": 1, "unknown": 0}.get(st, 0)

    def _bill_key(r: Dict[str, Any]) -> str:
        qid = _get_first(r, const.KEY_QBO_BILL_ID, "qboBillId", const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        qid = str(qid or "").strip()
        if qid:
            return f"qbo:{qid}"
        gid = _get_first(r, const.KEY_FIELDCRAFT_BILL_ID, "receiptId", "billId")
        gid = str(gid or "").strip()
        if gid:
            return f"fieldcraft:{gid}"
        name = r.get("displayName") or r.get(const.KEY_NAME) or ""
        name = str(name).strip().lower()
        if name:
            return f"name:{name}"
        return f"row:{id(r)}"

    def _score_row(r: Dict[str, Any]) -> int:
        score = 0
        for k in ("displayName", const.KEY_REASON, const.KEY_ERROR, const.KEY_QBO_BILL_ID, const.KEY_FIELDCRAFT_BILL_ID):
            v = r.get(k)
            if v is not None and str(v).strip() != "":
                score += 1
        return score

    def _project(r: Dict[str, Any], sources: set, picked_source: str) -> Dict[str, Any]:
        qid = _get_first(r, const.KEY_QBO_BILL_ID, "qboBillId", const.KEY_EXTERNAL_ID)
        gid = _get_first(r, const.KEY_FIELDCRAFT_BILL_ID, "receiptId", "billId")
        status = _norm_status(r.get(const.KEY_STATUS))
        raw_action = (r.get(const.KEY_ACTION) or "").strip()
        msg = r.get(const.KEY_REASON) or r.get(const.KEY_ERROR) or r.get(const.KEY_MESSAGE) or ""
        direction = picked_source if picked_source in ("Export", "Import") else "Overall"
        status_final, direction_final = _finalize_status_and_direction(direction=direction, raw_status=status, action=raw_action, message=str(msg or ""))
        return {
            "direction": direction_final,
            "status": status_final,
            "action": raw_action or None,
            "reason": r.get(const.KEY_REASON),
            "error": r.get(const.KEY_ERROR),
            "message": r.get(const.KEY_MESSAGE),
            "displayName": r.get("displayName") or r.get(const.KEY_NAME),
            "qboBillId": str(qid) if qid is not None else None,
            "fieldcraftBillId": gid,
            "syncToken": r.get(const.KEY_SYNC_TOKEN) or r.get(const.KEY_SYNC_TOKEN_SNAKE),
            "sources": sorted(list(sources)),
        }

    dedup: Dict[str, Dict[str, Any]] = {}
    for source_name, summary in (("Export", export_summary), ("Import", import_summary)):
        for r in _safe_list((summary or {}).get("results")):
            if not isinstance(r, dict):
                continue
            st = _norm_status(r.get(const.KEY_STATUS))
            key = _bill_key(r)
            if key not in dedup:
                dedup[key] = {"row": r, "status": st, "sources": {source_name}, "picked_source": source_name}
                continue
            cur = dedup[key]
            cur["sources"].add(source_name)
            if _rank(st) > _rank(cur.get("status")):
                cur["row"] = r
                cur["status"] = st
                cur["picked_source"] = source_name
            elif _rank(st) == _rank(cur.get("status")):
                if _score_row(r) > _score_row(cur.get("row") or {}):
                    cur["row"] = r
                    cur["status"] = st
                    cur["picked_source"] = source_name

    results = [_project(v["row"], v["sources"], v.get("picked_source") or "Overall") for v in dedup.values()]
    results.sort(key=lambda x: (_rank(x.get(const.KEY_STATUS) or "unknown"), str(x.get("displayName") or "").lower()), reverse=True)

    counts = {"total": len(results), "success": 0, "sync": 0, "skipped": 0, "failed": 0, "unknown": 0}
    for r in results:
        st = _norm_status(r.get(const.KEY_STATUS))
        if st in counts:
            counts[st] += 1
        else:
            counts["unknown"] += 1
    return {"results": results, "counts": counts}


# -------------------------
# Payments (export + import)
# -------------------------

def _payment_ids_for_actions(results: list, actions: set) -> set:
    ids = set()
    for r in _safe_list(results):
        if not isinstance(r, dict):
            continue

        act = _normalize_action(r.get(const.KEY_ACTION))
        if act not in actions:
            continue

        # Prefer QBO payment id; fall back to FIELDCRAFT payment id
        qid = _get_first(r, const.KEY_QBO_PAYMENT_ID, "qboPaymentId", const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        qid = str(qid or "").strip()
        if qid:
            ids.add(qid)
            continue

        gid = _get_first(r, const.KEY_FIELDCRAFT_PAYMENT_ID, "paymentId", "payment_receives_id")
        gid = str(gid or "").strip()
        if gid:
            ids.add(f"fieldcraft:{gid}")

    return ids


def build_payment_sync_overall(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    export_results = _safe_list((export_summary or {}).get("results"))
    import_results = _safe_list((import_summary or {}).get("results"))

    already_actions = {"alreadyMapped"}
    export_already = _payment_ids_for_actions(export_results, already_actions)
    import_already = _payment_ids_for_actions(import_results, already_actions)
    already_unique = export_already | import_already

    # "changed" for payments = created in either system
    changed_actions = {"createdInQbo", "createdInFieldCraft", "linkedExistingInFieldCraft", "mappedFromQbo"}
    export_changed = _payment_ids_for_actions(export_results, changed_actions)
    import_changed = _payment_ids_for_actions(import_results, changed_actions)
    changed_unique = export_changed | import_changed

    overall = {
        "alreadyInSyncUnique": len(already_unique),
        "changedUnique": len(changed_unique),
        "skippedMissingRequiredFields": _safe_int((import_summary or {}).get("skippedMissingRequired"), 0),
        "failedTotal": _safe_int((export_summary or {}).get("failed"), 0) + _safe_int((import_summary or {}).get("failed"), 0),
        "exportSkippedAlreadyMapped": _safe_int((export_summary or {}).get("skippedAlreadyMapped"), 0),
        "importSkippedAlreadyMapped": _safe_int((import_summary or {}).get("skippedAlreadyMapped"), 0),
    }
    return overall


def build_payment_sync_overall_results(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Build a de-duplicated list of per-payment results across export+import.

    Primary key preference:
      1) qboPaymentId
      2) fieldcraftPaymentId/payment_receives_id
      3) payment ref / private note

    When duplicates exist:
      failed > success > skipped > unknown
    """

    def _norm_status(val: Any) -> str:
        s = str(val or "").strip().lower()
        if s in ("success", "skipped", "failed"):
            return s
        return "unknown"

    def _rank(st: str) -> int:
        return {"failed": 3, "success": 2, "skipped": 1, "unknown": 0}.get(st, 0)

    def _pay_key(r: Dict[str, Any]) -> str:
        qid = _get_first(r, const.KEY_QBO_PAYMENT_ID, "qboPaymentId", const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        qid = str(qid or "").strip()
        if qid:
            return f"qbo:{qid}"

        gid = _get_first(r, const.KEY_FIELDCRAFT_PAYMENT_ID, "paymentId", "payment_receives_id")
        gid = str(gid or "").strip()
        if gid:
            return f"fieldcraft:{gid}"

        ref = _get_first(r, "paymentRef", "paymentRefNum", "ref", "reference", "privateNote")
        ref = str(ref or "").strip().lower()
        if ref:
            return f"ref:{ref}"

        return f"row:{id(r)}"

    def _score_row(r: Dict[str, Any]) -> int:
        score = 0
        for k in ("customerUid", "paymentId", "qboPaymentId", "amount", "txnDate", const.KEY_REASON, const.KEY_ERROR):
            v = r.get(k)
            if v is not None and str(v).strip() != "":
                score += 1
        return score

    def _project(r: Dict[str, Any], sources: set, picked_source: str) -> Dict[str, Any]:
        status = _norm_status(r.get(const.KEY_STATUS))
        action = _normalize_action(r.get(const.KEY_ACTION))

        msg = (
                r.get(const.KEY_REASON)
                or r.get(const.KEY_ERROR)
                or r.get(const.KEY_MESSAGE)
                or ""
        )

        msg = (
                r.get(const.KEY_REASON)
                or r.get(const.KEY_ERROR)
                or r.get(const.KEY_MESSAGE)
                or ""
        )

        # Customer is shown as a single Sync flow in UI.
        direction = "Sync"

        status, direction = _finalize_status_and_direction(
            direction=direction,
            raw_status=status,
            action=action or "",
            message=str(msg or ""),
        )

        status, direction = _finalize_status_and_direction(
            direction=direction,
            raw_status=status,
            action=action,
            message=str(msg or ""),
        )

        return {
            "direction": direction,
            "status": status,
            "action": action or None,
            "reason": r.get(const.KEY_REASON),
            "error": r.get(const.KEY_ERROR),
            "message": r.get(const.KEY_MESSAGE),

            "customerUid": r.get("customerUid") or r.get("customer_uid"),
            "paymentId": r.get("paymentId") or r.get("payment_receives_id"),
            "qboPaymentId": r.get("qboPaymentId") or r.get(const.KEY_QBO_PAYMENT_ID),
            "amount": r.get("amount") or r.get("totalAmount"),
            "txnDate": r.get("txnDate") or r.get("txn_date"),
            "paymentRef": r.get("paymentRef") or r.get("paymentRefNum") or r.get("ref"),
            "sources": sorted(list(sources)),
        }

    dedup: Dict[str, Dict[str, Any]] = {}

    for source_name, summary in (("Export", export_summary), ("Import", import_summary)):
        for r in _safe_list((summary or {}).get("results")):
            if not isinstance(r, dict):
                continue
            st = _norm_status(r.get(const.KEY_STATUS))
            key = _pay_key(r)
            if key not in dedup:
                dedup[key] = {"row": r, "status": st, "sources": {source_name}, "picked_source": source_name}
                continue

            cur = dedup[key]
            cur["sources"].add(source_name)

            if _rank(st) > _rank(cur.get("status")):
                cur["row"] = r
                cur["status"] = st
                cur["picked_source"] = source_name
            elif _rank(st) == _rank(cur.get("status")):
                if _score_row(r) > _score_row(cur.get("row") or {}):
                    cur["row"] = r
                    cur["status"] = st
                    cur["picked_source"] = source_name

    results = [_project(v["row"], v["sources"], v.get("picked_source") or "Overall") for v in dedup.values()]
    results.sort(key=lambda x: (_rank(x.get(const.KEY_STATUS) or "unknown"), str(x.get("customerUid") or "")), reverse=True)

    counts = {"total": len(results), "success": 0, "sync": 0, "skipped": 0, "failed": 0, "unknown": 0}
    for r in results:
        st = _norm_status(r.get(const.KEY_STATUS))
        if st in counts:
            counts[st] += 1
        else:
            counts["unknown"] += 1

    return {"results": results, "counts": counts}



def build_account_sync_overall(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Overall counters for Chart of Accounts (export + import).

    Mirrors the customer/vendor overall structure so UI can reuse the same patterns.
    """
    export_results = _safe_list((export_summary or {}).get("results"))
    import_results = _safe_list((import_summary or {}).get("results"))

    already_actions = {"alreadyMapped"}
    export_already = _account_ids_for_actions(export_results, already_actions)
    import_already = _account_ids_for_actions(import_results, already_actions)
    already_unique = export_already | import_already

    changed_actions = {"createdInQbo", "mappedFromQbo", "linkedExistingInFieldCraft", "createdInFieldCraft"}
    export_changed = _account_ids_for_actions(export_results, changed_actions)
    import_changed = _account_ids_for_actions(import_results, changed_actions)
    changed_unique = export_changed | import_changed

    overall = {
        "alreadyInSyncUnique": len(already_unique),
        "changedUnique": len(changed_unique),
        "failedTotal": _safe_int((export_summary or {}).get("failed"), 0) + _safe_int((import_summary or {}).get("failed"), 0),
        "exportSkippedAlreadyMapped": _safe_int((export_summary or {}).get("skippedAlreadyMapped"), 0),
        "importSkippedAlreadyMapped": _safe_int((import_summary or {}).get("skippedAlreadyMapped"), 0),
    }
    return overall


def _account_ids_for_actions(results: list, actions: set) -> set:
    ids = set()
    for r in _safe_list(results):
        if not isinstance(r, dict):
            continue
        act = _normalize_action(r.get(const.KEY_ACTION))
        if act not in actions:
            continue

        qid = _get_first(r, const.KEY_QBO_ACCOUNT_ID, const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        qid = str(qid or "").strip()
        if qid:
            ids.add(qid)
    return ids


def build_account_sync_overall_results(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Build a de-duplicated list of per-account results across export+import.

    Primary key is qboAccountId; fallback to fieldcraftAccountId; fallback to name+acctNo.

    When duplicates exist (same account appears in both export and import):
      failed > success > skipped > unknown

    Important: some account import paths historically report already-mapped rows as
    status=success with action=skippedAlreadyMapped. For overall UI we normalize
    that to status=skipped and action=alreadyMapped.
    """

    def _norm_status(val: Any) -> str:
        s = str(val or "").strip().lower()
        if s in ("success", "skipped", "failed"):
            return s
        return "unknown"

    def _rank(st: str) -> int:
        return {"failed": 3, "success": 2, "skipped": 1, "unknown": 0}.get(st, 0)

    def _account_key(r: Dict[str, Any]) -> str:
        qid = _get_first(r, const.KEY_QBO_ACCOUNT_ID, const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        qid = str(qid or "").strip()
        if qid:
            return f"qbo:{qid}"

        gid = _get_first(r, const.KEY_FIELDCRAFT_ACCOUNT_ID, "fieldcraftAccountId", "accountId")
        gid = str(gid or "").strip()
        if gid:
            return f"fieldcraft:{gid}"

        name = str(r.get(const.KEY_NAME) or r.get("name") or "").strip().lower()
        acct_no = str(r.get("accountNo") or r.get("acctNum") or "").strip().lower()
        if name or acct_no:
            return f"name:{name}|no:{acct_no}"

        return f"row:{id(r)}"

    def _score_row(r: Dict[str, Any]) -> int:
        score = 0
        for k in ("name", const.KEY_NAME, "accountNo", "accountType", "detailType", const.KEY_REASON, const.KEY_ERROR):
            v = r.get(k)
            if v is not None and str(v).strip() != "":
                score += 1
        return score

    def _project(r: Dict[str, Any], sources: set, picked_source: str) -> Dict[str, Any]:
        qid = _get_first(r, const.KEY_QBO_ACCOUNT_ID, const.KEY_EXTERNAL_ID)
        gid = _get_first(r, const.KEY_FIELDCRAFT_ACCOUNT_ID, "fieldcraftAccountId", "accountId")

        status = _norm_status(r.get(const.KEY_STATUS))
        action = _normalize_action(r.get(const.KEY_ACTION))

        if _is_already_mapped_action(action):
            action = "alreadyMapped"
            if status == "success":
                status = "skipped"

        direction = "Sync"

        return {
            "direction": direction,
            "status": status,
            "action": action or None,
            "reason": r.get(const.KEY_REASON),
            "error": r.get(const.KEY_ERROR),
            "message": r.get(const.KEY_MESSAGE),
            "name": r.get("name") or r.get(const.KEY_NAME),
            "accountNo": r.get("accountNo") or r.get("acctNum") or None,
            "accountType": r.get("accountType") or None,
            "detailType": r.get("detailType") or None,
            "qboAccountId": str(qid) if qid is not None else None,
            "fieldcraftAccountId": gid,
            "syncToken": r.get(const.KEY_SYNC_TOKEN) or r.get(const.KEY_SYNC_TOKEN_SNAKE),
            "sources": sorted(list(sources)),
        }

    dedup: Dict[str, Dict[str, Any]] = {}

    for source_name, summary in (("Export", export_summary), ("Import", import_summary)):
        for r in _safe_list((summary or {}).get("results")):
            if not isinstance(r, dict):
                continue
            st = _norm_status(r.get(const.KEY_STATUS))
            act = _normalize_action(r.get(const.KEY_ACTION))
            if _is_already_mapped_action(act) and st == "success":
                st = "skipped"

            key = _account_key(r)
            if key not in dedup:
                dedup[key] = {"row": r, "status": st, "sources": {source_name}, "picked_source": source_name}
                continue

            cur = dedup[key]
            cur["sources"].add(source_name)

            if _rank(st) > _rank(cur.get("status")):
                cur["row"] = r
                cur["status"] = st
                cur["picked_source"] = source_name
            elif _rank(st) == _rank(cur.get("status")):
                if _score_row(r) > _score_row(cur.get("row") or {}):
                    cur["row"] = r
                    cur["status"] = st
                    cur["picked_source"] = source_name

    results = [_project(v["row"], v["sources"], v.get("picked_source") or "Overall") for v in dedup.values()]
    results.sort(
        key=lambda x: (_rank(x.get(const.KEY_STATUS) or "unknown"), str(x.get("name") or "").lower()),
        reverse=True,
    )

    counts = {"total": len(results), "success": 0, "sync": 0, "skipped": 0, "failed": 0, "unknown": 0}
    for r in results:
        st = _norm_status(r.get(const.KEY_STATUS))
        if st in counts:
            counts[st] += 1
        else:
            counts["unknown"] += 1

    return {"results": results, "counts": counts}


def _normalize_action(action: Any) -> Optional[str]:
    if action is None:
        return None
    a = str(action).strip()
    if not a:
        return None

    # Normalize common action variants into stable identifiers.
    # NOTE: These are part of the public contract with the UI, so keep them
    # conservative and backwards compatible.
    if _is_already_mapped_action(a):
        return "alreadyMapped"

    c = _compact_text(a)
    if "createdinfieldcraft" in c:
        return "createdInFieldCraft"
    if "createdinqbo" in c:
        return "createdInQbo"
    if "mappedfromqbo" in c:
        return "mappedFromQbo"
    if "linkedexistinginfieldcraft" in c:
        return "linkedExistingInFieldCraft"

    return a


def _customer_ids_for_actions(results: list, actions: set) -> set:
    ids = set()
    for r in _safe_list(results):
        if not isinstance(r, dict):
            continue

        act = _normalize_action(r.get(const.KEY_ACTION))
        if act not in actions:
            continue

        qid = _get_first(r, const.KEY_QBO_CUSTOMER_ID, const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        if qid is None:
            continue

        qid = str(qid).strip()
        if qid:
            ids.add(qid)

    return ids


def build_customer_sync_overall(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    export_results = _safe_list((export_summary or {}).get("results"))
    import_results = _safe_list((import_summary or {}).get("results"))

    already_actions = {"alreadyMapped"}
    export_already = _customer_ids_for_actions(export_results, already_actions)
    import_already = _customer_ids_for_actions(import_results, already_actions)
    already_unique = export_already | import_already

    changed_actions = {"createdInQbo", "mappedFromQbo", "linkedExistingInFieldCraft", "createdInFieldCraft"}
    export_changed = _customer_ids_for_actions(export_results, changed_actions)
    import_changed = _customer_ids_for_actions(import_results, changed_actions)
    changed_unique = export_changed | import_changed

    skipped_missing_required = _safe_int((import_summary or {}).get("skippedMissingRequired"), 0)
    if skipped_missing_required == 0:
        skipped_missing_required = _safe_int((import_summary or {}).get("skippedMissingRequiredFields"), 0)

    overall = {
        "alreadyInSyncUnique": len(already_unique),
        "changedUnique": len(changed_unique),
        "skippedMissingRequiredFields": skipped_missing_required,
        "failedTotal": _safe_int((export_summary or {}).get("failed"), 0) + _safe_int((import_summary or {}).get("failed"), 0),
        "exportSkippedAlreadyMapped": _safe_int((export_summary or {}).get("skippedAlreadyMapped"), 0),
        "importSkippedAlreadyMapped": _safe_int((import_summary or {}).get("skippedAlreadyMapped"), 0),
    }
    return overall


def build_customer_sync_overall_results(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Build a de-duplicated list of per-customer results across export+import.

    Primary key is the QBO customer id (qboCustomerId/external_id). If missing, fall back
    to FIELDCRAFT customer id, then display name.

    When duplicates exist:
      failed > success > skipped > unknown
    """

    def _norm_status(val: Any) -> str:
        s = str(val or "").strip().lower()
        if s in ("success", "skipped", "failed"):
            return s
        return "unknown"

    def _rank(st: str) -> int:
        return {"failed": 3, "success": 2, "skipped": 1, "unknown": 0}.get(st, 0)

    def _customer_key(r: Dict[str, Any]) -> str:
        qid = _get_first(r, const.KEY_QBO_CUSTOMER_ID, const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        qid = str(qid or "").strip()
        if qid:
            return f"qbo:{qid}"

        gid = _get_first(r, const.KEY_FIELDCRAFT_CUSTOMER_ID, const.KEY_CUSTOMER_ID, const.KEY_CUSTOMER_ID_CAMEL)
        gid = str(gid or "").strip()
        if gid:
            return f"fieldcraft:{gid}"

        name = r.get(const.KEY_DISPLAY_NAME) or r.get(const.KEY_CUSTOMER_NAME) or r.get(const.KEY_NAME) or ""
        name = str(name).strip().lower()
        if name:
            return f"name:{name}"

        return f"row:{id(r)}"

    def _score_row(r: Dict[str, Any]) -> int:
        score = 0
        for k in (const.KEY_EMAIL, const.KEY_PHONE, const.KEY_DISPLAY_NAME, const.KEY_REASON, const.KEY_ERROR):
            v = r.get(k)
            if v is not None and str(v).strip() != "":
                score += 1
        return score

    def _project(r: Dict[str, Any], sources: set, picked_source: str) -> Dict[str, Any]:
        qid = _get_first(r, const.KEY_QBO_CUSTOMER_ID, const.KEY_EXTERNAL_ID)
        gid = _get_first(r, const.KEY_FIELDCRAFT_CUSTOMER_ID, const.KEY_CUSTOMER_ID)

        status = _norm_status(r.get(const.KEY_STATUS))

        raw_action = (r.get(const.KEY_ACTION) or "").strip()
        action = _normalize_action(raw_action)

        msg = (
                r.get(const.KEY_REASON)
                or r.get(const.KEY_ERROR)
                or r.get(const.KEY_MESSAGE)
                or ""
        )

        direction = "Overall"
        if picked_source in ("Export", "Import"):
            direction = picked_source

        status_final, direction_final = _finalize_status_and_direction(
            direction=direction,
            raw_status=status,
            action=raw_action,
            message=str(msg or ""),
        )

        return {
            "direction": direction_final,
            "status": status_final,
            "action": action or None,
            "reason": r.get(const.KEY_REASON),
            "error": r.get(const.KEY_ERROR),
            "message": r.get(const.KEY_MESSAGE),
            "displayName": r.get(const.KEY_DISPLAY_NAME) or r.get(const.KEY_CUSTOMER_NAME) or r.get(const.KEY_NAME),
            "email": r.get(const.KEY_EMAIL),
            "phone": r.get(const.KEY_PHONE),
            "qboCustomerId": str(qid) if qid is not None else None,
            "fieldcraftCustomerId": gid,
            "syncToken": r.get(const.KEY_SYNC_TOKEN) or r.get(const.KEY_SYNC_TOKEN_SNAKE),
            "sources": sorted(list(sources)),
        }

    dedup: Dict[str, Dict[str, Any]] = {}

    for source_name, summary in (("Export", export_summary), ("Import", import_summary)):
        for r in _safe_list((summary or {}).get("results")):
            if not isinstance(r, dict):
                continue
            st = _norm_status(r.get(const.KEY_STATUS))
            key = _customer_key(r)
            if key not in dedup:
                dedup[key] = {"row": r, "status": st, "sources": {source_name}, "picked_source": source_name}
                continue

            cur = dedup[key]
            cur["sources"].add(source_name)

            if _rank(st) > _rank(cur.get("status")):
                cur["row"] = r
                cur["status"] = st
                cur["picked_source"] = source_name
            elif _rank(st) == _rank(cur.get("status")):
                if _score_row(r) > _score_row(cur.get("row") or {}):
                    cur["row"] = r
                    cur["status"] = st
                    cur["picked_source"] = source_name

    results = [_project(v["row"], v["sources"], v.get("picked_source") or "Overall") for v in dedup.values()]
    results.sort(
        key=lambda x: (_rank(x.get(const.KEY_STATUS) or "unknown"), str(x.get(const.KEY_DISPLAY_NAME) or "").lower()),
        reverse=True,
    )

    counts = {"total": len(results), "success": 0, "sync": 0, "skipped": 0, "failed": 0, "unknown": 0}
    for r in results:
        st = str(r.get(const.KEY_STATUS) or "").strip().lower()
        if st == "success":
            counts["success"] += 1
        elif st == "failed":
            counts["failed"] += 1
        elif st == "skipped":
            counts["skipped"] += 1
        elif st == "sync":
            counts["sync"] += 1
        else:
            counts["unknown"] += 1

    return {"results": results, "counts": counts}


# -------------------------
# Employees (export + import)
# -------------------------

def _employee_ids_for_actions(results: list, actions: set) -> set:
    ids = set()
    for r in _safe_list(results):
        if not isinstance(r, dict):
            continue

        action = (r.get(const.KEY_ACTION) or "").strip()
        if action not in actions:
            continue

        qid = _get_first(r, const.KEY_QBO_EMPLOYEE_ID, const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        qid = str(qid or "").strip()
        if qid:
            ids.add(qid)
            continue

        gid = _get_first(r, const.KEY_FIELDCRAFT_EMPLOYEE_ID, "employee_id", "employeeId", "user_account_id", "userAccountId")
        gid = str(gid or "").strip()
        if gid:
            ids.add(f"fieldcraft:{gid}")

    return ids


def build_employee_sync_overall(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    export_results = _safe_list((export_summary or {}).get("results"))
    import_results = _safe_list((import_summary or {}).get("results"))

    already_actions = {"alreadyMapped", "alreadyCurrent"}
    already_unique = (
        _employee_ids_for_actions(export_results, already_actions)
        | _employee_ids_for_actions(import_results, already_actions)
    )

    changed_actions = {
        "createdInQbo",
        "updatedInQbo",
        "mappedFromQbo",
        "created in QBO",
        "updated in QBO",
        "mapped from QBO",
        "linked existing in FIELDCRAFT",
        "linked existing pending push",
        "created in FIELDCRAFT",
        "updated in FIELDCRAFT",
        "resolved import failure",
    }
    changed_unique = (
        _employee_ids_for_actions(export_results, changed_actions)
        | _employee_ids_for_actions(import_results, changed_actions)
    )

    return {
        "alreadyInSyncUnique": len(already_unique),
        "changedUnique": len(changed_unique),
        "skippedMissingRequiredFields": _safe_int((import_summary or {}).get("skippedMissingRequiredFields"), 0),
        "failedTotal": _safe_int((export_summary or {}).get("failed"), 0) + _safe_int((import_summary or {}).get("failed"), 0),
        "exportSkippedAlreadyMapped": _safe_int((export_summary or {}).get("skippedAlreadyMapped"), 0),
        "importSkippedAlreadyMapped": _safe_int((import_summary or {}).get("skippedAlreadyMapped"), 0),
    }


def build_employee_sync_overall_results(export_summary: Dict[str, Any], import_summary: Dict[str, Any]) -> Dict[str, Any]:
    """Build a de-duplicated list of per-employee results across export+import."""

    def _norm_status(val: Any) -> str:
        s = str(val or "").strip().lower()
        if s in ("success", "sync", "skipped", "failed"):
            return s
        return "unknown"

    def _rank(st: str) -> int:
        return {"failed": 4, "success": 3, "sync": 2, "skipped": 1, "unknown": 0}.get(st, 0)

    def _employee_key(r: Dict[str, Any]) -> str:
        qid = _get_first(r, const.KEY_QBO_EMPLOYEE_ID, const.KEY_EXTERNAL_ID, const.KEY_EXTERNAL_ID_CAMEL)
        qid = str(qid or "").strip()
        if qid:
            return f"qbo:{qid}"

        gid = _get_first(r, const.KEY_FIELDCRAFT_EMPLOYEE_ID, "employee_id", "employeeId", "user_account_id", "userAccountId")
        gid = str(gid or "").strip()
        if gid:
            return f"fieldcraft:{gid}"

        email = str(r.get(const.KEY_EMAIL) or "").strip().lower()
        if email:
            return f"email:{email}"

        name = r.get(const.KEY_DISPLAY_NAME) or r.get(const.KEY_NAME) or ""
        name = str(name).strip().lower()
        if name:
            return f"name:{name}"

        return f"row:{id(r)}"

    def _score_row(r: Dict[str, Any]) -> int:
        score = 0
        for k in (const.KEY_EMAIL, const.KEY_PHONE, const.KEY_DISPLAY_NAME, const.KEY_NAME, const.KEY_REASON, const.KEY_ERROR):
            v = r.get(k)
            if v is not None and str(v).strip() != "":
                score += 1
        return score

    def _project(r: Dict[str, Any], sources: set, picked_source: str) -> Dict[str, Any]:
        qid = _get_first(r, const.KEY_QBO_EMPLOYEE_ID, const.KEY_EXTERNAL_ID)
        gid = _get_first(r, const.KEY_FIELDCRAFT_EMPLOYEE_ID, "employee_id", "employeeId", "user_account_id", "userAccountId")

        raw_status = _norm_status(r.get(const.KEY_STATUS))
        raw_action = (r.get(const.KEY_ACTION) or "").strip()
        msg = r.get(const.KEY_REASON) or r.get(const.KEY_ERROR) or r.get(const.KEY_MESSAGE) or ""

        direction = picked_source if picked_source in ("Export", "Import") else "Overall"
        status_final, direction_final = _finalize_status_and_direction(
            direction=direction,
            raw_status=raw_status,
            action=raw_action,
            message=str(msg or ""),
        )

        return {
            "direction": direction_final,
            "status": status_final,
            "action": raw_action or None,
            "reason": r.get(const.KEY_REASON),
            "error": r.get(const.KEY_ERROR),
            "message": r.get(const.KEY_MESSAGE),
            "displayName": r.get(const.KEY_DISPLAY_NAME) or r.get(const.KEY_NAME),
            "email": r.get(const.KEY_EMAIL),
            "phone": r.get(const.KEY_PHONE),
            "qboEmployeeId": str(qid) if qid is not None else None,
            "fieldcraftEmployeeId": gid,
            "syncToken": r.get(const.KEY_SYNC_TOKEN) or r.get(const.KEY_SYNC_TOKEN_SNAKE),
            "sources": sorted(list(sources)),
        }

    dedup: Dict[str, Dict[str, Any]] = {}

    for source_name, summary in (("Export", export_summary), ("Import", import_summary)):
        for r in _safe_list((summary or {}).get("results")):
            if not isinstance(r, dict):
                continue

            st = _norm_status(r.get(const.KEY_STATUS))
            key = _employee_key(r)
            if key not in dedup:
                dedup[key] = {"row": r, "status": st, "sources": {source_name}, "picked_source": source_name}
                continue

            cur = dedup[key]
            cur["sources"].add(source_name)
            if _rank(st) > _rank(cur.get("status")):
                cur["row"] = r
                cur["status"] = st
                cur["picked_source"] = source_name
            elif _rank(st) == _rank(cur.get("status")) and _score_row(r) > _score_row(cur.get("row") or {}):
                cur["row"] = r
                cur["status"] = st
                cur["picked_source"] = source_name

    results = [_project(v["row"], v["sources"], v.get("picked_source") or "Overall") for v in dedup.values()]
    results.sort(
        key=lambda x: (_rank(x.get(const.KEY_STATUS) or "unknown"), str(x.get(const.KEY_DISPLAY_NAME) or "").lower()),
        reverse=True,
    )

    counts = {"total": len(results), "success": 0, "sync": 0, "skipped": 0, "failed": 0, "unknown": 0}
    for r in results:
        st = _norm_status(r.get(const.KEY_STATUS))
        if st in counts:
            counts[st] += 1
        else:
            counts["unknown"] += 1

    return {"results": results, "counts": counts}

def get_bearer_token(request: Request) -> Optional[str]:
    """Return bearer token from the request header, if present."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    return token or None
