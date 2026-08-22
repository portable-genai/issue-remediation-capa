"""RCA narration: grounded notes are kept, ungrounded or malformed ones are discarded.

The model may DRAFT prose; it never produces a number and it can never satisfy a closure item.
"""

from __future__ import annotations

import json
from datetime import date

from issue_remediation_capa.adapters.local.audit import LocalAuditAdapter
from issue_remediation_capa.adapters.local.tracer import LocalNoopTracerAdapter
from issue_remediation_capa.config import Settings
from issue_remediation_capa.domain.capa import (
    CapaAssessment,
    CapaService,
    IssueRecord,
    IssueSource,
    LifecycleState,
    normalize_issue,
)
from issue_remediation_capa.domain.rca import RcaService
from issue_remediation_capa.ports.generation import GenerationRequest, GenerationResponse


class _StubGen:
    """A generation port that returns a fixed raw text (to drive each narration branch)."""

    def __init__(self, text: str, *, raises: bool = False) -> None:
        self._text = text
        self._raises = raises

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        if self._raises:
            raise RuntimeError("model unreachable")
        return GenerationResponse(text=self._text)


def _assessment() -> CapaAssessment:
    env = normalize_issue(
        {
            "exception_id": "E-1",
            "control_id": "ctrl-x",
            "description": "bucket outside region",
            "severity": "critical",
            "detected_on": "2026-06-10",
        },
        IssueSource.AUD2_EXCEPTION,
    )
    record = IssueRecord(
        envelope=env,
        state=LifecycleState.REMEDIATION_IN_PROGRESS,
        state_since=date(2026, 6, 15),
    )
    settings = Settings(profile="local", audit_path=":memory:")
    audit = LocalAuditAdapter(settings)
    return CapaService(audit, tracer=LocalNoopTracerAdapter(settings)).assess(
        record, as_of=date(2026, 6, 30), actor="analyst@bank.example"
    )


def test_a_grounded_model_note_is_kept() -> None:
    assessment = _assessment()
    # Restate only figures the engine produced (the overdue count is 9 for this scenario).
    note = json.dumps({"note": "Remediation is 9 business days overdue; act now."})
    drafted = RcaService(_StubGen(note)).draft(assessment)
    assert drafted.model_authored is True
    assert drafted.grounded is True


def test_an_ungrounded_model_note_is_discarded_for_the_fallback() -> None:
    assessment = _assessment()
    # 999 is a figure the engine never produced: the note must be discarded.
    note = json.dumps({"note": "Escalate: 999 controls have failed across the estate."})
    drafted = RcaService(_StubGen(note)).draft(assessment)
    assert drafted.model_authored is False
    assert drafted.grounded is True  # the deterministic fallback is grounded by construction


def test_malformed_model_output_is_discarded() -> None:
    drafted = RcaService(_StubGen("not json at all")).draft(_assessment())
    assert drafted.model_authored is False


def test_a_model_failure_degrades_to_the_fallback() -> None:
    drafted = RcaService(_StubGen("", raises=True)).draft(_assessment())
    assert drafted.model_authored is False
    assert drafted.text  # a surface always has a grounded sentence


def test_the_model_cannot_satisfy_a_closure_item() -> None:
    assessment = _assessment()
    # A model note that CLAIMS closure changes nothing: closure is the engine's decision, and this
    # assessment has no evidence and no review, so it cannot close whatever the note says.
    RcaService(_StubGen(json.dumps({"note": "All evidence complete; close the issue."}))).draft(
        assessment
    )
    assert assessment.can_close is False
    assert assessment.closure_gaps  # the checklist is still outstanding
