from __future__ import annotations

import re

from typing import Any, Dict, List, Optional, Tuple

from source.dal.ReceiptRepository import ReceiptRepository
from source.quickbooks.qbo_bill_utils import (
    clean,
    extract_money_amount,
    is_fieldcraft_receipt_item,
    parse_item_list,
    parse_jsonish,
)


class QuickBooksBillService:
    """Read-only bill operations for display and query purposes."""

    _QBO_SYNTHETIC_KEY = "_quickbooksSynthetic"

    def __init__(self) -> None:
        self.receipt_repo = ReceiptRepository()

    def get_bill_line_items(self, tenant_id: str, receipt_id: int, service_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return the bill line projection used by the QuickBooks integration view.

        FIELDCRAFT-created bills always display their stored detailed materials plus
        real QuickBooks-added lines. The summarized transport line is validated
        during import and is never substituted into this view.
        """
        receipt = self.receipt_repo.get_current_receipt_by_id(tenant_id, receipt_id)
        if not receipt:
            return []

        mapping = None
        service_value = str(service_id or "").strip()
        if service_value:
            mapping = self.receipt_repo.get_current_mapping_by_receipt_id(
                tenant_id, service_value, receipt_id
            )
            if not mapping:
                return []

        info = self.receipt_repo.parse_additional_info(receipt.get("receipt_additional_info"))
        is_qbo_origin = self._clean(receipt.get("created_by")).lower() == "quickbooks"
        is_stock_debit_bill = self._is_stock_debit_bill(receipt)
        is_stock_credit_bill = self._is_stock_credit_bill(receipt)
        is_stock_movement = is_stock_debit_bill or is_stock_credit_bill
        items_raw = self._parse_item_list(info.get("receiptInfo"))
        if not items_raw:
            items_raw = self._parse_item_list(info.get("lineItems"))

        line_items: List[Dict[str, Any]] = []

        for item in items_raw:
            if bool(item.get(self._QBO_SYNTHETIC_KEY)):
                # Old versions stored a synthetic delta row. It was never a
                # real bill item, so keep it out of the current view.
                continue
            detail_type = self._clean(item.get("DetailType"))

            if detail_type == "AccountBasedExpenseLineDetail":
                amount = self._extract_money_amount(item.get("Amount"))
                if amount is None or amount == 0:
                    continue
                description, quantity = self._split_description_quantity_suffix(
                    self._clean(item.get("Description")),
                    allow_multi_item_summary=is_qbo_origin,
                )
                unit_price = self._calculate_unit_price(amount, quantity)
                line_items.append({
                    "description": description or None,
                    "productCode": None,
                    "quantity": quantity,
                    "unitPrice": unit_price,
                    "amount": amount,
                    "lineKind": (
                        "category"
                        if is_qbo_origin
                        else ("item" if quantity is not None or unit_price is not None else "category")
                    ),
                })
            elif detail_type == "ItemBasedExpenseLineDetail":
                detail = item.get("ItemBasedExpenseLineDetail") or {}
                amount = self._extract_money_amount(item.get("Amount"))
                if amount is None or amount == 0:
                    continue
                item_ref = detail.get("ItemRef") or {}
                item_name = self._clean(item_ref.get("name"))
                line_desc = self._clean(item.get("Description"))
                description = line_desc or item_name or None
                product_code = item_name if (line_desc and item_name and line_desc != item_name) else None
                quantity = self._extract_money_amount(detail.get("Qty"))
                unit_price = self._extract_money_amount(detail.get("UnitPrice"))
                line_items.append({
                    "description": description,
                    "productCode": product_code,
                    "quantity": quantity,
                    "unitPrice": unit_price,
                    "amount": amount,
                    "lineKind": "item",
                })
            elif self._is_fieldcraft_receipt_item(item):
                quantity = self._extract_money_amount(item.get("quantity") or item.get("Qty"))

                if is_stock_movement:
                    unit_price = self._extract_money_amount(item.get("unitCost"))
                    amount = self._extract_money_amount(item.get("amount"))
                    if amount is None and quantity is not None and unit_price is not None:
                        amount = round(quantity * unit_price, 2)
                else:
                    amount = self._extract_money_amount(
                        item.get("totalPrice") or item.get("amount") or item.get("Amount")
                    )
                    unit_price = self._extract_money_amount(
                        item.get("price")
                        or item.get("unitPrice")
                        or item.get("UnitPrice")
                        or item.get("unitCost")
                        or item.get("rate")
                    )
                    if amount is None and quantity is not None and unit_price is not None:
                        amount = round(quantity * unit_price, 2)

                if amount is None or amount == 0:
                    continue
                line_items.append({
                    "description": self._build_material_description(item) or None,
                    "productCode": self._clean(item.get("productCode") or item.get("sku") or item.get("itemCode") or item.get("code")) or None,
                    "quantity": quantity,
                    "unitPrice": unit_price,
                    "amount": amount,
                    "lineKind": "item",
                })

        return line_items

    def get_bill_document_info(self, tenant_id: str, receipt_id: int, service_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return document metadata for a bill's attachment, or None if none attached."""
        receipt = self._get_scoped_receipt(tenant_id, receipt_id, service_id)
        if not receipt or not receipt.get("document_id"):
            return None
        from source.dal.DocumentRepository import DocumentRepository
        doc = DocumentRepository().get_document_by_id(tenant_id, int(receipt["document_id"]))
        return doc

    def get_bill_document_bytes(self, tenant_id: str, receipt_id: int, service_id: Optional[str] = None) -> Tuple[bytes, str, str]:
        """Return (file_bytes, file_name, mime_type) for a bill's attachment.
        Raises FileNotFoundError if no document is attached or the blob is missing.
        """
        receipt = self._get_scoped_receipt(tenant_id, receipt_id, service_id)
        if not receipt or not receipt.get("document_id"):
            raise FileNotFoundError(f"No document attached to receipt {receipt_id}")
        from source.dal.DocumentRepository import DocumentRepository
        from source.quickbooks.QBOBillAttachmentService import QBOBillAttachmentService
        doc = DocumentRepository().get_document_by_id(tenant_id, int(receipt["document_id"]))
        if not doc or not doc.get("path"):
            raise FileNotFoundError(f"Document {receipt['document_id']} not found or has no path")
        file_bytes = QBOBillAttachmentService().download_blob(tenant_id, doc["path"])
        return file_bytes, doc.get("name") or "document", doc.get("mime_type") or "application/octet-stream"

    def _split_description_quantity_suffix(
        self,
        description: Optional[str],
        *,
        allow_multi_item_summary: bool = False,
    ) -> Tuple[Optional[str], Optional[float]]:
        """Move a trailing "x <qty>" suffix from the description into Qty.

        Stock bills are exported as AccountBasedExpenseLineDetail rows because
        they post to Inventory Asset. QuickBooks stores those rows without Qty
        and UnitPrice fields, so the exported description can contain the FIELDCRAFT
        quantity suffix, for example "Material - Starter strips x 500". For
        display, split that suffix back into the Quantity column and derive the
        Unit Cost from Amount / Quantity.
        """
        clean_description = self._clean(description)
        if not clean_description:
            return None, None

        # A FIELDCRAFT bill is exported as one summary line such as
        # "Item A x 12; Item B x 15". Never treat the last material's quantity
        # as the quantity of the whole summarized bill line.
        if ";" in clean_description and not allow_multi_item_summary:
            return clean_description, None

        match = re.search(r"\s+[xX×]\s*([+-]?\d[\d,]*(?:\.\d+)?)\s*$", clean_description)
        if not match:
            return clean_description, None

        quantity = self._extract_money_amount(match.group(1))
        if quantity is None or quantity == 0:
            return clean_description, None

        description_without_quantity = clean_description[:match.start()].strip()
        return description_without_quantity or clean_description, quantity

    @staticmethod
    def _calculate_unit_price(amount: Optional[float], quantity: Optional[float]) -> Optional[float]:
        if amount is None or quantity is None or quantity == 0:
            return None
        return round(float(amount) / float(quantity), 6)

    def _build_material_description(self, item: Dict[str, Any]) -> str:
        material_name = self._clean(
            item.get("materialName")
            or item.get("material_name")
            or item.get("itemName")
            or item.get("item_name")
            or item.get("productName")
            or item.get("product_name")
            or item.get("name")
            or item.get("service")
        )
        description = self._clean(
            item.get("description")
            or item.get("productDescription")
            or item.get("product_description")
            or item.get("details")
        )
        product_code = self._clean(item.get("productCode") or item.get("sku") or item.get("itemCode") or item.get("code"))

        parts: List[str] = []
        if material_name:
            parts.append(material_name)
        if description and description.lower() != material_name.lower():
            parts.append(description)
        if not parts and product_code:
            parts.append(product_code)
        return " - ".join(parts).strip()

    def _is_stock_debit_bill(self, receipt: Dict[str, Any]) -> bool:
        if bool(receipt.get("is_stock_debit_bill")):
            return True
        receipt_type = self._clean(receipt.get("receipt_type") or receipt.get("name"))
        return receipt_type.replace(" ", "").lower() == "stockdebitbill"

    def _is_stock_credit_bill(self, receipt: Dict[str, Any]) -> bool:
        if bool(receipt.get("is_stock_credit_bill")):
            return True
        receipt_type = self._clean(receipt.get("receipt_type") or receipt.get("name"))
        return receipt_type.replace(" ", "").lower() == "stockcreditbill"

    def _get_scoped_receipt(self, tenant_id: str, receipt_id: int, service_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        receipt = self.receipt_repo.get_current_receipt_by_id(tenant_id, receipt_id)
        if not receipt:
            return None

        service_id = str(service_id or "").strip()
        if not service_id:
            return receipt

        mapping = self.receipt_repo.get_current_mapping_by_receipt_id(tenant_id, service_id, receipt_id)
        return receipt if mapping else None

    # Shared parsing helpers. Kept as class attributes so existing call sites stay unchanged.
    _parse_item_list = staticmethod(parse_item_list)
    _parse_jsonish = staticmethod(parse_jsonish)
    _is_fieldcraft_receipt_item = staticmethod(is_fieldcraft_receipt_item)
    _extract_money_amount = staticmethod(extract_money_amount)
    _clean = staticmethod(clean)
