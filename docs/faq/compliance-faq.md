# Compliance FAQ

For compliance, model risk and the second line. The mapping table with a file reference on every
row is [`../../COMPLIANCE.md`](../../COMPLIANCE.md); this page answers the questions that come
back after reading it.

### Is an overdue figure from this system defensible?

That is the reason the clock is pure code. `add_business_days` and `business_days_between` in
`domain/capa.py` produce every date and every count, and four properties make the number mean
something:

- **Business days, with the calendar supplied.** The holiday set is a parameter, never an
  assumption, so a jurisdiction's calendar is an input somebody owns rather than a hidden default.
- **An explicit `as_of`.** Every aging call is made against a date the caller passed, so an
  assessment can be replayed exactly as it stood on the day it was made.
- **A total precedence.** `aging_finding` decides in a fixed order: a closed issue never ages, an
  SLA breach beats an approaching deadline, which beats stuck in state, which beats on track. Each
  branch reports the figure it decided on, so "12 business days overdue" is recomputable by hand.
- **The SLA belongs to the bank.** `DEFAULT_CLOCK` holds the per-severity remediation days, the
  approaching window and the stuck threshold in one frozen object, so a second-line reviewer reads
  the policy in one place rather than hunting literals.

The model plays no part in any of it, and the same issue with the same calendar always produces
the same verdict.

### Can an issue be closed without a human?

No, and this is a property of the engine rather than a promise in a document.
`plan_transition` refuses the move to `closed` on two independent grounds: `ClosureBlockedError`
when the issue type's evidence checklist has any gap, naming each missing item, and again when no
approved Hrz7 review reference is present. The model can summarize evidence, and it can never
satisfy a checklist item. Note the honest limit today: no route performs a transition, and the
assessment's `can_close` readout is computed from lifecycle fields the caller supplied, because
there is no durable store yet. Binding that store, so the state and the approved review reference
come from it rather than from the request, is the first adoption task.

### Who signs off, and how is the escalation guaranteed to arrive?

`requires_human_review` and the call to `ReviewRouterPort.route` are one act, not a flag plus an
intention: the API, the CLI and the agent tools all route in the same call that produced the
result, and `tests/unit/test_review_routing.py` asserts the routing rather than the flag. A
submitted closure always escalates, because a closure validated by the pipeline that assessed it
is not validated at all; so do an SLA breach, an approaching deadline and a stuck issue. Under the
managed profile the router REFUSES when no console is configured, so a deployment cannot swallow
an escalation silently.

### Where does the data live, and is residency enforced or just documented?

Enforced at deploy time. The region is chosen once (`asia-southeast1`) and shared by the runtime
and Terraform: `infra/terraform/variables.tf` validates the region against the residency allowlist
at plan, `org_policy.tf` pins `gcp.resourceLocations` to that region's location group, and every
regional resource (the CMEK key ring, the WORM log bucket, the Cloud Run service) is created in
it. `infra/terraform/production_edge.tftest.hcl` is the standing proof: its
`reject_region_outside_the_residency_allowlist` and `residency_defaults_are_in_country` runs fail
if the allowlist stops refusing or a resource drifts off region, and they run against a mocked
provider so they need no project and no credentials. The same region is what the managed embedding
adapter passes to `vertexai.init`, so issue text is embedded in the region the stack is pinned to.

### What about key management and least privilege?

One REGIONAL CMEK key with a 90-day rotation (`rotation_period = "7776000s"`), and an explicit key
binding for EACH service agent that encrypts under it, because CMEK does not cascade
(`infra/terraform/kms.tf`). One serving identity holding only the roles a request needs, each
traceable to a bound adapter, with `logging.logWriter` write only so the process cannot read back
the WORM trail it writes (`iam.tf`). Exportable service-account keys are forbidden by org policy
rather than merely avoided, and a key creation raises an alert if one happens anyway
(`org_policy.tf`, `monitoring.tf`).

### How long is the audit trail kept, and can it be edited?

180 days by default, and the variable refuses anything below 180, refuses to reduce an existing
locked retention, and is proved to refuse both (`reject_retention_below_six_months`,
`reject_reducing_existing_locked_retention`). The Cloud Logging bucket is LOCKED by default, which
is irreversible: once applied, retention cannot be reduced and the bucket cannot be deleted for
the full window, not even with project-owner rights. Confirm `retention_days` before the first
apply. DATA_READ audit logging is enabled too, so a read of the issue store is itself recorded.

Note that a remediation lifecycle can outlive a 180-day window: an issue raised in January and
closed in December leaves its earliest assessments outside the trail if retention is left at the
floor. Set `retention_days` against your longest expected remediation, not against the minimum.

Offline the same guarantee is earned differently: the log is hash-chained AND externally anchored,
because a truncated tail leaves a shorter chain that verifies perfectly. The retention schedule
and the legal basis for the trail are adopter-owned.

### What personal data does this system process?

More than a purely numeric service does, and the controls assume that. Issue subjects and
descriptions come from upstream feeds and can carry names, identifiers and free-text narrative, so
masking is unconditional rather than judged per record: before the audit write, before the
outbound review payload, and before any tool result that could enter a model's context, with the
jurisdiction rows and their ORDER chosen in `domain/pii.py`. The `pii_safety` metric holds this at
`>= 0.99` and is proved able to go red. Trace spans carry structural attributes only (action,
actor, source, state), never the issue id, the title or a closure gap's wording.

The one boundary worth naming explicitly: under the managed profile the issue subject and
description are sent to a Vertex embedding model to produce clustering vectors. Nothing but
numbers comes back, but the text has left the process, and that transfer needs the same
sign-off as any other model call.

### Can one business unit see another's issues?

No. `authorize_issue_access` compares the verified principal's tenant against the store's owning
tenant and raises rather than returning an empty result, and the tenant comes from the verified
principal rather than from the request body. The API answers 403 and not 404, deliberately: the
store exists. Note the honest limit: offline the fixture IS the demo bank's register, so
multi-tenant isolation at the STORE level is part of binding a durable store, which this repo has
not done yet.

### What model-risk evidence exists?

[`../model-card.md`](../model-card.md) records the model boundary as built: a generation model
writes one root-cause note that restates engine figures, its reply is schema-validated and
groundedness-checked and discarded on failure, and a deterministic fallback note is used instead;
an embedding model produces vectors for clustering and nothing else. The offline eval scores
`decision_accuracy`, `pii_safety`, `normalization_accuracy`, `deadline_accuracy`,
`closure_safety`, `theme_purity` and `rca_groundedness` on every change, and the groundedness
metric measures raw model output rather than filtered output so it can go red. What is NOT yet in
place: neither model is pinned to a confirmed id and version for the deployment region, there is
no token budget, rate limit or kill switch, no live-model eval run has been registered with the
Hrz4 promotion gate, and prompt-injection screening through Hrz1 is not bound. Until those close,
the managed model paths are not production-cleared and the deterministic path is what should be
relied on.

### Which regulations does this claim to satisfy?

None, on your behalf. The mapping in `COMPLIANCE.md` is to the CATALOG's own principles (P-01 to
P-13) and platform rules (R1 to R8). The crosswalk from those to MAS TRM, CPS 234, CPS 230, HKMA
or PDPA control ids, and the judgement that a control is SUFFICIENT for a regulation, is
explicitly adopter-owned. No row in that document should be quoted as regulatory assurance, and
the second-line review of the deterministic policy in `domain/` is bank-owned logic rather than a
vendor default to inherit unexamined. That applies most sharply to the remediation SLAs and the
closure checklists: those are the numbers a regulator asks about.

### What is still open at go-live?

The `Partial` and `TODO (repo owner)` rows in `COMPLIANCE.md`, each of which names exactly what is
missing. The ones that need a risk acceptance if you go live without them: the durable issue store
and its object-level authorisation (which is also what moves the closure preconditions off the
request body), rule R1 (the Hrz1 guardrail binding), rule R5 and P-08 (the Hrz4 metric bundle),
P-10 (timeouts, circuit breaker and a documented kill switch), and P-01's private-egress rule,
which depends on your own network rather than on this repo.
