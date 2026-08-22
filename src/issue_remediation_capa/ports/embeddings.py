"""EmbeddingsPort: the boundary that turns issue text into vectors for thematic clustering.

Thematic analysis clusters issues into systemic themes. The clustering maths is pure (see
:mod:`..domain.themes`); the vectors it consumes come from here. Under ``gcp`` this is a managed
Vertex AI embedding model; under ``local`` it is a deterministic, SDK-free hashing embedder, so
the offline gate clusters real vectors with no network and no cloud SDK; on-premises it fails
fast until the client binds its own model.

The port is deliberately narrow: text in, vectors out, nothing consequential. A theme never rests
on an embedding alone; merging or reopening on a theme is human-approved downstream.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingsPort(Protocol):
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Return one equal-length vector per input text, in the same order.

        The offline adapter answers deterministically (the same text always yields the same
        vector); the managed adapter reaches Vertex AI and refuses rather than returning empty
        when it cannot; the on-premises placeholder raises.
        """
        ...
