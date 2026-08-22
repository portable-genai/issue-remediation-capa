"""GCP EmbeddingsPort: Vertex AI text embeddings (SDK imports stay lazy).

The embedding model produces vectors for thematic clustering only; the clustering maths and every
consequential decision remain in pure code. The ``vertexai`` import lives INSIDE the method so the
``local`` / ``onprem`` profiles import this module with no cloud SDK installed (the portability
proof, and the reason the managed family refuses rather than succeeds under the offline gate).
"""

from __future__ import annotations

from ...config import Settings


class VertexEmbeddingAdapter:
    """Embed text through a managed Vertex AI embedding model."""

    #: A current text-embedding model. Vectors only ever feed clustering, never a decision.
    _MODEL = "text-embedding-005"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def embed(
        self, texts: tuple[str, ...]
    ) -> tuple[tuple[float, ...], ...]:  # pragma: no cover - needs live GCP
        # Lazy import: absent in the offline profiles and in CI, so this raises there rather than
        # answering, which is exactly the managed-family refusal the parity suite asserts.
        import vertexai
        from vertexai.language_models import TextEmbeddingModel

        vertexai.init(location=self._settings.region)
        model = TextEmbeddingModel.from_pretrained(self._MODEL)
        embeddings = model.get_embeddings(list(texts))
        return tuple(tuple(float(v) for v in emb.values) for emb in embeddings)
