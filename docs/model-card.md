# Model card: Issue Remediation and CAPA Tracker (`issue-remediation-capa`)

This is a STARTER model card. It records the model boundary as built and the controls that must be
completed before a managed deployment. The deterministic CAPA engine is the system of record. Two
model seams exist and they are deliberately unequal: a **generation** model writes one paragraph,
and an **embedding** model turns issue text into vectors for clustering. Neither produces a date,
a verdict or a closure.

## What the models do, and do not do

- **Generation does**: write a short root-cause and remediation note that restates a
  `CapaAssessment` the engine has ALREADY computed. It receives a system instruction plus a facts
  block of engine-owned values (`domain/rca.py:build_request`, drawn from
  `CapaAssessment.facts()`: severity, overdue business days, missing evidence count, aging kind,
  lifecycle state) and returns JSON.
- **Embedding does**: turn each issue's subject and description into a vector, which
  `domain/themes.py:cluster_issues` groups by cosine proximity into systemic themes. This is the
  one place a model influences an output: which theme an issue joins.
- **Neither does**: produce a due date, an aging verdict, an overdue count, a closure gap, a
  closure decision or an escalation. Those come from `add_business_days`, `aging_finding`,
  `closure_gaps` and `plan_transition` in the stdlib-only `domain/capa.py`. With the local stub
  generation adapter bound, every consequential field is identical, so a generation-model change
  cannot move a figure.

## Boundary and validation

- Each model is reachable through exactly one port, `ports/generation.py` and
  `ports/embeddings.py`. There is no third model seam in the repo.
- The generated note is held to two hard rules before it is allowed out (`domain/rca.py`):
  **schema validation**, so output that is not JSON with the requested `note` key is discarded
  rather than repaired; and **groundedness**, so every integer in the note must be one the engine
  produced (`grounded_integers`, `note_is_grounded`). A note that invents a figure is discarded.
- When a note is discarded, `fallback_text` builds a deterministic note purely from the engine
  facts, so a surface always has a grounded sentence and never a hallucinated one. A model call
  that RAISES lands in the same fallback: a narration failure degrades, it never crashes a
  decision. The result reports `model_authored`, so the eval and the demo can tell the paths apart.
- **The model can never close an issue.** The system instruction says so, and more importantly the
  engine enforces it: nothing in `domain/rca.py` can flip `closure_gaps`, and `plan_transition`
  refuses `closed` without a complete checklist and an approved `human-review-console` review reference.
- The parsing and groundedness checks are module-level pure functions rather than private methods,
  deliberately: the `rca_groundedness` eval metric measures the RAW model output through the very
  same contract the service enforces. A metric that watched only the already-filtered service
  output could never go red.
- The embedding path has no equivalent validator, because a vector has nothing to validate. It is
  bounded a different way: the clustering that consumes it is pure, deterministic and scored.
  `cluster_issues` walks issues in id order with no random seed and no dict-order dependence, and
  `theme_purity` scores the result against an INDEPENDENT gold labelling with a `>= 0.90`
  threshold, so a degraded embedder shows up as impure clusters rather than as silence.
- Personal data is masked before the audit write, before an outbound review payload and before a
  tool result can enter a model's context (`domain/pii.py`, `adapters/_review_payload.py`,
  `agent/tools.py`). Note the asymmetry that matters for review: the embedding call is the one path
  where issue subject and description leave the process unmasked under the managed profile.
- Every consequential result sets `requires_human_review` and is routed to `human-review-console` (rule R8) in the
  same call; nothing auto-executes.

## Adapters and profiles

| Profile | Generation adapter | Embedding adapter | Behaviour |
|---|---|---|---|
| `local` | `adapters/local/generation.py` | `adapters/local/embeddings.py` | Generation is a deterministic stub restating the request's engine facts as a JSON note, grounded by construction. Embedding is a real 64-dimension feature-hashing embedder built from `hashlib`: same text, same vector, so clustering and `theme_purity` are replayable. Both SDK-free, no network. A silent empty return would let a producer ship either seam unwired, so both emit real, inspectable output. |
| `gcp` | `adapters/gcp/generation.py` | `adapters/gcp/embeddings.py` | Generation is Gemini via `google.generativeai`, imported lazily inside the method, model id pinned in the adapter as `_MODEL`, currently `gemini-3.5-flash`, with `response_mime_type=application/json`, `temperature=0.2` and a caller-supplied `max_output_tokens` (the `GenerationRequest` default is 512). Embedding is Vertex AI via `vertexai.language_models.TextEmbeddingModel`, also lazily imported, model id pinned as `_MODEL`, currently `text-embedding-005`, initialised at `settings.region`. |
| `onprem` | `adapters/onprem/generation.py` | `adapters/onprem/embeddings.py` | Fail-fast placeholders: both refuse at call time rather than pretending, so a placeholder never becomes a silent no-op on a path where an empty answer (a blank note, a zero vector) would look like a working model. |

## Remaining controls (TODO, repo owner)

- **Model ids, versions and regions** (P-07). Both ids are pinned defaults in their adapters, not
  confirmed deployment decisions. `gemini-3.5-flash` in particular must be confirmed against your
  deployment region before a managed deploy: Gemini model ids are regional and an unavailable one
  fails at call time rather than at boot, so a stack that plans and applies cleanly can still fail
  on its first real request. `text-embedding-005` needs the same confirmation, plus one extra
  consideration: changing the embedding model changes every vector, so the theme threshold must be
  re-tuned and `theme_purity` re-scored rather than assumed to carry over. Pin the exact versions
  and record them here. Note the generation id also appears in a SECOND place, the
  `PromotionGateClient(..., model=...)` argument in `eval/run_eval.py`, and the two are not held
  equal by a test; change both together.
- **Budget, rate limit and a kill switch** (P-10, P-11): `max_output_tokens` is per request and
  there is no per-tenant token budget, no request rate limit, and no switch that forces
  deterministic-only operation. The generation fallback path already exists, since a discarded or
  failed note yields the deterministic text, but nothing yet lets an operator disable either model
  deliberately, and the theme feed has no deterministic fallback at all: with no embedder bound,
  `GET /v1/themes` fails rather than degrading.
- **Evaluation of the live models**: the offline eval scores the deterministic pipeline with the
  stub generator and the hashing embedder against the golden set and both oracles. Add a
  managed-profile run, registered with the `model-quality-gate` promotion gate (P-08, rule R5), that scores
  `rca_groundedness` with the real generation model bound and `theme_purity` with the real
  embedder bound.
- **Prompt-injection screening** (rule R1): the `agent-guardrail-gateway` is not bound, and this repo
  needs it. The facts block carries engine values rather than free text, which keeps the generation
  path narrow, but the issue subject and description that reach the EMBEDDING call are untrusted
  text from an upstream feed. Screen them, and fail closed to deterministic-only when the screen is
  unavailable.
- **Reasoning trace**: the audit record carries the engine assessment and its citations, not the
  prompt and reply pair. `COMPLIANCE.md` P-07 records that as owed.

Until these are complete the system is safe to run offline (deterministic engine plus the stub
generator and the hashing embedder) and the managed model paths are not production-cleared.
