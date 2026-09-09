"""GCP IssueIntakePort: read raw issue records from the live source feeds (SDK imports lazy).

Each source lands in a BigQuery landing table (the feeds publish there); this adapter reads the
rows for the requested source. The ``google.cloud.bigquery`` import lives INSIDE the method so
the ``local`` / ``onprem`` profiles import this module with no GCP SDK installed (the portability
proof, and the reason the managed family refuses rather than succeeds under the offline gate).

**This adapter could not have run.** The statement was ``SELECT * FROM `aud1_findings```: an
UNQUALIFIED table name, against a client constructed with no project and no default dataset,
which BigQuery rejects before it ever looks for the table. And nothing in ``infra/terraform/``
created any of the five landing tables, so there was nothing to find either way. Both are fixed
here: the dataset is configuration, every statement names it, and an unconfigured dataset makes
this adapter REFUSE rather than return an empty tuple, because an empty intake reads as an
institution with no open issues.

``SELECT *`` is gone with it. The read set is declared so a contract test can hold it against the
Terraform that creates the tables, which is the only way a column the adapter needs and the
schema lacks gets caught before a deployment meets it.
"""

from __future__ import annotations

from collections.abc import Mapping

from ... import demo_book
from ...config import Settings
from ...domain.capa import IssueSource

#: The READ SET per landing table: every column this adapter names. The tables have different
#: shapes because the five feeds publish different records, which is exactly why a single
#: ``SELECT *`` could hide a mismatch in any one of them.
SELECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    table.name: table.columns for table in demo_book.TABLES
}

#: The statement, as a template rather than assembled inline, so a contract test can read what
#: this adapter actually asks for. The table is QUALIFIED with the dataset: it was not, and an
#: unqualified name against a client with no default dataset is rejected by BigQuery before it
#: looks for the table at all.
_INTAKE_SQL = "SELECT {columns} FROM `{dataset}.{table}`"


class CloudIntakeAdapter:
    """Read raw issue records from the per-source BigQuery landing tables."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def fetch(
        self, source: IssueSource
    ) -> tuple[Mapping[str, object], ...]:  # pragma: no cover - needs live GCP
        table = demo_book.SOURCE_TABLES.get(source.value)
        if table is None:
            raise RuntimeError(f"no landing table is configured for source {source.value!r}")
        # The CONFIGURATION check runs before the SDK import, deliberately: an unconfigured
        # dataset is the more actionable of the two refusals, and an operator reading an
        # ImportError would go looking for a missing package rather than a missing variable.
        dataset = self._settings.bigquery_dataset.strip()
        if not dataset:
            raise RuntimeError(
                "CAPA_BQ_DATASET is not configured, so the intake has no landing tables to read. "
                "It refuses rather than returning an empty tuple: an empty intake reads as an "
                "institution with no open issues."
            )
        # Lazy import: absent in the offline profiles and in CI, so this raises there rather than
        # answering, which is exactly the managed-family refusal the parity suite asserts.
        from google.cloud import bigquery  # noqa: PLC0415

        client = bigquery.Client(project=self._settings.project_id or None)
        sql = _INTAKE_SQL.format(
            columns=", ".join(SELECTED_COLUMNS[table]), dataset=dataset, table=table
        )
        rows = client.query(sql).result()
        return tuple(dict(row.items()) for row in rows)
