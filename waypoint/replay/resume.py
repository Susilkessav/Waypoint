"""The return ladder: where a run may continue once a human hands control back.

R-RESUME-3 and R-RESUME-4. Pure - an artifact, the rendered inputs, the
sanitized screen after the handoff, and the index of the step that escalated - so
every rung is unit-testable (T8, T9). The first rung that holds wins:

1. a terminal outcome signature holds    -> that outcome
2. every postcondition holds             -> success: read the outputs
3. the escalated step's checkpoint holds -> resume after that step
4. a *declared* resume point holds       -> resume after the furthest one
5. nothing does                          -> unrecognised: escalate again

There is no default branch, and rung 4 considers only steps marked ``resume_point``.
A generic checkpoint such as "the form is visible" is true of an *empty* form;
resuming past it would skip the steps that fill it in (R-RESUME-4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias

from waypoint.artifact.schema import Capability
from waypoint.surface.ports import UISnapshot


@dataclass(frozen=True)
class Outcome:
    name: str
    class_: str


@dataclass(frozen=True)
class Success:
    pass


@dataclass(frozen=True)
class Resume:
    index: int
    via: str
    """"checkpoint" (rung 3) or "resume_point" (rung 4)."""


@dataclass(frozen=True)
class Unrecognized:
    snapshot: UISnapshot


LadderResult: TypeAlias = Outcome | Success | Resume | Unrecognized


def describe(result: LadderResult) -> str:
    if isinstance(result, Outcome):
        return f"outcome:{result.name}"
    if isinstance(result, Success):
        return "postcondition"
    if isinstance(result, Resume):
        return f"resume:{result.via}:steps[{result.index}]"
    return "unrecognized"


def return_ladder(
    cap: Capability, rendered: Mapping[str, str], snap: UISnapshot, index: int
) -> LadderResult:
    def holds(name: str) -> bool:
        return cap.signatures[name].evaluate(snap, rendered)

    for outcome in cap.outcomes:
        if holds(outcome.signature):
            return Outcome(outcome.name, outcome.class_)
    if cap.postconditions and all(holds(p.signature) for p in cap.postconditions):
        return Success()
    if index < len(cap.steps) and holds(cap.steps[index].checkpoint.signature):
        return Resume(index + 1, "checkpoint")
    points = [
        j for j in range(index, len(cap.steps))
        if cap.steps[j].resume_point and holds(cap.steps[j].checkpoint.signature)
    ]
    if points:
        return Resume(max(points) + 1, "resume_point")
    return Unrecognized(snap)
