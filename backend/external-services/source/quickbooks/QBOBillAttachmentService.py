from __future__ import annotations

import json
import logging
import mimetypes
import os
from typing import Any, Dict, List, Optional

import requests
from azure.storage.blob import BlobServiceClient, ContentSettings
from sqlalchemy import text

from source.dal.db_session import tenant_session_scope
from source.Utils import QuickBooksErrorHandler as qbo_err
from source.quickbooks.qbo_query_utils import escape_qbo_literal


TENANT_BLOB_CONNECTION_STRING_ENV = "TENANT_BLOB_CONNECTION_STRING"
logger = logging.getLogger(__name__)


class QBOBillAttachmentService:
    """Handles bill attachment transfer between FIELDCRAFT blob storage and QuickBooks."""

    # ------------------------------------------------------------------ #
    # Export side: FIELDCRAFT blob → QBO                                         #
    # ------------------------------------------------------------------ #

    def get_document_path(self, tenant_id: str, document_id: str) -> Optional[str]:
        """Return the blob path from the documents table, or None if not found."""
        logger.debug("get_document_path started | tenant=%s document_id=%s", tenant_id, document_id)
        try:
            with tenant_session_scope(tenant_id) as session:
                row = session.execute(
                    text("SELECT path FROM documents WHERE id = :doc_id"),
                    {"doc_id": int(document_id)},
                ).fetchone()
                path = str(row[0]).strip() if row and row[0] else None
                logger.debug("get_document_path completed | document_id=%s path=%r", document_id, path)
                return path
        except Exception:
            logger.exception("get_document_path failed | document_id=%s", document_id)
            raise

    def download_blob(self, tenant_id: str, blob_path: str) -> bytes:
        """Download a file from the tenant's Azure Blob container (container = tenant_id)."""
        logger.debug("download_blob started | container=%r blob=%r", tenant_id, blob_path)
        conn_str = os.getenv(TENANT_BLOB_CONNECTION_STRING_ENV)
        if not conn_str:
            raise RuntimeError(
                f"Azure Blob connection string not configured. Set {TENANT_BLOB_CONNECTION_STRING_ENV}."
            )
        try:
            client = BlobServiceClient.from_connection_string(conn_str)
            blob_client = client.get_blob_client(container=tenant_id, blob=blob_path)
            data = blob_client.download_blob().readall()
            logger.debug("download_blob completed | bytes=%s", len(data))
            return data
        except Exception:
            logger.exception("download_blob failed | container=%r blob=%r", tenant_id, blob_path)
            raise

    def upload_to_qbo(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        bill_id: str,
        file_bytes: bytes,
        file_name: str,
        content_type: str,
    ) -> Dict[str, Any]:
        """Upload a file as an attachment to a QBO Bill via the /upload endpoint."""
        url = f"{base_url}/v3/company/{realm_id}/upload"
        logger.debug("upload_to_qbo started | bill_id=%s file=%r content_type=%r size=%s bytes", bill_id, file_name, content_type, len(file_bytes))
        metadata = {
            "AttachableRef": [{"EntityRef": {"type": "Bill", "value": str(bill_id)}}],
            "FileName": file_name,
            "ContentType": content_type,
        }
        logger.debug("upload_to_qbo metadata=%s", json.dumps(metadata))
        # QBO requires multipart with a metadata part and a file content part.
        # Passing None as the filename for the metadata part omits the filename field.
        files = [
            ("file_metadata_01", (None, json.dumps(metadata), "application/json; charset=UTF-8")),
            ("file_content_01", (file_name, file_bytes, content_type)),
        ]
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        try:
            resp = qbo_err.qbo_post(url, headers=headers, files=files, timeout=60)
            logger.debug("upload_to_qbo response status=%s", resp.status_code)
            logger.debug("upload_to_qbo response body=%r", resp.text[:500])
            qbo_err.raise_for_qbo_response(
                resp,
                success_statuses=(200, 201),
                fallback_message="QBO attachment upload failed",
            )
            result = qbo_err.safe_json(resp)
            logger.info("upload_to_qbo completed | bill_id=%s file=%r", bill_id, file_name)
            return result
        except Exception:
            logger.exception("upload_to_qbo failed | bill_id=%s file=%r", bill_id, file_name)
            raise

    def attach_bill_document(
        self,
        *,
        tenant_id: str,
        access_token: str,
        base_url: str,
        realm_id: str,
        bill_id: str,
        document_id: str,
    ) -> None:
        """Full pipeline (export): look up document path → download blob → upload to QBO."""
        logger.info("attach_bill_document started | tenant=%s bill_id=%s document_id=%s", tenant_id, bill_id, document_id)
        try:
            blob_path = self.get_document_path(tenant_id, document_id)
            if not blob_path:
                raise ValueError(f"No document path found for document_id={document_id!r}")

            file_bytes = self.download_blob(tenant_id, blob_path)
            file_name = os.path.basename(blob_path)
            content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            logger.debug("attach_bill_document file resolved | file_name=%r content_type=%r", file_name, content_type)

            self.upload_to_qbo(
                access_token=access_token,
                base_url=base_url,
                realm_id=realm_id,
                bill_id=bill_id,
                file_bytes=file_bytes,
                file_name=file_name,
                content_type=content_type,
            )
            logger.info("attach_bill_document completed | bill_id=%s document_id=%s", bill_id, document_id)
        except Exception:
            logger.exception("attach_bill_document failed | bill_id=%s document_id=%s", bill_id, document_id)
            raise

    # ------------------------------------------------------------------ #
    # Import side: QBO → FIELDCRAFT blob                                         #
    # ------------------------------------------------------------------ #

    def fetch_qbo_attachments(
        self,
        *,
        access_token: str,
        base_url: str,
        realm_id: str,
        bill_id: str,
    ) -> List[Dict[str, Any]]:
        """Query QBO for all file attachments linked to a Bill. Returns list of Attachable dicts."""
        logger.debug("fetch_qbo_attachments started | bill_id=%s realmId_present=%s", bill_id, bool(realm_id))
        escaped_bill_id = escape_qbo_literal(bill_id)
        query = (
            f"select * from Attachable"
            f" where AttachableRef.EntityRef.Type = 'Bill'"
            f" and AttachableRef.EntityRef.value = '{escaped_bill_id}'"
        )
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        url = f"{base_url}/v3/company/{realm_id}/query"
        try:
            resp = qbo_err.qbo_get(url, headers=headers, params={"query": query}, timeout=30)
            logger.debug("fetch_qbo_attachments response status=%s", resp.status_code)
            qbo_err.raise_for_qbo_response(resp, fallback_message="QBO attachments query failed")
            data = qbo_err.safe_json(resp)
            attachables = (data.get("QueryResponse") or {}).get("Attachable") or []
            attachables = attachables if isinstance(attachables, list) else []
            # Only keep file attachments — those with a TempDownloadUri
            file_attachments = [a for a in attachables if a.get("TempDownloadUri")]
            logger.debug("fetch_qbo_attachments completed | total=%s file_attachments=%s", len(attachables), len(file_attachments))
            return file_attachments
        except Exception:
            logger.exception("fetch_qbo_attachments failed | bill_id=%s", bill_id)
            raise

    def download_qbo_attachment(self, temp_download_uri: str) -> bytes:
        """Download attachment bytes from QBO's TempDownloadUri (pre-signed, no auth needed)."""
        logger.debug("download_qbo_attachment started")
        try:
            resp = requests.get(temp_download_uri, timeout=60)
            logger.debug("download_qbo_attachment response status=%s size=%s bytes", resp.status_code, len(resp.content))
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Failed to download QBO attachment: HTTP {resp.status_code} — {resp.text[:200]}"
                )
            return resp.content
        except Exception:
            logger.exception("download_qbo_attachment failed")
            raise

    def upload_to_blob(self, tenant_id: str, blob_path: str, file_bytes: bytes, content_type: Optional[str] = None) -> None:
        """Upload file bytes to the tenant's Azure Blob container (container = tenant_id)."""
        logger.debug("upload_to_blob started | container=%r blob=%r size=%s bytes content_type=%r", tenant_id, blob_path, len(file_bytes), content_type)
        conn_str = os.getenv(TENANT_BLOB_CONNECTION_STRING_ENV)
        if not conn_str:
            raise RuntimeError(
                f"Azure Blob connection string not configured. Set {TENANT_BLOB_CONNECTION_STRING_ENV}."
            )
        try:
            client = BlobServiceClient.from_connection_string(conn_str)
            blob_client = client.get_blob_client(container=tenant_id, blob=blob_path)
            blob_client.upload_blob(
                file_bytes,
                overwrite=True,
                content_settings=ContentSettings(content_type=content_type) if content_type else None,
            )
            logger.info("upload_to_blob completed | blob=%r size=%s bytes", blob_path, len(file_bytes))
        except Exception:
            logger.exception("upload_to_blob failed | container=%r blob=%r", tenant_id, blob_path)
            raise
