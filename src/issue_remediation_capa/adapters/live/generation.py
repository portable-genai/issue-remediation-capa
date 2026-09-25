"""Live GenerationPort: narrate the root-cause note with the laptop's local open-weight model.

The ``live`` profile binds every port to its ``local`` adapter except this one, which calls the
fleet's shared local model server (OpenAI-compatible ``/chat/completions``, Gemma 4 31B by
default) through ``hex_service_kit.localmodel``. The kit client owns what every local server gets
wrong: it states the schema in the prompt, strips a markdown fence, validates the answer and
feeds a failure back for a corrected reply. This adapter only maps the port's request and
response onto it. Embeddings stay on the offline hashing adapter under ``live``: the local model
server serves chat completions, not embeddings.

The discipline is the managed adapter's: the model restates engine-owned facts and decides
nothing. ``RcaService`` still parses and grounding-checks the returned text and falls back to the
deterministic note on any failure, so a local model can no more smuggle a figure into a note
than Gemini can.

No SDK and no network at construction: the client is built from the three-state
``LOCAL_MODEL_URL`` / ``LOCAL_MODEL`` / ``LOCAL_MODEL_TIMEOUT`` settings and first speaks to the
server when a note is requested.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from hex_service_kit.localmodel import (
    LocalModelClient,
    LocalModelOutputError,
    LocalModelSettings,
    LocalModelUnavailable,
)

from ...config import Settings
from ...ports.generation import GenerationRequest, GenerationResponse

_log = logging.getLogger(__name__)

#: The sampling temperature the managed adapter narrates at. The request carries none, so the
#: live lane samples exactly as the managed lane does rather than choosing a value of its own.
_TEMPERATURE = 0.2


def response_schema(request: GenerationRequest) -> dict[str, Any]:
    """The JSON Schema the request's ``response_keys`` describe: each key a non-empty string."""
    return {
        "type": "object",
        "properties": {key: {"type": "string", "minLength": 1} for key in request.response_keys},
        "required": list(request.response_keys),
    }


class LocalModelGenerationAdapter:
    """Kit-backed narrator for the ``live`` profile."""

    def __init__(self, settings: Settings, *, client: LocalModelClient | None = None) -> None:
        self._settings = settings
        self._client = client or LocalModelClient(LocalModelSettings.from_env())

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        messages = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.prompt},
        ]
        try:
            completion = self._client.complete_json(
                messages,
                schema=response_schema(request),
                temperature=_TEMPERATURE,
                max_tokens=request.max_output_tokens,
            )
        except (LocalModelUnavailable, LocalModelOutputError):
            # RcaService falls back to the deterministic note on any narrator failure, so this
            # line is the presenter's only sign of it; an unavailable server's message ends with
            # the two lines that start one.
            _log.warning("live narration failed", exc_info=True)
            raise
        usage: tuple[tuple[str, int], ...] = ()
        if completion.usage is not None:
            usage = (
                ("input_tokens", completion.usage.input_tokens),
                ("output_tokens", completion.usage.output_tokens),
            )
        # The validated value, re-serialised: the raw text may still carry the fence or the
        # sentence the kit stripped, and RcaService parses this as JSON.
        return GenerationResponse(
            text=json.dumps(completion.data), model=completion.model, usage=usage
        )
