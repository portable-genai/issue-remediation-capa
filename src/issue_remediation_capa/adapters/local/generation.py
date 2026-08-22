"""Local GenerationPort: a deterministic, SDK-free narrator for the offline profile.

It stands in for a managed model in the gate, the tests and the demo. It never decides anything:
it restates the engine-owned facts it is handed as a short JSON note, so its output is grounded by
construction and the whole offline pipeline (including the RCA narration path) runs with no
network and no cloud SDK. A silent empty return would let a producer ship the narration seam
unwired, so this deliberately produces a real, inspectable note.
"""

from __future__ import annotations

import json

from ...config import Settings
from ...ports.generation import GenerationRequest, GenerationResponse


class LocalGenerationAdapter:
    """Restate the request's engine facts as a deterministic JSON note (no model, no network)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        values = dict(request.facts)
        note = (
            f"Issue is {values.get('severity', 'low')} severity in state "
            f"{values.get('state', 'raised')}; aging is {values.get('aging', 'on_track')} with "
            f"{values.get('overdue_business_days', '0')} business day(s) overdue and "
            f"{values.get('missing_evidence', '0')} closure-evidence item(s) outstanding."
        )
        return GenerationResponse(text=json.dumps({"note": note}), model="local-deterministic")
