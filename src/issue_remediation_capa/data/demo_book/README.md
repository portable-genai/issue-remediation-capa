# The shipped issue-intake book

Everything in these files is **fictional**: invented parties, `.example` domains, synthetic
identifiers.

One file per managed BigQuery landing table, newline-delimited JSON, each row's keys in that
table's column order. The same files feed three readers, which is the point of them being files:

| Reader | How it reads them |
|---|---|
| the offline profiles | `adapters/local/intake.py`, through DuckDB, over the same five schemas the warehouse holds |
| the deployment | `scripts/load_demo_book.py`, into `issue_intake.*` |
| the tests | `tests/contract/test_demo_book.py`, which holds these columns against `infra/terraform/bigquery.tf` |

## Every row carries every column

A warehouse row has NULLs, never absent keys. The offline fixture used to omit a field to make a
record schema-invalid, which is a shape BigQuery cannot produce. `domain/capa.py::_require` reads
`raw.get(key)`, so `None` and missing reach the same refusal, and the drop-not-default rule holds
either way; but only if the book carries the column and nulls it. Otherwise the laptop and the
deployment drop different sets of records, and nothing says so.

## The two records that must stay broken

`F-2026-013` (an unknown rating, and no description) and `C-5502` (no summary) are broken on
purpose. The drop-not-default rule is the one an offline gate is most likely to prove vacuously:
with no invalid record in the book, a normalizer that admitted everything would pass every test.
`demo_book.INVALID_RECORDS` names them, and deleting one fails the build.

## Everything is a string

A landing table holds what the feed published. `gross_loss` is `"420000"`, text, because that is
what the feed publishes and the normalizer is what parses it. Typing the column would make the
warehouse do a conversion the offline store does not, and the two would hand the normalizer
different types for one record.
