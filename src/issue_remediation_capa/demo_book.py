"""The shipped demo book: fictional issue-intake landing rows, served the same way on both sides.

The rows live as newline-delimited JSON under ``issue_remediation_capa/data/demo_book/``, one
file per BigQuery landing table and in that table's column order, so one set of files feeds the
DuckDB store the offline profiles read, the loader that fills the managed dataset, and the tests.
The reading, the overwrite guard and the tenant rule come from :mod:`hex_service_kit.demobook`;
what is here is about THIS system.

**The managed adapter could not have run.** Two reasons, and the second is the one no missing
Terraform explains. Nothing in ``infra/terraform/`` created the landing tables, so there was
nothing to read; and the statement was ``SELECT * FROM `aud1_findings```, an UNQUALIFIED table
name against a client constructed with no default dataset, which BigQuery rejects outright
before it ever looks for the table. The five tables are declared now, the dataset is
configuration, and every statement names it.

**A warehouse row has NULLs, never absent keys.** The offline fixture omitted a field to make a
record schema-invalid, and BigQuery cannot: a landing table returns every column, empty ones as
NULL. ``domain/capa.py::_require`` reads ``raw.get(key)``, so ``None`` and missing arrive at the
same refusal and the drop-not-default rule holds either way. The book therefore carries every
column on every row, with NULL where the offline fixture had nothing, and the two stores hand the
normalizer the same mapping. Getting this wrong would not have failed loudly: it would have
quietly admitted or dropped a different set of records on the deployment than on the laptop.

Everything is fictional. See ``data/demo_book/README.md``.
"""

from __future__ import annotations

from hex_service_kit.demobook import BookError, NdjsonBook, Table

#: The tenant the manifest records. Landing rows carry no per-row tag: an issue register is this
#: institution's own, and the cross-tenant refusal in ``domain/capa.py`` is about the STORE.
SHIPPED_TENANT = "demo-bank"
CROSS_TENANT: dict[str, str] = {}


def _table(name: str, columns: tuple[str, ...], key: str, nullable: tuple[str, ...] = ()) -> Table:
    """One landing table. Everything is TEXT: a landing table holds what the feed published.

    ``gross_loss`` is the clearest case. The feed publishes it as a string and the normalizer
    parses it; typing it NUMERIC here would make the warehouse do a conversion the laptop does
    not, and the two stores would hand the normalizer different types for the same record.
    """
    return Table(
        name=name,
        columns=columns,
        types={column: ("TEXT" if column in nullable else "TEXT NOT NULL") for column in columns},
        primary_key=(key,),
    )


AUD1_FINDINGS = _table(
    "aud1_findings",
    ("finding_id", "description", "engagement", "raised_on", "rating", "title"),
    "finding_id",
    nullable=("description",),
)
AUD2_EXCEPTIONS = _table(
    "aud2_exceptions",
    ("exception_id", "control_id", "description", "detected_on", "severity"),
    "exception_id",
)
RSK1_HORIZON_CHANGES = _table(
    "rsk1_horizon_changes",
    ("change_id", "impact", "obligation_ref", "published_on", "summary"),
    "change_id",
)
DOC6_FINDINGS = _table(
    "doc6_findings",
    ("complaint_id", "logged_on", "severity", "summary", "theme"),
    "complaint_id",
    nullable=("summary",),
)
LOSS_EVENTS = _table(
    "loss_events",
    ("event_id", "category", "gross_loss", "narrative", "occurred_on"),
    "event_id",
)

TABLES = (AUD1_FINDINGS, AUD2_EXCEPTIONS, RSK1_HORIZON_CHANGES, DOC6_FINDINGS, LOSS_EVENTS)

#: The landing table per source, on BOTH sides. One map, so the local store and the warehouse
#: cannot disagree about where a source's rows live. A source with no table is a deferred feeder
#: (breach-reportability-assessor / whistleblower-triage) and has no adapter path, matching the
#: extensible enum.
#:
#: Keyed by the source's VALUE rather than the enum member, so this module imports nothing from
#: the domain. The book is data: it describes tables and rows, and a data module that reaches
#: into ``domain.capa`` also reaches through ``ports/__init__`` and back into ``domain.capa``,
#: which is a circular import the loader script hits first because it imports the book before
#: anything else. A test holds this map against the enum, so keying by value cannot drift.
SOURCE_TABLES: dict[str, str] = {
    "aud1_finding": AUD1_FINDINGS.name,
    "aud2_exception": AUD2_EXCEPTIONS.name,
    "rsk1_horizon": RSK1_HORIZON_CHANGES.name,
    "doc6_finding": DOC6_FINDINGS.name,
    "loss_event": LOSS_EVENTS.name,
}

BOOK = NdjsonBook("issue_remediation_capa.data.demo_book", TABLES)

#: The records the book ships deliberately BROKEN, and why. The drop-not-default rule is the one
#: an offline gate is most likely to prove vacuously: with no invalid record in the book, a
#: normalizer that admitted everything would pass every test. Named here so deleting one is a
#: failure rather than a quiet loss of coverage.
INVALID_RECORDS: dict[str, str] = {
    "F-2026-013": "an unknown rating, and no description",
    "C-5502": "no summary",
}


def validate() -> None:
    """The book's own invariants, on top of the shape the kit checks."""
    BOOK.validate()
    seen: set[str] = set()
    for table in TABLES:
        rows = BOOK.rows(table.name)
        if not rows:
            raise BookError(f"{table.name} ships no rows, so its source proves nothing")
        key = table.primary_key[0]
        for row in rows:
            identifier = row.get(key)
            if not identifier:
                raise BookError(f"a {table.name} row has no {key}; it can never be cited")
            if identifier in seen:
                raise BookError(f"{identifier} appears twice; the register would double-count it")
            seen.add(str(identifier))
            missing = sorted(set(table.columns) - set(row))
            if missing:
                raise BookError(
                    f"{table.name} row {identifier} omits {missing}. A warehouse row carries "
                    "every column, empty ones as NULL, so an omitted key here is a laptop that "
                    "drops a different set of records than the deployment does."
                )

    still_invalid = sorted(set(INVALID_RECORDS) - seen)
    if still_invalid:
        raise BookError(
            f"the deliberately invalid records {still_invalid} are gone from the book. The "
            "drop-not-default rule is proved by records that MUST be dropped; without them a "
            "normalizer that admitted everything would pass every test."
        )
