from __future__ import annotations

import json
import logging
import mimetypes
import os
from typing import Any, Dict, List, Optional

from azure.storage.blob import BlobServiceClient

from source.Utils import QuickBooksErrorHandler as qbo_err
from source.dal.DocumentRepository import DocumentRepository
from source.quickbooks.qbo_query_utils import escape_qbo_literal


TENANT_BLOB_CONNECTION_STRING_ENV = "TENANT_BLOB_CONNECTION_STRING"
logger = logging.getLogger(__name__)


class QBOInvoiceAttachmentService:
    """Transfer a FIELDCRAFT invoice document from tenant blob storage to a QBO Invoice."""

    MAX_UPLOAD_BYTES = 100 * 1024 * 1024
    DOCUMENT_NOTE_PREFIX = "FIELDCRAFT invoice document id:"

    def __init__(self) -> None:
        self.document_repo = DocumentRepository()

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _headers(access_token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

    def _document_marker(self, document_id: int) -> str:
        return f"{self.DOCUMENT_NOTE_PREFIX} {int(document_id)}"

    def _load_document(
        self,
        *,
        tenant_id: str,
        document_id: int,
        invoice_project_id: Optional[int],
    ) -> Dict[str, Any]:
        document = self.document_repo.get_document_by_id(tenant_id, int(document_id))
        if not document:
            raise ValueError(f"Invoice document {document_id} was not found in FIELDCRAFT.")
        if document.get("invalid") is True:
            raise ValueError(f"Invoice document {document_id} is invalid in FIELDCRAFT.")

        status = self._clean(document.get("status")).lower()
        if status and status != "active":
            raise ValueError(
                f"Invoice document {document_id} is not active in FIELDCRAFT (status={status})."
            )

        blob_path = self._clean(document.get("path"))
        if not blob_path:
            raise ValueError(f"Invoice document {document_id} has no blob path.")

        document_project_id = document.get("project_id")
        if (
            invoice_project_id is not None
            and document_project_id is not None
            and int(document_project_id) != int(invoice_project_id)
        ):
            raise ValueError(
                f"Invoice document {document_id} belongs to FIELDCRAFT project {document_project_id}, "
                f"not invoice project {invoice_project_id}."
            )
        return document

    def _download_blob(self, *, tenant_id: str, blob_path: str) -> bytes:
        connection_string = self._clean(os.getenv(TENANT_BLOB_CONNECTION_STRING_ENV))
        if not connection_string:
            raise RuntimeError(
                f"Azure Blob connection string is not configured. "
                f"Set {TENANT_BLOB_CONNECTION_STRING_ENV}."
            )

        client = BlobServiceClient.from_connection_string(connection_string)
        blob_client = client.get_blob_client(container=tenant_id, blob=blob_path)
        file_bytes = blob_client.download_blob().readall()
        if not file_bytes:
            raise ValueError(f"Invoice blob {blob_path!r} is empty.")
        if len(file_bytes) > self.MAX_UPLOAD_BYTES:
            raise ValueError(
                f"Invoice attachment is {len(file_bytes)} bytes; QuickBooks uploads are limited "
                f"to {self.MAX_UPLOAD_BYTES} bytes per request."
            )
        return file_bytes

    def _get_invoice_attachments(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        invoice_id: str,
    ) -> List[Dict[str, Any]]:
        escaped_invoice_id = escape_qbo_literal(str(invoice_id))
        query = (
            "select * from Attachable"
            " where AttachableRef.EntityRef.Type = 'Invoice'"
            f" and AttachableRef.EntityRef.value = '{escaped_invoice_id}'"
        )
        response = qbo_err.qbo_get(
            f"{base_url}/v3/company/{realm_id}/query",
            headers=self._headers(access_token),
            params={"query": query},
            timeout=30,
        )
        qbo_err.raise_for_qbo_response(
            response,
            fallback_message="QuickBooks invoice attachment lookup failed.",
        )
        rows = (qbo_err.safe_json(response).get("QueryResponse") or {}).get("Attachable") or []
        if isinstance(rows, dict):
            return [rows]
        return rows if isinstance(rows, list) else []

    def _find_existing_document_attachment(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        invoice_id: str,
        document_id: int,
    ) -> Optional[Dict[str, Any]]:
        marker = self._document_marker(document_id)
        for attachment in self._get_invoice_attachments(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            invoice_id=invoice_id,
        ):
            if self._clean(attachment.get("Note")) == marker:
                return attachment
        return None

    def _upload_to_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        invoice_id: str,
        document_id: int,
        file_bytes: bytes,
        file_name: str,
        content_type: str,
    ) -> Dict[str, Any]:
        metadata = {
            "AttachableRef": [
                {
                    "EntityRef": {
                        "type": "Invoice",
                        "value": str(invoice_id),
                    }
                }
            ],
            "FileName": file_name,
            "ContentType": content_type,
            "Note": self._document_marker(document_id),
        }
        files = [
            (
                "file_metadata_01",
                (None, json.dumps(metadata), "application/json; charset=UTF-8"),
            ),
            ("file_content_01", (file_name, file_bytes, content_type)),
        ]
        response = qbo_err.qbo_post(
            f"{base_url}/v3/company/{realm_id}/upload",
            headers=self._headers(access_token),
            files=files,
            timeout=60,
        )
        qbo_err.raise_for_qbo_response(
            response,
            success_statuses=(200, 201),
            fallback_message="QuickBooks invoice attachment upload failed.",
        )
        payload = qbo_err.safe_json(response)
        responses = payload.get("AttachableResponse") or []
        if isinstance(responses, dict):
            responses = [responses]
        for row in responses if isinstance(responses, list) else []:
            if isinstance(row, dict) and isinstance(row.get("Attachable"), dict):
                return row["Attachable"]
        raise ValueError("QuickBooks attachment upload returned no usable Attachable record.")

    def get_invoice_document_bytes(
        self,
        *,
        tenant_id: str,
        document_id: int,
        invoice_project_id: Optional[int] = None,
    ) -> tuple[bytes, str, str]:
        """Return the validated FIELDCRAFT Invoice document for authenticated UI preview/download."""
        document = self._load_document(
            tenant_id=tenant_id,
            document_id=int(document_id),
            invoice_project_id=invoice_project_id,
        )
        blob_path = self._clean(document.get("path"))
        file_name = os.path.basename(self._clean(document.get("name")) or blob_path)
        if not file_name:
            raise ValueError(f"Invoice document {document_id} has no usable filename.")
        content_type = (
            self._clean(document.get("mime_type"))
            or mimetypes.guess_type(file_name)[0]
            or "application/octet-stream"
        )
        return self._download_blob(tenant_id=tenant_id, blob_path=blob_path), file_name, content_type

    def attach_invoice_document(
        self,
        *,
        tenant_id: str,
        access_token: str,
        base_url: str,
        realm_id: str,
        invoice_id: str,
        document_id: int,
        invoice_project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Attach one FIELDCRAFT document to an existing QBO Invoice without duplicating retries."""
        document = self._load_document(
            tenant_id=tenant_id,
            document_id=int(document_id),
            invoice_project_id=invoice_project_id,
        )
        existing = self._find_existing_document_attachment(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            invoice_id=invoice_id,
            document_id=int(document_id),
        )
        if existing:
            return {
                "status": "already_attached",
                "documentId": int(document_id),
                "attachableId": self._clean(existing.get("Id")) or None,
                "fileName": self._clean(existing.get("FileName")) or self._clean(document.get("name")) or None,
            }

        blob_path = self._clean(document.get("path"))
        file_name = os.path.basename(self._clean(document.get("name")) or blob_path)
        if not file_name:
            raise ValueError(f"Invoice document {document_id} has no usable filename.")
        content_type = (
            self._clean(document.get("mime_type"))
            or mimetypes.guess_type(file_name)[0]
            or "application/octet-stream"
        )
        file_bytes = self._download_blob(tenant_id=tenant_id, blob_path=blob_path)
        uploaded = self._upload_to_qbo(
            access_token=access_token,
            base_url=base_url,
            realm_id=realm_id,
            invoice_id=invoice_id,
            document_id=int(document_id),
            file_bytes=file_bytes,
            file_name=file_name,
            content_type=content_type,
        )
        logger.info(
            "QuickBooks invoice attachment uploaded | tenant=%s qbo_invoice_id=%s document_id=%s file=%r",
            tenant_id,
            invoice_id,
            document_id,
            file_name,
        )
        return {
            "status": "uploaded",
            "documentId": int(document_id),
            "attachableId": self._clean(uploaded.get("Id")) or None,
            "fileName": file_name,
        }
