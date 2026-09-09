"""Local IssueIntakePort: the shipped intake book, in DuckDB, over the landing tables' schema.

It stands in for the five live source feeds in the gate, the tests and the demo. Every record is
obviously fictional (``.example`` domains, invented parties). The book deliberately spans all five
sources and carries a couple of schema-INVALID records, so the intake service's drop behaviour is
exercised offline rather than only asserted. A silent empty return would let a producer ship the
intake seam unwired, so this returns real, inspectable records.

It used to read an in-process dictionary, and a store that runs no SQL cannot disagree with the
warehouse about anything: the managed adapter queried an unqualified table name that BigQuery
rejects outright, against landing tables nothing created, and a green offline gate could see
neither. The store here holds the SAME five tables in the SAME column order as
``infra/terraform/bigquery.tf``.

**Every row carries every column, NULL included.** A warehouse row has no absent keys, and
``domain/capa.py::_require`` treats ``None`` and missing identically, so the record that is
invalid for want of a description is invalid the same way on both sides. The old fixture omitted
the key, which is a shape BigQuery cannot produce.
"""

from __future__ import annotations

from collections.abc import Mapping

from hex_service_kit.demobook import DuckDbStore

from ... import demo_book
from ...config import Settings
from ...domain.capa import IssueSource


class LocalFixtureIntakeAdapter:
    """Serve the shipped intake book from DuckDB for the SDK-free offline profiles."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._store = DuckDbStore(demo_book.BOOK, settings.book_path)

    def fetch(self, source: IssueSource) -> tuple[Mapping[str, object], ...]:
        table = demo_book.SOURCE_TABLES.get(source.value)
        if table is None:
            # A deferred feeder has no landing table and no adapter path, matching the
            # extensible enum. An empty tuple is the honest answer: the source is not wired.
            return ()
        columns = demo_book.BOOK.table(table).columns
        rows = self._store.connection.execute(
            f"SELECT {', '.join(columns)} FROM {table}"  # noqa: S608 - identifiers, not input
        ).fetchall()
        return tuple(dict(zip(columns, row, strict=True)) for row in rows)

    def close(self) -> None:
        self._store.close()
