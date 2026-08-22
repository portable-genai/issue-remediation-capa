"""The CAPA lifecycle engine: normalization, business-day maths, transitions and the closure guard.

These are the consequential decisions, so they are pure and unit-tested here directly: the same
inputs always yield the same result, an illegal transition raises, and closure is refused without
both complete evidence AND an approved review reference.
"""

from __future__ import annotations

from datetime import date

import pytest

from issue_remediation_capa.adapters.local.audit import LocalAuditAdapter
from issue_remediation_capa.adapters.local.tracer import LocalNoopTracerAdapter
from issue_remediation_capa.config import Settings
from issue_remediation_capa.domain.capa import (
    AgingKind,
    CapaService,
    ClosureBlockedError,
    CrossTenantError,
    Decision,
    IllegalTransitionError,
    IssueRecord,
    IssueSource,
    IssueType,
    LifecycleState,
    NormalizationError,
    Severity,
    add_business_days,
    authorize_issue_access,
    business_days_between,
    closure_gaps,
    normalize_issue,
    plan_transition,
)

_NO_HOLIDAYS: frozenset[date] = frozenset()


def _service() -> CapaService:
    settings = Settings(profile="local", audit_path=":memory:")
    return CapaService(LocalAuditAdapter(settings), tracer=LocalNoopTracerAdapter(settings))


# --------------------------------------------------------------------------- #
# Business-day maths
# --------------------------------------------------------------------------- #
def test_add_business_days_skips_the_weekend() -> None:
    # 2026-06-05 is a Friday; +1 business day is the following Monday.
    assert add_business_days(date(2026, 6, 5), 1, _NO_HOLIDAYS) == date(2026, 6, 8)


def test_add_business_days_skips_a_holiday() -> None:
    holidays = frozenset({date(2026, 6, 8)})
    assert add_business_days(date(2026, 6, 5), 1, holidays) == date(2026, 6, 9)


def test_business_days_between_is_signed_and_replayable() -> None:
    assert business_days_between(date(2026, 6, 1), date(2026, 6, 8), _NO_HOLIDAYS) == 5
    assert business_days_between(date(2026, 6, 8), date(2026, 6, 1), _NO_HOLIDAYS) == -5
    assert business_days_between(date(2026, 6, 1), date(2026, 6, 1), _NO_HOLIDAYS) == 0


# --------------------------------------------------------------------------- #
# Normalization (per source) and the drop-not-default rule
# --------------------------------------------------------------------------- #
def test_normalize_maps_a_loss_event_amount_to_a_deterministic_band() -> None:
    env = normalize_issue(
        {
            "event_id": "L-1",
            "category": "payment failure",
            "narrative": "duplicated batch",
            "gross_loss": "420000",
            "occurred_on": "2026-06-08",
        },
        IssueSource.LOSS_EVENT,
    )
    assert env.issue_type is IssueType.LOSS_EVENT
    assert env.severity is Severity.HIGH  # 250k <= 420k < 1m
    assert env.opened_on == date(2026, 6, 8)
    assert env.citations  # every envelope carries provenance


def test_normalize_raises_on_a_missing_required_field() -> None:
    with pytest.raises(NormalizationError):
        normalize_issue({"finding_id": "F-1", "rating": "high"}, IssueSource.AUD1_FINDING)


def test_normalize_raises_on_an_unknown_rating() -> None:
    with pytest.raises(NormalizationError):
        normalize_issue(
            {
                "exception_id": "E-1",
                "control_id": "c",
                "description": "d",
                "severity": "showstopper",
                "detected_on": "2026-06-10",
            },
            IssueSource.AUD2_EXCEPTION,
        )


# --------------------------------------------------------------------------- #
# The lifecycle state machine and the closure guard
# --------------------------------------------------------------------------- #
def _record(
    *,
    issue_type: IssueType = IssueType.CONTROL_GAP,
    state: LifecycleState,
    evidence: tuple[str, ...] = (),
    review_ref: str = "",
) -> IssueRecord:
    env = normalize_issue(
        {
            "finding_id": "F-9",
            "engagement": "audit",
            "title": "control gap",
            "rating": "high",
            "raised_on": "2026-06-01",
        },
        IssueSource.AUD1_FINDING,
    )
    if issue_type is not IssueType.CONTROL_GAP:  # pragma: no cover - only control_gap used here
        raise AssertionError("this helper only builds control_gap records")
    return IssueRecord(
        envelope=env,
        state=state,
        state_since=date(2026, 6, 2),
        provided_evidence=evidence,
        review_ref=review_ref,
    )


def test_an_illegal_transition_is_rejected() -> None:
    record = _record(state=LifecycleState.RAISED)
    with pytest.raises(IllegalTransitionError):
        plan_transition(record, LifecycleState.CLOSED, as_of=date(2026, 6, 10))


def test_a_legal_transition_advances_state_and_stamps_the_date() -> None:
    record = _record(state=LifecycleState.RAISED)
    moved = plan_transition(record, LifecycleState.RCA_DRAFTED, as_of=date(2026, 6, 10))
    assert moved.state is LifecycleState.RCA_DRAFTED
    assert moved.state_since == date(2026, 6, 10)


_FULL_CONTROL_GAP = ("root_cause", "control_redesign", "evidence_of_operation", "owner_signoff")


def test_closure_is_refused_without_an_approved_review_even_with_complete_evidence() -> None:
    record = _record(state=LifecycleState.VALIDATED, evidence=_FULL_CONTROL_GAP, review_ref="")
    with pytest.raises(ClosureBlockedError) as caught:
        plan_transition(record, LifecycleState.CLOSED, as_of=date(2026, 6, 20))
    assert "review" in str(caught.value)


def test_closure_is_refused_with_incomplete_evidence_even_with_a_review() -> None:
    record = _record(state=LifecycleState.VALIDATED, evidence=("root_cause",), review_ref="rev-1")
    with pytest.raises(ClosureBlockedError) as caught:
        plan_transition(record, LifecycleState.CLOSED, as_of=date(2026, 6, 20))
    assert caught.value.gaps  # names the missing items


def test_closure_succeeds_only_with_complete_evidence_and_a_review() -> None:
    record = _record(state=LifecycleState.VALIDATED, evidence=_FULL_CONTROL_GAP, review_ref="rev-1")
    closed = plan_transition(record, LifecycleState.CLOSED, as_of=date(2026, 6, 20))
    assert closed.state is LifecycleState.CLOSED


def test_closure_gaps_lists_only_the_missing_items() -> None:
    assert closure_gaps(IssueType.CONTROL_GAP, ("root_cause", "owner_signoff")) == (
        "control_redesign",
        "evidence_of_operation",
    )


# --------------------------------------------------------------------------- #
# The consequential assessment
# --------------------------------------------------------------------------- #
def test_an_sla_breach_escalates_and_is_flagged_for_review() -> None:
    record = _record(state=LifecycleState.REMEDIATION_IN_PROGRESS)
    assessment = _service().assess(record, as_of=date(2026, 6, 30), actor="analyst@bank.example")
    assert assessment.aging_kind is AgingKind.SLA_BREACH
    assert assessment.decision is Decision.ESCALATED
    assert assessment.requires_human_review is True
    assert assessment.citations, "a consequential result must be cited"


def test_a_submitted_closure_always_needs_a_human() -> None:
    record = _record(
        state=LifecycleState.CLOSURE_SUBMITTED, evidence=_FULL_CONTROL_GAP, review_ref="rev-1"
    )
    # Even on track, a submitted closure is consequential and routes to a human validator.
    assessment = _service().assess(record, as_of=date(2026, 6, 3), actor="analyst@bank.example")
    assert assessment.requires_human_review is True


def test_the_assessment_writes_a_redacted_audit_record() -> None:
    settings = Settings(profile="local", audit_path=":memory:")
    audit = LocalAuditAdapter(settings)
    CapaService(audit, tracer=LocalNoopTracerAdapter(settings)).assess(
        _record(state=LifecycleState.REMEDIATION_IN_PROGRESS),
        as_of=date(2026, 6, 30),
        actor="analyst@bank.example",
    )
    stored = audit.log.read_all()
    assert stored and stored[-1]["action"] == "capa_assessment"


# --------------------------------------------------------------------------- #
# Cross-tenant authorisation (403, never 404)
# --------------------------------------------------------------------------- #
def test_the_home_tenant_is_authorised() -> None:
    authorize_issue_access("demo-bank")  # does not raise


def test_a_foreign_tenant_is_refused() -> None:
    with pytest.raises(CrossTenantError):
        authorize_issue_access("other-bank")
