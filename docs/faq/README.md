# FAQ index

Answers to the questions different teams ask when evaluating, adopting or reviewing this
repository as the system of record for the post-finding issue and CAPA lifecycle. Each file is
written for a specific audience; skim the one that matches your role.

| FAQ | For | Answers |
|---|---|---|
| [security-faq.md](security-faq.md) | AppSec / security review | server-side identity, tenant isolation on the issue store, the exposure guard, secrets, supply chain, the audit chain |
| [portability-faq.md](portability-faq.md) | Architecture / cloud / exit planning | no-lock-in, the three profiles, the sovereign exit, where the issues live |
| [features-faq.md](features-faq.md) | Product / risk / delivery | what the lifecycle engine computes, what the model is allowed to write, and the boundary with sibling catalog systems |
| [adoption-faq.md](adoption-faq.md) | Engineering leads forking the repo | rename, upstream fixes, extension points, what stays open |
| [compliance-faq.md](compliance-faq.md) | Compliance / model risk / second line | why an SLA breach and a closure refusal are defensible, maker-checker, residency, retention, model-risk evidence |

These FAQs deliberately do **not** re-document capabilities owned by sibling systems in the GRC
catalog. Where a concern belongs to another repo (the upstream finding sources `internal-audit-lifecycle`, `continuous-controls-monitoring`, `compliance-advisory`
and `complaints-review`, the enterprise risk consumer `rcsa-kri-erm`, the guardrail gateway `agent-guardrail-gateway`, the knowledge base `enterprise-knowledge-base`,
the agent registry `agent-registry`, the eval platform `model-quality-gate`, observability and the WORM sink `agent-observability`, the
human-review console `human-review-console`), the FAQ points at it and explains the boundary rather than duplicating
it. See [features-faq.md](features-faq.md) for the full "what this repo owns vs what it
integrates" map.

Authority order for anything these pages disagree with: [`SPEC.md`](../../SPEC.md), then
[`ARCHITECTURE.md`](../../ARCHITECTURE.md), then [`COMPLIANCE.md`](../../COMPLIANCE.md), then
[`README.md`](../../README.md). These pages restate; they do not decide.
