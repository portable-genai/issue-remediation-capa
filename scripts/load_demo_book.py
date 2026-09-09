#!/usr/bin/env python3
"""Load the shipped fictional issue landing rows into the managed BigQuery dataset.

The laptop reads the book from DuckDB and the deployment reads it from BigQuery. This is
what puts the same rows in the second place, from the same files, so the two surfaces can be
compared rather than merely both work. The pattern is shared: the reading, the overwrite
guard and the tenant rule come from :mod:`hex_service_kit.demobook`, so the rule that a demo
loader may never truncate a real book is proved in one place across every repository that
has one.

Two things it will not do.

**It will not overwrite a book it did not write.** It truncates and reloads, which is right
for a demo book and catastrophic for a real one, so it proceeds only when every target table
is empty or the dataset's own ``book_manifest`` declares what it holds fictional.

**It will not decide the tenant.** On a deployment the tenant is whatever the identity
adapter resolves from the IAP assertion, and rows loaded under any other value are invisible
to every real user because object authorization is fail-closed. An invisible row reads
exactly like an empty dataset, so ``--tenant`` is required.

Usage::

    python scripts/load_demo_book.py --project <id> --tenant <hosted-domain> --dry-run
    python scripts/load_demo_book.py --project <id> --tenant <hosted-domain>

``--dry-run`` writes the NDJSON it would load to a directory and stops: no credentials, no
``[gcp]`` extra, because the BigQuery import is inside the function that loads.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from hex_service_kit.demobook import may_overwrite, retenant  # noqa: E402

from issue_remediation_capa import demo_book  # noqa: E402
from issue_remediation_capa.config import Settings  # noqa: E402

#: The manifest is written last: it records the load that wrote the others.
_TABLES = (*(table.name for table in demo_book.TABLES), demo_book.BOOK.MANIFEST.name)


def _source_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def rows_to_load(tenant: str) -> dict[str, list[dict[str, Any]]]:
    """The rows this run would write, per table, with the tenant and manifest resolved."""
    demo_book.validate()
    loaded_at = datetime.now(UTC).isoformat()
    commit = _source_commit()
    out: dict[str, list[dict[str, Any]]] = {}
    for name in _TABLES:
        rows = demo_book.BOOK.rows(name)
        rows = retenant(rows, tenant, keep_separate=demo_book.CROSS_TENANT)
        if name == demo_book.BOOK.MANIFEST.name:
            rows = [dict(row, loaded_at=loaded_at, source_commit=commit) for row in rows]
        out[name] = rows
    return out


def write_ndjson(rows: dict[str, list[dict[str, Any]]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for table, table_rows in rows.items():
        path = out_dir / f"{table}.ndjson"
        path.write_text("".join(json.dumps(row) + "\n" for row in table_rows), encoding="utf-8")
        written.append(path)
    return written


def _existing(client: Any, dataset_ref: str) -> dict[str, int]:
    from google.cloud import bigquery  # noqa: PLC0415 - lazy
    from google.cloud.exceptions import NotFound  # noqa: PLC0415

    counts: dict[str, int] = {}
    for table in _TABLES:
        try:
            ref = bigquery.TableReference.from_string(f"{dataset_ref}.{table}")
            counts[table] = int(client.get_table(ref).num_rows)
        except NotFound:
            raise SystemExit(
                f"table {dataset_ref}.{table} does not exist. Apply infra/terraform first: "
                "the loader fills tables, it never creates them."
            ) from None
    return counts


def load(project: str, dataset: str, rows: dict[str, list[dict[str, Any]]], location: str) -> None:
    from google.cloud import bigquery  # noqa: PLC0415 - lazy

    client = bigquery.Client(project=project, location=location)
    dataset_ref = f"{project}.{dataset}"
    counts = _existing(client, dataset_ref)
    manifest_name = demo_book.BOOK.MANIFEST.name
    manifest = (
        [
            dict(row)
            for row in client.query(
                f"SELECT fictional FROM `{dataset_ref}.{manifest_name}`"
            ).result()
        ]
        if counts.get(manifest_name)
        else []
    )
    if not may_overwrite(counts, manifest):
        held = ", ".join(f"{table}={n}" for table, n in sorted(counts.items()) if n)
        raise SystemExit(
            f"refusing to load: {dataset_ref} holds rows ({held}) and its {manifest_name} does "
            "not say they are fictional. This loader truncates; point it at an empty dataset "
            "or one holding a previous demo book."
        )
    for table in _TABLES:
        job = client.load_table_from_json(
            rows[table],
            f"{dataset_ref}.{table}",
            job_config=bigquery.LoadJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
            ),
        )
        job.result()
        print(f"  {table}: {len(rows[table])} rows")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default="", help="the GCP project holding the dataset")
    parser.add_argument("--dataset", default="", help="dataset id (default: from settings.yaml)")
    parser.add_argument("--location", default="", help="BigQuery location (default: the region)")
    parser.add_argument(
        "--tenant",
        required=True,
        help=(
            "the owning tenant to stamp on every customer row. On a deployment this is what "
            "the identity adapter resolves; rows under any other value are invisible because "
            "object authorization is fail-closed."
        ),
    )
    parser.add_argument(
        "--dry-run",
        metavar="DIR",
        nargs="?",
        const="build/demo-book",
        help="write the NDJSON that would be loaded to DIR and stop (no credentials needed)",
    )
    args = parser.parse_args(argv)

    settings = Settings.load()
    dataset = (args.dataset or settings.bigquery_dataset).strip()
    if not dataset:
        raise SystemExit(
            "no dataset: set CAPA_BQ_DATASET or pass --dataset. The loader fills the five "
            "landing tables the managed intake reads, and it will not guess which."
        )
    location = args.location or settings.region
    rows = rows_to_load(args.tenant)

    if args.dry_run is not None:
        out_dir = Path(args.dry_run)
        written = write_ndjson(rows, out_dir)
        total = sum(len(r) for r in rows.values())
        print(f"dry run: {total} rows for {dataset} (tenant {args.tenant!r}), not loaded")
        for path in written:
            print(f"  {path} ({len(rows[path.stem])} rows)")
        return 0

    project = args.project or settings.project_id
    if not project or project.startswith("your-"):
        raise SystemExit("--project is required (or set GOOGLE_CLOUD_PROJECT)")
    print(f"loading the demo book into {project}.{dataset} as tenant {args.tenant!r}")
    load(project, dataset, rows, location)
    print(
        "done. Verify: CAPA_PROFILE=gcp issue_remediation_capa register, which must admit "
        "seven records and DROP the two the book ships broken."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
