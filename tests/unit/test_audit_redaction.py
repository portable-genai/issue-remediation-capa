"""Nothing redaction removed survives anywhere else in the WORM record (check C3).

``TriageService.triage`` masked ``redacted_summary`` and then handed the SAME event its citations
untouched, so the identifier the summary no longer carried was persisted verbatim one field away,
in a record that is by design immutable and long-retained. ``CapaService.assess`` was worse: it
passed an engine-built summary through with no redaction at all and forwarded the intake
envelope's citations unchanged. The summary is not the record.

Two rules this suite holds, and they pull in opposite directions, which is why both are written
down:

* every CONTENT field is scanned: the summary, and each citation's locator, title and snippet.
  The triage locator is built from the case subject and its snippet is cut from the case text;
  the CAPA citation's title and snippet are copied from an intake feed's own wording. All of it
  is raw client text with a structural-looking name.
* the ATTRIBUTION field is not. ``actor`` is the verified principal and is an address by design,
  so a blanket scan over a whole audit row could never go green, and a scan that "fixed" that by
  masking the actor would erase the only column that says who acted.

Scored two ways, as the eval metric is: the shared pack's own rows, plus the planted literals,
which still fire if a pattern row is broken.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest
from hex_service_kit import to_jsonable
from pii_kit import pack_leak
from review_kit import Review

from issue_remediation_capa.adapters._review_payload import result_to_review
from issue_remediation_capa.adapters.local.audit import LocalAuditAdapter
from issue_remediation_capa.api.app import _capa_to_review
from issue_remediation_capa.domain.capa import (
    CapaService,
    IssueRecord,
    IssueSource,
    LifecycleState,
    normalize_issue,
)
from issue_remediation_capa.domain.models import TriageInput
from issue_remediation_capa.domain.pii import PII_PATTERNS
from issue_remediation_capa.domain.triage_service import TriageService

from tests.fixtures import sample_cases

_PLANTED = (sample_cases.PLANTED_NRIC, sample_cases.PLANTED_EMAIL)

#: The outbound review's ATTRIBUTION columns, excluded from the leak scan for the same reason
#: ``actor`` is excluded from the audit scan. See :func:`_payload`.
_ATTRIBUTION_FIELDS = frozenset({"maker", "tenant"})


def _content(row: Mapping[str, Any]) -> str:
    """Every content-bearing field of one audit row, as one scannable blob.

    ``actor`` and the structural columns are excluded deliberately: see the module docstring.
    """
    return " ".join(
        (
            str(row.get("redacted_summary", "")),
            json.dumps(row.get("citations", []), sort_keys=True),
        )
    )


def _assert_clean(rows: list[Mapping[str, Any]]) -> None:
    assert rows, "the service path wrote no audit record, so this proves nothing"
    for row in rows:
        blob = _content(row)
        assert not pack_leak(blob, PII_PATTERNS), f"pack row matched in the WORM record: {blob}"
        for token in _PLANTED:
            assert token not in blob, f"planted {token!r} survived into the WORM record: {blob}"


def _capa_record() -> IssueRecord:
    """One overdue Aud1 issue whose citation title and snippet carry the planted identifiers."""
    return IssueRecord(
        envelope=normalize_issue(sample_cases.PII_AUD1_RAW, IssueSource.AUD1_FINDING),
        state=LifecycleState.REMEDIATION_IN_PROGRESS,
        state_since=date(2026, 1, 10),
    )


@pytest.mark.parametrize(
    "case",
    [sample_cases.PII_CASE, sample_cases.PII_SUBJECT_CASE],
    ids=["identifier-in-text", "identifier-in-subject-and-text"],
)
def test_no_identifier_reaches_the_audit_record(
    triage_service: TriageService, container: Any, case: TriageInput
) -> None:
    triage_service.triage(case, actor=sample_cases.ACTOR)

    audit = container.audit
    assert isinstance(audit, LocalAuditAdapter)
    _assert_clean(list(audit.log.read_all()))


def test_no_identifier_reaches_the_audit_record_from_the_capa_path(container: Any) -> None:
    """The SECOND construction site, which never called ``redact`` on anything at all."""
    audit = container.audit
    assert isinstance(audit, LocalAuditAdapter)
    CapaService(audit, tracer=container.tracer).assess(
        _capa_record(), as_of=date(2026, 6, 1), actor=sample_cases.ACTOR
    )

    _assert_clean(list(audit.log.read_all()))


def test_the_actor_is_kept_verbatim_because_it_is_attribution(
    triage_service: TriageService, container: Any
) -> None:
    """The caveat, pinned: the principal is an address and must NOT be masked away."""
    triage_service.triage(sample_cases.PII_CASE, actor=sample_cases.ACTOR)

    audit = container.audit
    assert isinstance(audit, LocalAuditAdapter)
    actors = [str(row.get("actor", "")) for row in audit.log.read_all()]
    assert actors == [sample_cases.ACTOR]


def _payload(review: Review) -> str:
    """The WHOLE outbound review as one scannable blob, minus the attribution fields.

    Serialised generically off the dataclass rather than from a hand-listed set of names, so a
    field added to ``Review`` later is scanned by DEFAULT instead of by somebody remembering to
    extend this. That is the whole lesson of ``case_ref`` and ``source_key``: they were missed
    because a reader listing "the content fields" did not read a case reference or an
    idempotency key as content, and both were built from the case subject.

    ``maker`` and ``tenant`` are excluded for the same reason ``actor`` is excluded from the
    audit scan: the maker is the verified principal and is an address by design, so a blanket
    scan over the whole payload could never go green.
    """
    body = {
        name: value
        for name, value in to_jsonable(review).items()
        if name not in _ATTRIBUTION_FIELDS
    }
    return json.dumps(body, sort_keys=True)


def _assert_payload_clean(review: Review) -> None:
    blob = _payload(review)
    assert not pack_leak(blob, PII_PATTERNS), f"pack row matched in the review payload: {blob}"
    for token in _PLANTED:
        assert token not in blob, f"planted {token!r} crossed to the console: {blob}"


def test_the_whole_review_payload_is_redacted_not_only_its_narrative_fields(
    triage_service: TriageService,
) -> None:
    """Every field that crosses to the console, including the ones with structural names.

    ``subject`` and ``summary`` were masked and ``case_ref`` and ``source_key`` were not, so the
    identifier the payload had just removed from two fields crossed the wire in the two beside
    them. A citation LOCATOR is the same trap one level down.
    """
    result = triage_service.triage(sample_cases.PII_SUBJECT_CASE, actor=sample_cases.ACTOR)

    _assert_payload_clean(
        result_to_review(result, maker=sample_cases.ACTOR, tenant=sample_cases.TENANT)
    )


def test_the_whole_capa_review_payload_is_redacted(container: Any) -> None:
    """The CAPA assessment rides the same shared envelope, with feed-authored citation fields."""
    assessment = CapaService(container.audit, tracer=container.tracer).assess(
        _capa_record(), as_of=date(2026, 6, 1), actor=sample_cases.ACTOR
    )

    _assert_payload_clean(
        result_to_review(
            _capa_to_review(assessment), maker=sample_cases.ACTOR, tenant=sample_cases.TENANT
        )
    )


def test_the_payload_scan_excludes_attribution_so_it_can_ever_be_green() -> None:
    """The caveat, pinned: the maker is an address, so scanning it makes the test permanently red.

    Without this, the next person to widen ``_payload`` to the whole dataclass gets a red they
    cannot fix, and relaxes the assertion instead of narrowing the scan.
    """
    assert "maker" in to_jsonable(
        Review(
            action="a",
            subject="s",
            maker=sample_cases.ACTOR,
            tenant=sample_cases.TENANT,
            summary="s",
            severity="high",
            required_approvals=1,
            sod_group="g",
            case_ref="c",
            source_key="k",
        )
    ), "Review no longer carries a maker; re-check what this scan must exclude"
    assert frozenset({"maker", "tenant"}) == _ATTRIBUTION_FIELDS


def test_the_source_key_is_stable_under_redaction_so_retries_stay_idempotent(
    triage_service: TriageService,
) -> None:
    """The named cost of reusing the masked subject: the key must still survive a retry.

    ``pii_kit.redact`` substitutes a fixed literal token per pattern, with no hash and no salt,
    so the same subject always yields the same key. Pinned rather than assumed, because a masking
    style that ever became random would silently turn every retried delivery into a second review.
    """
    result = triage_service.triage(sample_cases.PII_SUBJECT_CASE, actor=sample_cases.ACTOR)
    keys = {
        result_to_review(result, maker=sample_cases.ACTOR, tenant=sample_cases.TENANT).source_key
        for _ in range(200)
    }

    assert len(keys) == 1, f"the idempotency key is not stable under redaction: {keys}"
    assert sample_cases.PLANTED_NRIC not in keys.pop()
