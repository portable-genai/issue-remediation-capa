"""Thematic analysis: deterministic clustering of issues into systemic themes (the rcsa-kri-erm
feed).

issue-remediation-capa surfaces systemic root-cause patterns across many issues, and feeds those
THEMES one-way to rcsa-kri-erm as a risk signal. The embedding of each issue is produced OUTSIDE
this module, behind the :class:`~..ports.embeddings.EmbeddingsPort` (a real Vertex model under
``gcp``, a deterministic hashing embedder under ``local``); this module takes the vectors and
clusters them with PURE stdlib maths, so a given set of vectors plus a threshold always yields
byte-identical themes.

Clustering is greedy single-pass over issues sorted by id: each issue joins the first existing
theme whose centroid is within the cosine threshold, or opens a new one. That is deterministic by
construction (no random seed, no dict-order dependence), which is what lets the theme feed be
replayable and the ``theme_purity`` metric be meaningful. Each theme carries its member-issue
citations, so a downstream reader can trace a theme back to the issues that formed it.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .kernel import Citation

__all__ = [
    "ClusteredIssue",
    "Theme",
    "cluster_issues",
    "cosine_similarity",
    "theme_purity",
]


@dataclass(frozen=True, slots=True)
class ClusteredIssue:
    """One issue as clustering sees it: an id, its vector, a label hint and its citation."""

    issue_id: str
    vector: tuple[float, ...]
    label_hint: str
    citation: Citation


@dataclass(frozen=True, slots=True)
class Theme:
    """A systemic theme: a stable id, a label, its member issue ids and their citations."""

    theme_id: str
    label: str
    member_ids: tuple[str, ...]
    citations: tuple[Citation, ...]


def cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine similarity of two equal-length vectors; 0.0 if either is the zero vector."""
    if len(a) != len(b):
        raise ValueError("cosine_similarity needs two equal-length vectors")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _centroid(vectors: list[tuple[float, ...]]) -> tuple[float, ...]:
    dim = len(vectors[0])
    return tuple(sum(v[i] for v in vectors) / len(vectors) for i in range(dim))


def _label(hints: list[str]) -> str:
    """The theme label: the most common label hint, ties broken alphabetically (deterministic)."""
    counts = Counter(hints)
    best = max(counts, key=lambda name: (counts[name], _neg_key(name)))
    return best


def _neg_key(name: str) -> tuple[int, ...]:
    """A tie-break key that prefers the alphabetically-earliest name (smaller code points win)."""
    return tuple(-ord(ch) for ch in name)


def cluster_issues(items: tuple[ClusteredIssue, ...], *, threshold: float) -> tuple[Theme, ...]:
    """Cluster issues into themes by cosine proximity of their vectors (deterministic).

    Issues are processed in id order and each joins the first theme whose CURRENT centroid is
    within ``threshold``, or opens a new theme. No randomness and no dict-iteration dependence,
    so the same vectors and threshold always produce the same themes.
    """
    ordered = sorted(items, key=lambda it: it.issue_id)
    theme_vectors: list[list[tuple[float, ...]]] = []
    theme_members: list[list[ClusteredIssue]] = []
    for item in ordered:
        placed = False
        for idx, vectors in enumerate(theme_vectors):
            if cosine_similarity(item.vector, _centroid(vectors)) >= threshold:
                vectors.append(item.vector)
                theme_members[idx].append(item)
                placed = True
                break
        if not placed:
            theme_vectors.append([item.vector])
            theme_members.append([item])

    themes: list[Theme] = []
    for idx, members in enumerate(theme_members):
        themes.append(
            Theme(
                theme_id=f"theme-{idx + 1}",
                label=_label([m.label_hint for m in members]),
                member_ids=tuple(m.issue_id for m in members),
                citations=tuple(m.citation for m in members),
            )
        )
    return tuple(themes)


def theme_purity(themes: tuple[Theme, ...], gold_labels: dict[str, str]) -> float:
    """Cluster purity against an INDEPENDENT gold labelling: the standard clustering-purity score.

    For each theme, count its members by their gold label and take the majority; sum the majority
    counts across themes and divide by the total number of assigned issues. A clustering that
    merged unrelated issues scores below 1.0, so the metric can go red.
    """
    total = 0
    majority = 0
    for theme in themes:
        golds = [gold_labels[mid] for mid in theme.member_ids if mid in gold_labels]
        if not golds:
            continue
        total += len(golds)
        majority += Counter(golds).most_common(1)[0][1]
    return round(majority / total, 4) if total else 0.0
