"""Unit tests for Gold embedding providers."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# StubEmbeddingProvider
# ---------------------------------------------------------------------------

@pytest.mark.bpe
class TestStubEmbeddingProvider:
    def _provider(self, dim=4):
        from gold.src.embeddings import StubEmbeddingProvider
        return StubEmbeddingProvider(dim=dim)

    def test_embed_returns_correct_length(self):
        p = self._provider(dim=8)
        results = p.embed(["hello world"])
        assert len(results) == 1
        assert len(results[0]) == 8

    def test_embed_multiple_texts(self):
        p = self._provider(dim=4)
        results = p.embed(["text1", "text2", "text3"])
        assert len(results) == 3
        assert all(len(v) == 4 for v in results)

    def test_embed_is_deterministic(self):
        p = self._provider(dim=4)
        a = p.embed(["roofing trade estimate"])
        b = p.embed(["roofing trade estimate"])
        assert a == b

    def test_different_texts_produce_different_embeddings(self):
        p = self._provider(dim=16)
        a = p.embed(["text A"])
        b = p.embed(["text B"])
        assert a != b

    def test_values_in_minus_one_plus_one_range(self):
        p = self._provider(dim=32)
        vectors = p.embed(["test"])
        for val in vectors[0]:
            assert -1.0 <= val <= 1.0

    def test_empty_string(self):
        p = self._provider(dim=4)
        results = p.embed([""])
        assert len(results) == 1
        assert len(results[0]) == 4

    def test_dim_attribute(self):
        p = self._provider(dim=1536)
        assert p.dim == 1536

    def test_name_and_version(self):
        p = self._provider(dim=4)
        assert p.name == "stub-sha256"
        assert p.version == "0.0.1"

    def test_empty_texts_list(self):
        p = self._provider(dim=4)
        results = p.embed([])
        assert results == []


# ---------------------------------------------------------------------------
# build_provider
# ---------------------------------------------------------------------------

@pytest.mark.bpe
class TestBuildProvider:
    def test_stub_provider_default(self):
        from gold.src.embeddings import build_provider, StubEmbeddingProvider
        with patch.dict("os.environ", {"EMBEDDING_PROVIDER": "stub", "EMBEDDING_DIM": "4"}):
            p = build_provider()
        assert isinstance(p, StubEmbeddingProvider)
        assert p.dim == 4

    def test_stub_respects_embedding_dim(self):
        from gold.src.embeddings import build_provider, StubEmbeddingProvider
        with patch.dict("os.environ", {"EMBEDDING_PROVIDER": "stub", "EMBEDDING_DIM": "16"}):
            p = build_provider()
        assert p.dim == 16

    def test_unknown_provider_raises(self):
        from gold.src.embeddings import build_provider
        with patch.dict("os.environ", {"EMBEDDING_PROVIDER": "unknown_xyz"}):
            with pytest.raises(RuntimeError, match="unknown EMBEDDING_PROVIDER"):
                build_provider()

    def test_azure_openai_missing_endpoint_raises(self):
        from gold.src.embeddings import build_provider
        env = {
            "EMBEDDING_PROVIDER": "azure_openai",
            "EMBEDDING_DIM": "1536",
        }
        env.pop("AZURE_OPENAI_ENDPOINT", None)
        env.pop("AZURE_OPENAI_DEPLOYMENT", None)
        with patch.dict("os.environ", env, clear=False):
            import os
            os.environ.pop("AZURE_OPENAI_ENDPOINT", None)
            os.environ.pop("AZURE_OPENAI_DEPLOYMENT", None)
            with pytest.raises(RuntimeError, match="AZURE_OPENAI_ENDPOINT"):
                build_provider()


# ---------------------------------------------------------------------------
# AzureOpenAIEmbeddingProvider — mocked openai client
# ---------------------------------------------------------------------------

@pytest.mark.bpe
class TestAzureOpenAIEmbeddingProvider:
    def test_name_includes_deployment(self):
        from gold.src.embeddings import AzureOpenAIEmbeddingProvider
        mock_client_cls = MagicMock()
        with patch("gold.src.embeddings.AzureOpenAI", mock_client_cls, create=True):
            try:
                p = AzureOpenAIEmbeddingProvider(
                    endpoint="https://x.openai.azure.com",
                    deployment="ada-002",
                    api_version="2024-01",
                    api_key="k",
                    dim=1536,
                )
                assert "ada-002" in p.name
            except (RuntimeError, ImportError):
                pytest.skip("openai package not installed")

    def test_embed_success(self):
        from gold.src.embeddings import AzureOpenAIEmbeddingProvider

        mock_resp = MagicMock()
        mock_resp.data = [MagicMock(embedding=[0.1, 0.2, 0.3, 0.4])]
        mock_client_instance = MagicMock()
        mock_client_instance.embeddings.create.return_value = mock_resp

        mock_openai = MagicMock()
        mock_openai.AzureOpenAI = MagicMock(return_value=mock_client_instance)

        with patch.dict("sys.modules", {"openai": mock_openai}):
            p = AzureOpenAIEmbeddingProvider(
                endpoint="https://x.openai.azure.com",
                deployment="ada-002",
                api_version="2024-01",
                api_key="k",
                dim=4,
            )
            mock_openai.RateLimitError = type("RateLimitError", (Exception,), {})
            mock_openai.APIConnectionError = type("APIConnectionError", (Exception,), {})
            mock_openai.APIError = type("APIError", (Exception,), {})
            results = p.embed(["test text"])
        assert results == [[0.1, 0.2, 0.3, 0.4]]

    def test_embed_rate_limit_retries(self):
        from gold.src.embeddings import AzureOpenAIEmbeddingProvider

        mock_resp = MagicMock()
        mock_resp.data = [MagicMock(embedding=[0.1, 0.2])]

        RateLimitError = type("RateLimitError", (Exception,), {})
        APIConnectionError = type("APIConnectionError", (Exception,), {})
        APIError = type("APIError", (Exception,), {})

        mock_client_instance = MagicMock()
        mock_client_instance.embeddings.create.side_effect = [
            RateLimitError("rate limit"),
            mock_resp,
        ]

        mock_openai = MagicMock()
        mock_openai.AzureOpenAI = MagicMock(return_value=mock_client_instance)
        mock_openai.RateLimitError = RateLimitError
        mock_openai.APIConnectionError = APIConnectionError
        mock_openai.APIError = APIError

        with patch.dict("sys.modules", {"openai": mock_openai}), patch("time.sleep"):
            p = AzureOpenAIEmbeddingProvider(
                endpoint="https://x.openai.azure.com",
                deployment="ada-002",
                api_version="2024-01",
                api_key="k",
                dim=2,
                max_retries=2,
                backoff_s=0.0,
            )
            results = p.embed(["text"])
        assert results == [[0.1, 0.2]]
