"""The demo book: one issue intake, served the same way on the laptop and the deployment.

Pinned here, and each was watched failing first:

* **the managed adapter could not have run.** Its statement was ``SELECT * FROM `aud1_findings```:
  an UNQUALIFIED table name against a client built with no project and no default dataset, which
  BigQuery rejects before it looks for the table. And nothing in ``infra/terraform/`` created any
  of the five landing tables, no API was enabled, no IAM role named BigQuery and no CMEK bound
  the dataset;
* **a warehouse row has NULLs, never absent keys.** The offline fixture omitted a field to make a
  record schema-invalid, which is a shape BigQuery cannot produce. ``_require`` treats ``None``
  and missing identically, so the drop rule survives either way, but only if the book carries the
  column and nulls it. Otherwise the laptop and the deployment drop different sets of records and
  nothing says so;
* the adapter's read set is declared instead of hidden inside ``SELECT *``, every column in it is
  one the Terraform declares, and the book ships exactly those columns;
* the set held against the Terraform is ``load_order()``, the set the LOADER writes.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from issue_remediation_capa import demo_book, pipeline
from issue_remediation_capa.adapters.gcp import intake as managed
from issue_remediation_capa.adapters.local.intake import LocalFixtureIntakeAdapter
from issue_remediation_capa.config import Settings
from issue_remediation_capa.domain.capa import IssueSource

from tests import REPO_ROOT

_TF = REPO_ROOT / "infra" / "terraform" / "bigquery.tf"


def _settings() -> Settings:
    return dataclasses.replace(
        Settings.load(), profile="local", audit_path=":memory:", book_path=":memory:"
    )


@pytest.fixture
def intake() -> LocalFixtureIntakeAdapter:
    adapter = LocalFixtureIntakeAdapter(_settings())
    yield adapter
    adapter.close()


# --------------------------------------------------------------------------- #
# The shipped rows
# --------------------------------------------------------------------------- #
def test_the_shipped_book_is_internally_consistent() -> None:
    demo_book.validate()
    assert [len(demo_book.BOOK.rows(t.name)) for t in demo_book.TABLES] == [3, 2, 1, 2, 1]
    assert demo_book.BOOK.manifest()["fictional"] is True


def test_the_book_spans_every_wired_source() -> None:
    """A source with no rows proves nothing about the normalizer wired to it."""
    for source, table in demo_book.SOURCE_TABLES.items():
        assert demo_book.BOOK.rows(table), f"{source} ships no rows"


def test_every_key_in_the_source_map_is_a_real_source() -> None:
    """The map is keyed by VALUE so the book imports nothing from the domain; this holds it."""
    known = {source.value for source in IssueSource}
    unknown = sorted(set(demo_book.SOURCE_TABLES) - known)
    assert not unknown, f"the source map names sources the enum does not have: {unknown}"


def test_a_row_that_omits_a_column_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """An omitted key is a shape BigQuery cannot produce, so it would only exist offline."""
    real = demo_book.BOOK.rows

    def omitted(name: str):  # type: ignore[no-untyped-def]
        rows = real(name)
        if name != "aud1_findings":
            return rows
        first = {k: v for k, v in rows[0].items() if k != "engagement"}
        return [first, *rows[1:]]

    monkeypatch.setattr(demo_book.BOOK, "rows", omitted)
    with pytest.raises(demo_book.BookError, match="omits"):
        demo_book.validate()


def test_deleting_a_deliberately_invalid_record_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a record that MUST be dropped, a normalizer admitting everything passes."""
    real = demo_book.BOOK.rows

    def cleaned(name: str):  # type: ignore[no-untyped-def]
        return [row for row in real(name) if row.get("finding_id") != "F-2026-013"]

    monkeypatch.setattr(demo_book.BOOK, "rows", cleaned)
    with pytest.raises(demo_book.BookError, match="drop-not-default"):
        demo_book.validate()


def test_a_duplicated_identifier_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    real = demo_book.BOOK.rows

    def duplicated(name: str):  # type: ignore[no-untyped-def]
        rows = real(name)
        return [rows[0], *rows] if name == "aud1_findings" else rows

    monkeypatch.setattr(demo_book.BOOK, "rows", duplicated)
    with pytest.raises(demo_book.BookError, match="appears twice"):
        demo_book.validate()


# --------------------------------------------------------------------------- #
# The managed schema, which did not exist
# --------------------------------------------------------------------------- #
def _terraform_tables() -> dict[str, set[str]]:
    assert _TF.exists(), (
        "infra/terraform/bigquery.tf is missing. The adapter queries five landing tables; "
        "without this file nothing creates them."
    )
    text = _TF.read_text(encoding="utf-8")
    blocks = re.findall(
        r'resource\s+"google_bigquery_table"\s+"\w+"\s*\{(.*?)\n\}', text, flags=re.DOTALL
    )
    assert blocks, "no google_bigquery_table blocks found; the regex or the file moved"
    out: dict[str, set[str]] = {}
    for block in blocks:
        table_id = re.search(r'table_id\s*=\s*"(\w+)"', block)
        assert table_id is not None
        out[table_id.group(1)] = set(re.findall(r'name\s*=\s*"(\w+)"', block))
    return out


def test_the_dataset_the_settings_name_is_actually_created() -> None:
    """Nothing created it, no API enabled it, no IAM role named it, and no CMEK bound it."""
    text = _TF.read_text(encoding="utf-8")
    assert 'dataset_id  = "issue_intake"' in text, "no google_bigquery_dataset creates it"
    apis = (REPO_ROOT / "infra" / "terraform" / "apis.tf").read_text(encoding="utf-8")
    assert '"bigquery.googleapis.com"' in apis, "the BigQuery API is not enabled"
    iam = (REPO_ROOT / "infra" / "terraform" / "iam.tf").read_text(encoding="utf-8")
    assert "roles/bigquery.dataViewer" in iam, "the serving identity cannot read the dataset"
    kms = (REPO_ROOT / "infra" / "terraform" / "kms.tf").read_text(encoding="utf-8")
    assert "bigquery-encryption.iam.gserviceaccount.com" in kms, "no CMEK binding for BigQuery"


def test_the_managed_adapter_reads_only_columns_the_terraform_declares() -> None:
    declared = _terraform_tables()
    for table, columns in managed.SELECTED_COLUMNS.items():
        assert table in declared, f"{table!r} is not a Terraform table"
        undeclared = sorted(set(columns) - declared[table])
        assert not undeclared, f"{table} reads columns Terraform never declares: {undeclared}"


def test_the_book_and_the_terraform_declare_the_same_columns() -> None:
    """``load_order()``, so the manifest the loader writes is held too."""
    declared = _terraform_tables()
    for table in demo_book.BOOK.load_order():
        assert table.name in declared, f"the book ships {table.name} and Terraform does not"
        book_columns, tf_columns = sorted(table.columns), sorted(declared[table.name])
        assert book_columns == tf_columns, (
            f"{table.name}: book {book_columns} vs terraform {tf_columns}"
        )


def test_every_landing_table_a_source_names_exists() -> None:
    declared = _terraform_tables()
    for source, table in demo_book.SOURCE_TABLES.items():
        assert table in declared, f"{source} reads {table!r} and Terraform does not create it"


def test_the_managed_query_qualifies_the_table_with_the_dataset() -> None:
    """An unqualified name is rejected by BigQuery before it looks for the table."""
    assert "{dataset}.{table}" in managed._INTAKE_SQL, (
        "the landing table is not qualified with a dataset, so BigQuery rejects the query "
        "before it looks for the table"
    )
    assert "SELECT *" not in managed._INTAKE_SQL, "the read set must be declared, not implied"


# --------------------------------------------------------------------------- #
# The two stores, over one book
# --------------------------------------------------------------------------- #
def test_the_store_hands_the_normalizer_every_column_null_included(
    intake: LocalFixtureIntakeAdapter,
) -> None:
    """The record that is invalid for want of a description carries the key, nulled."""
    rows = {row["finding_id"]: row for row in intake.fetch(IssueSource.AUD1_FINDING)}
    broken = rows["F-2026-013"]
    assert "description" in broken, "a warehouse row cannot omit a key, only null it"
    assert broken["description"] is None


def test_the_broken_records_are_still_dropped_rather_than_defaulted(
    intake: LocalFixtureIntakeAdapter,
) -> None:
    """The rule the book's invalid records exist to prove, run over the store that serves them."""
    admitted = {envelope.external_id for envelope in pipeline.collect_all_issues(intake)}
    assert not admitted & set(demo_book.INVALID_RECORDS), (
        f"a record that should have been dropped was admitted: "
        f"{sorted(admitted & set(demo_book.INVALID_RECORDS))}"
    )
    assert len(admitted) == 7, "the seven valid records must still be admitted"


def test_a_deferred_source_with_no_landing_table_answers_empty(
    intake: LocalFixtureIntakeAdapter,
) -> None:
    """A deferred feeder is not wired, and an empty tuple is the honest answer."""
    deferred = [s for s in IssueSource if s.value not in demo_book.SOURCE_TABLES]
    if not deferred:
        pytest.skip("every source is wired in this wave")
    assert intake.fetch(deferred[0]) == ()


# --------------------------------------------------------------------------- #
# The key the dataset stamps onto every table it creates
# --------------------------------------------------------------------------- #
# Watched failing first, on a copy of bigquery.tf with one table's block deleted: the per-table
# assertion names the table, and the count assertion catches a table added later with no block
# at all.
_TABLE_BLOCK = re.compile(r'resource\s+"google_bigquery_table"\s+"(\w+)"\s*\{(.*?)\n\}', re.DOTALL)
_TABLE_KEY = re.compile(r"\n\s*encryption_configuration\s*\{[^}]*?kms_key_name\s*=\s*([^\s#]+)")
_ANY_TABLE_BLOCK = re.compile(r"\n\s*encryption_configuration\s*\{")


def _dataset_default_key() -> str:
    """The key the dataset's ``default_encryption_configuration`` names."""
    block = re.search(
        r"default_encryption_configuration\s*\{(.*?)\n  \}",
        _TF.read_text(encoding="utf-8"),
        flags=re.DOTALL,
    )
    assert block is not None, "the dataset declares no default_encryption_configuration"
    key = re.search(r"kms_key_name\s*=\s*([^\s#]+)", block.group(1))
    assert key is not None, "the dataset's default_encryption_configuration names no key"
    return key.group(1)


def test_every_table_declares_the_key_the_dataset_would_stamp_on_it() -> None:
    """An inherited CMEK key is a REPLACEMENT waiting to happen, and a replaced table is empty.

    The dataset's ``default_encryption_configuration`` makes BigQuery stamp that key onto every
    table it creates in the dataset, so the live table carries an ``encryption_configuration``
    whether or not the Terraform declares one. Terraform then reads the undeclared block as a
    REMOVAL, and removing an encryption configuration FORCES REPLACEMENT: the table is destroyed
    and recreated, and a recreated table holds no rows. Proved by execution against a sibling
    deployment on 2026-09-12, where every loaded table planned as ``must be replaced`` with
    ``encryption_configuration { # forces replacement }`` as the cause.

    CMEK cascades in BigQuery's model and not in Terraform's, which is why the key is named
    twice, and why nothing but a check like this notices when it is named once.
    """
    text = _TF.read_text(encoding="utf-8")
    expected = _dataset_default_key()
    blocks = _TABLE_BLOCK.findall(text)
    assert blocks, "no google_bigquery_table blocks found; the regex or the file moved"

    for name, block in blocks:
        declared = _TABLE_KEY.search(block)
        assert declared is not None, (
            f"google_bigquery_table.{name} declares no encryption_configuration. The dataset "
            "stamps its key onto the table anyway, so the next plan reads the server-set block "
            "as a removal and REPLACES the table, which destroys every row it holds."
        )
        assert declared.group(1) == expected, (
            f"google_bigquery_table.{name} names {declared.group(1)} where the dataset stamps "
            f"{expected}. A table keyed differently from the dataset default is still a "
            "replacement at the next plan."
        )

    # The count is the half that catches a table added LATER with no block at all: iterating the
    # tables found cannot fail over a table nobody declared a key for if nobody looks at how many
    # keys were declared.
    assert len(_ANY_TABLE_BLOCK.findall(text)) == len(blocks), (
        f"{len(blocks)} google_bigquery_table resources and "
        f"{len(_ANY_TABLE_BLOCK.findall(text))} table-level encryption_configuration blocks; "
        "every table needs exactly one, naming the dataset's key."
    )
