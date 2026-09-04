# Features FAQ

For a product owner, a risk lead or a delivery manager deciding what this system does, what it
refuses to do, and where its responsibility ends.

### What does it actually do?

It is the single system of record for what happens to an issue AFTER somebody raises it, in four
deterministic steps and one narrated one:

1. **Feed-agnostic intake** (`domain/capa.py`, `pipeline.py`): five source families (`internal-audit-lifecycle`
   findings, `continuous-controls-monitoring` exceptions, the `compliance-advisory` horizon change-feed, `complaints-review` findings and loss events) arrive
   in their own shapes and normalize into one `IssueEnvelope`. A record missing a required field
   raises `NormalizationError` and is DROPPED, never defaulted.
2. **The lifecycle state machine** on the `human-review-console` case-spine shape, as configuration: `raised` to
   `rca_drafted` to `remediation_in_progress` to `closure_submitted` to `validated` to `closed`,
   with a bounce-back from a rejected closure to remediation. An illegal transition raises.
3. **Deadlines and aging**: business-day maths with an explicit `as_of` and a holiday set passed
   in, yielding one of four verdicts with a total precedence (a closed issue never ages; SLA
   breach beats approaching deadline, which beats stuck in state, which beats on track).
4. **Closure that cannot self-serve**: `plan_transition` refuses the move to `closed` unless the
   issue type's evidence checklist is complete AND an approved `human-review-console` review reference is present.
5. **Thematic clustering** (`domain/themes.py`): issue vectors from the embeddings port are
   clustered greedily in id order by cosine proximity, so the same vectors and threshold always
   produce the same themes, each carrying its member citations.

`domain/rca.py` then asks the model for a short root-cause and remediation note that restates the
figures the engine produced.

### What makes an SLA breach or a closure refusal defensible?

Four rules in the engine, all pure code:

- **The clock is business days with an explicit calendar.** `add_business_days` and
  `business_days_between` take the holiday set as an argument rather than assuming one, and every
  aging call takes an explicit `as_of`, so the same issue on the same date always yields the same
  verdict, on any machine.
- **The aging precedence is total, not a chain of guesses.** Every branch of `aging_finding`
  reports the business-day figure it decided on, so "12 business days overdue" can be recomputed
  by hand from the issue and the calendar.
- **Only complete evidence closes an issue.** `closure_gaps` compares the provided evidence
  against the config-owned checklist for that issue type and names each missing item.
- **A closure also needs a human.** `plan_transition` refuses `closed` without an approved `human-review-console`
  review reference, so an issue cannot be closed by the pipeline that assessed it.

The model plays no part in any of it, and it can never tick a checklist item.

### What is the model allowed to write?

Only a short root-cause and remediation note that restates figures the engine produced, and it is
held to two hard rules before the note is allowed out: the reply must parse as JSON with the
requested key (malformed output is discarded, never repaired), and every integer in the note must
be one the engine actually produced (a note that invents a figure is discarded). When a note is
discarded, a deterministic note built from the engine facts is used instead, and the service
reports which path produced it, so the eval and the demo can tell them apart. The managed profile
also uses an embedding model, but only to turn issue text into vectors for clustering; the
clustering maths itself is stdlib. See [`../model-card.md`](../model-card.md).

### What will it refuse to do?

- **It will not admit a malformed record.** A schema-invalid raw record is dropped rather than
  defaulted, because a defaulted issue is one nobody can trace back to its source.
- **It will not close an issue on incomplete evidence, or with no approved review.**
  `ClosureBlockedError` names the missing checklist items.
- **It will not walk the lifecycle graph by accident.** `IllegalTransitionError` refuses any move
  the state machine does not permit.
- **It will not serve another tenant's issue store.** `authorize_issue_access` raises
  `CrossTenantError`, which the API maps to 403 and not 404, so a caller cannot probe for another
  store by asking politely.
- **It will not auto-execute a consequential result.** A submitted closure, an SLA breach, an
  approaching deadline and a stuck issue all set `requires_human_review` and are ROUTED to the
  `human-review-console` in the same call that produced them (rule R8).
- **It will not let `rcsa-kri-erm` write back.** The theme feed is read only; there is deliberately no
  write path.
- **It will not answer without provenance.** Every claim carries a `Citation`, and a theme carries
  its member issues' citations.

### Which surfaces expose it?

The FastAPI app (`POST /v1/issues/assess` for the lifecycle assessment, `GET /v1/themes` for the
one-way `rcsa-kri-erm` feed, plus the template's `POST /v1/triage`), the argparse CLI, the agent tools
(`triage_case`, `verify_audit_trail`, advertised on the A2A card at
`/.well-known/agent-card.json`), the embeddable `ui/` micro-frontend, and the eval harness. Each
routes escalations in the same call, so rule R8 does not hold on some surfaces and not others.

Note that the repo carries two verticals side by side today: the `issue-remediation-capa` engine
(`domain/capa.py`, `domain/themes.py`, `domain/rca.py`, `/v1/issues/assess` and `/v1/themes`) and
the template's generic triage service (`domain/triage_service.py`, `/v1/triage`, the CLI and the
agent tools). The triage path is scaffolding the render started from, and it doubles as the shared
R8 review envelope a CAPA assessment is projected onto before routing.

### What does this repo own, and what does it integrate?

| Concern | Owner | How this repo touches it |
|---|---|---|
| The issue and CAPA lifecycle to closure, and the systemic themes over it | **this repo (`issue-remediation-capa`)** | it IS the system of record. Upstream systems hand issues over rather than tracking remediation themselves. |
| Internal-audit findings | `internal-audit-lifecycle` internal audit lifecycle copilot | read as the `aud1_finding` source; `internal-audit-lifecycle` emits an approved finding one way and holds no remediation state. This repo never re-derives a finding's severity. |
| Control-testing exceptions | `continuous-controls-monitoring` control testing | read as the `aud2_exception` source. |
| The regulatory corpus and the change horizon | `compliance-advisory` | read as the `rsk1_horizon` source; this repo consumes change records, it does not track the corpus. |
| Document-review findings | `complaints-review` | read as the `doc6_finding` source. |
| Deferred feeders | `breach-reportability-assessor`, `whistleblower-triage` | named in the code as the sources the extensible-enum shape is meant to take later. Neither is a member of `IssueSource` yet and neither is wired, so adding one is a member, a normalizer and an adapter path rather than a redesign. |
| Enterprise risk aggregation | `rcsa-kri-erm` | consumes `GET /v1/themes`, one way and read only. `rcsa-kri-erm` interprets a systemic theme; this repo does not take a risk position. |
| Agent discovery and entitlements | `agent-registry` | this agent publishes a card; the registry owns discovery. |
| Model and agent promotion | `model-quality-gate` AI quality and model risk | `eval/run_eval.py --mode gate` asks `model-quality-gate`; the offline smoke mode never promotes. |
| Traces and the immutable audit sink | `agent-observability` agent observability | `AuditSinkPort` and `ObservabilityTracerPort`. |
| Human review and maker-checker | `human-review-console` human review console | `ReviewRouterPort` over the shared `review-kit`, and the approved reference it returns is what a closure requires. This repo produces escalations; it does not render a queue. |
| Prompt-injection defence and output filtering | `agent-guardrail-gateway` agent guardrail gateway | **not wired today.** It becomes mandatory the moment untrusted free text reaches the drafter (rule R1), and an upstream issue description is exactly that. |
| Grounded retrieval over an enterprise corpus | `enterprise-knowledge-base` | not wired; this service reasons over its own issue records rather than over documents, and the embeddings port serves clustering rather than retrieval. |

### Can I demo it without a cloud project?

Yes, and the demo is code rather than a deck. `make demo` runs a presenter-paced walkthrough over
eight steps (opened, routine, escalation, redaction, review queue, audit, tamper, portability) on
its own loopback server; `make demo-selftest` runs the same arc headless and asserts every
narrated claim, so a claim that stops being true fails a build rather than a meeting;
`make demo-static` renders the same audit-first panels to static HTML for screenshots.

### What is not built yet?

The honest list is [`../practices-audit.md`](../practices-audit.md) and the `TODO (repo owner)`
rows in [`../../COMPLIANCE.md`](../../COMPLIANCE.md). The three that matter most for a production
decision: a durable issue store behind a port (offline the fixture feed IS the register and no
lifecycle state survives a request), the `agent-guardrail-gateway` binding before upstream issue text reaches
the drafter, and registering this repo's metric bundle with `model-quality-gate` so `--mode gate` has an authority
to ask.
