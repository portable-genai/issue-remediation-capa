"""API request/response schemas (Pydantic) mapped to/from the pure-domain models."""

from __future__ import annotations

from pydantic import BaseModel

from ..domain.capa import CapaAssessment
from ..domain.models import TriageResult
from ..domain.rca import DraftedRca
from ..domain.themes import Theme


class TriageRequest(BaseModel):
    subject: str
    text: str


class CitationModel(BaseModel):
    source_id: str
    title: str
    snippet: str = ""


class TriageResponse(BaseModel):
    subject: str
    severity: str
    decision: str
    summary: str
    requires_human_review: bool
    #: Where the escalation WENT (rule R8): the human-review-console review id, or the local queue
    #: reference.
    #: Empty only when the result did not escalate. A caller can tell a routed escalation from
    #: a flag that stopped here, which is the whole point of the rule.
    review_ref: str = ""
    citations: list[CitationModel] = []

    @classmethod
    def from_domain(cls, result: TriageResult, *, review_ref: str = "") -> TriageResponse:
        return cls(
            subject=result.subject,
            severity=result.severity.value,
            decision=result.decision.value,
            summary=result.summary,
            requires_human_review=result.requires_human_review,
            review_ref=review_ref,
            citations=[
                CitationModel(source_id=c.source_id, title=c.title, snippet=c.snippet)
                for c in result.citations
            ],
        )


class IssueAssessRequest(BaseModel):
    """Assess one issue's lifecycle position.

    The issue is selected from the intake feed by ``source`` + ``external_id`` (its raw record is
    fetched and normalized server-side), and its current lifecycle fields are supplied here.
    ``as_of`` fixes the clock so the assessment is replayable. ``provided_evidence`` and
    ``review_ref`` drive the closure guard: neither the model nor this request can close an issue,
    only satisfy the pure-code preconditions the engine then checks.
    """

    source: str
    external_id: str
    state: str = "raised"
    state_since: str
    as_of: str
    provided_evidence: list[str] = []
    review_ref: str = ""


class IssueAssessResponse(BaseModel):
    issue_id: str
    subject: str
    severity: str
    decision: str
    summary: str
    requires_human_review: bool
    source: str
    state: str
    aging_kind: str
    due_on: str
    overdue_business_days: int
    closure_gaps: list[str] = []
    can_close: bool = False
    #: The model-drafted (or deterministically fallen-back) RCA note. Grounded: every figure in it
    #: comes from the engine, never the model, and it can never satisfy a closure-checklist item.
    rca_note: str = ""
    rca_model_authored: bool = False
    #: Where the escalation WENT (rule R8): the human-review-console review id, or the local queue
    #: reference.
    review_ref: str = ""
    citations: list[CitationModel] = []

    @classmethod
    def from_domain(
        cls,
        assessment: CapaAssessment,
        *,
        rca: DraftedRca,
        review_ref: str = "",
    ) -> IssueAssessResponse:
        return cls(
            issue_id=assessment.issue_id,
            subject=assessment.subject,
            severity=assessment.severity.value,
            decision=assessment.decision.value,
            summary=assessment.summary,
            requires_human_review=assessment.requires_human_review,
            source=assessment.source.value,
            state=assessment.state.value,
            aging_kind=assessment.aging_kind.value,
            due_on=assessment.due_on.isoformat(),
            overdue_business_days=assessment.overdue_business_days,
            closure_gaps=list(assessment.closure_gaps),
            can_close=assessment.can_close,
            rca_note=rca.text,
            rca_model_authored=rca.model_authored,
            review_ref=review_ref,
            citations=[
                CitationModel(source_id=c.source_id, title=c.title, snippet=c.snippet)
                for c in assessment.citations
            ],
        )


class ThemeModel(BaseModel):
    theme_id: str
    label: str
    member_ids: list[str]
    citations: list[CitationModel] = []

    @classmethod
    def from_domain(cls, theme: Theme) -> ThemeModel:
        return cls(
            theme_id=theme.theme_id,
            label=theme.label,
            member_ids=list(theme.member_ids),
            citations=[
                CitationModel(source_id=c.source_id, title=c.title, snippet=c.snippet)
                for c in theme.citations
            ],
        )


class ThemesResponse(BaseModel):
    """The one-way, read-only theme feed rcsa-kri-erm consumes. There is no write counterpart by
    design.
    """

    themes: list[ThemeModel] = []


class HealthResponse(BaseModel):
    status: str
    profile: str
    region: str
    #: Provenance the UI banner states on every page: where the runtime sits and which model
    #: answers. Both are read off the service because the browser cannot know either.
    runtime: str = "local"  # "gcp" | "local"
    generator_model: str = "deterministic-offline-stub"
