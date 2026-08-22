"""Application pipeline: wire the ports to the pure domain (intake -> normalize -> cluster).

This is the thin orchestration layer the API, the CLI, the eval and the demo all reuse, so the
normalization-and-drop rule and the clustering path have ONE home rather than being retyped per
surface. It imports the domain (pure) and the port Protocols (pure); it holds no business logic
of its own. Every consequential decision is still the deterministic engine's, and the model still
only narrates.
"""

from __future__ import annotations

from .domain.capa import (
    IssueEnvelope,
    IssueSource,
    NormalizationError,
    normalize_issue,
)
from .domain.themes import ClusteredIssue, Theme, cluster_issues
from .ports.embeddings import EmbeddingsPort
from .ports.intake import IssueIntakePort

__all__ = [
    "DEFAULT_THEME_THRESHOLD",
    "build_themes",
    "collect_all_issues",
    "collect_issues",
]

#: The cosine threshold above which two issues share a theme. A module constant (config-owned
#: candidate): high enough that unrelated issues stay apart, low enough that near-duplicates merge.
DEFAULT_THEME_THRESHOLD = 0.55


def collect_issues(intake: IssueIntakePort, source: IssueSource) -> tuple[IssueEnvelope, ...]:
    """Fetch and normalize one source's records, DROPPING any that are schema-invalid.

    A dropped record is never admitted on a default: an uncited or malformed record simply does
    not enter the register. This is the drop-not-default rule the plan requires, applied here so
    every surface inherits it.
    """
    out: list[IssueEnvelope] = []
    for raw in intake.fetch(source):
        try:
            out.append(normalize_issue(raw, source))
        except NormalizationError:
            continue
    return tuple(out)


def collect_all_issues(intake: IssueIntakePort) -> tuple[IssueEnvelope, ...]:
    """Collect normalized issues across every wired source, in a stable source order."""
    out: list[IssueEnvelope] = []
    for source in IssueSource:
        out.extend(collect_issues(intake, source))
    return tuple(out)


def build_themes(
    intake: IssueIntakePort,
    embeddings: EmbeddingsPort,
    *,
    threshold: float = DEFAULT_THEME_THRESHOLD,
) -> tuple[Theme, ...]:
    """Collect issues, embed their text and cluster them into systemic themes (deterministic).

    The embedding vectors come from the port (a deterministic hashing embedder offline); the
    clustering maths is pure. Each clustered issue keeps its first citation, so a theme traces
    back to the issues that formed it.
    """
    envelopes = collect_all_issues(intake)
    if not envelopes:
        return ()
    texts = tuple(f"{env.subject} {env.description}" for env in envelopes)
    vectors = embeddings.embed(texts)
    items = tuple(
        ClusteredIssue(
            issue_id=env.issue_id,
            vector=vec,
            label_hint=env.issue_type.value,
            # Every normalized envelope carries at least one citation (the normalizers guarantee
            # it), so this indexes safely and the theme traces back to its source record.
            citation=env.citations[0],
        )
        for env, vec in zip(envelopes, vectors, strict=True)
    )
    return cluster_issues(items, threshold=threshold)
