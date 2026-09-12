# bigquery_cmek.tftest.hcl: the key the dataset stamps onto its tables, asserted at PLAN time.
#
# `mock_provider` only, so this runs with NO credentials, NO project and NO network beyond the
# provider download, the same way the rest of this directory's tests do:
#
#   terraform init -backend=false && terraform test
#
# What it defends. The dataset declares a `default_encryption_configuration`, so BigQuery stamps
# that key onto every table it creates in the dataset. A `google_bigquery_table` resource that
# declares no `encryption_configuration` therefore describes a table the server has already keyed,
# and the next plan reads the absent block as a REMOVAL. Removing an encryption configuration
# FORCES REPLACEMENT: the table is destroyed and recreated, and a recreated table holds no rows.
# Proved by execution against a sibling deployment on 2026-09-12, where every loaded table planned
# as `must be replaced` with `encryption_configuration { # forces replacement }` as the cause.
# CMEK cascades in BigQuery's model and not in Terraform's, which is why the key is named twice.
#
# PRESENCE, not the key id: under `mock_provider` the crypto key's id is unknown at plan time, so
# comparing against it is an unresolvable condition rather than a check. The identity of the key is
# asserted textually instead, by `tests/contract/test_demo_book.py`, which also fails when a table
# is added later with no block at all. This file is the plan-level half: it proves the declaration
# survives into a plan rather than merely appearing in the source.

mock_provider "google" {}

run "every_table_declares_the_datasets_key" {
  command = plan

  variables {
    project_id    = "fictional-agent-project"
    enable_vpc_sc = false
  }

  assert {
    condition     = length(google_bigquery_dataset.issue_intake.default_encryption_configuration) == 1
    error_message = "The dataset must declare the CMEK default; without it the tables below have no key to agree with."
  }

  assert {
    condition     = length(google_bigquery_table.aud1_findings.encryption_configuration) == 1
    error_message = "google_bigquery_table.aud1_findings must declare the key the dataset stamps on it; an undeclared block reads as a removal at the next plan, which REPLACES the table and destroys every row it holds."
  }

  assert {
    condition     = length(google_bigquery_table.aud2_exceptions.encryption_configuration) == 1
    error_message = "google_bigquery_table.aud2_exceptions must declare the key the dataset stamps on it; an undeclared block reads as a removal at the next plan, which REPLACES the table and destroys every row it holds."
  }

  assert {
    condition     = length(google_bigquery_table.rsk1_horizon_changes.encryption_configuration) == 1
    error_message = "google_bigquery_table.rsk1_horizon_changes must declare the key the dataset stamps on it; an undeclared block reads as a removal at the next plan, which REPLACES the table and destroys every row it holds."
  }

  assert {
    condition     = length(google_bigquery_table.doc6_findings.encryption_configuration) == 1
    error_message = "google_bigquery_table.doc6_findings must declare the key the dataset stamps on it; an undeclared block reads as a removal at the next plan, which REPLACES the table and destroys every row it holds."
  }

  assert {
    condition     = length(google_bigquery_table.loss_events.encryption_configuration) == 1
    error_message = "google_bigquery_table.loss_events must declare the key the dataset stamps on it; an undeclared block reads as a removal at the next plan, which REPLACES the table and destroys every row it holds."
  }

  assert {
    condition     = length(google_bigquery_table.book_manifest.encryption_configuration) == 1
    error_message = "google_bigquery_table.book_manifest must declare the key the dataset stamps on it; an undeclared block reads as a removal at the next plan, which REPLACES the table and destroys every row it holds."
  }
}
