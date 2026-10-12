"""Client for the local Qdrant vector store (eden-qdrant, docker-compose.local.yml),
used by RETRIEVAL_BACKEND=qdrant to persist route embeddings instead of holding
them in an in-process numpy array (see navigation_agent.py's top_candidates).

At this scale (~55 routes) an in-memory array is genuinely faster — no
network round-trip — which is why "tfidf" and "semantic" (in-memory) stay the
defaults. Qdrant earns its keep once retrieval covers more than the route
registry (e.g. indexing projects/documents for future Finance/Schedule
agents), where re-embedding everything at every process start stops being
free and persistence/filtering start to matter.
"""

import logging
import os
import uuid

from qdrant_client import QdrantClient as _QdrantClient
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

logger = logging.getLogger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "eden_routes")
# Separate collection from routes: project names are per-tenant and change
# over time (routes are a static registry re-indexed wholesale at startup),
# so entity points are deleted/re-upserted per tenant rather than the whole
# collection being recreated each time.
QDRANT_ENTITY_COLLECTION = os.getenv("QDRANT_ENTITY_COLLECTION", "eden_project_names")


class QdrantVectorStore:
    def __init__(
        self,
        url: str | None = None,
        collection: str | None = None,
        entity_collection: str | None = None,
        timeout: float = 10.0,
    ):
        self._url = url or QDRANT_URL
        self._collection = collection or QDRANT_COLLECTION
        self._entity_collection = entity_collection or QDRANT_ENTITY_COLLECTION
        self._client = _QdrantClient(url=self._url, timeout=timeout)

    def is_reachable(self) -> bool:
        try:
            self._client.get_collections()
            return True
        except Exception as e:
            logger.warning(f"Qdrant unreachable: {e}")
            return False

    def index_routes(self, route_ids: list[str], vectors) -> None:
        """(Re)create the collection and upsert one point per route, id-keyed
        by the route's position (Qdrant point ids must be int/UUID, so the
        route's own string id is stored as payload, not the point id).
        Called once at startup — recreating is cheap at ~55 routes and
        guarantees the collection always matches the current registry
        (stale entries from a previous run/registry version never linger)."""
        dim = len(vectors[0])
        # delete-then-create rather than the deprecated recreate_collection.
        if self._client.collection_exists(self._collection):
            self._client.delete_collection(self._collection)
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
        )
        self._client.upsert(
            collection_name=self._collection,
            points=[
                qmodels.PointStruct(id=i, vector=vec.tolist() if hasattr(vec, "tolist") else list(vec),
                                     payload={"route_id": route_id})
                for i, (route_id, vec) in enumerate(zip(route_ids, vectors))
            ],
        )

    def search(self, query_vector, k: int) -> list[tuple[str, float]]:
        """Returns (route_id, score) pairs, highest similarity first."""
        vec = query_vector.tolist() if hasattr(query_vector, "tolist") else list(query_vector)
        try:
            # query_points is the current API; .search was deprecated in
            # qdrant-client 1.10+.
            hits = self._client.query_points(
                collection_name=self._collection, query=vec, limit=k
            ).points
        except UnexpectedResponse as e:
            logger.error(f"Qdrant search failed: {e}")
            return []
        return [(h.payload["route_id"], float(h.score)) for h in hits]

    def index_projects(
        self, tenant_id: str, project_ids: list[str], project_names: list[str], vectors
    ) -> None:
        """Upserts one point per project name, scoped by a tenantId field in
        each point's payload. Unlike index_routes, the collection is never
        wholesale-recreated -- other tenants' points must survive -- so this
        deletes only this tenant's existing points first (keeps repeated
        calls, e.g. across separate resolve_project() calls, idempotent
        instead of accumulating stale duplicates as project sets change)."""
        dim = len(vectors[0])
        if not self._client.collection_exists(self._entity_collection):
            self._client.create_collection(
                collection_name=self._entity_collection,
                vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
            )
        self._client.delete(
            collection_name=self._entity_collection,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(key="tenantId", match=qmodels.MatchValue(value=tenant_id))]
                )
            ),
        )
        self._client.upsert(
            collection_name=self._entity_collection,
            points=[
                qmodels.PointStruct(
                    # Point ids must be int/UUID -- deterministic UUID5 from
                    # (tenant, project) so re-indexing the same project
                    # overwrites its own point rather than creating a
                    # duplicate if the delete-then-upsert ever races.
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{tenant_id}:{project_id}")),
                    vector=vec.tolist() if hasattr(vec, "tolist") else list(vec),
                    payload={"tenantId": tenant_id, "project_id": project_id, "project_name": project_name},
                )
                for project_id, project_name, vec in zip(project_ids, project_names, vectors)
            ],
        )

    def search_projects(self, tenant_id: str, query_vector, k: int) -> list[tuple[str, str, float]]:
        """Returns (project_id, project_name, score) triples, highest
        similarity first, filtered to one tenant's own points."""
        vec = query_vector.tolist() if hasattr(query_vector, "tolist") else list(query_vector)
        try:
            hits = self._client.query_points(
                collection_name=self._entity_collection,
                query=vec,
                limit=k,
                query_filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(key="tenantId", match=qmodels.MatchValue(value=tenant_id))]
                ),
            ).points
        except UnexpectedResponse as e:
            logger.error(f"Qdrant project search failed: {e}")
            return []
        return [(h.payload["project_id"], h.payload["project_name"], float(h.score)) for h in hits]
