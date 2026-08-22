"""On-prem EmbeddingsPort: fail-fast portability placeholder (the sovereign-exit proof, P-12)."""

from __future__ import annotations

from ...config import Settings


class OnPremEmbeddingAdapter:
    """Satisfies EmbeddingsPort but refuses at call time: the client wires its own model."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise NotImplementedError(
            "on-prem embeddings is a portability placeholder: bind the client's own embedding "
            "model endpoint (see docs/onprem-migration.md)"
        )
