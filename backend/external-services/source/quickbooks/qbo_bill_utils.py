from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional


def clean(value: Any) -> str:
    return str(value or "").strip()


def parse_jsonish(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def parse_item_list(raw: Any) -> List[Dict[str, Any]]:
    data = parse_jsonish(raw)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def extract_money_amount(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    match = re.search(r"\(?\s*(?:-\s*)?\$?\s*(?:-\s*)?\d[\d,]*(?:\.\d+)?\s*\)?", text)
    if not match:
        return None

    token = match.group(0).strip()
    is_negative = "-" in token or (token.startswith("(") and token.endswith(")"))
    normalized = re.sub(r"[^\d.]", "", token)
    if not normalized or normalized == ".":
        return None

    try:
        amount = float(normalized)
    except Exception:
        return None
    return -amount if is_negative else amount


def is_fieldcraft_receipt_item(item: Dict[str, Any]) -> bool:
    if item.get("DetailType"):
        return False
    has_description = bool(clean(item.get("description") or item.get("name") or item.get("productCode") or item.get("sku") or item.get("itemCode") or item.get("code")))
    has_amount = extract_money_amount(item.get("totalPrice") or item.get("amount") or item.get("totalCost")) is not None
    has_price = extract_money_amount(item.get("price") or item.get("unitPrice") or item.get("unitCost") or item.get("rate")) is not None
    return has_description and (has_amount or has_price)


def format_quantity(quantity: Optional[float]) -> str:
    if quantity is None:
        return ""
    if float(quantity).is_integer():
        return str(int(quantity))
    return f"{quantity:g}"


def build_receipt_item_description(item: Dict[str, Any]) -> str:
    """Build the description used by the single managed QuickBooks bill line."""
    material_name = clean(
        item.get("materialName")
        or item.get("material_name")
        or item.get("itemName")
        or item.get("item_name")
        or item.get("productName")
        or item.get("product_name")
        or item.get("name")
        or item.get("service")
    )
    description = clean(
        item.get("description")
        or item.get("productDescription")
        or item.get("product_description")
        or item.get("details")
    )
    product_code = clean(
        item.get("sku")
        or item.get("productCode")
        or item.get("itemCode")
        or item.get("code")
    )

    parts: List[str] = []
    if material_name:
        parts.append(material_name)
    if description and description.lower() != material_name.lower():
        parts.append(description)
    if not parts and product_code:
        parts.append(product_code)

    label = " - ".join(parts).strip()
    quantity_label = format_quantity(
        extract_money_amount(item.get("quantity") or item.get("Qty") or item.get("qty"))
    )
    if label and quantity_label:
        return f"{label} x {quantity_label}"
    return label


def build_receipt_items_summary(items: List[Dict[str, Any]]) -> str:
    descriptions = [build_receipt_item_description(item) for item in items]
    return "; ".join(description for description in descriptions if description)


def description_fingerprint(value: Any) -> str:
    """Stable, compact signature for the managed QBO line description."""
    normalized = " ".join(clean(value).split()).casefold()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def managed_bill_line_baseline(line: Dict[str, Any]) -> Dict[str, Any]:
    """Return the stable fields FIELDCRAFT owns on its summarized QuickBooks line.

    QuickBooks can add response-only/default fields to a bill line, so the
    baseline intentionally captures only fields that FIELDCRAFT explicitly sends.
    The import side compares these values to detect user edits without being
    tripped by harmless QuickBooks response decoration.
    """
    line = line or {}
    detail_type = clean(line.get("DetailType"))
    baseline: Dict[str, Any] = {"detailType": detail_type}

    if detail_type == "AccountBasedExpenseLineDetail":
        detail = line.get("AccountBasedExpenseLineDetail") or {}
        account_ref = clean((detail.get("AccountRef") or {}).get("value"))
        if account_ref:
            baseline["accountRef"] = account_ref

        customer_ref = clean((detail.get("CustomerRef") or {}).get("value"))
        if customer_ref:
            baseline["customerRef"] = customer_ref

        billable_status = clean(detail.get("BillableStatus"))
        if billable_status:
            baseline["billableStatus"] = billable_status

    project_ref = clean((line.get("ProjectRef") or {}).get("value"))
    if project_ref:
        baseline["projectRef"] = project_ref

    return baseline
