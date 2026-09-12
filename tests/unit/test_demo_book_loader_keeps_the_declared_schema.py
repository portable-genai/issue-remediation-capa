"""The demo-book loader writes into the schema each table already has, never over it.

A BigQuery load with ``WRITE_TRUNCATE`` and no ``schema=`` autodetects a schema from the rows
and **replaces** the table's own. Nothing fails when that happens: the rows land, the demo
reads them, and every gate stays green. What it costs arrives at the next ``terraform plan``,
where every table the loader touched reports ``must be replaced`` -- because the live schema now
carries its columns in the order the JSON happened to serialise them with every mode relaxed to
NULLABLE, while ``infra/terraform/bigquery.tf`` declares them REQUIRED in its own order.
BigQuery cannot narrow a column's mode in place, so Terraform's only move is to destroy and
recreate, and a recreated table holds no rows.

Found in a sibling repository on 2026-09-12 by planning its stack before a full apply: all five
tables of a book loaded on 2026-09-09 planned as replacements. This repository's book is loaded
by the same shared pattern into a dataset that is already applied, so it was armed the same way.

Driven against a fake BigQuery client rather than the real one, because what is asserted is the
job configuration the loader builds, and this repository's venv deliberately holds no cloud SDK
(its portability proof depends on that). The fake stands in for ``google.cloud.bigquery`` the
way the module imports it, lazily, inside ``load``.

Watched failing first, by deleting the ``schema=`` argument on a copy of the loader: every
assertion below reports ``None`` where the table's schema is owed, which is exactly the
configuration that replaced the live schema.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_LOADER = Path(__file__).resolve().parents[2] / "scripts" / "load_demo_book.py"

_DATASET = "issue_intake"


def _schema_of(table: str) -> tuple[str, ...]:
    """A schema that differs per table, so reusing one table's schema for another is caught."""
    return (f"{table}.declared_column", f"{table}.another_declared_column")


class _FakeTable:
    def __init__(self, table_id: str) -> None:
        self.schema = _schema_of(table_id)
        self.num_rows = 0


class _FakeRef:
    """What ``bigquery.TableReference.from_string`` returns, reduced to what the loader uses."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.table_id = value.rsplit(".", 1)[1]

    def __str__(self) -> str:
        return self.value


class _FakeJob:
    def result(self) -> None:
        return None


class _FakeClient:
    """Records the job configuration every load was given, per table."""

    def __init__(self) -> None:
        self.loads: list[tuple[str, Any]] = []

    def get_table(self, ref: Any) -> _FakeTable:
        return _FakeTable(ref.table_id)

    def load_table_from_json(self, _rows: Any, ref: Any, job_config: Any) -> _FakeJob:
        self.loads.append((ref.table_id, job_config))
        return _FakeJob()

    def query(self, _sql: str) -> Any:  # pragma: no cover - every table is empty here
        raise AssertionError("an empty dataset must not be asked what its manifest declares")


class _LoadJobConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.schema = kwargs.get("schema")
        self.write_disposition = kwargs.get("write_disposition")


def _install_fake_bigquery(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    """Stand in for the cloud SDK this repository's offline environment does not hold."""
    bigquery = types.SimpleNamespace(
        Client=lambda **_kwargs: client,
        LoadJobConfig=_LoadJobConfig,
        WriteDisposition=types.SimpleNamespace(WRITE_TRUNCATE="WRITE_TRUNCATE"),
        TableReference=types.SimpleNamespace(from_string=_FakeRef),
    )
    exceptions = types.ModuleType("google.cloud.exceptions")
    exceptions.NotFound = type("NotFound", (Exception,), {})  # type: ignore[attr-defined]
    cloud = types.ModuleType("google.cloud")
    cloud.bigquery = bigquery  # type: ignore[attr-defined]
    cloud.exceptions = exceptions  # type: ignore[attr-defined]
    google = sys.modules.get("google") or types.ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery)
    monkeypatch.setitem(sys.modules, "google.cloud.exceptions", exceptions)


def _loader_module() -> Any:
    spec = importlib.util.spec_from_file_location("load_demo_book_under_test", _LOADER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_load_carries_the_schema_the_table_already_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _loader_module()
    client = _FakeClient()
    _install_fake_bigquery(monkeypatch, client)

    rows: dict[str, list[dict[str, Any]]] = {table: [] for table in module._TABLES}

    module.load("fictional-project", _DATASET, rows, "asia-southeast1")

    loaded = [table for table, _config in client.loads]
    assert loaded == list(module._TABLES), (
        "the loader must write every table of the book, manifest last; a check over zero loads "
        f"asserts nothing. Wrote {loaded}"
    )
    for table, config in client.loads:
        assert config.write_disposition == "WRITE_TRUNCATE"
        assert config.schema == _schema_of(table), (
            f"the load into {table} must pass that table's own schema. Without it BigQuery "
            "autodetects one from the rows and REPLACES the schema Terraform declared, and the "
            "next terraform plan then reports the table as must-be-replaced -- which destroys "
            f"every row it holds. Got {config.schema!r}"
        )
