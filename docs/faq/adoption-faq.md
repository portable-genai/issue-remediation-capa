# Adoption FAQ

For an engineering lead forking this repo as their institution's issue and CAPA tracker. The
step-by-step is [`../ADOPTING.md`](../ADOPTING.md); this answers the "will it hurt later?"
questions.

### How do I rebrand it for my organisation?

`scripts/rename_fork.py` rewrites the package name (`issue_remediation_capa`, which is also the
console script), the `CAPA_` env prefix (including the bare token that
`infra/terraform/render.tf.json` carries, so Terraform sets the same variable names on the
service), the Terraform `name_prefix` resource stem (`aud3-svc`) and the distribution / git id in
one pass. Preview with `--dry-run`, apply with `--yes`, then recreate the venv, `make install`,
and run `make gate`. The catalog id `issue-remediation-capa` is left alone unless you pass `--catalog-id`, so a fork
stays traceable to the entry it descends from. The script does the mechanical rename; the human
decisions (region, IdP, your feeds and their normalizers, the remediation clock and closure
checklists, the theme threshold, the eval golden set) are the checklist in `ADOPTING.md`.

### If several institutions fork this, how does each take upstream fixes?

Track upstream via **git tags**. The repo declares a core-vs-adopter-owned boundary
(`ADOPTING.md` section 2): upstream owns `domain/kernel.py`, `ports/`, `tests/contract/`, the eval
harness mechanics, CI and the Terraform stack; you own `config/settings.yaml` values, the intake
fixture, the clock and the checklists, the theme threshold, `adapters/onprem/*`, UI theming and
`terraform.tfvars`. The commons packages are pinned by commit, so you take their fixes by bumping
the pin rather than by merging code. Rebase your adopter-owned changes onto each release rather
than merging `main` continuously.

### What do we have to supply that is not in this repo?

Four things, and two of them are code here:

1. **Your feeds, and a normalizer for each.** The five sources this build knows each have a
   `_norm_*` function in `domain/capa.py` mapping a source-shaped record onto `IssueEnvelope`.
   Yours replace or extend them. Keep the drop-not-default rule: a record missing a required field
   raises and is dropped, because a defaulted issue is one nobody can trace.
2. **A durable issue store.** Offline the fixture feed IS the register and lifecycle state arrives
   on the request. A deployment needs a store bound behind a port of its own, carrying each
   store's owning tenant on its rows, and it is the store that must supply `state`,
   `provided_evidence` and the approved `review_ref` rather than the caller. This is the largest
   single piece of adoption work and it is not started.
3. **Your holiday calendar.** The business-day maths takes the holiday set as an argument and
   never assumes one, which is correct but means somebody has to supply it, per jurisdiction, and
   keep it current. A wrong calendar moves every due date.
4. **The review console.** An `human-review-console` deployment reachable at `HUMAN_REVIEW_URL`. The managed
   router REFUSES to swallow an escalation when this is empty, so a fork cannot ship rule R8
   unwired and green, and the approved reference it returns is what a closure requires.

### How do I add a new source feed?

Three edits and no redesign, which is the whole point of the extensible enum: a member on
`IssueSource`, a `_norm_<source>` function registered in the `_NORMALIZERS` table, and a path in
each intake adapter (a fixture offline, a landing table under `gcp`, a raise on-premises).
`IssueSource` is a `LenientStrEnum`, so an unrecognised value from a future release is read rather
than crashed on, and `normalize_issue` raises for a source with no normalizer rather than guessing
one. `breach-reportability-assessor` and `whistleblower-triage` are named in the code comments as the deferred feeders this shape is meant to
take, but neither is a member yet, so adding one of them is the same three edits. Add oracle rows
to `eval/datasets/capa_oracle.jsonl` for the new source in the same change, or
`normalization_accuracy` will pass without ever seeing it.

### How do I add a new outbound dependency (a new port)?

There is a fixed touch list and a contract test that enforces it. A port must be registered in
FIVE places or it runs with no enforcement at all: `ports/__init__.py` (`PORT_PROTOCOLS`),
`config.DEFAULT_BINDINGS`, a `Container` accessor, `config/settings.yaml`, and a `PortCase` in
`tests/contract/canonical.py`. Then bind it in all three families.
`tests/contract/test_port_parity.py` asserts set equality across the five. The durable issue store
is exactly this job, and it is the port a real deployment adds first. See
[`../../CONTRIBUTING.md`](../../CONTRIBUTING.md).

### Can I retune the clock and the checklists without touching code?

Not yet, and this is stated honestly. `DEFAULT_CLOCK` is already a frozen `ClockSpec` rather than
literals scattered through the engine, and `CLOSURE_CHECKLISTS` and `LEGAL_TRANSITIONS` are
module-level tables, so retuning is a small, reviewable edit. What does not exist is a `policy:`
block in `config/settings.yaml` with a `from_policy(...)` constructor, which is the open B4 item in
[`../practices-audit.md`](../practices-audit.md). If your issue-management function must own these
numbers as configuration rather than as code, plan that addition as part of adoption.

### Why are there two verticals in here?

Because the render started from the template's generic triage service and the `issue-remediation-capa` engine was
built alongside it. `domain/triage_service.py` (with `/v1/triage`, the CLI `triage` command and the
`triage_case` agent tool) is scaffolding; `domain/capa.py`, `domain/themes.py` and `domain/rca.py`
(with `/v1/issues/assess` and `/v1/themes`) are the reason this system exists. The triage path also
carries the shared R8 review envelope a CAPA assessment is projected onto before routing, so keep
the envelope type even if you delete the route.

### Does the gate run for my fork out of the box?

Yes. `make gate` is offline, credential-free and network-free (ruff, ruff format, mypy strict, the
whole suite except integration, and the eval), and the CI workflow references no `secrets.`, so a
fork's build is green immediately. You add secrets only when you wire the `gcp` profile. Note the
eval measures the REFERENCE fixture feed until you rebuild the golden set and both oracles for your
own; that is an explicit adoption step, not a silent pass.

### The eval reports high scores. Should we believe them?

Only because each metric is proved able to report something else.
`tests/unit/test_capa_metrics_go_red.py` hands the CAPA metrics planted mutants and fails the build
if they still pass, and `tests/unit/test_not_falsely_green.py` does the same for the safety metric.
Each engine metric is scored against the oracle's OWN hand-computed literals rather than against
the engine's own verdict, and `rca_groundedness` measures the RAW model output through the same
pure functions the service enforces, so it can actually go red; a metric that watched only the
already-filtered service output would be green by construction.

### Will the demo rot after I diverge?

It is guarded, and the guard is inside the gate. A demo step lives in `demo.STEPS` and in
`walkthrough.CHECKS`, and `tests/unit/test_demo_surface.py` holds the two equal, so a claim the
demo makes but nobody verifies cannot exist. `make demo-selftest` runs the whole eight-step arc
headless over the real loopback server and exits non-zero when a claim stops being true. If you
diverge, keep the step keys and the `facts` dict the checks read.

### What is still open?

[`../practices-audit.md`](../practices-audit.md) carries the per-check verdict and the work list.
The three that matter most before production: the durable issue store (which is also what moves
the closure preconditions off the request body), binding the `agent-guardrail-gateway` before
upstream issue text reaches the drafter, and registering this repo's metric bundle with `model-quality-gate` so
`eval/run_eval.py --mode gate` has an authority to ask. The Terraform stack is written, validated
and tested against a mocked provider; it has never been applied.
