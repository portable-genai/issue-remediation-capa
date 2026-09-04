"""GCP IssueIntakePort: read raw issue records from the live source feeds (SDK imports lazy).

Each source lands in a BigQuery landing table (the feeds publish there); this adapter reads the
rows for the requested source. The ``google.cloud.bigquery`` import lives INSIDE the method so
the ``local`` / ``onprem`` profiles import this module with no GCP SDK installed (the portability
proof, and the reason the managed family refuses rather than succeeds under the offline gate).
"""

from __future__ import annotations

from collections.abc import Mapping

from ...config import Settings
from ...domain.capa import IssueSource

#: The landing table per source, in the configured dataset. A source with no table is a
#: deferred feeder (breach-reportability-assessor / whistleblower-triage) and has no adapter path,
#: matching the extensible enum.
_SOURCE_TABLES: dict[IssueSource, str] = {
    IssueSource.AUD1_FINDING: "aud1_findings",
    IssueSource.AUD2_EXCEPTION: "aud2_exceptions",
    IssueSource.RSK1_HORIZON: "rsk1_horizon_changes",
    IssueSource.DOC6_FINDING: "doc6_findings",
    IssueSource.LOSS_EVENT: "loss_events",
}


class CloudIntakeAdapter:
    """Read raw issue records from the per-source BigQuery landing tables."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def fetch(
        self, source: IssueSource
    ) -> tuple[Mapping[str, object], ...]:  # pragma: no cover - needs live GCP
        # Lazy import: absent in the offline profiles and in CI, so this raises there rather than
        # answering, which is exactly the managed-family refusal the parity suite asserts.
        from google.cloud import bigquery

        table = _SOURCE_TABLES.get(source)
        if table is None:
            raise RuntimeError(f"no landing table is configured for source {source.value!r}")
        client = bigquery.Client()
        rows = client.query(f"SELECT * FROM `{table}`").result()
        return tuple(dict(row.items()) for row in rows)
