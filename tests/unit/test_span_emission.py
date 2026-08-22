"""Both consequential paths open ONE span each, and no span carries content.

A trace backend is not the WORM audit trail. It has no redaction stage, no retention policy
written against a regulator's requirement, and a far wider read audience than the audit store.
So the value of tracing these paths depends entirely on the spans carrying structural
attributes only: which action, whose, which source feed, which lifecycle state. A case
subject, an issue id, a free-text description or a planted identifier reaching a span has
left the boundary redaction exists to hold, and it has left it silently.

Two orchestrators are pinned because BOTH are real request paths: ``/v1/triage`` drives
``TriageService.triage`` and ``/v1/issues/assess`` drives ``CapaService.assess``. Each content
case drives input with a planted NRIC, so the check runs against data that would actually leak.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest

from issue_remediation_capa.config import build_container
from issue_remediation_capa.domain.capa import (
    CapaService,
    IssueRecord,
    IssueSource,
    LifecycleState,
    normalize_issue,
)
from issue_remediation_capa.domain.models import TriageInput
from issue_remediation_capa.domain.triage_service import TriageService

from tests.conftest import local_settings
from tests.fixtures import sample_cases

#: The complete attribute key set each span may carry. Adding to one of these is a decision
#: about what leaves the trust boundary, so it is made here rather than at the call site.
_TRIAGE_KEYS = {"action", "actor"}
_ASSESS_KEYS = {"action", "actor", "source", "state"}


class _RecordingTracer:
    """Captures every span name and attribute so the test can inspect what was emitted."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, str]]] = []

    @contextmanager
    def span(self, name: str, **attributes: str) -> Iterator[None]:
        self.spans.append((name, dict(attributes)))
        yield

    def record_token_usage(self, usage: object, model: str) -> None:
        return None


def _triage(case: TriageInput) -> _RecordingTracer:
    container = build_container(local_settings())
    tracer = _RecordingTracer()
    service = TriageService(container.audit, tracer=tracer)  # type: ignore[arg-type]
    service.triage(case, actor=sample_cases.ACTOR)
    return tracer


def _pii_record() -> IssueRecord:
    """An overdue control-gap finding whose title carries the planted NRIC."""
    envelope = normalize_issue(
        {
            "finding_id": "F-PII",
            "engagement": "audit",
            "title": f"control gap, NRIC {sample_cases.PLANTED_NRIC} on file",
            "rating": "high",
            "raised_on": "2026-06-01",
        },
        IssueSource.AUD1_FINDING,
    )
    return IssueRecord(
        envelope=envelope,
        state=LifecycleState.REMEDIATION_IN_PROGRESS,
        state_since=date(2026, 6, 2),
    )


def _assess(record: IssueRecord) -> _RecordingTracer:
    container = build_container(local_settings())
    tracer = _RecordingTracer()
    service = CapaService(container.audit, tracer=tracer)  # type: ignore[arg-type]
    service.assess(record, as_of=date(2026, 6, 30), actor=sample_cases.ACTOR)
    return tracer


def _emitted(tracer: _RecordingTracer) -> str:
    """Every attribute KEY and VALUE that was emitted, as one searchable blob."""
    parts: list[str] = []
    for name, attributes in tracer.spans:
        parts.append(name)
        parts.extend(attributes)
        parts.extend(attributes.values())
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# The spans exist at all
# --------------------------------------------------------------------------- #
def test_triaging_a_case_opens_exactly_one_named_span() -> None:
    tracer = _triage(sample_cases.ROUTINE_CASE)
    assert [name for name, _ in tracer.spans] == ["capa.triage"]


def test_assessing_an_issue_opens_exactly_one_named_span() -> None:
    tracer = _assess(_pii_record())
    assert [name for name, _ in tracer.spans] == ["capa.assess"]


# --------------------------------------------------------------------------- #
# What the spans carry
# --------------------------------------------------------------------------- #
def test_the_triage_span_carries_the_structural_attributes_an_operator_needs() -> None:
    _, attributes = _triage(sample_cases.ROUTINE_CASE).spans[0]
    assert attributes["action"] == "triage"
    assert attributes["actor"] == sample_cases.ACTOR


def test_the_assess_span_carries_the_structural_attributes_an_operator_needs() -> None:
    """Enough to answer "whose assessment is slow, from which feed, in which state"."""
    _, attributes = _assess(_pii_record()).spans[0]
    assert attributes["action"] == "assess"
    assert attributes["actor"] == sample_cases.ACTOR
    assert attributes["source"] == IssueSource.AUD1_FINDING.value
    assert attributes["state"] == LifecycleState.REMEDIATION_IN_PROGRESS.value


@pytest.mark.parametrize(
    "case",
    [sample_cases.ROUTINE_CASE, sample_cases.ESCALATING_CASE, sample_cases.PII_CASE],
    ids=["routine", "escalating", "pii"],
)
def test_the_triage_attribute_set_is_a_fixed_allowlist_whatever_the_verdict(
    case: TriageInput,
) -> None:
    """An escalating case must not start attaching its findings to the span to explain itself."""
    for _, attributes in _triage(case).spans:
        assert set(attributes) == _TRIAGE_KEYS


def test_the_assess_attribute_set_is_a_fixed_allowlist() -> None:
    """An overdue issue must not start attaching its gaps to the span to explain itself."""
    for _, attributes in _assess(_pii_record()).spans:
        assert set(attributes) == _ASSESS_KEYS, (
            "a new span attribute appeared; confirm it is structural, then widen "
            "the allowlist here deliberately"
        )


# --------------------------------------------------------------------------- #
# What the spans must never carry
# --------------------------------------------------------------------------- #
def test_no_triage_span_attribute_carries_case_content_or_the_planted_identifier() -> None:
    """The case used here has an NRIC planted in its description, so a leak would show."""
    emitted = _emitted(_triage(sample_cases.PII_CASE))
    forbidden = [
        sample_cases.PLANTED_NRIC,
        sample_cases.PII_CASE.subject,
        sample_cases.PII_CASE.text,
        "ops@gamma.example",
    ]
    for literal in forbidden:
        assert literal, "an empty needle would pass this test for the wrong reason"
        assert literal.lower() not in emitted.lower(), f"a span attribute carried {literal!r}"


def test_no_assess_span_attribute_carries_issue_content_or_the_planted_identifier() -> None:
    """The issue used here has an NRIC planted in its title, so a leak would show."""
    record = _pii_record()
    emitted = _emitted(_assess(record))
    forbidden = [
        sample_cases.PLANTED_NRIC,
        record.envelope.issue_id,
        record.envelope.subject,
    ]
    for literal in forbidden:
        assert literal, "an empty needle would pass this test for the wrong reason"
        assert literal.lower() not in emitted.lower(), f"a span attribute carried {literal!r}"


def test_every_emitted_attribute_value_is_a_string_the_port_declares() -> None:
    """``span(name, **attributes: str)``: a non-string would serialise however the SDK felt."""
    values: list[Any] = [
        v
        for tracer in (_triage(sample_cases.ESCALATING_CASE), _assess(_pii_record()))
        for _, attributes in tracer.spans
        for v in attributes.values()
    ]
    assert values
    assert all(isinstance(value, str) for value in values)
