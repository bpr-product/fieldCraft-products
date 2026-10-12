"""In-process embeddings client — FastEmbed (ONNX Runtime), replacing the
sentence-transformers microservice (embeddings_service/, docker-compose.local.yml's
`embeddings` container) this repo used to call over HTTP for candidate
retrieval (see navigation_agent.py's top_candidates()).

That container existed only because sentence-transformers pulls in torch,
and installing torch directly into this project's Windows Python
environment crashed with a pagefile error. FastEmbed runs the same
all-MiniLM-L6-v2 model on ONNX Runtime instead of torch, so that crash
doesn't apply here — there is nothing to isolate. This removes the
container, the per-request HTTP round-trip, and the reachability check
that existed purely to detect "is the container up" (still kept as a
method, now checking whether the local ONNX model loaded, so callers don't
have to change).

Model download note: FastEmbed pulls the ONNX model from Hugging Face (or
a GCS mirror) on first use and caches it locally — the first call after a
fresh install needs network access once. If that first download fails
(firewall, corporate proxy/cert restrictions to huggingface.co), __init__
catches it and is_reachable() reports False, same as the old "container
didn't come up" case — callers (navigation_agent.py) already fall back to
TF-IDF on that signal.

Init is bounded to INIT_TIMEOUT_SECONDS on a worker thread rather than
calling TextEmbedding(...) inline: fastembed's own download_model has a
built-in retry/backoff on failure (observed: 3 attempts, sleeping
3s/9s/27s between them — around 40s total) that we don't control. The old
HTTP client's is_reachable() failed fast (~3s connection-refused) on a
down container; without this bound, every NavigationAgent() construction
with a blocked/absent network would silently inherit FastEmbed's ~40s
retry tax instead — exactly what happened here (verified: it turned a
sub-minute test run into one that didn't finish in over 10 minutes,
because every test constructing a real NavigationAgent paid that cost).
The worker thread that times out is abandoned (daemon), not killed — it
finishes its own retries in the background and is simply never awaited.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
INIT_TIMEOUT_SECONDS = 8.0

_init_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fastembed-init")


def _load_model():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=_MODEL_NAME)


class EmbeddingsClient:
    """Same embed()/is_reachable() contract the old HTTP client had, so
    navigation_agent.py's callers don't change — now backed by an
    in-process ONNX model instead of a network call to a container."""

    def __init__(self):
        self._model = None
        self._init_error: Exception | None = None
        future = _init_executor.submit(_load_model)
        try:
            self._model = future.result(timeout=INIT_TIMEOUT_SECONDS)
        except FutureTimeoutError as e:
            self._init_error = e
            logger.warning(
                f"FastEmbed model load exceeded {INIT_TIMEOUT_SECONDS}s "
                "(likely a slow/blocked download) — giving up for this process"
            )
        except Exception as e:
            self._init_error = e
            logger.warning(f"FastEmbed model load failed: {e}")

    def embed(self, texts: list[str]) -> np.ndarray:
        """Returns an (N, dim) float32 array of L2-normalized embeddings,
        one row per input text, in the same order. Raises if the model
        failed to load — callers decide the fallback (e.g. drop to TF-IDF)
        since a silent empty result would be worse than a visible error."""
        if self._model is None:
            raise RuntimeError(f"FastEmbed model unavailable: {self._init_error}")
        vectors = np.array(list(self._model.embed(texts)), dtype=np.float32)
        # Explicit normalization rather than trusting FastEmbed's default for
        # this model — matches the old service's normalize_embeddings=True
        # contract exactly, so cosine-similarity scoring downstream doesn't
        # silently shift if that default ever changes upstream.
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms

    def is_reachable(self) -> bool:
        return self._model is not None
