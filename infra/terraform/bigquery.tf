# bigquery.tf : the five per-source issue landing tables this vertical reads (CMEK, read-only).
#
# THIS FILE DID NOT EXIST, and the adapter that reads it could not have run either way.
# `adapters/gcp/intake.py` queried ``SELECT * FROM `aud1_findings```: an UNQUALIFIED table name,
# against a client constructed with no project and no default dataset, which BigQuery rejects
# before it ever looks for the table. Nothing here created any of the five tables, no API was
# enabled, no IAM role named BigQuery and no CMEK bound the dataset. All of it arrives together,
# because a dataset missing any one of them fails a different silent way.
#
# Every column is STRING. A landing table holds what the feed PUBLISHED, and the normalizer in
# `domain/capa.py` is what parses and refuses: it reads a date with `date.fromisoformat` and a
# loss amount from text. Typing a column here would make the warehouse do a conversion the
# offline store does not, and the two would hand the normalizer different types for one record.
#
# NULLABLE where a record is allowed to arrive without the field, so the drop-not-default rule
# has something to drop. `_require` reads `raw.get(key)`, so NULL and missing reach the same
# refusal; a warehouse row cannot omit a key, only null it.
#
# General Principle map:
#   P-03 (residency): created in the EFFECTIVE region. `location` is OPTIONAL on this resource,
#         so a null would not fail the plan: it would silently create the dataset in the US
#         multi-region and break residency with a green gate. var.region defaults to null;
#         local.region is the resolved one.
#   P-09 (CMEK explicit): CMEK does not cascade, so the BigQuery service-agent key binding is
#         declared in kms.tf alongside the key.
#   P-04 (data minimisation): the columns below are exactly the ones the adapter reads
#         (`SELECTED_COLUMNS`), and a contract test holds the two together. The adapter used to
#         say `SELECT *`, which is a read set no test can see.
#
# The serving identity gets dataViewer and nothing more (iam.tf). This service triages issues and
# raises none: in production the rows are written by the five feeds. For a DEMO deployment they
# come from `scripts/load_demo_book.py`, which runs as an operator and never as the service.

resource "google_bigquery_dataset" "issue_intake" {
  dataset_id  = "issue_intake" # matches CAPA_BQ_DATASET
  project     = var.project_id
  location    = local.region # P-03
  description = "Per-source issue landing tables for issue-remediation-capa (internal, CMEK)."

  default_encryption_configuration {
    kms_key_name = google_kms_crypto_key.cmek.id # CMEK does not cascade (P-09)
  }

  delete_contents_on_destroy = false

  depends_on = [
    google_project_service.required,
    google_kms_crypto_key_iam_member.bigquery,
  ]
}

# internal-audit-lifecycle findings (AUD1).
resource "google_bigquery_table" "aud1_findings" {
  dataset_id          = google_bigquery_dataset.issue_intake.dataset_id
  table_id            = "aud1_findings"
  project             = var.project_id
  deletion_protection = true

  schema = jsonencode([
    { name = "finding_id", type = "STRING", mode = "REQUIRED" },
    { name = "description", type = "STRING", mode = "NULLABLE" },
    { name = "engagement", type = "STRING", mode = "REQUIRED" },
    { name = "raised_on", type = "STRING", mode = "REQUIRED" },
    { name = "rating", type = "STRING", mode = "REQUIRED" },
    { name = "title", type = "STRING", mode = "REQUIRED" },
  ])
}

# continuous-controls-monitoring exceptions (AUD2).
resource "google_bigquery_table" "aud2_exceptions" {
  dataset_id          = google_bigquery_dataset.issue_intake.dataset_id
  table_id            = "aud2_exceptions"
  project             = var.project_id
  deletion_protection = true

  schema = jsonencode([
    { name = "exception_id", type = "STRING", mode = "REQUIRED" },
    { name = "control_id", type = "STRING", mode = "REQUIRED" },
    { name = "description", type = "STRING", mode = "REQUIRED" },
    { name = "detected_on", type = "STRING", mode = "REQUIRED" },
    { name = "severity", type = "STRING", mode = "REQUIRED" },
  ])
}

# regulatory-horizon obligation changes (RSK1).
resource "google_bigquery_table" "rsk1_horizon_changes" {
  dataset_id          = google_bigquery_dataset.issue_intake.dataset_id
  table_id            = "rsk1_horizon_changes"
  project             = var.project_id
  deletion_protection = true

  schema = jsonencode([
    { name = "change_id", type = "STRING", mode = "REQUIRED" },
    { name = "impact", type = "STRING", mode = "REQUIRED" },
    { name = "obligation_ref", type = "STRING", mode = "REQUIRED" },
    { name = "published_on", type = "STRING", mode = "REQUIRED" },
    { name = "summary", type = "STRING", mode = "REQUIRED" },
  ])
}

# complaints-review themes (DOC6).
resource "google_bigquery_table" "doc6_findings" {
  dataset_id          = google_bigquery_dataset.issue_intake.dataset_id
  table_id            = "doc6_findings"
  project             = var.project_id
  deletion_protection = true

  schema = jsonencode([
    { name = "complaint_id", type = "STRING", mode = "REQUIRED" },
    { name = "logged_on", type = "STRING", mode = "REQUIRED" },
    { name = "severity", type = "STRING", mode = "REQUIRED" },
    { name = "summary", type = "STRING", mode = "NULLABLE" },
    { name = "theme", type = "STRING", mode = "REQUIRED" },
  ])
}

# operational loss events.
resource "google_bigquery_table" "loss_events" {
  dataset_id          = google_bigquery_dataset.issue_intake.dataset_id
  table_id            = "loss_events"
  project             = var.project_id
  deletion_protection = true

  schema = jsonencode([
    { name = "event_id", type = "STRING", mode = "REQUIRED" },
    { name = "category", type = "STRING", mode = "REQUIRED" },
    { name = "gross_loss", type = "STRING", mode = "REQUIRED" },
    { name = "narrative", type = "STRING", mode = "REQUIRED" },
    { name = "occurred_on", type = "STRING", mode = "REQUIRED" },
  ])
}

# The manifest the demo loader writes LAST, because it records the load that wrote the others.
# It is deliberately not one of this repository's own tables: `hex_service_kit.demobook` keeps it
# out of `TABLES` and appends it in `load_order()`, and the loader creates nothing, so a manifest
# missing from here is a load that exits on a not-found before writing a single row.
#
# `fictional` is what the overwrite guard reads. A demo loader truncates, which is right for a
# demo book and catastrophic for a real issue register, so it proceeds only when every table is
# empty or this row says what the dataset holds is fictional.
resource "google_bigquery_table" "book_manifest" {
  dataset_id          = google_bigquery_dataset.issue_intake.dataset_id
  table_id            = "book_manifest"
  project             = var.project_id
  deletion_protection = true

  schema = jsonencode([
    { name = "book_version", type = "STRING", mode = "REQUIRED" },
    { name = "as_of_date", type = "DATE", mode = "REQUIRED" },
    { name = "fictional", type = "BOOL", mode = "REQUIRED" },
    { name = "loaded_at", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "source_commit", type = "STRING", mode = "NULLABLE" },
    { name = "tenant", type = "STRING", mode = "REQUIRED" },
  ])
}
