"""One bounded, policy-checked model call when a step's target is not where it was.

Replay is deterministic on purpose, so letting a model back in is a deliberate, narrow
exception - not a general fallback:

* **One step, one call, once per run.** Only when a target cannot be found, or matches
  several elements, and only if the artifact itself permits it and the caller asked for it.
* **Only safe steps.** Never an irreversible or unknown-risk step, and never while an
  irreversible action of this run is unresolved (R-REC).
* **The model may only point at an element.** It cannot choose the action, the value, a
  URL, or anything else: those come from the artifact. It sees the same sanitized screen
  discovery sees (R-SENS-3).
* **Its choice is then checked, deterministically.** The element must still be the kind of
  control the step recorded, and - where the recorded locator identified a record, such as
  "the View link in this member's row" - it must sit with that record's values (R-LOC-5).
  The action then goes through the policy engine like any other, and the step's own
  checkpoint must hold afterwards. Nothing is retried a second time.
* **It is never silently normal.** The run is marked assisted: evidence records the
  prompt, the choice and every check; confidence counts the run as degraded, never clean
  (R-PKG-6); and a repaired draft is written for a person to review, so the next approved
  version needs no model at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from waypoint.policy.redactor import Redactor, Sink
from waypoint.surface.locators import Candidate, LocatorBundle
from waypoint.surface.ports import UIElement, UISnapshot

ALLOWED_CODES = frozenset({"locator_not_found", "ambiguous_locator"})
"""The only failures a model may be asked about: the target, not the outcome."""


@dataclass(frozen=True)
class AssistRequest:
    """Everything the model is allowed to see, and nothing else."""

    where: str
    intent: str
    action: str
    reason: str
    recorded: dict[str, Any]
    """How the target was recorded: role, name, label, anchor - never a value."""
    snapshot: UISnapshot
    elements: tuple[tuple[str, UIElement], ...]

    def described(self) -> str:
        parts = [f"{k} {v!r}" for k, v in self.recorded.items() if v]
        return ", ".join(parts) or "not described"


@dataclass(frozen=True)
class Choice:
    element: str | None
    """A prompt-local id from the request, or None: "I cannot see it"."""
    reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class Assistant(Protocol):
    model: str

    def choose(self, request: AssistRequest) -> Choice: ...


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str
    element: UIElement | None = None


def recorded_shape(bundle: LocatorBundle | None) -> dict[str, Any]:
    """What the artifact says the target is, in the vocabulary the model is shown."""
    if bundle is None:
        return {}
    first = bundle.candidates[0] if bundle.candidates else None
    if first is None:
        return {}
    return {
        "role": first.role,
        "name": first.name,
        "label": first.label,
        "anchor": first.anchor.text if first.anchor and first.anchor.text else None,
        "recorded_tier": bundle.recorded_tier,
    }


def _identity_inputs(bundle: LocatorBundle | None) -> set[str]:
    """Inputs the recorded locator used to pick *this* record, not just this kind of control."""
    names: set[str] = set()
    for candidate in bundle.candidates if bundle else ():
        for target in _targets(candidate):
            if target and target.text_ref:
                names.add(target.text_ref.removeprefix("$inputs."))
    return names


def _targets(candidate: Candidate) -> list[Any]:
    out = [candidate.anchor]
    if candidate.identity is not None:
        out.append(candidate.identity.target)
    return out


def check(choice: Choice, request: AssistRequest, bundle: LocatorBundle | None,
          rendered: Mapping[str, str]) -> Verdict:
    """Deterministic validation of the model's choice. Nothing here trusts the model."""
    if choice.element is None:
        return Verdict(False, choice.reason or "the model did not choose an element")
    element = dict(request.elements).get(choice.element)
    if element is None:
        return Verdict(False, f"{choice.element!r} is not an element on this screen")
    if not element.enabled:
        return Verdict(False, f"{choice.element!r} is not enabled")
    expected_role = request.recorded.get("role")
    if expected_role and element.role != expected_role:
        return Verdict(False, f"expected a {expected_role}, chose a {element.role}")
    for name in sorted(_identity_inputs(bundle)):
        placeholder = rendered.get(name)
        if placeholder is None:
            continue
        if placeholder not in element.anchors and placeholder != element.name:
            return Verdict(False, f"the recorded locator identified the record by {name}, and "
                                  "the chosen element does not sit with that record (R-LOC-5)")
    return Verdict(True, "role and record identity match what was recorded", element)


def elements_for(snapshot: UISnapshot) -> tuple[tuple[str, UIElement], ...]:
    return tuple((f"e{i}", e) for i, e in enumerate(snapshot.elements, 1))


@dataclass
class RecordedChoice:
    """A model's answer, kept by what it picked rather than by a prompt-local id."""

    where: str
    snapshot_hash: str
    role: str
    name: str
    reason: str


class RecordingAssistant:
    """Wraps a live assistant and writes what it chose, so a run can be reproduced offline."""

    def __init__(self, inner: Assistant) -> None:
        self.inner = inner
        self.model = inner.model
        self.recorded: list[RecordedChoice] = []

    def choose(self, request: AssistRequest) -> Choice:
        choice = self.inner.choose(request)
        element = dict(request.elements).get(choice.element or "")
        if element is not None:
            self.recorded.append(RecordedChoice(request.where, request.snapshot.hash,
                                                element.role, element.name, choice.reason))
        return choice

    def to_json(self, redactor: Redactor | None = None) -> str:
        import json

        scrubber = redactor or Redactor()
        return json.dumps({"model": self.model,
                           "choices": [{**asdict(c),
                                        "name": scrubber.scrub(c.name, Sink.EVIDENCE),
                                        "reason": scrubber.scrub(c.reason, Sink.EVIDENCE)}
                                       for c in self.recorded]}, indent=2) + "\n"


class CassetteAssistant:
    """Replays a recorded answer by what it picked, refusing when the screen has moved on."""

    def __init__(self, choices: Sequence[RecordedChoice], model: str = "cassette") -> None:
        self.choices = list(choices)
        self.model = f"cassette:{model}"

    @staticmethod
    def load(path: Path) -> CassetteAssistant:
        import json

        body = json.loads(Path(path).read_text())
        return CassetteAssistant([RecordedChoice(**c) for c in body.get("choices", [])],
                                 body.get("model", "cassette"))

    def choose(self, request: AssistRequest) -> Choice:
        for recorded in self.choices:
            if (recorded.where != request.where
                    or recorded.snapshot_hash != request.snapshot.hash):
                continue
            matches = [eid for eid, element in request.elements
                       if (element.role, element.name) == (recorded.role, recorded.name)]
            if len(matches) == 1:
                return Choice(matches[0], f"recorded: {recorded.reason}")
        return Choice(None, "the recording has no answer for this screen")


class ScriptedAssistant:
    """A deterministic stand-in used by tests and offline demonstrations."""

    model = "scripted-assistant"

    def __init__(self, pick: Sequence[tuple[str, str]] | None = None,
                 answer: str | None = None) -> None:
        self.pick = list(pick or ())
        self.answer = answer
        self.requests: list[AssistRequest] = []

    def choose(self, request: AssistRequest) -> Choice:
        self.requests.append(request)
        if self.answer is not None:
            return Choice(self.answer, "scripted")
        for role, name in self.pick:
            for eid, element in request.elements:
                if element.role == role and element.name == name:
                    return Choice(eid, f"scripted: the {role} named {name!r}")
        return Choice(None, "scripted: nothing matched")
