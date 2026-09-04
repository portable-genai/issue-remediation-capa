# Security FAQ

For AppSec and security architecture. Every answer names the file that is the evidence, so the
review can read the control rather than the claim.

### Who is the actor on a decision, and can a caller assert it?

A server-verified `Principal`, always. The request schemas carry no `actor` field: the audit actor
and the review maker both come from the identity adapter, and every client-supplied actor, tenant,
role, ACL and authorization header is discarded at the browser boundary
(`ui/lib/embed-policy.mjs`). Under the `gcp` profile the adapter verifies the IAP-injected
assertion against the configured audience, against IAP's own key set and against the issuer
(`adapters/gcp/identity.py`); an unset or emptied `CAPA_IAP_AUDIENCE` REFUSES every caller,
because `audience=None` means google-auth does not verify the audience at all and would accept any
Google-signed token from any project.

### Can one tenant read another tenant's issue store?

No, and the refusal is loud rather than empty. `authorize_issue_access` in `domain/capa.py`
compares the verified principal's tenant against the store's owning tenant and raises
`CrossTenantError`, which `api/app.py` maps to **403 and never 404**: the store exists and the
caller is simply not authorised for it. An empty result would be indistinguishable from a store
with no issues, which is how a probe becomes an information leak. The tenant comes from the
verified principal, never from the request body, and both `/v1/issues/assess` and `/v1/themes` go
through the same check.

### The assessment request carries the lifecycle fields. Can a caller fake a closure?

Not a closure, but it can make the READOUT say closure is possible, and that is worth
understanding before deployment. There is no durable issue store yet, so `IssueAssessRequest`
supplies `state`, `state_since`, `provided_evidence` and `review_ref`, and `can_close` is computed
from them. No route performs a transition: `plan_transition`, the function that actually refuses a
closure without a complete checklist and an approved review reference, is not reachable from the
API today, and nothing in this repo records an issue as closed. Once you bind a durable store,
those four fields must come from the store and the review reference must be verified against `human-review-console`
rather than accepted from the body. Until then, treat `can_close` as an assessment of the inputs
you supplied, not as an authorisation.

### What happens if the profile variable goes missing in production?

The process still binds the SDK-free adapters (the alternative is importing cloud SDKs that are
not installed), but nobody chose them, so every relaxation is withdrawn: the seeded dev personas
refuse to construct, no service-to-service scheme is selected, the dev CORS allowlist and the
`X-Dev-Persona` header are gone, the interactive docs are not registered, and the loopback
exposure guard refuses every route to any non-loopback peer. An emptied or mis-capitalised value
raises AT IMPORT, so the process fails to boot rather than serving on a posture nobody chose
(`config.py`, `tests/unit/test_profile_single_source.py`).

### Does setting the service-to-service token open anything?

No, and this is enforced rather than intended. The exposure guard's posture is derived from the
identity BINDING (the adapter declares `VERIFIED` / `CLIENT_ASSERTED` / `UNIMPLEMENTED`), never
from a credential. `CAPA_S2S_TOKEN` authenticates a calling SERVICE and no end user.
`tests/unit/test_end_user_auth_posture.py` walks the guard's argument through the constants it
names and fails the build if a credential reappears at any depth, because it did once: setting the
token switched the guard off for the end-user routes it was protecting.

### Where does personal data go?

Issue subjects and descriptions arrive from upstream feeds and can carry personal data, so the
masking rule is unconditional rather than judged per record: it is applied before the audit write
(`domain/triage_service.py`), before a review payload leaves the process
(`adapters/_review_payload.py`), and before a tool result can enter a model's context
(`agent/tools.py`, which walks the whole nested result). The pattern set and its ORDER are this
vertical's (`domain/pii.py`, national rows for SG, HK, JP and AU first, universal rows last), drawn
from the shared `pii-kit`. The `pii_safety` eval metric holds this at `>= 0.99`, scored two ways
(the pack scan plus an independent planted-literal oracle), and
`tests/unit/test_not_falsely_green.py` proves the metric can go red.

Trace spans are separate and stricter. `CapaService.assess` attaches the action, the actor, the
source feed and the lifecycle state, all structural, and never the issue id, the title, the
description or a closure gap's wording, because a trace backend has no redaction stage, a wider
read audience and no retention rule written against a regulator's requirement.

### Can the model exfiltrate or invent anything?

The model is reachable through exactly one port (`ports/generation.py`), it receives a system
instruction plus a facts block the engine built, and its reply is parsed and REJECTED unless it is
well-formed JSON with the requested key and every integer in it is one the engine produced
(`domain/rca.py`: `parse_note`, `note_is_grounded`). A rejected reply is discarded and the
deterministic fallback note is used instead. The groundedness checks are module-level pure
functions rather than private methods, deliberately, so the eval measures the RAW model output
through the very same contract the service enforces: a metric that watched only the filtered
output could never go red. Crucially, nothing the model writes can flip `closure_gaps` or
authorise a closure.

The second model surface is the embeddings port, and it carries a different exposure: under `gcp`
the issue subject and description are SENT to a managed embedding model to produce vectors for
clustering. Nothing comes back but numbers, and no decision is taken from them beyond which theme
an issue joins, but the text has left the process. Prompt-injection screening through the `agent-guardrail-gateway` is **not** wired yet on either path.

### How is the audit trail protected?

Append-only and hash-chained, AND externally anchored. The chain catches an edit, a deletion or a
reorder; only the anchor catches a TRUNCATED TAIL, because dropping the newest rows leaves a
shorter chain that verifies perfectly. `audit_anchor_path` (`CAPA_AUDIT_ANCHOR`) writes the chain
head to a file on another volume, and `tests/unit/test_audit_anchor.py` proves the detection,
proves the control case goes UNDETECTED without an anchor, and proves an append after truncation
refuses rather than re-anchoring. Under the managed profile the sink is a locked Cloud Logging
bucket (`infra/terraform/logging_worm.tf`), which provides non-rewritability itself.

### What about supply chain?

Both lockfiles are committed and pin every dependency exactly; the catalog commons are pinned to
40-character COMMIT shas rather than tags, because a re-pushed tag changes what installs with no
diff in the lockfile. The base image is digest-pinned, Actions are SHA-pinned, dependabot covers
pip, docker, github-actions and npm, and `pip-audit` plus `npm audit --audit-level=high` are HARD
CI failures. `tests/unit/test_repo_artifacts.py` asserts each of these from inside the repo, and it
asks git whether each pinned sha is a COMMIT object rather than an annotated tag object, which a
regular expression cannot tell apart.

### What is deliberately out of scope?

- **Login.** This repo authenticates nobody itself: the platform in front of it does, and the UI
  forwards the assertion without parsing or trusting a parsed copy.
- **Injection defence and output filtering.** Owned by `agent-guardrail-gateway`; not bound yet.
- **The review queue.** Owned by `human-review-console`; this repo produces escalations and routes them.
- **Raising the issue.** Owned by `internal-audit-lifecycle`, `continuous-controls-monitoring`, `compliance-advisory` and `complaints-review`. This repo normalizes what they raise
  and never re-derives a finding's severity or re-runs a control test.
- **Durable storage of the issue register.** Not bound today: offline the fixture feed IS the
  register and lifecycle state comes in on the request. A deployment needs a store behind a port,
  and its access control is part of that work.
- **Network egress control.** VPC-SC governs access to Google APIs across perimeters, not
  arbitrary internet egress. The private-egress rule that lets this service reach its source
  feeds and the `human-review-console` and nothing else is an adopter network decision, called out in
  `COMPLIANCE.md` P-01.
