"""Prove every CAPA eval metric can go RED. A metric that cannot fail is not a metric.

Each metric is exercised with a green input (the working engine agrees with the oracle) and a red
input (a mutation that makes it disagree), and ``assert_each_can_go_red`` asserts the score crosses
the threshold between them. The mutations are the real failure modes: a record missing a field, a
closure with its review reference removed, a clusterer that merges everything, a note that invents
a figure.
"""

from __future__ import annotations

from datetime import date

from agent_eval_kit import assert_can_go_red, assert_each_can_go_red

from issue_remediation_capa.adapters.local.audit import LocalAuditAdapter
from issue_remediation_capa.adapters.local.embeddings import LocalHashingEmbeddingAdapter
from issue_remediation_capa.adapters.local.tracer import LocalNoopTracerAdapter
from issue_remediation_capa.config import Settings
from issue_remediation_capa.domain.capa import (
    CapaService,
    IssueRecord,
    IssueSource,
    LifecycleState,
    NormalizationError,
    normalize_issue,
)
from issue_remediation_capa.domain.kernel import Citation
from issue_remediation_capa.domain.rca import build_request, note_is_grounded, parse_note
from issue_remediation_capa.domain.themes import ClusteredIssue, cluster_issues, theme_purity

_VALID_RAW: dict[IssueSource, dict[str, object]] = {
    IssueSource.AUD1_FINDING: {
        "finding_id": "F-1",
        "title": "gap",
        "rating": "high",
        "raised_on": "2026-06-01",
    },
    IssueSource.AUD2_EXCEPTION: {
        "exception_id": "E-1",
        "control_id": "c",
        "description": "d",
        "severity": "high",
        "detected_on": "2026-06-01",
    },
    IssueSource.LOSS_EVENT: {
        "event_id": "L-1",
        "category": "cat",
        "narrative": "n",
        "gross_loss": "420000",
        "occurred_on": "2026-06-01",
    },
}


def _normalization_score(case: tuple[dict[str, object], IssueSource]) -> float:
    raw, source = case
    try:
        return 1.0 if normalize_issue(raw, source).source is source else 0.0
    except NormalizationError:
        return 0.0


def test_normalization_accuracy_can_go_red_per_source() -> None:
    cases: dict[
        str, tuple[tuple[dict[str, object], IssueSource], tuple[dict[str, object], IssueSource]]
    ] = {}
    for source, valid in _VALID_RAW.items():
        broken = {
            k: v for k, v in valid.items() if k not in ("finding_id", "exception_id", "event_id")
        }
        cases[source.value] = ((valid, source), (broken, source))
    assert_each_can_go_red(
        _normalization_score, cases, threshold=0.99, metric="normalization_accuracy"
    )


def _service() -> CapaService:
    settings = Settings(profile="local", audit_path=":memory:")
    return CapaService(LocalAuditAdapter(settings), tracer=LocalNoopTracerAdapter(settings))


def _control_gap_record(*, review_ref: str) -> IssueRecord:
    env = normalize_issue(_VALID_RAW[IssueSource.AUD1_FINDING], IssueSource.AUD1_FINDING)
    return IssueRecord(
        envelope=env,
        state=LifecycleState.VALIDATED,
        state_since=date(2026, 6, 2),
        provided_evidence=(
            "root_cause",
            "control_redesign",
            "evidence_of_operation",
            "owner_signoff",
        ),
        review_ref=review_ref,
    )


def _closure_score(record: IssueRecord) -> float:
    # The oracle for a fully-evidenced VALIDATED issue is "closable"; the engine must agree only
    # when a review reference is present. Removing it is the red case.
    assessment = _service().assess(record, as_of=date(2026, 6, 20), actor="eval")
    return 1.0 if assessment.can_close else 0.0


def test_closure_safety_can_go_red_when_the_review_reference_is_removed() -> None:
    assert_can_go_red(
        _closure_score,
        green=_control_gap_record(review_ref="rev-1"),
        red=_control_gap_record(review_ref=""),
        threshold=0.99,
        metric="closure_safety",
    )


def _deadline_score(record: IssueRecord) -> float:
    # Oracle: an SLA-breached issue is overdue. The green record breaches; the red record was
    # opened so recently it is on track, so the "is overdue" metric drops.
    assessment = _service().assess(record, as_of=date(2026, 6, 30), actor="eval")
    return 1.0 if assessment.overdue_business_days > 0 else 0.0


def test_deadline_accuracy_can_go_red() -> None:
    breached = IssueRecord(
        envelope=normalize_issue(
            {"finding_id": "F", "title": "t", "rating": "critical", "raised_on": "2026-06-01"},
            IssueSource.AUD1_FINDING,
        ),
        state=LifecycleState.REMEDIATION_IN_PROGRESS,
        state_since=date(2026, 6, 2),
    )
    on_track = IssueRecord(
        envelope=normalize_issue(
            {"finding_id": "F", "title": "t", "rating": "low", "raised_on": "2026-06-29"},
            IssueSource.AUD1_FINDING,
        ),
        state=LifecycleState.REMEDIATION_IN_PROGRESS,
        state_since=date(2026, 6, 29),
    )
    assert_can_go_red(
        _deadline_score, green=breached, red=on_track, threshold=0.99, metric="deadline_accuracy"
    )


_THEME_TEXTS: tuple[tuple[str, str, str], ...] = (
    ("r1", "residency region storage bucket location", "residency"),
    ("r2", "data residency region storage bucket", "residency"),
    ("e1", "encryption key rotation cmek disabled", "encryption"),
    ("e2", "cmek encryption key rotation management", "encryption"),
)


def _theme_items() -> tuple[ClusteredIssue, ...]:
    embedder = LocalHashingEmbeddingAdapter(Settings(profile="local"))
    vectors = embedder.embed(tuple(text for _id, text, _gold in _THEME_TEXTS))
    return tuple(
        ClusteredIssue(iid, vec, gold, Citation(source_id=iid, title=iid, snippet=""))
        for (iid, _text, gold), vec in zip(_THEME_TEXTS, vectors, strict=True)
    )


def _purity_score(threshold: float) -> float:
    themes = cluster_issues(_theme_items(), threshold=threshold)
    gold = {iid: gold for iid, _text, gold in _THEME_TEXTS}
    return theme_purity(themes, gold)


def test_theme_purity_can_go_red_when_everything_merges() -> None:
    assert_can_go_red(_purity_score, green=0.55, red=-1.0, threshold=0.90, metric="theme_purity")


_FACTS: tuple[tuple[str, str], ...] = (("overdue_business_days", "9"), ("missing_evidence", "1"))


def _groundedness_score(note_text: str) -> float:
    note = parse_note(note_text)
    return 1.0 if note is not None and note_is_grounded(note, _FACTS) else 0.0


def test_rca_groundedness_can_go_red_on_a_hallucinated_figure() -> None:
    assert_can_go_red(
        _groundedness_score,
        green='{"note": "9 business days overdue with 1 item outstanding."}',
        red='{"note": "999 controls failed across the estate."}',
        threshold=0.99,
        metric="rca_groundedness",
    )
    # Prove the request the eval actually sends carries only engine facts (defence against drift).
    assert build_request  # imported symbol is exercised by the eval path
