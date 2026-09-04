# Portability FAQ

For architecture, cloud governance and exit planning. The question underneath all of these is
"how do we leave, and how do we know the answer is true today rather than on the day it was
written?"

### What is the lock-in surface?

Every outbound dependency is a `@runtime_checkable` Protocol in `ports/`, bound per profile from
`config/settings.yaml`. There are eight of them: `audit`, `identity`, `review_router`,
`generation`, `intake`, `embeddings`, `tracer` and `evaluation`. There is no cloud SDK import
anywhere in `domain/`, and the managed adapters import their SDK LAZILY inside the method, so the
other two families import with no SDK installed at all. The business-day maths, the state machine,
the closure checklist and the clustering arithmetic are stdlib, not managed services.

### What are the three profiles?

| Profile | What it is | Who it is for |
|---|---|---|
| `local` | SDK-free offline stack: seeded dev personas, a hash-chained SQLite WORM audit log, a fixture feed covering all five sources, a deterministic hashing embedder, a deterministic stub drafter | dev, test, CI, and the offline demo |
| `gcp` | the managed stack: IAP identity, Cloud Logging WORM, BigQuery landing tables per source, Vertex AI text embeddings, Gemini drafting, an HTTP client to the `human-review-console` | a managed deployment |
| `onprem` | fail-fast `NotImplementedError` placeholders | the sovereign exit: a client binds its own in-country implementations here |

`CAPA_PROFILE` selects the family. Unset means the offline adapters bind but nobody chose them,
which withdraws every relaxation rather than granting one.

### Is the portability claim tested, or just documented?

Tested, three ways, all in the offline gate or one command:

- `tests/contract/test_port_parity.py` asserts set equality across all five homes of a port (the
  `PORT_PROTOCOLS` map, `config.DEFAULT_BINDINGS`, the `Container` accessor, `settings.yaml` and
  the canonical-call table), so a port cannot be added in four places and run unenforced.
- `tests/contract/test_behavioral_parity.py` proves the offline family ANSWERS, the on-premises
  family RAISES and the managed family REFUSES rather than silently succeeding. This matters most
  on the drafting, intake and embedding seams: a placeholder that quietly returned an empty note,
  an empty feed or a zero vector would look exactly like a working adapter, and an empty feed in
  particular would render as "no issues" rather than as a failure.
- `make portability` is the executable claim: eight named checks with a pass or fail each (port
  map completeness, adapter construction and Protocol conformance, the offline family answering,
  the exit family refusing, rewritten-record detection, anchored truncation detection, the trail
  leaving this codebase intact, and no cloud SDK imported), exiting non-zero on any failure. The
  stronger SDK-free proof lives in `tests/contract/_sdk_free_probe.py`, which BLOCKS the `google`
  import in a fresh interpreter rather than hoping the machine has none installed.

### Where do the issues live, and can we take them with us?

Today the offline fixture feed IS the register, lifecycle state arrives on the request, and the
audit trail is the durable artefact. That is honest rather than ideal: a deployment needs an issue
store bound behind a port of its own, and choosing it is the second item on the adoption checklist
in [`../ADOPTING.md`](../ADOPTING.md). What already exports cleanly is the audit trail, which
round-trips to and from JSON Lines, so the record of every assessment is a file copy. The issue
envelopes and themes are plain frozen dataclasses, so serialising them is a schema decision rather
than a vendor extraction.

### What is the embedding model's lock-in cost?

Small, and bounded by design. The managed adapter names one Vertex model
(`text-embedding-005`) and the offline adapter is a 64-dimension feature-hashing embedder built
from `hashlib`, so the port genuinely has two working implementations rather than one plus a stub.
What does NOT transfer is the tuning: cosine distances differ between embedders, so
`DEFAULT_THEME_THRESHOLD` and any theme ids derived from a clustering run are specific to the
embedder that produced the vectors. Changing embedder means re-tuning the threshold and re-scoring
`theme_purity`, not rewriting code.

### How do we actually exit?

[`../onprem-migration.md`](../onprem-migration.md) is the path. The short version: the domain is
pure stdlib and moves unchanged; what you implement is one adapter per port under
`adapters/onprem/`, each of which currently raises with a message naming what to bind (the intake
placeholder names the client's own source feeds, the embeddings placeholder names the client's own
embedding model endpoint). Nothing in `domain/` has to change, which is the point of the split.

### Can it run with no model at all?

Yes, and that is the load-bearing property rather than a convenience. Every consequential figure
is produced by the deterministic engine, so with the stub generation adapter bound the
normalization, the due date, the aging verdict, the closure gaps and the escalation are identical.
The model changes one paragraph of prose and nothing else, and even that has a deterministic
fallback used whenever the note fails the groundedness check. The embeddings port is the one place
a model changes an output: the theme membership depends on the vectors. It changes no verdict,
sets no deadline and closes nothing. See [`../model-card.md`](../model-card.md).

### Is the data residency claim portable too?

The region is chosen once and shared by the runtime and Terraform:
`config/settings.yaml:region`, `infra/terraform/render.tf.json:render_region`, and the Terraform
`region` / `allowed_regions` pair, which refuses an unapproved region at plan time. The runtime
region is also what the managed embedding adapter passes to `vertexai.init`, so issue text is
embedded in the same region the stack is pinned to. Changing jurisdiction is a configuration
change in those three places plus a re-run of `infra/terraform/production_edge.tftest.hcl`, not a
code change.
