"""IssueIntakePort: the feed-agnostic boundary that pulls raw issue records per source.

issue-remediation-capa is fed by five sources (internal-audit-lifecycle findings,
continuous-controls-monitoring exceptions, the compliance-advisory horizon change-feed,
complaints-review findings and loss events). Each has its own transport in a real deployment; this
port hides that behind one method that returns raw records for a named source, which the domain then
NORMALIZES into the shared :class:`~..domain.capa.IssueEnvelope`. The deferred
breach-reportability-assessor / whistleblower-triage feeders have no adapter, which is exactly why
the source is an extensible enum rather than a fixed set of methods.

The domain stays pure: this is a Protocol. The adapters (not this module) read a fixture set
offline, reach the real feeds under the managed profile, and fail fast on-premises.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ..domain.capa import IssueSource


@runtime_checkable
class IssueIntakePort(Protocol):
    def fetch(self, source: IssueSource) -> tuple[Mapping[str, object], ...]:
        """Return the raw issue records currently available from ``source``.

        Records are RAW: source-shaped dicts the domain normalizer turns into envelopes, dropping
        any that are schema-invalid. An offline adapter answers from a fixture set; the managed
        adapter reaches the live feed (and refuses, never silently returns empty, when it cannot);
        the on-premises placeholder raises.
        """
        ...
