"""Approved capabilities, as tools an agent can discover and call by name.

This is the production entry point the brief describes: the agent-facing product decides
*what* to do and calls a capability; this system does it. So the catalog exposes exactly
what a caller needs to choose and invoke one, and nothing else:

* **Only approved releases.** A draft is not listed and cannot be invoked - approval is a
  person's decision (R-PKG-2), and an agent must not be able to route around it.
* **A typed contract, generated from the artifact.** Inputs become a JSON schema with the
  patterns and enumerations the artifact declares, so a bad argument is refused before a
  browser starts; outputs and the named business outcomes are described, so the caller can
  branch on "no such member" rather than parsing an error string.
* **Honest labels.** A capability with an irreversible step says so (``requires_human``):
  invoked unattended it will stop and ask for a person, and the caller should expect that.
  Measured confidence travels with the entry (R-PKG-6), so a caller can prefer a proven
  capability, and one that declares a bar it does not meet is listed as unavailable.

Invocation returns the replay result as it is - success, a business outcome, a failure, or
an escalation with an intervention id. Nothing is flattened into an exception: "no such
member" is an answer (R-OUT-2).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from waypoint.artifact.approval import approval_status
from waypoint.artifact.schema import SEMVER, Capability, InputSpec, load
from waypoint.replay.engine import ReplayOptions, replay, validate_inputs
from waypoint.replay.ledger import Confidence, Ledger, score
from waypoint.replay.result import ReplayResult

Format = Literal["anthropic", "openai"]


@dataclass(frozen=True)
class Entry:
    capability: Capability
    path: Path
    confidence: Confidence

    @property
    def name(self) -> str:
        return self.capability.capability_id

    @property
    def version(self) -> str:
        return self.capability.version

    @property
    def requires_human(self) -> bool:
        """True when a step commits something: unattended, it will stop and ask."""
        return any(step.risk in ("irreversible", "unknown")
                   for _, step in self.capability.all_steps())

    @property
    def available(self) -> bool:
        bar = self.capability.policy.min_confidence
        return bar is None or self.confidence.meets(bar)

    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        bar = self.capability.policy.min_confidence or 0.0
        return (f"measured confidence {self.confidence.summary()} is below the {bar:.2f} this "
                "capability requires; run `waypoint stability` before invoking it unattended")

    def summary(self) -> dict[str, Any]:
        cap = self.capability
        return {
            "name": self.name,
            "version": self.version,
            "title": cap.name,
            "description": cap.description,
            "inputs": sorted(cap.inputs.properties),
            "outputs": sorted(cap.outputs.properties),
            "outcomes": [{"name": o.name, "class": o.class_, "description": o.description}
                         for o in cap.outcomes],
            "requires_human": self.requires_human,
            "confidence": self.confidence.summary(),
            "available": self.available,
        }


def _property_schema(name: str, spec: InputSpec) -> dict[str, Any]:
    body: dict[str, Any] = {"type": "string"}
    description = spec.description or f"The {name.replace('_', ' ')}."
    if spec.pattern:
        body["pattern"] = spec.pattern
    if spec.enum:
        body["enum"] = list(spec.enum)
    if spec.format == "money":
        description += " An amount, e.g. 250.00."
    body["description"] = description
    return body


def tool_definition(entry: Entry, fmt: Format = "anthropic") -> dict[str, Any]:
    """The capability as a function an agent may call, described in its own terms."""
    cap = entry.capability
    returns = ", ".join(sorted(cap.outputs.properties)) or "nothing"
    outcomes = ", ".join(o.name for o in cap.outcomes)
    description = (
        f"{cap.description.rstrip('.')}. Returns: {returns}."
        + (f" May instead report a known outcome: {outcomes}." if outcomes else "")
        + (" Includes a step that cannot be undone, so it stops for a person."
           if entry.requires_human else "")
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {name: _property_schema(name, spec)
                       for name, spec in cap.inputs.properties.items()},
        "required": list(cap.inputs.required),
        "additionalProperties": False,
    }
    if fmt == "openai":
        return {"type": "function",
                "function": {"name": entry.name, "description": description,
                             "parameters": schema}}
    return {"name": entry.name, "description": description, "input_schema": schema}


class UnknownCapability(KeyError):
    pass


class Unavailable(RuntimeError):
    """Listed, but not callable right now: it does not meet the bar it declares."""


class Catalog:
    def __init__(self, root: Path, ledger: Ledger | None = None) -> None:
        self.root = Path(root)
        self.ledger = ledger

    # ------------------------------------------------------------- listing

    def entries(self) -> list[Entry]:
        """The highest approved release of every capability, newest first by name."""
        found: dict[str, Entry] = {}
        for path in sorted(self.root.glob("*/*.json")):
            if not re.match(SEMVER, path.stem):
                continue
            try:
                cap = load(path)
            except Exception:  # noqa: BLE001 - a broken file is not a callable capability
                continue
            if not approval_status(cap).approved:
                continue
            entry = Entry(cap, path, self._confidence(cap))
            current = found.get(cap.capability_id)
            if current is None or _semver(cap.version) > _semver(current.version):
                found[cap.capability_id] = entry
        return [found[name] for name in sorted(found)]

    def get(self, name: str, version: str | None = None) -> Entry:
        if version is not None:
            path = self.root / name / f"{version}.json"
            if path.exists():
                cap = load(path)
                if approval_status(cap).approved:
                    return Entry(cap, path, self._confidence(cap))
        else:
            for entry in self.entries():
                if entry.name == name:
                    return entry
        raise UnknownCapability(
            f"no approved capability named {name!r}"
            + (f" at version {version}" if version else "")
            + "; `waypoint catalog list` shows what is callable")

    def tools(self, fmt: Format = "anthropic") -> list[dict[str, Any]]:
        return [tool_definition(e, fmt) for e in self.entries() if e.available]

    def _confidence(self, cap: Capability) -> Confidence:
        return self.ledger.confidence(cap) if self.ledger is not None else score([])

    # ---------------------------------------------------------- invocation

    def invoke(self, name: str, arguments: Mapping[str, Any], *,
               options: ReplayOptions | None = None, version: str | None = None) -> ReplayResult:
        """Call a capability by name with typed arguments, as an agent would."""
        entry = self.get(name, version)
        if not entry.available:
            raise Unavailable(entry.unavailable_reason() or "not available")
        values = {k: str(v) for k, v in arguments.items()}
        unknown = sorted(set(values) - set(entry.capability.inputs.properties))
        if unknown:
            raise ValueError(f"{name} takes no argument(s) {unknown}")
        problems = validate_inputs(entry.capability, values)
        if problems:
            raise ValueError("; ".join(problems))
        return replay(entry.capability, values, options or ReplayOptions())


def _semver(version: str) -> tuple[int, int, int, int, str]:
    core, _, pre = version.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    return (major, minor, patch, 0 if pre else 1, pre)


def result_for_agent(result: ReplayResult) -> dict[str, Any]:
    """What the calling agent is told: an answer, or why there is not one yet."""
    body: dict[str, Any] = {"status": result.status, "capability": result.capability_id,
                            "version": result.version}
    if result.outputs:
        body["outputs"] = result.outputs
    if result.outcome:
        body["outcome"] = result.outcome
    if result.failure is not None:
        body["error"] = {"code": result.failure.code, "message": result.failure.message,
                         "step": result.failure.step}
    handoffs: Sequence[Any] = result.telemetry.get("handoffs") or ()
    if result.status == "escalated":
        body["requires_intervention"] = True
        body["intervention_history"] = [h.get("intervention") for h in handoffs]
        # invoke is synchronous: by the time it returns, its browser session has ended.
        body["waiting_on_a_person"] = False
    return body
