#!/usr/bin/env python3
"""Evaluation gate for Issue Remediation and CAPA Tracker (Aud3).

Two named layers via ``--mode`` (the scaffold is ``agent_eval_kit.eval_main``):

* **smoke** (default) - the offline pre-merge check CI runs on every change: it drives the real
  ``TriageService`` against a golden set with SDK-free local adapters and scores two metrics.
* **gate** - the promotion verdict from the shared Hrz4 authority (requires the ``gcp``
  profile), via ``agent_eval_kit.PromotionGateClient``.

Exit is ``0`` iff every metric meets its threshold (and, in gate mode, the authority agrees).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from agent_eval_kit import EvalMetricResult, EvalReport, PromotionGateClient, eval_main
from pii_kit import pack_leak

from issue_remediation_capa.adapters.local.audit import (
    LocalAuditAdapter,
)
from issue_remediation_capa.adapters.local.embeddings import (
    LocalHashingEmbeddingAdapter,
)
from issue_remediation_capa.adapters.local.generation import (
    LocalGenerationAdapter,
)
from issue_remediation_capa.adapters.local.tracer import (
    LocalNoopTracerAdapter,
)
from issue_remediation_capa.config import (
    Settings,
)
from issue_remediation_capa.domain.capa import (
    CapaService,
    IssueRecord,
    IssueSource,
    LifecycleState,
    normalize_issue,
)
from issue_remediation_capa.domain.kernel import (
    Citation,
)
from issue_remediation_capa.domain.models import (
    TriageInput,
)
from issue_remediation_capa.domain.pii import (
    PII_PATTERNS,
)
from issue_remediation_capa.domain.rca import (
    build_request,
    note_is_grounded,
    parse_note,
)
from issue_remediation_capa.domain.themes import (
    ClusteredIssue,
    cluster_issues,
    theme_purity,
)
from issue_remediation_capa.domain.triage_service import (
    TriageService,
)
from issue_remediation_capa.pipeline import (
    DEFAULT_THEME_THRESHOLD,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = _REPO_ROOT / "eval" / "datasets" / "golden_cases.jsonl"
#: The CAPA lifecycle oracle: hand-computed normalization, due-date/aging and closure outcomes.
CAPA_DATASET = _REPO_ROOT / "eval" / "datasets" / "capa_oracle.jsonl"
#: The thematic-clustering oracle: issues with independent gold theme labels.
THEMES_DATASET = _REPO_ROOT / "eval" / "datasets" / "themes_oracle.jsonl"

THRESHOLDS: dict[str, float] = {
    "decision_accuracy": 0.80,
    "pii_safety": 0.99,
    "normalization_accuracy": 0.99,
    "deadline_accuracy": 0.99,
    "closure_safety": 0.99,
    "theme_purity": 0.90,
    "rca_groundedness": 0.99,
}
#: The registered Hrz4 metric bundle for this vertical (Hrz4 owns the metrics + thresholds).
_BUNDLE = "issue-remediation-capa"


def _load(path: Path) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    if not cases:
        raise SystemExit(f"{path}: golden dataset is empty")
    return cases


def _load_scenarios(path: Path) -> list[dict[str, object]]:
    """Load a jsonl oracle whose rows carry mixed types (bools, ints, lists), not just strings."""
    cases: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    if not cases:
        raise SystemExit(f"{path}: golden dataset is empty")
    return cases


def _mean(scores: list[float]) -> float:
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def audit_texts(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every CONTENT-bearing field of every audit row, which is what a leak scan has to read.

    Collecting ``redacted_summary`` and nothing else would name the one field the
    redactor already masks: the metric would ask the redactor whether it had redacted, believe
    the answer, and report a green while the SAME record's citation snippet carries the
    identifier verbatim. Citations travel inside the record, and they carry raw source text in
    ``snippet`` and, routinely, in ``source_id`` (a locator built from the case subject or from
    an intake feed's own id).

    ``actor`` is excluded deliberately: it is the verified principal and an address by design, so
    a blanket scan over a whole row could never go green, and a metric nobody can make green
    gets deleted rather than fixed.
    """
    texts: list[str] = []
    for row in rows:
        texts.append(str(row.get("redacted_summary", "")))
        texts.append(json.dumps(row.get("citations", []), sort_keys=True))
    return texts


def pii_safety(records: Sequence[str], planted: Sequence[str]) -> float:
    """No identifier may survive into an audit record, by the pack rows OR by planted literal.

    Two oracles, because they fail independently: the pack scan uses the same rows the redactor
    masks with (so a redactor that skipped a field is caught), and the planted-literal check
    fires even if a pattern row is broken (so a pack that stopped matching is caught too).
    """
    pack_leaked = any(pack_leak(text, PII_PATTERNS) for text in records)
    literal_leaked = any(token in text for token in planted for text in records)
    return 0.0 if (pack_leaked or literal_leaked) else 1.0


def _record_from(case: dict[str, object]) -> IssueRecord:
    """Rebuild the issue record a CAPA-oracle row describes (raw record + lifecycle fields)."""
    source = IssueSource(str(case["source"]))
    raw = case["raw"]
    assert isinstance(raw, dict)
    envelope = normalize_issue(raw, source)
    evidence_raw = case.get("provided_evidence")
    evidence = tuple(str(item) for item in evidence_raw) if isinstance(evidence_raw, list) else ()
    return IssueRecord(
        envelope=envelope,
        state=LifecycleState(str(case["state"])),
        state_since=date.fromisoformat(str(case["state_since"])),
        provided_evidence=evidence,
        review_ref=str(case.get("review_ref") or ""),
    )


def score_capa(cases: list[dict[str, object]]) -> tuple[float, float, float, float]:
    """Score normalization, deadlines, closure and RCA groundedness against the CAPA oracle.

    Returns ``(normalization_accuracy, deadline_accuracy, closure_safety, rca_groundedness)``. Each
    compares the deterministic engine's output to the row's INDEPENDENT hand-computed literals.
    ``rca_groundedness`` runs the RAW local narrator (never the already-filtered service output,
    which could not go red) and scores whether every figure in its note is one the engine produced.
    """
    settings = Settings(profile="local", audit_path=":memory:")
    audit = LocalAuditAdapter(settings)
    service = CapaService(audit, tracer=LocalNoopTracerAdapter(settings))
    generation = LocalGenerationAdapter(Settings(profile="local"))

    norm_scores: list[float] = []
    deadline_scores: list[float] = []
    closure_scores: list[float] = []
    grounded_scores: list[float] = []
    for case in cases:
        record = _record_from(case)
        env = record.envelope
        norm_ok = (
            env.source.value == str(case["expected_source"])
            and env.issue_type.value == str(case["expected_type"])
            and env.severity.value == str(case["expected_severity"])
            and env.opened_on.isoformat() == str(case["expected_opened"])
        )
        norm_scores.append(1.0 if norm_ok else 0.0)

        assessment = service.assess(
            record, as_of=date.fromisoformat(str(case["as_of"])), actor="eval-bot"
        )
        deadline_ok = (
            assessment.due_on.isoformat() == str(case["expected_due"])
            and assessment.aging_kind.value == str(case["expected_aging"])
            and assessment.overdue_business_days == int(str(case["expected_overdue"]))
        )
        deadline_scores.append(1.0 if deadline_ok else 0.0)
        closure_scores.append(
            1.0 if assessment.can_close == bool(case["expected_can_close"]) else 0.0
        )

        request = build_request(assessment)
        raw_note = generation.generate(request)
        note = parse_note(raw_note.text)
        grounded = note is not None and note_is_grounded(note, request.facts)
        grounded_scores.append(1.0 if grounded else 0.0)

    return (
        _mean(norm_scores),
        _mean(deadline_scores),
        _mean(closure_scores),
        _mean(grounded_scores),
    )


def score_theme_purity(cases: list[dict[str, object]]) -> float:
    """Cluster the oracle issues over the local embedder and score purity vs the gold labels."""
    embedder = LocalHashingEmbeddingAdapter(Settings(profile="local"))
    texts = tuple(str(case["text"]) for case in cases)
    vectors = embedder.embed(texts)
    items = tuple(
        ClusteredIssue(
            issue_id=str(case["id"]),
            vector=vector,
            label_hint=str(case["gold"]),
            citation=Citation(source_id=str(case["id"]), title=str(case["id"]), snippet=""),
        )
        for case, vector in zip(cases, vectors, strict=True)
    )
    themes = cluster_issues(items, threshold=DEFAULT_THEME_THRESHOLD)
    gold = {str(case["id"]): str(case["gold"]) for case in cases}
    return theme_purity(themes, gold)


def run_smoke(dataset: Path) -> EvalReport:
    cases = _load(dataset)
    settings = Settings(profile="local", audit_path=":memory:")
    audit = LocalAuditAdapter(settings)
    service = TriageService(audit, tracer=LocalNoopTracerAdapter(settings))

    decision_scores: list[float] = []
    for case in cases:
        result = service.triage(
            TriageInput(subject=case["subject"], text=case["text"]), actor="eval-bot"
        )
        decision_scores.append(1.0 if result.severity.value == case["expected_severity"] else 0.0)

    # pii_safety: no raw identifier may survive into any audit record. `audit_texts` decides
    # WHICH fields count as the record's content; see its docstring for why the summary alone is
    # not the record.
    records = audit_texts(audit.log.read_all())
    planted = [case["planted"] for case in cases if case.get("planted")]

    # The CAPA vertical: the deterministic engine against a hand-computed oracle (normalization,
    # due dates / aging, closure), and the RCA narrator held to the engine's figures. The theme
    # clusterer against an independent gold labelling. All offline (SDK-free local adapters).
    capa_cases = _load_scenarios(CAPA_DATASET)
    theme_cases = _load_scenarios(THEMES_DATASET)
    norm_acc, deadline_acc, closure_safety, rca_grounded = score_capa(capa_cases)
    purity = score_theme_purity(theme_cases)

    results = (
        EvalMetricResult.scored(
            "decision_accuracy", _mean(decision_scores), THRESHOLDS["decision_accuracy"]
        ),
        EvalMetricResult.scored(
            "pii_safety", pii_safety(records, planted), THRESHOLDS["pii_safety"]
        ),
        EvalMetricResult.scored(
            "normalization_accuracy", norm_acc, THRESHOLDS["normalization_accuracy"]
        ),
        EvalMetricResult.scored("deadline_accuracy", deadline_acc, THRESHOLDS["deadline_accuracy"]),
        EvalMetricResult.scored("closure_safety", closure_safety, THRESHOLDS["closure_safety"]),
        EvalMetricResult.scored("theme_purity", purity, THRESHOLDS["theme_purity"]),
        EvalMetricResult.scored("rca_groundedness", rca_grounded, THRESHOLDS["rca_groundedness"]),
    )
    return EvalReport(
        dataset=str(dataset),
        results=results,
        n_examples=len(cases) + len(capa_cases) + len(theme_cases),
    )


def run_gate(dataset: Path) -> tuple[EvalReport, bool]:
    settings = Settings.load()
    if settings.profile != "gcp":
        raise SystemExit(
            "--mode gate is the promotion authority and requires "
            f"CAPA_PROFILE=gcp (got {settings.profile!r}); "
            "run --mode smoke for the offline pre-merge check."
        )
    client = PromotionGateClient(
        os.environ.get("CAPA_QUALITY_URL", "http://localhost:8084"),
        bundle=_BUNDLE,
        model="gemini-3.5-flash",
    )
    return client.evaluate(str(dataset)), client.gate(str(dataset))


if __name__ == "__main__":
    raise SystemExit(
        eval_main(
            smoke=run_smoke,
            gate=run_gate,
            default_dataset=DEFAULT_DATASET,
            description="Offline / Hrz4 evaluation gate for Aud3.",
        )
    )
