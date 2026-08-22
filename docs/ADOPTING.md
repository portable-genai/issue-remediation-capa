# Adopting this repo as your base

This repository (Aud3, Issue Remediation and CAPA Tracker) is a **common base** that a bank or
other regulated institution forks to build its own **system of record for the post-finding issue
and CAPA lifecycle**: the service that normalizes issues from every upstream feed, runs them
through a lifecycle state machine on a business-day remediation clock, refuses to close one
without complete evidence and an approved review, and clusters them into systemic themes. It ships
a reusable hexagonal core (a pure-stdlib domain, typed ports, three swappable adapter profiles, a
green offline gate) plus a fully worked CAPA vertical you can keep, reseed or retune.

This guide is the step-by-step for making it yours. It has two halves: a **mechanical rebrand**
(one script) and the **human decisions** the script cannot make for you.

> Related reading: [`ARCHITECTURE.md`](../ARCHITECTURE.md) (the port table and topology),
> [`CONTRIBUTING.md`](../CONTRIBUTING.md) (adding an adapter, adding a port), the
> [`faq/`](faq/) directory, [`model-card.md`](model-card.md) (the model boundary),
> [`practices-audit.md`](practices-audit.md) (the per-check verdict).

---

## 1. What you keep vs what you rewrite

The core is hexagonal, and the boundary between reusable machinery and this vertical is a
physical module split with an enforced dependency direction (practices-audit check A7).
`domain/kernel.py` owns the vertical-neutral contracts and imports nothing from the vertical;
`domain/models.py` holds this service's own request and result types.

| Layer | Where | For your own issue register |
|---|---|---|
| **Vertical-neutral machinery** | `domain/kernel.py` (`Citation`, `AuditEvent`, `Severity`, `Decision`, `utcnow`), every Protocol in `ports/`, the container wiring in `config.py` | keep untouched |
| **The lifecycle engine** | `domain/capa.py`: the per-source normalizers and the drop-not-default rule, the business-day maths (`add_business_days`, `business_days_between`), the aging precedence in `aging_finding`, the guarded `plan_transition`, and `CapaService.assess`; plus `domain/themes.py` (`cluster_issues`, `theme_purity`) and the wiring in `pipeline.py` | keep the shapes, retune the numbers |
| **Policy (your numbers and rules)** | `DEFAULT_CLOCK` (the per-severity remediation SLA in business days, the approaching window, the stuck-in-state threshold), `LEGAL_TRANSITIONS`, `CLOSURE_CHECKLISTS`, `DEFAULT_THEME_THRESHOLD` in `pipeline.py`, the jurisdiction list in `domain/pii.py`, the metric thresholds in `eval/run_eval.py` | change deliberately (see section 4) |
| **Vertical (the register content)** | the fixture feed in `adapters/local/intake.py`, the vertical models in `domain/models.py`, the drafting prompt in `domain/rca.py`, the eval golden set and its two oracles | reseed and rewrite for your feeds |

If your product is another *case lifecycle to closure* service, the hexagon, the three profiles,
the deterministic-verdict pattern, the eval gate and the Hrz7 review routing transfer directly;
you replace the source normalizers and retune the clock and the checklists.

## 2. Core-vs-adopter-owned files (so upstream merges stay mechanical)

Upstream keeps evolving these; avoid diverging from them so you can pull fixes cleanly:

- **Upstream-owned** (take our changes): `domain/kernel.py`, `ports/`, `tests/contract/`, the
  eval harness mechanics (`eval/run_eval.py`), the CI workflows, the hexagon wiring (`config.py`
  `Container`) and the deploy stack in `infra/terraform/`.
- **Adopter-owned** (yours; expect to edit): `config/settings.yaml` *values*, the intake fixture
  and every other fixture, the clock and the closure checklists in `domain/capa.py`, the theme
  threshold, `adapters/onprem/*`, UI theming and branding, the golden eval dataset and both
  oracles, `infra/terraform/terraform.tfvars`, and the regulator crosswalk section of
  `COMPLIANCE.md`.

Track upstream via git tags; rebase your adopter-owned changes onto each release rather than
merging `main` continuously.

## 3. The mechanical rebrand (one script)

`scripts/rename_fork.py` rewrites the package name (`issue_remediation_capa`, which is also the
console script), the `CAPA_` env prefix (including the bare token that
`infra/terraform/render.tf.json` carries as `render_env_prefix`, so Terraform sets the same
variable names on the service), the cloud resource stem (`aud3-svc`, the Terraform `name_prefix`)
and the distribution / git id (`issue-remediation-capa`) in one pass. Preview first, then
apply:

```bash
# Preview (writes nothing):
python scripts/rename_fork.py --package acme_capa_tracker --env-prefix ACME \
    --resource acme-capa --dry-run

# Apply:
python scripts/rename_fork.py --package acme_capa_tracker --env-prefix ACME \
    --resource acme-capa --yes

# Then recreate the environment (the distribution name changed) and prove it is green:
python3.12 -m venv .venv && source .venv/bin/activate
make install
make gate
```

`--dist` defaults to the `--resource` value; pass it explicitly when your git id differs from
your resource stem. `--resource` is validated against the same `^[a-z][a-z0-9-]{2,18}$` regex the
Terraform `name_prefix` variable enforces, so a stem the stack would refuse fails here instead of
at plan time. Add `--include-docs` to sweep Markdown prose too. The script skips itself, so the
renamer is never left half-rewritten. The catalog id `Aud3` is left alone unless you pass
`--catalog-id`, so a fork stays traceable to the entry it descends from. The script deliberately
does NOT touch the human decisions below.

## 4. The human decisions (the script can't make these)

1. **Region / residency.** The build defaults to `asia-southeast1` (MAS / Singapore), chosen once
   and shared: `config/settings.yaml:region`, `infra/terraform/render.tf.json:render_region` and
   the Terraform `region` / `allowed_regions` pair. Set all of them to your in-country region and
   re-run `infra/terraform/production_edge.tftest.hcl`, which refuses a region outside the
   allowlist at plan time. See [`runbook.md`](runbook.md). The region is also what the managed
   embedding adapter initialises Vertex AI with, so moving it moves where issue text is embedded.
2. **Identity / IdP.** This repo owns no login flow: the `gcp` profile verifies the IAP-injected
   assertion at the edge, `local` uses seeded dev personas, and `onprem` is a client IdP
   placeholder. Wire your issuer on the deployed service (auth is configured ON the service, not
   in this code) and set `CAPA_IAP_AUDIENCE`. An unset or emptied audience refuses every caller
   rather than verifying without one.
3. **Your feeds, and the normalizer per feed.** The five sources in `IssueSource` are the ones
   this build knows (Aud1 findings, Aud2 exceptions, the Rsk1 horizon change-feed, Doc6 findings
   and loss events), each with its own `_norm_*` function mapping a source-shaped record onto the
   shared `IssueEnvelope`. Adding your sixth is three edits and no redesign: a member on
   `IssueSource`, an entry in the `_NORMALIZERS` table, and a path in each intake adapter. The
   enum is a `LenientStrEnum`, so an unrecognised wire value from a future release is read rather
   than crashed on, and `normalize_issue` raises for a source with no normalizer instead of
   guessing one. (The deferred Rgc10 and Rgc13 feeders are named in the code comments as the
   sources this shape is meant to accommodate; neither is a member yet.) The **drop-not-default
   rule** is the part to keep: a record missing a required field raises `NormalizationError` and
   `collect_issues` drops it, because a defaulted issue is an issue nobody can trace.
4. **The remediation clock and the closure checklists, which your issue-management function
   owns.** `DEFAULT_CLOCK` sets the per-severity SLA in BUSINESS days (critical 5, high 10, medium
   20, low 40), the approaching-deadline window (3) and the stuck-in-state threshold (15). The
   holiday set is passed IN, never assumed, so a deployment supplies its own calendar per
   jurisdiction. `CLOSURE_CHECKLISTS` decides what evidence each issue type needs before it can
   close, and `LEGAL_TRANSITIONS` decides which moves the lifecycle permits at all. These are
   module-level today rather than a `policy:` settings section (practices-audit check B4); change
   them deliberately and add a test that pins your values.
5. **The theme threshold.** `DEFAULT_THEME_THRESHOLD` in `pipeline.py` is the cosine proximity at
   which two issues share a theme. It is tuned for the offline hashing embedder and the fixture
   corpus, so re-tune it against YOUR embedder and your issues, and re-check `theme_purity`
   against a gold labelling you built. A threshold carried over unexamined merges unrelated issues
   into one systemic theme and reports it to Erm1 as a risk signal.
6. **Tenancy.** `ISSUE_TENANT` and `authorize_issue_access` enforce that a caller may only read its
   own issue store, and a cross-tenant read raises 403 rather than returning an empty result or a
   404. Offline the fixture IS the demo bank's store. Decide how your deployment carries the
   owning tenant on issue rows before you serve a second one.
7. **Reference data is fictional.** Every fixture record and the eval datasets use obviously fake
   parties and `.example` domains, and two fixture records are deliberately schema-invalid so the
   drop path is exercised rather than merely asserted. Replace them with your own synthetic data.
   **Do not run against a real issue register without your own security and model-risk sign-off.**
8. **Eval golden set.** Rebuild the golden dataset and both oracles for your feeds: a fork
   inherits a green gate that measures the WRONG register until you do. The seven metrics
   (`decision_accuracy`, `pii_safety`, `normalization_accuracy`, `deadline_accuracy`,
   `closure_safety`, `theme_purity`, `rca_groundedness`) and their thresholds are generic; the
   golden cases in `eval/datasets/golden_cases.jsonl`, `capa_oracle.jsonl` and
   `themes_oracle.jsonl` are yours, and the deadline oracle in particular must be recomputed by
   hand against your calendar.
9. **Deployment posture.** Review the Dockerfile (digest-pinned base, non-root uid 10001),
   `infra/terraform/` (Org Policy, CMEK, a dry-run-first VPC-SC perimeter, the locked WORM log
   bucket) and the loopback-by-default binding before you expose anything. The WORM lock is
   irreversible: confirm `retention_days` before the first apply.

## 5. Do not duplicate the platform

This repo is one system in a catalog of composable GRC systems. It is deliberately the OWNER of
everything that happens to an issue AFTER it is raised, so upstream systems hand issues over
rather than tracking remediation themselves. What it integrates rather than rebuilds (see
[`faq/features-faq.md`](faq/features-faq.md) for the full map):

- **The upstream finding sources**, each a member of `IssueSource` and a landing table in
  `adapters/gcp/intake.py`: **Aud1** internal-audit findings, **Aud2** control-testing exceptions,
  the **Rsk1** regulatory change horizon, **Doc6** document-review findings, and loss events. This
  repo NORMALIZES what they raise; it never re-derives a finding's severity or re-runs a control
  test. **Rgc10** and **Rgc13** are named in the code as deferred feeders this shape is meant to
  take later; neither is wired, and neither is a member of the source enum yet.
- **Erm1** enterprise risk: consumes the theme feed at `GET /v1/themes`, one way and read only.
  There is no write path, because a systemic theme is a signal Erm1 interprets, not a state this
  repo takes back.
- **Hrz7** human-review / maker-checker console: every escalation is routed to it over the shared
  `review-kit` (rule R8), and the approved review reference it returns is the SAME thing
  `plan_transition` demands before an issue may close. You wire your endpoint
  (`HRZ_HUMAN_REVIEW_URL`); you do not re-implement the console.
- **Hrz5** observability plus immutable WORM audit: audit events and trace spans go to it through
  `AuditSinkPort` and `ObservabilityTracerPort`.
- **Hrz4** AI-quality / model-risk gate: owns promotion. `eval/run_eval.py --mode gate` is the
  client half and refuses to run off the managed profile.
- **Hrz3** agent registry: this agent publishes its A2A card at
  `/.well-known/agent-card.json`; register it rather than inventing a discovery mechanism.

The guardrail gateway (Hrz1) is **not** integrated today, and the enterprise knowledge base
(Hrz2) is not either: this service reasons over its own issue records rather than over a document
corpus, and the embeddings port serves clustering rather than retrieval. Hrz1 becomes mandatory
the moment untrusted free text reaches the drafter, and an issue description from an upstream feed
is exactly that: see rule R1 in [`../COMPLIANCE.md`](../COMPLIANCE.md).

## 6. Adoption checklist

- [ ] Ran `scripts/rename_fork.py`, recreated the venv, `make gate` green.
- [ ] Set the region in all three places (settings, `render.tf.json`, tfvars) and re-ran the
      Terraform residency tests.
- [ ] Wired your IdP audience on the deployed service (this repo owns no login flow).
- [ ] Mapped every one of your feeds to a source member, a normalizer and an adapter path, and
      kept the drop-not-default rule.
- [ ] Bound a durable issue store: offline the fixture feed IS the register, and nothing persists
      lifecycle state between requests.
- [ ] Owned the remediation clock, the holiday calendar and the closure checklists with your
      issue-management function, and pinned your numbers in a test.
- [ ] Re-tuned the theme threshold against your own embedder and re-scored `theme_purity` against
      your own gold labelling.
- [ ] Decided how the owning tenant is carried on issue rows before serving a second tenant.
- [ ] Replaced every synthetic fixture.
- [ ] Rebuilt the eval golden set and both oracles, recomputing the deadline oracle by hand.
- [ ] Reviewed the deploy posture (Dockerfile, Terraform, `retention_days`, bind address).
- [ ] Wired your Hrz7 review endpoint and decided which sibling services you integrate vs stub.
- [ ] Read [`model-card.md`](model-card.md) and closed its remaining controls before enabling the
      managed drafter and the managed embedder.
- [ ] Recorded your baseline upstream tag so you can take future fixes.
