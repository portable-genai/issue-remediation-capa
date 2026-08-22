"""Canonical synthetic cases, shared by the unit and contract suites.

Every party is obviously fictional and every address is an ``.example`` domain or an RFC 5737 /
RFC 3849 literal. One canonical escalating case and one canonical routine case are enough for
the contract suite: parity means the SAME request through every implementation, so the request
has to have one home rather than being retyped per test.
"""

from __future__ import annotations

from issue_remediation_capa.domain.models import (
    TriageInput,
)

#: The verified principal the tests attribute work to (never a client-asserted actor).
ACTOR = "analyst@bank.example"

#: A tenant partition, so the outbound-review assertions are not all on the empty string.
TENANT = "demo-bank"

#: A case that MUST escalate: the deterministic band is HIGH, so rule R8 routing applies.
ESCALATING_CASE = TriageInput(
    subject="Acme Holdings (FICTIONAL)",
    text="urgent data breach reported by the branch",
)

#: A case that must NOT escalate: a router that manufactured a review here would be lying.
ROUTINE_CASE = TriageInput(
    subject="Beta Trading (FICTIONAL)",
    text="routine note about a stationery order",
)

#: A planted identifier, so a redaction assertion has an independent literal to look for
#: rather than trusting the pattern pack to agree with itself.
PLANTED_NRIC = "S1234567D"

#: A planted address, so the universal rows have an independent literal of their own.
PLANTED_EMAIL = "kai.tan@delta.example"

#: An escalating case that also carries personal data, for the redact-before-anything proofs.
PII_CASE = TriageInput(
    subject="Gamma LLP (FICTIONAL)",
    text=f"urgent breach, NRIC {PLANTED_NRIC} and mail ops@gamma.example on file",
)

#: The same, with the identifier in the SUBJECT as well. The citation LOCATOR is built from the
#: subject and the citation SNIPPET is cut from the text, so a redactor that masks only the
#: summary writes both back into the WORM record from a field nobody was looking at.
PII_SUBJECT_CASE = TriageInput(
    subject=f"Delta Pte Ltd (FICTIONAL) NRIC {PLANTED_NRIC}",
    text=f"urgent breach, contact {PLANTED_EMAIL} about NRIC {PLANTED_NRIC}",
)

#: One raw Aud1 intake row carrying the identifiers in the fields the normalizer copies into a
#: citation TITLE and SNIPPET. The CAPA assessment path never redacted at all, so this is the
#: input that proves the kernel boundary covers a second service, not only triage.
PII_AUD1_RAW: dict[str, object] = {
    "finding_id": "AUD1-9001",
    "title": f"Access review gap for NRIC {PLANTED_NRIC}",
    "rating": "high",
    "description": f"branch escalation, contact {PLANTED_EMAIL} about NRIC {PLANTED_NRIC}",
    "raised_on": "2026-01-05",
    "engagement": f"engagement for {PLANTED_EMAIL}",
}
