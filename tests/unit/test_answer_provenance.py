"""The service half of the model pills: which model ANSWERED, and whether it searched.

The console shows two pills at the top right: the model that answered the last request, and
``Search`` when that answer used an online search tool. Both come from response headers the kit
emits (``install_answer_provenance`` in ``api/app.py``) for whatever the model adapters NOTED as
they called. Before a request is answered the pill shows ``generator_model`` from ``/healthz``,
so that value must be the model the bound adapter calls, never one a configuration flag names
while the adapter calls another.

Sampling is decided per call. The root-cause note is drafting, so it sends no temperature at all
(some models reject the parameter, so free means absent, never 1.0). The Gemini adapter is driven
here through a FAKE ``google.genai`` module.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hex_service_kit import provenance
from hex_service_kit.localmodel import LocalModelClient, LocalModelSettings

from issue_remediation_capa import config
from issue_remediation_capa.adapters.gcp.generation import CloudGenerationAdapter
from issue_remediation_capa.adapters.live.generation import LocalModelGenerationAdapter
from issue_remediation_capa.adapters.local.generation import LocalGenerationAdapter
from issue_remediation_capa.config import Settings
from issue_remediation_capa.ports.generation import GenerationRequest, GenerationResponse

from tests import REPO_ROOT
from tests.conftest import local_settings

ANSWERED_BY = "x-answered-by"
SEARCH_USED = "x-search-used"
STUB = LocalGenerationAdapter.MODEL
_AUDITOR = {"X-Dev-Persona": "auditor"}
_ASSESS = {
    "source": "aud2_exception",
    "external_id": "E-88",
    "state": "remediation_in_progress",
    "state_since": "2026-06-15",
    "as_of": "2026-06-30",
    "provided_evidence": [],
    "review_ref": "",
}


def _assess(api_client: TestClient) -> dict[str, str]:
    response = api_client.post("/v1/issues/assess", json=_ASSESS, headers=_AUDITOR)
    assert response.status_code == 200, response.text
    return dict(response.headers)


def _request(**overrides: Any) -> GenerationRequest:
    base: dict[str, Any] = {
        "system": "You draft a CAPA root-cause note.",
        "prompt": "Facts: severity=critical overdue_business_days=4",
        "facts": (("severity", "critical"), ("overdue_business_days", "4")),
    }
    base.update(overrides)
    return GenerationRequest(**base)


def test_the_local_narrator_answers_as_the_stub_the_pill_first_names(
    api_client: TestClient,
) -> None:
    """Under ``local`` the pill before and after the answer name the same stub."""
    headers = _assess(api_client)
    assert headers[ANSWERED_BY] == STUB == local_settings().generator_model
    assert SEARCH_USED not in headers


def test_a_call_that_searched_says_so_and_the_next_request_starts_fresh(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = LocalGenerationAdapter.generate

    def searching(self: LocalGenerationAdapter, request: GenerationRequest) -> GenerationResponse:
        provenance.note_model("fake-searching-model")
        provenance.note_search()
        return original(self, request)

    monkeypatch.setattr(LocalGenerationAdapter, "generate", searching)
    headers = _assess(api_client)
    assert headers[ANSWERED_BY] == f"fake-searching-model, {STUB}"
    assert headers[SEARCH_USED] == "true"
    monkeypatch.setattr(LocalGenerationAdapter, "generate", original)
    headers = _assess(api_client)
    assert headers[ANSWERED_BY] == STUB
    assert SEARCH_USED not in headers


def test_the_root_cause_note_is_drafted_with_no_temperature(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one call site is the root-cause note: drafting, so free, so no temperature sent."""
    seen: list[GenerationRequest] = []
    original = LocalGenerationAdapter.generate

    def recording(self: LocalGenerationAdapter, request: GenerationRequest) -> GenerationResponse:
        seen.append(request)
        return original(self, request)

    monkeypatch.setattr(LocalGenerationAdapter, "generate", recording)
    _assess(api_client)
    assert seen, "the assessment never reached the narrator"
    assert all(request.temperature is None for request in seen)


# --------------------------------------------------------------------------------------- #
# The Gemini adapter, through a fake SDK.
# --------------------------------------------------------------------------------------- #
def _fake_genai(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def generate_content(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(text='{"note": "ok"}')

    genai = types.ModuleType("google.genai")
    genai_types = types.ModuleType("google.genai.types")
    genai_types.GenerateContentConfig = lambda **kw: SimpleNamespace(**kw)  # type: ignore[attr-defined]
    genai.types = genai_types  # type: ignore[attr-defined]
    genai.Client = lambda **_: SimpleNamespace(  # type: ignore[attr-defined]
        models=SimpleNamespace(generate_content=generate_content)
    )
    google = sys.modules.get("google") or types.ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setattr(google, "genai", genai, raising=False)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_types)
    return calls


def _gcp_settings() -> Settings:
    return dataclasses.replace(local_settings(), profile="gcp")


def test_the_gemini_narrator_notes_its_model_and_sends_no_free_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_genai(monkeypatch)
    with provenance.scope() as record:
        response = CloudGenerationAdapter(_gcp_settings()).generate(_request())
    assert record.models == [response.model] == [_gcp_settings().generator_model]
    assert record.search_used is False
    assert not hasattr(calls[0]["config"], "temperature"), "free must be absent, never 1.0"


def test_a_pinned_request_reaches_the_gemini_config(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_genai(monkeypatch)
    CloudGenerationAdapter(_gcp_settings()).generate(_request(temperature=0.0))
    assert calls[0]["config"].temperature == 0.0


def test_the_live_narrator_sends_no_temperature_and_the_kit_notes_the_model() -> None:
    bodies: list[dict[str, Any]] = []

    def transport(url: str, body: bytes | None, timeout: float) -> bytes:
        assert body is not None
        bodies.append(json.loads(body))
        return json.dumps(
            {
                "model": "a-local-model",
                "choices": [{"message": {"content": '{"note": "ok"}'}}],
                "usage": {},
            }
        ).encode()

    client = LocalModelClient(LocalModelSettings(), transport=transport)
    with provenance.scope() as record:
        LocalModelGenerationAdapter(local_settings(), client=client).generate(_request())
    assert "temperature" not in bodies[0]
    assert record.models == ["a-local-model"]


# --------------------------------------------------------------------------------------- #
# generator_model is the model the adapter calls.
# --------------------------------------------------------------------------------------- #
def test_no_flag_swaps_in_a_model_the_adapter_never_calls() -> None:
    """The latent false banner: a flag that moved the pill but not the model that answered."""
    models = SimpleNamespace(
        reasoning="the-model-the-adapter-calls",
        hard_reasoning="a-model-nobody-calls",
        use_hard_reasoning=True,
    )
    named = config._model_from_settings(SimpleNamespace(models=models), "models.reasoning")
    assert named == "the-model-the-adapter-calls"


def test_the_hard_reasoning_flag_does_not_exist() -> None:
    settings_file = (REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    assert "use_hard_reasoning" not in settings_file
    for source in sorted((REPO_ROOT / "src").rglob("*.py")):
        assert "use_hard_reasoning" not in source.read_text(encoding="utf-8"), source
