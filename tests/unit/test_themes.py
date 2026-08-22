"""Thematic clustering: deterministic, cited, and purity that can go red."""

from __future__ import annotations

from issue_remediation_capa.adapters.local.embeddings import LocalHashingEmbeddingAdapter
from issue_remediation_capa.config import Settings
from issue_remediation_capa.domain.kernel import Citation
from issue_remediation_capa.domain.themes import (
    ClusteredIssue,
    cluster_issues,
    cosine_similarity,
    theme_purity,
)

_TEXTS: tuple[tuple[str, str, str], ...] = (
    ("r1", "residency region storage bucket location control", "residency"),
    ("r2", "data residency region storage bucket violation", "residency"),
    ("e1", "encryption key rotation cmek disabled production", "encryption"),
    ("e2", "cmek encryption key management rotation disabled", "encryption"),
)


def _items() -> tuple[ClusteredIssue, ...]:
    embedder = LocalHashingEmbeddingAdapter(Settings(profile="local"))
    vectors = embedder.embed(tuple(text for _id, text, _gold in _TEXTS))
    return tuple(
        ClusteredIssue(iid, vec, gold, Citation(source_id=iid, title=iid, snippet=""))
        for (iid, _text, gold), vec in zip(_TEXTS, vectors, strict=True)
    )


def test_clustering_is_deterministic() -> None:
    first = cluster_issues(_items(), threshold=0.55)
    second = cluster_issues(_items(), threshold=0.55)
    assert [t.member_ids for t in first] == [t.member_ids for t in second]


def test_clustering_separates_the_two_themes_and_cites_members() -> None:
    themes = cluster_issues(_items(), threshold=0.55)
    assert len(themes) == 2
    for theme in themes:
        assert theme.citations, "a theme must cite the issues that formed it"
    gold = {iid: gold for iid, _text, gold in _TEXTS}
    assert theme_purity(themes, gold) == 1.0


def test_purity_goes_red_when_everything_is_merged() -> None:
    # A threshold that admits any pair collapses the two themes into one; purity drops below 1.0.
    themes = cluster_issues(_items(), threshold=-1.0)
    gold = {iid: gold for iid, _text, gold in _TEXTS}
    assert len(themes) == 1
    assert theme_purity(themes, gold) < 1.0


def test_cosine_of_a_zero_vector_is_zero() -> None:
    assert cosine_similarity((0.0, 0.0), (1.0, 1.0)) == 0.0
