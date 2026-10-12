"""Embedding providers for the Gold layer (BPE, RefProject, AI Assistant).

Today supports:
  - "stub"          — deterministic SHA-256 vector. Dev only.
  - "azure_openai"  — Azure OpenAI embedding deployment. Production default.

Selection is via the EMBEDDING_PROVIDER env var (default: stub).

Azure OpenAI config (env):
  AZURE_OPENAI_ENDPOINT       e.g. https://<resource>.openai.azure.com
  AZURE_OPENAI_DEPLOYMENT     deployment name for the embedding model
  AZURE_OPENAI_API_VERSION    default "2024-02-15-preview"
  AZURE_OPENAI_API_KEY        fallback when KEY_VAULT_URL is unset
  EMBEDDING_DIM               must match the deployed model
                              (text-embedding-3-small=1536, ada-002=1536, 3-large=3072)
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Protocol, Sequence

log = logging.getLogger(__name__)

DEFAULT_API_VERSION = "2024-02-15-preview"


class EmbeddingProvider(Protocol):
    name: str
    version: str
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class StubEmbeddingProvider:
    """Deterministic SHA-256 derived vector. Dev only."""
    name = "stub-sha256"
    version = "0.0.1"

    def __init__(self, dim: int):
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, seed: str) -> list[float]:
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            h = hashlib.sha256(f"{seed}|{counter}".encode()).digest()
            for i in range(0, len(h), 4):
                if len(out) >= self.dim:
                    break
                n = int.from_bytes(h[i:i + 4], "big", signed=False)
                out.append((n / 0xFFFFFFFF) * 2.0 - 1.0)
            counter += 1
        return out


class AzureOpenAIEmbeddingProvider:
    """Batched Azure OpenAI embeddings with retry on transient errors."""

    def __init__(
        self,
        endpoint: str,
        deployment: str,
        api_version: str,
        api_key: str,
        dim: int,
        max_retries: int = 5,
        backoff_s: float = 1.5,
    ):
        try:
            from openai import AzureOpenAI
        except ImportError as exc:
            raise RuntimeError("openai>=1.0 must be installed for AzureOpenAIEmbeddingProvider") from exc

        self._client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        self.deployment = deployment
        self.name = f"azure-openai:{deployment}"
        self.version = api_version
        self.dim = dim
        self._max_retries = max_retries
        self._backoff_s = backoff_s

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        from openai import APIConnectionError, APIError, RateLimitError

        delay = self._backoff_s
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._client.embeddings.create(
                    model=self.deployment,
                    input=list(texts),
                )
                return [d.embedding for d in resp.data]
            except (RateLimitError, APIConnectionError) as exc:
                if attempt == self._max_retries:
                    log.error("embedding call failed after %d attempts: %s", attempt, exc)
                    raise
                log.warning(
                    "embedding transient error attempt %d/%d: %s — retry in %.1fs",
                    attempt, self._max_retries, exc, delay,
                )
                time.sleep(delay)
                delay *= 2
            except APIError as exc:
                log.error("embedding non-retryable APIError: %s", exc)
                raise
        raise RuntimeError("unreachable")  # pragma: no cover


def build_provider() -> EmbeddingProvider:
    """Construct the provider selected by EMBEDDING_PROVIDER."""
    kind = os.environ.get("EMBEDDING_PROVIDER", "stub").lower()
    dim = int(os.environ.get("EMBEDDING_DIM", "1536"))

    if kind == "stub":
        return StubEmbeddingProvider(dim=dim)

    if kind == "azure_openai":
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION)
        if not endpoint or not deployment:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT are required")
        from secret_resolver import get_azure_openai_key
        return AzureOpenAIEmbeddingProvider(
            endpoint=endpoint,
            deployment=deployment,
            api_version=api_version,
            api_key=get_azure_openai_key(),
            dim=dim,
        )

    raise RuntimeError(f"unknown EMBEDDING_PROVIDER: {kind!r}")
