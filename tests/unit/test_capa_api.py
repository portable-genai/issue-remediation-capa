"""The /v1/issues/assess and /v1/themes surfaces: R8 routing, cross-tenant 403, read-only feed."""

from __future__ import annotations

from fastapi.testclient import TestClient

_AUDITOR = {"X-Dev-Persona": "auditor"}  # tenant demo-bank: the issue-store owner
_OTHER_TENANT = {"X-Dev-Persona": "other-tenant"}  # tenant other-bank


def _assess_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "source": "aud2_exception",
        "external_id": "E-88",
        "state": "remediation_in_progress",
        "state_since": "2026-06-15",
        "as_of": "2026-06-30",
        "provided_evidence": [],
        "review_ref": "",
    }
    body.update(overrides)
    return body


def test_assess_escalates_an_sla_breach_and_routes_it(api_client: TestClient) -> None:
    resp = api_client.post("/v1/issues/assess", json=_assess_body(), headers=_AUDITOR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["aging_kind"] == "sla_breach"
    assert body["requires_human_review"] is True
    # Rule R8: the escalation was routed, not merely flagged.
    assert body["review_ref"]
    assert body["rca_note"], "a surface always returns a grounded RCA note"
    assert body["can_close"] is False


def test_assess_denies_a_cross_tenant_principal_with_403(api_client: TestClient) -> None:
    resp = api_client.post("/v1/issues/assess", json=_assess_body(), headers=_OTHER_TENANT)
    # 403, not 404: the store exists and the caller is simply not authorised for it.
    assert resp.status_code == 403


def test_assess_returns_404_for_an_unknown_issue(api_client: TestClient) -> None:
    resp = api_client.post(
        "/v1/issues/assess", json=_assess_body(external_id="NOPE"), headers=_AUDITOR
    )
    assert resp.status_code == 404


def test_themes_feed_returns_clustered_issues(api_client: TestClient) -> None:
    resp = api_client.get("/v1/themes", headers=_AUDITOR)
    assert resp.status_code == 200
    themes = resp.json()["themes"]
    assert themes, "the fixture feed produces at least one theme"
    assert all(t["member_ids"] for t in themes)
    assert all(t["citations"] for t in themes)


def test_the_theme_feed_has_no_write_path(api_client: TestClient) -> None:
    # The rcsa-kri-erm feed is one-way: a write verb must not be routed to a handler.
    assert api_client.post("/v1/themes", json={}, headers=_AUDITOR).status_code == 405
    assert api_client.delete("/v1/themes", headers=_AUDITOR).status_code == 405


def test_themes_denies_a_cross_tenant_principal_with_403(api_client: TestClient) -> None:
    assert api_client.get("/v1/themes", headers=_OTHER_TENANT).status_code == 403
