"""RCA narration: the model DRAFTS a root-cause note, and never produces a number or a verdict.

Given a :class:`~.capa.CapaAssessment` (already computed by the deterministic engine), this asks
the generation port for a short root-cause-and-remediation note, then holds that note to three
hard rules before it is allowed out:

* **Guardrail screening, audited and degraded on a block (rule R1).** The built prompt is
  screened INPUT before it reaches the generation port, and the model's raw answer is screened
  OUTPUT before it is parsed; each screen's text is used exactly as given. A block in either
  direction, or a guardrail that cannot decide (fail closed), is audited ``Decision.BLOCKED``
  and then treated like a narration failure below: the narration is optional by design, so the
  service answers with the deterministic fallback and never with a partial model note.
* **Schema validation, discard on failure.** The model must return JSON with the requested keys.
  Malformed output, or output missing a key, is discarded, not repaired.
* **Groundedness, discard on failure.** Every integer in the note must be one the engine produced
  (the request's ``facts``: the overdue business-day count, the missing-evidence count). A note
  that invents a figure is discarded.

When a model note is discarded (schema-invalid, ungrounded, unreachable or guardrail-blocked), a
deterministic note built purely from the engine facts is used instead, so a surface always has a
grounded sentence and never a hallucinated or unsafe one. Crucially, the model can SUMMARIZE
evidence but it can NEVER satisfy a closure-checklist item: closure is decided in :mod:`.capa`,
and nothing here can flip ``closure_gaps`` or authorise a closure.

The request-building, parsing and groundedness checks are module-level pure functions rather than
private methods, so the eval can measure the RAW model output through the very same contract the
service enforces (a groundedness metric that watched only the already-filtered service output
could never go red). The model, the guardrail and the audit sink are reached only through the
injected ports.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

from pii_kit import redact

from ..ports.audit import AuditSinkPort
from ..ports.generation import GenerationPort, GenerationRequest
from ..ports.guardrail import GuardrailPort
from .capa import CapaAssessment
from .kernel import AuditEvent, Decision, Direction, utcnow
from .pii import PII_PATTERNS

__all__ = [
    "DraftedRca",
    "RcaService",
    "build_request",
    "fallback_text",
    "note_is_grounded",
    "parse_note",
]

_INT = re.compile(r"-?\d+")

_SYSTEM = (
    "You are an issue-management analyst assistant. You restate the remediation figures you are "
    "given as a short root-cause and remediation note. You never invent a number, and you never "
    "declare an issue closed or an evidence item satisfied: use only the figures in the facts "
    "block, which the engine computed."
)


@dataclass(frozen=True, slots=True)
class DraftedRca:
    """A root-cause note plus how it was produced."""

    text: str
    model_authored: bool
    grounded: bool


def grounded_integers(facts: tuple[tuple[str, str], ...]) -> set[str]:
    """Every integer token that appears in the engine-owned facts (the grounded number set)."""
    allowed: set[str] = set()
    for _key, value in facts:
        allowed.update(_INT.findall(value))
    return allowed


def note_is_grounded(text: str, facts: tuple[tuple[str, str], ...]) -> bool:
    """True when every integer in ``text`` is one the engine facts contain."""
    allowed = grounded_integers(facts)
    return all(token in allowed for token in _INT.findall(text))


def build_request(assessment: CapaAssessment) -> GenerationRequest:
    """The exact narration request the service sends, exposed so the eval can reuse it."""
    facts = assessment.facts()
    block = "\n".join(f"{key}={value}" for key, value in facts)
    prompt = (
        f"Issue: {assessment.issue_id}\n"
        f"Facts (use ONLY these numbers):\n{block}\n"
        'Return JSON of the form {"note": "<one sentence root-cause and remediation summary>"}.'
    )
    return GenerationRequest(system=_SYSTEM, prompt=prompt, facts=facts, response_keys=("note",))


def parse_note(text: str) -> str | None:
    """Parse the model's raw text into the ``note`` string, or ``None`` if it is not valid."""
    try:
        parsed = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    note = parsed.get("note")
    if not isinstance(note, str) or not note.strip():
        return None
    return note.strip()


def fallback_text(facts: tuple[tuple[str, str], ...]) -> str:
    """A deterministic, grounded-by-construction note built purely from the engine facts."""
    values = dict(facts)
    return (
        f"Issue is {values.get('severity', 'low')} severity in state "
        f"{values.get('state', 'raised')}; aging is {values.get('aging', 'on_track')} with "
        f"{values.get('overdue_business_days', '0')} business day(s) overdue and "
        f"{values.get('missing_evidence', '0')} closure-evidence item(s) outstanding."
    )


class RcaService:
    """Draft a grounded root-cause note for a CAPA assessment."""

    def __init__(
        self, generation: GenerationPort, guardrail: GuardrailPort, audit: AuditSinkPort
    ) -> None:
        self._generation = generation
        self._guardrail = guardrail
        self._audit = audit

    def draft(self, assessment: CapaAssessment, *, actor: str) -> DraftedRca:
        request = build_request(assessment)
        fallback = DraftedRca(
            text=fallback_text(request.facts), model_authored=False, grounded=True
        )

        # 1) Guardrail screen (INPUT), before the built prompt reaches the model (rule R1). The
        # prompt is screened AS SENT: every caller-influenced field it carries (the issue id and
        # the lifecycle state) reaches the model inside this one string, and the screened text
        # is what the model is then given, exactly as the screen handed it back.
        prompt = self._screen(request.prompt, Direction.INPUT, assessment, actor=actor)
        if prompt is None:
            return fallback

        try:
            response = self._generation.generate(replace(request, prompt=prompt))
        except Exception:  # noqa: BLE001 - a narration failure must degrade, never crash a decision
            return fallback

        # 2) Guardrail screen (OUTPUT) on the model's raw answer, before it is parsed at all.
        answer = self._screen(response.text, Direction.OUTPUT, assessment, actor=actor)
        if answer is None:
            return fallback

        note = parse_note(answer)
        if note is None or not note_is_grounded(note, request.facts):
            # Schema-invalid or ungrounded: discard the model output, never repair it.
            return fallback
        return DraftedRca(text=note, model_authored=True, grounded=True)

    def _screen(
        self, text: str, direction: Direction, assessment: CapaAssessment, *, actor: str
    ) -> str | None:
        """Screen one text in one direction: the text to use from here on, or ``None``.

        ``None`` means refused, and the refusal is already audited ``Decision.BLOCKED``: a
        policy block, and equally a guardrail that raised instead of deciding (fail closed, the
        model never sees an unscreened prompt and no unscreened answer is used). The allowed
        text is the verdict's ``sanitized_text`` exactly as given, never the unscreened original.
        """
        try:
            verdict = self._guardrail.screen(text, direction)
        except Exception as exc:  # noqa: BLE001 - an undecided screen is a refusal (fail closed)
            self._audit_blocked(
                direction, f"guardrail unavailable ({type(exc).__name__})", assessment, actor
            )
            return None
        if not verdict.allowed or verdict.sanitized_text is None:
            reason = verdict.reason or f"rca {direction.value} blocked by guardrail"
            self._audit_blocked(direction, reason, assessment, actor)
            return None
        return verdict.sanitized_text

    def _audit_blocked(
        self, direction: Direction, reason: str, assessment: CapaAssessment, actor: str
    ) -> None:
        """Audit a guardrail refusal of the RCA narration (rules R1/R2).

        Never carries the refused text: only that a refusal happened, in which direction and
        why, against the issue it was drafted for. The issue id is the engine's own key, not the
        refused text, and its severity is the band the assessment already recorded.
        """
        self._audit.record(
            AuditEvent(
                action="rca_narration",
                actor=actor,
                decision=Decision.BLOCKED,
                severity=assessment.severity,
                redacted_summary=redact(
                    f"{assessment.issue_id}: rca narration blocked ({direction.value}): {reason}",
                    PII_PATTERNS,
                ),
                citations=assessment.citations,
                timestamp=utcnow(),
            )
        )
