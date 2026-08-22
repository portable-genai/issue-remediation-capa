"""RCA narration: the model DRAFTS a root-cause note, and never produces a number or a verdict.

Given a :class:`~.capa.CapaAssessment` (already computed by the deterministic engine), this asks
the generation port for a short root-cause-and-remediation note, then holds that note to two hard
rules before it is allowed out:

* **Schema validation, discard on failure.** The model must return JSON with the requested keys.
  Malformed output, or output missing a key, is discarded, not repaired.
* **Groundedness, discard on failure.** Every integer in the note must be one the engine produced
  (the request's ``facts``: the overdue business-day count, the missing-evidence count). A note
  that invents a figure is discarded.

When a model note is discarded, a deterministic note built purely from the engine facts is used
instead, so a surface always has a grounded sentence and never a hallucinated one. Crucially, the
model can SUMMARIZE evidence but it can NEVER satisfy a closure-checklist item: closure is decided
in :mod:`.capa`, and nothing here can flip ``closure_gaps`` or authorise a closure.

The request-building, parsing and groundedness checks are module-level pure functions rather than
private methods, so the eval can measure the RAW model output through the very same contract the
service enforces (a groundedness metric that watched only the already-filtered service output
could never go red). Pure stdlib: the model is reached only through the injected port.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..ports.generation import GenerationPort, GenerationRequest
from .capa import CapaAssessment

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

    def __init__(self, generation: GenerationPort) -> None:
        self._generation = generation

    def draft(self, assessment: CapaAssessment) -> DraftedRca:
        request = build_request(assessment)
        try:
            response = self._generation.generate(request)
        except Exception:  # noqa: BLE001 - a narration failure must degrade, never crash a decision
            return DraftedRca(
                text=fallback_text(request.facts), model_authored=False, grounded=True
            )

        note = parse_note(response.text)
        if note is None or not note_is_grounded(note, request.facts):
            # Schema-invalid or ungrounded: discard the model output, never repair it.
            return DraftedRca(
                text=fallback_text(request.facts), model_authored=False, grounded=True
            )
        return DraftedRca(text=note, model_authored=True, grounded=True)
