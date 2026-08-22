"""The pii_safety metric the GATE SHIPS is proved able to go red (check E2).

The previous version of this file scored a local one-line helper defined three lines above the
assertion. It passed, and it proved nothing about the gate: the shipped metric read
``redacted_summary`` and nothing else, which is the ONE field the redactor was already masking,
so it asked the redactor whether it had redacted and believed the answer. It reported
``pii_safety 1.000 PASS`` with the identifier sitting in the same record's citation.

So the falsification runs against ``run_eval`` itself, imported as the gate imports it, and the
mutant is the leak the metric exists to catch: the SAME row, summary clean either way, differing
only in the citation. A metric that reads the wrong field cannot tell the two apart and stays
green on the red input, which is exactly the failure ``assert_can_go_red`` refuses.
"""

from __future__ import annotations

from typing import Any

import run_eval as ev
from agent_eval_kit import assert_can_go_red

from issue_remediation_capa.adapters.local.audit import LocalAuditAdapter
from issue_remediation_capa.adapters.local.tracer import LocalNoopTracerAdapter
from issue_remediation_capa.config import Settings
from issue_remediation_capa.domain.triage_service import TriageService

from tests.fixtures import sample_cases

_PLANTED = (sample_cases.PLANTED_NRIC,)

#: The summary is CLEAN in both rows. That is the whole point: the summary was never the field
#: that leaked, so a metric that only reads it scores these two identically.
_CLEAN_ROW: dict[str, Any] = {
    "action": "triage",
    "actor": sample_cases.ACTOR,
    "redacted_summary": "Gamma LLP (FICTIONAL): triaged high :: NRIC [REDACTED:SG_NRIC_FIN]",
    "citations": [
        {
            "source_id": "case:Gamma LLP (FICTIONAL)",
            "title": "Case description",
            "snippet": "urgent breach, NRIC [REDACTED:SG_NRIC_FIN] on file",
        }
    ],
}

#: Redaction off, in the citation only (the mutant the shipped metric used to score 1.000).
_LEAKY_ROW: dict[str, Any] = {
    **_CLEAN_ROW,
    "citations": [
        {
            "source_id": f"case:Gamma LLP (FICTIONAL) {sample_cases.PLANTED_NRIC}",
            "title": "Case description",
            "snippet": f"urgent breach, NRIC {sample_cases.PLANTED_NRIC} on file",
        }
    ],
}


def _score(rows: list[dict[str, Any]]) -> float:
    """The gate's own scorer over the gate's own field selection. No re-implementation here."""
    return ev.pii_safety(ev.audit_texts(rows), _PLANTED)


def test_pii_safety_can_go_red() -> None:
    assert_can_go_red(
        _score,
        green=[_CLEAN_ROW],
        red=[_LEAKY_ROW],
        threshold=ev.THRESHOLDS["pii_safety"],
        metric="pii_safety",
    )


def test_pii_safety_is_green_on_the_record_the_real_service_writes() -> None:
    """Green, and green over a real run rather than over an empty list of nothing."""
    settings = Settings(profile="local", audit_path=":memory:")
    audit = LocalAuditAdapter(settings)
    TriageService(audit, tracer=LocalNoopTracerAdapter(settings)).triage(
        sample_cases.PII_SUBJECT_CASE, actor=sample_cases.ACTOR
    )

    texts = ev.audit_texts(audit.log.read_all())
    assert any("[REDACTED:" in text for text in texts), (
        "the scan found no redaction marker, so it is reading fields that carry no content "
        "and its green means nothing"
    )
    assert ev.pii_safety(texts, (*_PLANTED, sample_cases.PLANTED_EMAIL)) == 1.0


def test_the_scan_excludes_the_actor_so_it_can_ever_be_green() -> None:
    """The caveat, pinned: widening this to whole rows makes the metric permanently red.

    ``actor`` is the verified principal and is an address by design. A well-meaning "scan the
    whole record" change would make every run fail on the attribution column, and the next
    person would relax the threshold rather than narrow the scan.
    """
    row: dict[str, Any] = {**_CLEAN_ROW, "actor": "analyst@bank.example"}
    assert ev.pii_safety(ev.audit_texts([row]), _PLANTED) == 1.0
