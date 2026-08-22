"""On-prem IssueIntakePort: fail-fast portability placeholder (the sovereign-exit proof, P-12)."""

from __future__ import annotations

from collections.abc import Mapping

from ...config import Settings
from ...domain.capa import IssueSource


class OnPremIntakeAdapter:
    """Satisfies IssueIntakePort but refuses at call time: the client wires its own feeds."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def fetch(self, source: IssueSource) -> tuple[Mapping[str, object], ...]:
        raise NotImplementedError(
            "on-prem issue intake is a portability placeholder: bind the client's own source "
            "feeds (see docs/onprem-migration.md)"
        )
