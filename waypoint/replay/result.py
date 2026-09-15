"""The replay result contract (R-OUT-2).

Four statuses, not two. ``business_outcome`` is a *successful* run that returns a
named, expected answer - "no such member" - which the caller switches on; it exits
0 like success. ``failure`` means the contract could not be kept; ``escalated``
means the run met a state it would not act on without a human.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Status = Literal["success", "business_outcome", "failure", "escalated"]

EXIT_CODES: dict[str, int] = {"success": 0, "business_outcome": 0, "failure": 1, "escalated": 3}
"""Exit 2 is left to usage errors, which the CLI framework already uses."""


@dataclass(frozen=True)
class FailureDetail:
    """Enough to debug without a transcript: where, what was expected, what was seen."""

    code: str
    message: str
    step: str | None = None
    intent: str | None = None
    expected_signature: str | None = None
    observed_signatures: tuple[str, ...] = ()
    resolved_tier: int | None = None
    url: str | None = None
    frame_urls: tuple[str, ...] = ()
    screenshot: str | None = None
    snapshot: str | None = None


@dataclass(frozen=True)
class ReplayResult:
    status: Status
    capability_id: str
    version: str
    run_id: str
    variant: str = "base"
    outputs: dict[str, str] | None = None
    """Full values: this is the caller's channel. Evidence gets a redacted copy."""
    outcome: str | None = None
    failure: FailureDetail | None = None
    telemetry: dict[str, Any] = field(default_factory=dict)
    evidence_dir: str | None = None

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    def to_dict(self, *, outputs: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Serializable form; pass ``outputs`` to substitute (e.g. redacted) values."""
        body = asdict(self)
        if outputs is not None:
            body["outputs"] = dict(outputs)
        return body
