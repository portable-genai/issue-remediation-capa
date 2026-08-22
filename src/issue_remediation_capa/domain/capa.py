"""The issue and CAPA lifecycle vertical: the deterministic engine (Aud3's reason to exist).

Aud3 OWNS the post-finding lifecycle of audit / exam / incident issues and their corrective and
preventive actions, to closure. The consequential machinery here is PURE CODE:

* **Feed-agnostic intake.** Five source families (Aud1 findings, Aud2 exceptions, the Rsk1
  horizon change-feed, Doc6 findings and loss events) arrive in their own idiosyncratic shapes
  and are normalized into ONE :class:`IssueEnvelope`. A raw record missing a field the envelope
  requires raises :class:`NormalizationError`, and the intake service drops it rather than
  defaulting it. The source enum is extensible: the deferred Rgc10 and Rgc13 feeders are simply
  members nobody wired an adapter for yet.
* **The lifecycle state machine** cribs Hrz7's case spine as CONFIGURATION (see
  :data:`LEGAL_TRANSITIONS`): raised -> rca_drafted -> remediation_in_progress ->
  closure_submitted -> validated -> closed, with a bounce-back from a rejected closure. An
  illegal transition raises; nothing walks the graph by accident.
* **Deadlines and aging** are business-day maths with an explicit ``as_of`` and the holiday set
  passed in, so the same issue on the same date always yields the same status. SLA breach,
  approaching deadline and stuck-in-state are computed, never guessed.
* **Closure that cannot self-serve.** The evidence-adequacy checklist is config-owned per issue
  type, and the engine refuses the transition to ``closed`` unless the checklist is satisfied AND
  an approved Hrz7 review reference is present. Nothing auto-closes.

A model may DRAFT the root-cause narrative (see :mod:`.rca`); it never produces a date, a band or
a closure verdict, and it can never satisfy a checklist item. Pure stdlib: no web framework, no
cloud SDK, no clock beyond the ``as_of`` the caller passes and the one the audit envelope uses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta

from hex_service_kit.enums import LenientStrEnum

from ..ports.audit import AuditSinkPort
from ..ports.observability import ObservabilityTracerPort
from .kernel import AuditEvent, Citation, Decision, Severity, utcnow

__all__ = [
    "DEFAULT_CLOCK",
    "ISSUE_TENANT",
    "AgingFinding",
    "AgingKind",
    "CapaAssessment",
    "CapaService",
    "ClockSpec",
    "ClosureBlockedError",
    "CrossTenantError",
    "IllegalTransitionError",
    "IssueEnvelope",
    "IssueRecord",
    "IssueSource",
    "IssueType",
    "LifecycleState",
    "NormalizationError",
    "add_business_days",
    "aging_finding",
    "authorize_issue_access",
    "business_days_between",
    "closure_gaps",
    "due_date",
    "normalize_issue",
    "plan_transition",
]


# --------------------------------------------------------------------------- #
# Taxonomies (a member IS its wire value; lenient so an unknown value is caught, not crashed on)
# --------------------------------------------------------------------------- #
class IssueSource(LenientStrEnum):
    """Where an issue entered Aud3. Extensible on purpose: the deferred Rgc10 / Rgc13 feeders are
    members with no adapter yet, which is the whole accommodation the plan asks for."""

    AUD1_FINDING = "aud1_finding"
    AUD2_EXCEPTION = "aud2_exception"
    RSK1_HORIZON = "rsk1_horizon"
    DOC6_FINDING = "doc6_finding"
    LOSS_EVENT = "loss_event"


class IssueType(LenientStrEnum):
    """The issue's nature, which selects the closure-evidence checklist."""

    CONTROL_GAP = "control_gap"
    PROCESS_FAILURE = "process_failure"
    REGULATORY_BREACH = "regulatory_breach"
    LOSS_EVENT = "loss_event"


class LifecycleState(LenientStrEnum):
    """The issue / CAPA lifecycle states (the Hrz7 case-spine shape, as configuration)."""

    RAISED = "raised"
    RCA_DRAFTED = "rca_drafted"
    REMEDIATION_IN_PROGRESS = "remediation_in_progress"
    CLOSURE_SUBMITTED = "closure_submitted"
    VALIDATED = "validated"
    CLOSED = "closed"


class AgingKind(LenientStrEnum):
    """The deterministic aging verdict for one issue at an ``as_of`` date."""

    ON_TRACK = "on_track"
    APPROACHING_DEADLINE = "approaching_deadline"
    STUCK_IN_STATE = "stuck_in_state"
    SLA_BREACH = "sla_breach"


#: The tenant that OWNS the offline seed issue store. A verified principal from any other tenant
#: is refused (403, never 404): the store EXISTS, the caller is simply not authorised for it.
ISSUE_TENANT = "demo-bank"


# --------------------------------------------------------------------------- #
# Cross-tenant authorisation (403, never 404, against the VERIFIED principal)
# --------------------------------------------------------------------------- #
class CrossTenantError(Exception):
    """A verified principal asked for an issue store owned by a different tenant."""

    def __init__(self, principal_tenant: str, store_tenant: str) -> None:
        super().__init__(
            f"tenant {principal_tenant!r} is not authorised for the issue store owned by "
            f"{store_tenant!r}"
        )
        self.principal_tenant = principal_tenant
        self.store_tenant = store_tenant


def authorize_issue_access(principal_tenant: str, *, store_tenant: str = ISSUE_TENANT) -> None:
    """Authorise a VERIFIED principal's tenant against the issue store's owning tenant."""
    if principal_tenant != store_tenant:
        raise CrossTenantError(principal_tenant, store_tenant)


# --------------------------------------------------------------------------- #
# The normalized issue envelope + feed-agnostic intake
# --------------------------------------------------------------------------- #
class NormalizationError(ValueError):
    """A raw record is missing a required field; the intake service drops it rather than default."""


@dataclass(frozen=True, slots=True)
class IssueEnvelope:
    """One issue in Aud3's own vocabulary, whatever source it came from."""

    source: IssueSource
    external_id: str
    issue_id: str
    issue_type: IssueType
    subject: str
    severity: Severity
    description: str
    opened_on: date
    citations: tuple[Citation, ...] = ()


#: Rating / severity words used across the audit and complaints feeds, mapped once.
_RATING_TO_SEVERITY: dict[str, Severity] = {
    "critical": Severity.CRITICAL,
    "severe": Severity.CRITICAL,
    "high": Severity.HIGH,
    "significant": Severity.HIGH,
    "major": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "moderate": Severity.MEDIUM,
    "low": Severity.LOW,
    "minor": Severity.LOW,
}

#: Loss-event severity is a DETERMINISTIC band over the gross loss (config-owned, B4). Ordered
#: most severe first; the first threshold the amount meets or exceeds wins.
_LOSS_BANDS: tuple[tuple[int, Severity], ...] = (
    (1_000_000, Severity.CRITICAL),
    (250_000, Severity.HIGH),
    (50_000, Severity.MEDIUM),
    (0, Severity.LOW),
)


def _require(raw: Mapping[str, object], key: str, source: IssueSource) -> str:
    value = raw.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise NormalizationError(f"{source.value} record is missing required field {key!r}")
    return str(value).strip()


def _parse_date(value: str, source: IssueSource) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise NormalizationError(f"{source.value} record has an invalid date {value!r}") from exc


def _severity_from_rating(value: str, source: IssueSource) -> Severity:
    resolved = _RATING_TO_SEVERITY.get(value.strip().lower())
    if resolved is None:
        raise NormalizationError(f"{source.value} record has an unknown rating {value!r}")
    return resolved


def _severity_from_loss(amount_raw: str, source: IssueSource) -> Severity:
    try:
        amount = int(round(float(amount_raw)))
    except ValueError as exc:
        raise NormalizationError(
            f"{source.value} record has a non-numeric gross loss {amount_raw!r}"
        ) from exc
    for threshold, severity in _LOSS_BANDS:
        if amount >= threshold:
            return severity
    return Severity.LOW  # pragma: no cover - the 0 band always matches a non-negative amount


def _cit(source_id: str, title: str, snippet: str) -> Citation:
    return Citation(source_id=source_id, title=title, snippet=snippet[:120])


def _norm_aud1(raw: Mapping[str, object]) -> IssueEnvelope:
    src = IssueSource.AUD1_FINDING
    ext = _require(raw, "finding_id", src)
    title = _require(raw, "title", src)
    return IssueEnvelope(
        source=src,
        external_id=ext,
        issue_id=f"aud3-{ext}",
        issue_type=IssueType.CONTROL_GAP,
        subject=title,
        severity=_severity_from_rating(_require(raw, "rating", src), src),
        description=str(raw.get("description") or title),
        opened_on=_parse_date(_require(raw, "raised_on", src), src),
        citations=(_cit(ext, f"Aud1 finding: {title}", str(raw.get("engagement") or ext)),),
    )


def _norm_aud2(raw: Mapping[str, object]) -> IssueEnvelope:
    src = IssueSource.AUD2_EXCEPTION
    ext = _require(raw, "exception_id", src)
    desc = _require(raw, "description", src)
    return IssueEnvelope(
        source=src,
        external_id=ext,
        issue_id=f"aud3-{ext}",
        issue_type=IssueType.CONTROL_GAP,
        subject=f"Control exception {_require(raw, 'control_id', src)}",
        severity=_severity_from_rating(_require(raw, "severity", src), src),
        description=desc,
        opened_on=_parse_date(_require(raw, "detected_on", src), src),
        citations=(_cit(ext, f"Aud2 exception on {raw.get('control_id')}", desc),),
    )


def _norm_rsk1(raw: Mapping[str, object]) -> IssueEnvelope:
    src = IssueSource.RSK1_HORIZON
    ext = _require(raw, "change_id", src)
    summary = _require(raw, "summary", src)
    return IssueEnvelope(
        source=src,
        external_id=ext,
        issue_id=f"aud3-{ext}",
        issue_type=IssueType.REGULATORY_BREACH,
        subject=f"Horizon change {_require(raw, 'obligation_ref', src)}",
        severity=_severity_from_rating(_require(raw, "impact", src), src),
        description=summary,
        opened_on=_parse_date(_require(raw, "published_on", src), src),
        citations=(_cit(ext, f"Rsk1 horizon change: {summary}", str(raw.get("obligation_ref"))),),
    )


def _norm_doc6(raw: Mapping[str, object]) -> IssueEnvelope:
    src = IssueSource.DOC6_FINDING
    ext = _require(raw, "complaint_id", src)
    summary = _require(raw, "summary", src)
    return IssueEnvelope(
        source=src,
        external_id=ext,
        issue_id=f"aud3-{ext}",
        issue_type=IssueType.PROCESS_FAILURE,
        subject=f"Complaint theme {_require(raw, 'theme', src)}",
        severity=_severity_from_rating(_require(raw, "severity", src), src),
        description=summary,
        opened_on=_parse_date(_require(raw, "logged_on", src), src),
        citations=(_cit(ext, f"Doc6 conduct finding: {summary}", str(raw.get("theme"))),),
    )


def _norm_loss(raw: Mapping[str, object]) -> IssueEnvelope:
    src = IssueSource.LOSS_EVENT
    ext = _require(raw, "event_id", src)
    narrative = _require(raw, "narrative", src)
    category = _require(raw, "category", src)
    return IssueEnvelope(
        source=src,
        external_id=ext,
        issue_id=f"aud3-{ext}",
        issue_type=IssueType.LOSS_EVENT,
        subject=f"Loss event: {category}",
        severity=_severity_from_loss(_require(raw, "gross_loss", src), src),
        description=narrative,
        opened_on=_parse_date(_require(raw, "occurred_on", src), src),
        citations=(_cit(ext, f"Loss event: {category}", narrative),),
    )


_NORMALIZERS: dict[IssueSource, object] = {
    IssueSource.AUD1_FINDING: _norm_aud1,
    IssueSource.AUD2_EXCEPTION: _norm_aud2,
    IssueSource.RSK1_HORIZON: _norm_rsk1,
    IssueSource.DOC6_FINDING: _norm_doc6,
    IssueSource.LOSS_EVENT: _norm_loss,
}


def normalize_issue(raw: Mapping[str, object], source: IssueSource) -> IssueEnvelope:
    """Normalize one raw source record into the shared :class:`IssueEnvelope`.

    Raises :class:`NormalizationError` for a schema-invalid record (a missing required field, an
    unknown rating, a bad date), which the intake service drops rather than admitting on a
    default. There is deliberately no adapter for a deferred source, so a source with no
    normalizer raises too.
    """
    normalizer = _NORMALIZERS.get(source)
    if normalizer is None:
        raise NormalizationError(f"no normalizer is wired for source {source.value!r}")
    return normalizer(raw)  # type: ignore[operator, no-any-return]


# --------------------------------------------------------------------------- #
# Business-day maths (pure, replayable, explicit holiday set)
# --------------------------------------------------------------------------- #
def _is_business_day(day: date, holidays: frozenset[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


def add_business_days(start: date, days: int, holidays: frozenset[date]) -> date:
    """Return the date ``days`` business days after ``start`` (weekends and holidays skipped)."""
    if days < 0:
        raise ValueError("add_business_days needs a non-negative day count")
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if _is_business_day(current, holidays):
            remaining -= 1
    return current


def business_days_between(start: date, end: date, holidays: frozenset[date]) -> int:
    """Signed business-day count from ``start`` to ``end`` (positive when ``end`` is later)."""
    if start == end:
        return 0
    step = 1 if end > start else -1
    count = 0
    current = start
    while current != end:
        current += timedelta(days=step)
        if _is_business_day(current, holidays):
            count += step
    return count


# --------------------------------------------------------------------------- #
# The remediation clock and aging (config-owned bank policy, B4)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ClockSpec:
    """Bank-owned remediation SLAs, in BUSINESS days, plus the aging thresholds.

    A module constant today (see :data:`DEFAULT_CLOCK`); lifting it into a ``policy:`` block in
    ``config/settings.yaml`` is the B4 follow-up. The numbers are the client's, so they live in
    one frozen object rather than being scattered as literals through the engine.
    """

    remediation_days: tuple[tuple[Severity, int], ...]
    approaching_window: int
    stuck_threshold: int

    def days_for(self, severity: Severity) -> int:
        for band, days in self.remediation_days:
            if band is severity:
                return days
        raise KeyError(f"no remediation SLA configured for severity {severity.value!r}")


DEFAULT_CLOCK = ClockSpec(
    remediation_days=(
        (Severity.CRITICAL, 5),
        (Severity.HIGH, 10),
        (Severity.MEDIUM, 20),
        (Severity.LOW, 40),
    ),
    approaching_window=3,
    stuck_threshold=15,
)


def due_date(
    opened_on: date,
    severity: Severity,
    *,
    clock: ClockSpec = DEFAULT_CLOCK,
    holidays: frozenset[date],
) -> date:
    """The remediation due date: ``opened_on`` plus the severity's SLA in business days."""
    return add_business_days(opened_on, clock.days_for(severity), holidays)


@dataclass(frozen=True, slots=True)
class AgingFinding:
    """The deterministic aging verdict for one issue, with the numbers behind it."""

    kind: AgingKind
    due_on: date
    overdue_business_days: int
    detail: str


def aging_finding(
    record: IssueRecord,
    *,
    as_of: date,
    clock: ClockSpec = DEFAULT_CLOCK,
    holidays: frozenset[date],
) -> AgingFinding:
    """Compute the aging verdict for ``record`` at ``as_of``.

    Precedence is deliberate and total: a closed issue never ages; otherwise an SLA breach wins
    over an approaching deadline, which wins over being stuck in one state, which wins over being
    on track. Every branch reports the business-day figure it decided on.
    """
    due = due_date(
        record.envelope.opened_on, record.envelope.severity, clock=clock, holidays=holidays
    )
    if record.state is LifecycleState.CLOSED:
        return AgingFinding(AgingKind.ON_TRACK, due, 0, "closed; the remediation clock has stopped")
    if as_of > due:
        overdue = business_days_between(due, as_of, holidays)
        return AgingFinding(
            AgingKind.SLA_BREACH, due, overdue, f"remediation is {overdue} business day(s) overdue"
        )
    to_due = business_days_between(as_of, due, holidays)
    if to_due <= clock.approaching_window:
        return AgingFinding(
            AgingKind.APPROACHING_DEADLINE, due, 0, f"due in {to_due} business day(s)"
        )
    in_state = business_days_between(record.state_since, as_of, holidays)
    if in_state > clock.stuck_threshold:
        return AgingFinding(
            AgingKind.STUCK_IN_STATE,
            due,
            0,
            f"in {record.state.value} for {in_state} business day(s)",
        )
    return AgingFinding(AgingKind.ON_TRACK, due, 0, f"on track; due in {to_due} business day(s)")


# --------------------------------------------------------------------------- #
# The lifecycle state machine (Hrz7 case spine, as configuration)
# --------------------------------------------------------------------------- #
LEGAL_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.RAISED: frozenset({LifecycleState.RCA_DRAFTED}),
    LifecycleState.RCA_DRAFTED: frozenset({LifecycleState.REMEDIATION_IN_PROGRESS}),
    LifecycleState.REMEDIATION_IN_PROGRESS: frozenset({LifecycleState.CLOSURE_SUBMITTED}),
    # A rejected closure bounces back to remediation rather than closing.
    LifecycleState.CLOSURE_SUBMITTED: frozenset(
        {LifecycleState.VALIDATED, LifecycleState.REMEDIATION_IN_PROGRESS}
    ),
    LifecycleState.VALIDATED: frozenset({LifecycleState.CLOSED}),
    LifecycleState.CLOSED: frozenset(),
}

#: The closure-evidence checklist per issue type (config-owned). Closing without EVERY item is
#: refused: the model may summarize evidence, but it can never tick one of these boxes.
CLOSURE_CHECKLISTS: dict[IssueType, tuple[str, ...]] = {
    IssueType.CONTROL_GAP: (
        "root_cause",
        "control_redesign",
        "evidence_of_operation",
        "owner_signoff",
    ),
    IssueType.PROCESS_FAILURE: ("root_cause", "process_change", "training_record", "owner_signoff"),
    IssueType.REGULATORY_BREACH: (
        "root_cause",
        "remediation_plan",
        "regulator_notification",
        "owner_signoff",
    ),
    IssueType.LOSS_EVENT: ("root_cause", "recovery_action", "control_enhancement", "owner_signoff"),
}


class IllegalTransitionError(Exception):
    """A lifecycle transition that the state machine does not permit."""

    def __init__(self, current: LifecycleState, target: LifecycleState) -> None:
        super().__init__(f"{current.value} -> {target.value} is not a legal lifecycle transition")
        self.current = current
        self.target = target


class ClosureBlockedError(Exception):
    """A transition to ``closed`` was refused: incomplete evidence or no approved review."""

    def __init__(self, reason: str, *, gaps: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.gaps = gaps


def closure_gaps(issue_type: IssueType, provided_evidence: tuple[str, ...]) -> tuple[str, ...]:
    """The closure-checklist items NOT yet provided for ``issue_type`` (empty means complete)."""
    provided = {item.strip() for item in provided_evidence if item.strip()}
    required = CLOSURE_CHECKLISTS.get(issue_type, ())
    return tuple(item for item in required if item not in provided)


@dataclass(frozen=True, slots=True)
class IssueRecord:
    """One issue plus its lifecycle position, closure evidence and approved review reference."""

    envelope: IssueEnvelope
    state: LifecycleState
    state_since: date
    provided_evidence: tuple[str, ...] = ()
    review_ref: str = ""


def plan_transition(
    record: IssueRecord,
    target: LifecycleState,
    *,
    as_of: date,
    review_ref: str = "",
) -> IssueRecord:
    """Return the record after a LEGAL transition to ``target``; raise otherwise.

    The transition to ``closed`` is the guarded one: it is refused unless the issue type's
    closure checklist is complete AND ``review_ref`` names an approved Hrz7 review. This is what
    makes "never auto-closes" a property of the engine rather than a promise in a doc.
    """
    if target not in LEGAL_TRANSITIONS[record.state]:
        raise IllegalTransitionError(record.state, target)
    resolved_ref = review_ref or record.review_ref
    if target is LifecycleState.CLOSED:
        gaps = closure_gaps(record.envelope.issue_type, record.provided_evidence)
        if gaps:
            raise ClosureBlockedError(
                f"closure evidence is incomplete: missing {', '.join(gaps)}", gaps=gaps
            )
        if not resolved_ref:
            raise ClosureBlockedError(
                "closure requires an approved Hrz7 review reference; none was supplied"
            )
    return replace(record, state=target, state_since=as_of, review_ref=resolved_ref)


# --------------------------------------------------------------------------- #
# The consequential assessment (the R8-compatible envelope)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CapaAssessment:
    """The consequential lifecycle picture for one issue: aging, closure readiness, provenance.

    The field set overlaps the shared review envelope (``subject`` / ``severity`` / ``decision``
    / ``summary`` / ``requires_human_review`` / ``citations``) so a surface can route it to Hrz7
    through the shared R8 path without the domain importing a surface type.
    """

    issue_id: str
    subject: str
    severity: Severity
    decision: Decision
    summary: str
    requires_human_review: bool
    source: IssueSource
    state: LifecycleState
    aging_kind: AgingKind
    due_on: date
    overdue_business_days: int
    closure_gaps: tuple[str, ...]
    can_close: bool
    citations: tuple[Citation, ...] = ()

    def facts(self) -> tuple[tuple[str, str], ...]:
        """The engine-owned figures a narration may cite, and nothing else grounds it."""
        return (
            ("severity", self.severity.value),
            ("overdue_business_days", str(self.overdue_business_days)),
            ("missing_evidence", str(len(self.closure_gaps))),
            ("aging", self.aging_kind.value),
            ("state", self.state.value),
        )


#: States whose consequential nature always demands a human: a submitted closure must be
#: validated by a person, never rubber-stamped by the pipeline.
_ESCALATING_STATES = frozenset({LifecycleState.CLOSURE_SUBMITTED})
_ESCALATING_AGING = frozenset(
    {AgingKind.SLA_BREACH, AgingKind.APPROACHING_DEADLINE, AgingKind.STUCK_IN_STATE}
)


#: One span per assessed issue. Structural attributes only: see :meth:`CapaService.assess`.
_ASSESS_SPAN = "capa.assess"


class CapaService:
    """Assess an issue's lifecycle into a consequential, cited, audited result.

    Mirrors the reference service shape: the decision is deterministic (aging plus the closure
    checklist), an already-redacted audit record is written before the result is returned, every
    result is cited, and any consequential result escalates to a human (routed under rule R8 by
    the calling surface) rather than auto-executing.
    """

    def __init__(self, audit: AuditSinkPort, *, tracer: ObservabilityTracerPort) -> None:
        self._audit = audit
        # REQUIRED, and deliberately not an optional with a no-op default. A default would let a
        # new surface construct a service that emits nothing, and the deployment would look
        # traced because the exporter was bound. A surface that forgets the tracer fails to
        # construct instead, which is a failure somebody sees.
        self._tracer = tracer

    def assess(
        self,
        record: IssueRecord,
        *,
        as_of: date,
        actor: str,
        clock: ClockSpec = DEFAULT_CLOCK,
        holidays: frozenset[date] = frozenset(),
    ) -> CapaAssessment:
        """Assess one issue's lifecycle and record the audit event.

        The whole path runs inside one span, and its attributes are STRUCTURAL only: the
        action, the actor, the source feed and the lifecycle state, both enums. Never the
        issue id, never the issue's title or description and never a closure gap's wording.
        A trace backend is not the WORM audit trail: it has no redaction stage, a wider read
        audience and no retention rule written against a regulator's requirement, so anything
        content-shaped that reaches a span has left the boundary the intake redaction exists
        to hold, and it has left it silently.
        """
        with self._tracer.span(
            _ASSESS_SPAN,
            action="assess",
            actor=actor,
            source=record.envelope.source.value,
            state=record.state.value,
        ):
            aging = aging_finding(record, as_of=as_of, clock=clock, holidays=holidays)
            gaps = closure_gaps(record.envelope.issue_type, record.provided_evidence)
            can_close = not gaps and bool(record.review_ref)
            escalate = aging.kind in _ESCALATING_AGING or record.state in _ESCALATING_STATES
            decision = Decision.ESCALATED if escalate else Decision.ALLOWED
            summary = (
                f"{record.envelope.issue_id} [{record.envelope.source.value}] state="
                f"{record.state.value} severity={record.envelope.severity.value} "
                f"aging={aging.kind.value} overdue={aging.overdue_business_days} "
                f"missing_evidence={len(gaps)}"
            )
            self._audit.record(
                AuditEvent(
                    action="capa_assessment",
                    actor=actor,
                    decision=decision,
                    severity=record.envelope.severity,
                    redacted_summary=summary,
                    citations=record.envelope.citations,
                    timestamp=utcnow(),
                )
            )
            return CapaAssessment(
                issue_id=record.envelope.issue_id,
                subject=record.envelope.subject,
                severity=record.envelope.severity,
                decision=decision,
                summary=summary,
                requires_human_review=escalate,
                source=record.envelope.source,
                state=record.state,
                aging_kind=aging.kind,
                due_on=aging.due_on,
                overdue_business_days=aging.overdue_business_days,
                closure_gaps=gaps,
                can_close=can_close,
                citations=record.envelope.citations,
            )
