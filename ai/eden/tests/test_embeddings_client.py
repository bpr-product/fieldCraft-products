"""Unit tests for the in-process FastEmbed embeddings client that replaced
the sentence-transformers HTTP microservice. FastEmbed's TextEmbedding is
mocked — these tests prove EmbeddingsClient's contract (normalize, raise on
failure, is_reachable mirrors init success), not FastEmbed's own model
behavior, which needs a real model download to exercise (see this module's
docstring on the huggingface.co download step).
"""
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import source.utils.embeddings_client as embeddings_client_module
from source.utils.embeddings_client import EmbeddingsClient


def test_embed_returns_l2_normalized_vectors():
    fake_model = MagicMock()
    fake_model.embed.return_value = iter([[3.0, 4.0], [1.0, 0.0]])
    with patch("fastembed.TextEmbedding", return_value=fake_model):
        client = EmbeddingsClient()
        vectors = client.embed(["take me to invoices", "show schedule"])
    assert vectors.shape == (2, 2)
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), [1.0, 1.0])
    np.testing.assert_allclose(vectors[0], [0.6, 0.8])


def test_is_reachable_true_when_model_loads():
    with patch("fastembed.TextEmbedding", return_value=MagicMock()):
        client = EmbeddingsClient()
    assert client.is_reachable() is True


def test_is_reachable_false_and_embed_raises_when_model_load_fails():
    with patch("fastembed.TextEmbedding", side_effect=ValueError("model download failed")):
        client = EmbeddingsClient()
    assert client.is_reachable() is False
    with pytest.raises(RuntimeError):
        client.embed(["hello"])


def test_embed_handles_zero_vector_without_dividing_by_zero():
    fake_model = MagicMock()
    fake_model.embed.return_value = iter([[0.0, 0.0]])
    with patch("fastembed.TextEmbedding", return_value=fake_model):
        client = EmbeddingsClient()
        vectors = client.embed(["empty"])
    assert not np.isnan(vectors).any()


def test_init_gives_up_after_timeout_instead_of_blocking_on_slow_download(monkeypatch):
    # Reproduces the real bug: FastEmbed's own download_model has a built-in
    # retry/backoff (observed ~40s: 3s/9s/27s sleeps) that we don't control.
    # __init__ must give up on its OWN short timeout rather than inheriting
    # that — this is what makes RETRIEVAL_BACKEND=semantic safe to leave on
    # in a dev .env when the network/model download is unavailable.
    monkeypatch.setattr(embeddings_client_module, "INIT_TIMEOUT_SECONDS", 0.2)

    def _slow_load():
        time.sleep(5)
        return MagicMock()

    monkeypatch.setattr(embeddings_client_module, "_load_model", _slow_load)

    started = time.monotonic()
    client = EmbeddingsClient()
    elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"init blocked for {elapsed:.1f}s instead of respecting the timeout"
    assert client.is_reachable() is False
