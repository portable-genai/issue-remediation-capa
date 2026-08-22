"""Local EmbeddingsPort: a deterministic, SDK-free hashing embedder for the offline profile.

It stands in for a managed embedding model in the gate, the tests and the demo. Each text is
lower-cased, split into word tokens, and each token is hashed into a fixed-dimension bag-of-words
vector (feature hashing) with an L2 normalisation at the end. Pure stdlib (``hashlib``), so the
whole thematic-clustering path runs offline with no network and no cloud SDK, and the SAME text
always yields the SAME vector, which is what makes the clustering and the ``theme_purity`` metric
replayable. A silent empty return would let a producer ship the embeddings seam unwired, so this
produces real, inspectable vectors.
"""

from __future__ import annotations

import hashlib
import math
import re

from ...config import Settings

#: The embedding dimension. Small and fixed: enough separation for offline clustering, cheap to
#: compute, and identical across runs.
_DIM = 64
_TOKEN = re.compile(r"[a-z0-9]+")


def _embed_one(text: str) -> tuple[float, ...]:
    buckets = [0.0] * _DIM
    for token in _TOKEN.findall(text.lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = digest[0] % _DIM
        sign = 1.0 if digest[1] % 2 == 0 else -1.0
        buckets[index] += sign
    norm = math.sqrt(sum(value * value for value in buckets))
    if norm == 0.0:
        return tuple(buckets)
    return tuple(value / norm for value in buckets)


class LocalHashingEmbeddingAdapter:
    """Deterministic feature-hashing embedder (no model, no network, no cloud SDK)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(_embed_one(text) for text in texts)
