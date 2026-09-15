"""The discovery transcript: evidence of what happened, never a contract (REPORT.md §2).

It is written for the compiler, which needs - per step - the screen before, the
decision, a locator bundle synthesized while the target still existed, the result,
and the screen after. Everything in it is already sanitized: snapshots are the
redacted ``UISnapshot``, typed values are recorded only as a reference
(``$inputs.x``, ``$secrets.x``) or as "an unbound literal of N characters". A
transcript can therefore be kept as evidence without being a leak.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from waypoint.surface.ports import UIElement, UISnapshot

Ending = Literal["finished", "gave_up", "stuck", "max_steps", "refused", "blocked", "error"]


def snapshot_to_dict(snap: UISnapshot) -> dict[str, Any]:
    return asdict(snap)


def snapshot_from_dict(data: dict[str, Any]) -> UISnapshot:
    elements = tuple(
        UIElement(**{**e, "frame_path": tuple(e["frame_path"]),
                     "bbox": None if e["bbox"] is None else tuple(e["bbox"]),
                     "anchors": tuple(e["anchors"])})
        for e in data["elements"]
    )
    frame_urls = tuple((tuple(p), u) for p, u in data.get("frame_urls", ()))
    return UISnapshot(data["url"], data["title"], elements, data["text_digest"], data["hash"],
                      frame_urls)


@dataclass
class BindingSpec:
    """A declared input, without its value - values never enter the transcript."""

    name: str
    type: str = "string"
    sensitivity: str = "internal"


@dataclass
class OutputSpecDecl:
    name: str
    type: str = "string"
    format: str | None = None
    sensitivity: str = "internal"


@dataclass
class Step:
    turn: int
    pre: dict[str, Any]
    decision: dict[str, Any]
    action: str
    value_ref: str | None = None
    value_provenance: str | None = None
    target: dict[str, Any] | None = None
    """The sanitized element acted on."""
    bundle: dict[str, Any] | None = None
    """A LocatorBundle synthesized while the target was live (R-LOC-1, R-LOC-5)."""
    bundle_error: str | None = None
    ok: bool = False
    error_code: str | None = None
    error: str | None = None
    navigated: bool = False
    risk: str = "safe"
    post_hash: str | None = None
    performed_by: Literal["model", "human"] = "model"
    """``human`` for a step a person demonstrated while holding control during discovery."""
    unrecorded: str | None = None
    """For ``action == "gap"``: what a person did that could not be recorded as a step."""


@dataclass
class FinishRecord:
    snapshot: dict[str, Any]
    success: dict[str, Any]
    summary: str
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    """output name -> {"element": sanitized element, "bundle": LocatorBundle | None,
    "bundle_error": str | None}"""


@dataclass
class Transcript:
    run_id: str
    capability_id: str
    goal: str
    entry: str
    model: str
    bindings: list[BindingSpec]
    expected_outputs: list[OutputSpecDecl]
    steps: list[Step] = field(default_factory=list)
    finish: FinishRecord | None = None
    ending: Ending | None = None
    ending_detail: str = ""
    started_at: str = ""
    finished_at: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n"

    def save(self, path: Path) -> None:
        path.write_text(self.to_json())

    @staticmethod
    def load(path: Path) -> Transcript:
        data = json.loads(path.read_text())
        finish = data.get("finish")
        return Transcript(
            run_id=data["run_id"], capability_id=data["capability_id"], goal=data["goal"],
            entry=data["entry"], model=data["model"],
            bindings=[BindingSpec(**b) for b in data["bindings"]],
            expected_outputs=[OutputSpecDecl(**o) for o in data["expected_outputs"]],
            steps=[Step(**s) for s in data["steps"]],
            finish=None if finish is None else FinishRecord(**finish),
            ending=data.get("ending"), ending_detail=data.get("ending_detail", ""),
            started_at=data.get("started_at", ""), finished_at=data.get("finished_at", ""),
        )
