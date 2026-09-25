"""The ``live`` profile: the laptop stack with a real local model behind the generation port.

Every test here is offline. The shared kit client takes an injected transport, so a scripted
fake stands in for the model server and the adapter is exercised through the same client a
presenter's laptop uses, retries and all.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest
from hex_service_kit.localmodel import (
    LocalModelClient,
    LocalModelOutputError,
    LocalModelSettings,
    LocalModelUnavailable,
)

from issue_remediation_capa.adapters.live.generation import LocalModelGenerationAdapter
from issue_remediation_capa.adapters.local.identity import LocalIdentityAdapter
from issue_remediation_capa.config import (
    DEFAULT_BINDINGS,
    LIVE_PROFILE,
    ProfileChoice,
    build_container,
)
from issue_remediation_capa.domain.rca import RcaService, build_request
from issue_remediation_capa.ports import PORT_PROTOCOLS
from issue_remediation_capa.ports.generation import GenerationRequest

from tests.conftest import local_settings
from tests.unit.test_rca import _assessment

_SERVED_MODEL = "local-test-model"


class FakeServer:
    """Answers each chat call with the next scripted reply and records what it was sent."""

    def __init__(self, replies: list[str], usage: dict[str, int] | None = None) -> None:
        self.replies = list(replies)
        self.usage = usage or {}
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, body: bytes | None, timeout: float) -> bytes:
        assert body is not None
        payload = json.loads(body)
        self.calls.append(payload)
        reply = {
            "model": _SERVED_MODEL,
            "choices": [{"message": {"content": self.replies.pop(0)}}],
            "usage": self.usage,
        }
        return json.dumps(reply).encode()


def _refuse(url: str, body: bytes | None, timeout: float) -> bytes:
    raise urllib.error.URLError("connection refused")


def _adapter(transport: Any) -> LocalModelGenerationAdapter:
    client = LocalModelClient(LocalModelSettings(), transport=transport)
    return LocalModelGenerationAdapter(local_settings(profile=LIVE_PROFILE), client=client)


def _request() -> GenerationRequest:
    return GenerationRequest(
        system="You draft a CAPA root-cause note.",
        prompt="Facts: severity=critical overdue_business_days=4",
        facts=(("severity", "critical"), ("overdue_business_days", "4")),
    )


def test_a_fenced_invalid_first_answer_is_corrected_and_the_value_returned() -> None:
    server = FakeServer(
        [
            '```json\n{"summary": "wrong key"}\n```',
            'Here it is:\n```json\n{"note": "Critical issue, 4 business days overdue."}\n```',
        ]
    )
    response = _adapter(server).generate(_request())

    assert json.loads(response.text) == {"note": "Critical issue, 4 business days overdue."}
    assert response.model == _SERVED_MODEL, "the id that ANSWERED, not a configured guess"
    assert len(server.calls) == 2, "the missing 'note' must be fed back and retried"
    assert "note" in server.calls[1]["messages"][-1]["content"]


def test_the_request_is_mapped_onto_the_chat_call() -> None:
    server = FakeServer(['{"note": "ok"}'])
    _adapter(server).generate(_request())

    sent = server.calls[0]
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert sent["messages"][0]["content"].startswith("You draft a CAPA root-cause note.")
    assert "JSON Schema" in sent["messages"][0]["content"], "the schema rides in the prompt"
    assert sent["messages"][1]["content"] == _request().prompt
    assert sent["max_tokens"] == _request().max_output_tokens
    assert sent["temperature"] == 0.2, "the managed adapter's sampling, not a new choice"


def test_usage_is_reported_only_when_the_server_reports_it() -> None:
    silent = _adapter(FakeServer(['{"note": "ok"}'])).generate(_request())
    assert silent.usage == (), "no usage reported must not become a fabricated zero"

    counted = _adapter(
        FakeServer(['{"note": "ok"}'], usage={"prompt_tokens": 11, "completion_tokens": 7})
    ).generate(_request())
    assert counted.usage == (("input_tokens", 11), ("output_tokens", 7))


def test_no_valid_answer_raises_the_output_error() -> None:
    server = FakeServer(["no json", "still none", "nor here"])
    with pytest.raises(LocalModelOutputError):
        _adapter(server).generate(_request())


def test_an_absent_server_raises_unavailable_with_the_start_recipe() -> None:
    with pytest.raises(LocalModelUnavailable, match="mlx_vlm.server"):
        _adapter(_refuse).generate(_request())


def test_the_rca_draft_uses_the_live_note_and_falls_back_when_the_server_is_down() -> None:
    assessment = _assessment()
    facts = dict(build_request(assessment).facts)
    note = f"Issue is {facts['severity']} severity and needs remediation evidence."
    drafted = RcaService(_adapter(FakeServer([json.dumps({"note": note})]))).draft(assessment)
    assert (drafted.text, drafted.model_authored) == (note, True)

    fallback = RcaService(_adapter(_refuse)).draft(assessment)
    assert fallback.model_authored is False


def test_the_container_builds_every_port_under_live() -> None:
    container = build_container(local_settings(profile=LIVE_PROFILE))
    for port, protocol in PORT_PROTOCOLS.items():
        assert isinstance(getattr(container, port), protocol), port
    assert isinstance(container.generation, LocalModelGenerationAdapter)


def test_live_binds_what_local_binds_except_the_model_port() -> None:
    differing = {
        port for port, table in DEFAULT_BINDINGS.items() if table["live"] != table["local"]
    }
    assert differing == {"generation"}


def test_live_takes_the_laptop_posture() -> None:
    choice = ProfileChoice(profile=LIVE_PROFILE, explicit=True)
    assert choice.exposure_profile == "local"
    assert choice.bind_profile == "local"
    # The seeded personas construct under a deliberate live, exactly as under local.
    LocalIdentityAdapter(local_settings(profile=LIVE_PROFILE))


def test_the_banner_names_the_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_MODEL", "some-org/some-local-model")
    assert local_settings(profile=LIVE_PROFILE).generator_model == "some-org/some-local-model"
