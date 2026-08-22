"""Local IssueIntakePort: a deterministic, SDK-free fixture feed for the offline profile.

It stands in for the five live source feeds in the gate, the tests and the demo. Every record is
obviously fictional (``.example`` domains, invented parties). The fixture deliberately spans all
five sources and carries a couple of schema-INVALID records, so the intake service's drop
behaviour is exercised offline rather than only asserted. A silent empty return would let a
producer ship the intake seam unwired, so this returns real, inspectable records.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...config import Settings
from ...domain.capa import IssueSource

#: Obviously-fictional raw records per source. Two are deliberately schema-invalid (a missing
#: field, an unknown rating) so the intake service's drop-not-default behaviour has offline
#: material. All parties and identifiers are synthetic.
_FIXTURE: dict[IssueSource, tuple[Mapping[str, object], ...]] = {
    IssueSource.AUD1_FINDING: (
        {
            "finding_id": "F-2026-011",
            "engagement": "Cloud controls audit",
            "title": "Residency control not evidenced for archive tier",
            "rating": "high",
            "raised_on": "2026-06-01",
            "description": "The archive storage tier had no residency evidence for the quarter.",
        },
        {
            "finding_id": "F-2026-012",
            "engagement": "Cloud controls audit",
            "title": "Encryption key rotation overdue on ledger store",
            "rating": "high",
            "raised_on": "2026-06-03",
            "description": "Key rotation for the ledger store exceeded the policy interval.",
        },
        # Schema-invalid: unknown rating. Dropped, never admitted on a default.
        {
            "finding_id": "F-2026-013",
            "engagement": "Cloud controls audit",
            "title": "Log retention shorter than mandated",
            "rating": "showstopper",
            "raised_on": "2026-06-04",
        },
    ),
    IssueSource.AUD2_EXCEPTION: (
        {
            "exception_id": "E-88",
            "control_id": "ctrl-residency",
            "description": "Continuous test found a bucket outside the residency region.",
            "severity": "critical",
            "detected_on": "2026-06-10",
        },
        {
            "exception_id": "E-91",
            "control_id": "ctrl-cmek",
            "description": "A CMEK key was disabled on a production dataset.",
            "severity": "high",
            "detected_on": "2026-06-12",
        },
    ),
    IssueSource.RSK1_HORIZON: (
        {
            "change_id": "H-204",
            "obligation_ref": "obl-incident",
            "summary": "Incident notification window shortened to one hour.",
            "impact": "high",
            "published_on": "2026-05-20",
        },
    ),
    IssueSource.DOC6_FINDING: (
        {
            "complaint_id": "C-5501",
            "theme": "unclear fee disclosure",
            "summary": "Repeated complaints about unclear fee disclosure at onboarding.",
            "severity": "medium",
            "logged_on": "2026-06-15",
        },
        # Schema-invalid: missing 'summary'. Dropped.
        {
            "complaint_id": "C-5502",
            "theme": "statement delivery",
            "severity": "low",
            "logged_on": "2026-06-16",
        },
    ),
    IssueSource.LOSS_EVENT: (
        {
            "event_id": "L-777",
            "category": "payment failure",
            "narrative": "A duplicated batch caused an outbound payment overpayment.",
            "gross_loss": "420000",
            "occurred_on": "2026-06-08",
        },
    ),
}


class LocalFixtureIntakeAdapter:
    """Return canned, obviously-fictional raw records per source (no network, no cloud SDK)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def fetch(self, source: IssueSource) -> tuple[Mapping[str, object], ...]:
        return _FIXTURE.get(source, ())
