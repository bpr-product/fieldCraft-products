from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from source.dal.db_session import tenant_session_scope
from source.dal.document_models import DocumentModel


class DocumentRepository:

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def create_bill_document(
        self,
        tenant_id: str,
        *,
        name: str,
        path: str,
        mime_type: str,
        size: int,
        document_uid: str,
        project_id: Optional[int] = None,
        created_by: str = "quickbooks",
    ) -> int:
        """Insert a Bill document row and return the new document id."""
        now = self._now()
        doc = DocumentModel(
            created_at=now,
            updated_at=now,
            document_type="Bill",
            mime_type=mime_type,
            path=path,
            size=size,
            type=mime_type,
            project_id=project_id,
            invalid=False,
            status="Active",
            version=0,
            document_uid=uuid.UUID(document_uid),
            name=name,
            created_by=created_by,
            parent_path="Bill",
        )
        with tenant_session_scope(tenant_id) as session:
            session.add(doc)
            session.flush()
            doc_id = int(doc.id)
            session.commit()
        print(f"[DocumentRepository] created document id={doc_id} name={name!r} path={path!r}")
        return doc_id

    def get_document_by_id(self, tenant_id: str, document_id: int) -> Optional[Dict[str, Any]]:
        """Return document metadata by ID, or None if not found."""
        with tenant_session_scope(tenant_id) as session:
            row = session.get(DocumentModel, int(document_id))
            if not row:
                return None
            return {
                "id": int(row.id),
                "name": str(row.name or ""),
                "mime_type": str(row.mime_type or "application/octet-stream"),
                "path": str(row.path or ""),
                "size": int(row.size) if row.size else None,
                "document_type": str(row.document_type or ""),
                "project_id": int(row.project_id) if row.project_id is not None else None,
                "invalid": bool(row.invalid),
                "status": str(row.status or ""),
                "md5": str(row.md5 or ""),
                "updated_at": row.updated_at,
            }
