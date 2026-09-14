"""What the discovery model may decide - and nothing else (PLAN.md 4.4).

The model acts only by calling one of these tools, each a strict JSON schema, so a
decision is either schema-valid or rejected: there is no free-form command string
for page text to inject into. Two further restrictions are deliberate:

* There is no navigate-by-URL tool. Discovery moves through the application the way
  an operator does, by clicking, so a hostile page cannot talk the agent into
  typing an address - and the allowlist stays the backstop rather than the only line.
* Every state-changing decision carries ``intent`` (why) and ``expect`` (what must
  be true afterwards). ``intent`` is what makes the compiled artifact reviewable;
  ``expect`` is the checkpoint nomination the compiler verifies (R-PKG-5).

``recheck`` is the one tool that touches nothing: it restates the expectation for the
action just taken, and the loop keeps it only if it is true on the screen that action
produced. It exists because an expectation is nominated before its result can be seen,
and a wrong one is only found afterwards - when, without this, nobody could fix it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

DecisionKind = Literal["click", "type", "select", "key", "recheck", "finish", "give_up"]
KEYS = ("Enter", "Tab", "Escape")
ELEMENT_ID = re.compile(r"^e[1-9][0-9]*$")


class InvalidDecision(ValueError):
    """A tool call that parsed as JSON but is not a decision the loop can execute."""


@dataclass(frozen=True)
class ExpectedElement:
    role: str
    name: str
    anchor: str = ""


@dataclass(frozen=True)
class Expectation:
    """A checkpoint nomination, in the sanitized vocabulary the model sees."""

    elements: tuple[ExpectedElement, ...] = ()
    text: str = ""

    def is_empty(self) -> bool:
        return not self.elements and not self.text


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    intent: str = ""
    element: str | None = None
    value: str | None = field(default=None, repr=False)
    key: str | None = None
    expect: Expectation | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Decision:
        expect = data.get("expect")
        return Decision(
            kind=data["kind"],
            intent=data.get("intent", ""),
            element=data.get("element"),
            value=data.get("value"),
            key=data.get("key"),
            expect=None if expect is None else _expectation(expect),
            outputs=dict(data.get("outputs") or {}),
            summary=data.get("summary", ""),
            reason=data.get("reason", ""),
        )


# ------------------------------------------------------------------- schemas

_EXPECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "What must be true on screen if this worked. Copy names exactly from the element "
        "table, including placeholders such as ‹$inputs.member_id›. Prefer an element that "
        "shows WHICH record is on screen, not just which page."
    ),
    "properties": {
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "name": {"type": "string"},
                    "anchor": {"type": "string", "description": "One nearby label, copied "
                               "from that element's near=... list - never the whole list. "
                               "Or ''."},
                },
                "required": ["role", "name", "anchor"],
                "additionalProperties": False,
            },
        },
        "text": {"type": "string", "description": "Visible text that must appear, or ''."},
    },
    "required": ["elements", "text"],
    "additionalProperties": False,
}


def _tool(name: str, description: str, properties: dict[str, Any],
          strict: bool = True) -> dict[str, Any]:
    """One tool schema. ``strict`` asks the API to constrain decoding to the schema.

    Strict tools share one compiled grammar with a size limit, and the expectation schema
    repeated across them is most of it. ``recheck`` therefore goes without: every decision
    is parsed and validated here in any case, so an off-schema call is refused either way.
    """
    tool: dict[str, Any] = {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }
    if strict:
        tool["strict"] = True
    return tool


_ELEMENT = {"type": "string", "description": "An element id from the current table, e.g. e12."}
_INTENT = {"type": "string", "description": "Why - one short sentence a reviewer will read."}
_VALUE = {
    "type": "string",
    "description": "'$inputs.<name>' or '$secrets.<name>' for anything bound - never the value.",
}

TOOLS: list[dict[str, Any]] = [
    _tool("click", "Click one element.", {"element": _ELEMENT, "intent": _INTENT,
                                          "expect": _EXPECT_SCHEMA}),
    _tool("type_text", "Type into a field.", {"element": _ELEMENT, "value": _VALUE,
                                              "intent": _INTENT, "expect": _EXPECT_SCHEMA}),
    _tool("select_option", "Choose an option in a list.", {"element": _ELEMENT, "value": _VALUE,
                                                           "intent": _INTENT,
                                                           "expect": _EXPECT_SCHEMA}),
    _tool("press_key", "Press a key in the focused control.", {
        "key": {"type": "string", "enum": list(KEYS)}, "intent": _INTENT,
        "expect": _EXPECT_SCHEMA}),
    _tool("finish", "Declare the goal met.", {
        "outputs": {
            "type": "array",
            "description": "Each requested output and the element that holds it.",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "element": _ELEMENT},
                "required": ["name", "element"],
                "additionalProperties": False,
            },
        },
        "success": _EXPECT_SCHEMA,
        "summary": {"type": "string", "description": "One sentence: what was achieved."},
    }),
    _tool("recheck", "Restate what must be true after your last action, when you were told "
                     "the expectation you gave was false. Changes nothing on screen.",
          {"expect": _EXPECT_SCHEMA}, strict=False),
    _tool("give_up", "Stop: the goal cannot be completed safely from here.",
          {"reason": {"type": "string"}}),
]

_KIND_BY_TOOL: dict[str, DecisionKind] = {
    "click": "click", "type_text": "type", "select_option": "select", "press_key": "key",
    "recheck": "recheck", "finish": "finish", "give_up": "give_up",
}


def _expectation(raw: dict[str, Any]) -> Expectation:
    elements = tuple(
        ExpectedElement(str(e.get("role", "")), str(e.get("name", "")), str(e.get("anchor", "")))
        for e in raw.get("elements") or []
        if e.get("role") or e.get("name")
    )
    return Expectation(elements=elements, text=str(raw.get("text") or ""))


def parse_tool_call(name: str, arguments: dict[str, Any]) -> Decision:
    """Turn one tool call into a Decision, or raise InvalidDecision explaining why."""
    kind = _KIND_BY_TOOL.get(name)
    if kind is None:
        raise InvalidDecision(f"unknown tool {name!r}")
    if kind == "give_up":
        return Decision("give_up", reason=str(arguments.get("reason", "")))
    if kind == "finish":
        try:
            outputs = {str(o["name"]): str(o["element"]) for o in arguments.get("outputs") or []}
        except (TypeError, KeyError, IndexError) as exc:  # a shape the schema did not enforce
            raise InvalidDecision(
                f"outputs must be a list of {{name, element}} objects ({exc})"
            ) from None
        bad = [e for e in outputs.values() if not ELEMENT_ID.match(e)]
        if bad:
            raise InvalidDecision(f"finish names unknown element ids: {bad}")
        success = _expectation(arguments.get("success") or {})
        if success.is_empty():
            raise InvalidDecision("finish needs a success expectation naming something on screen")
        return Decision("finish", outputs=outputs, expect=success,
                        summary=str(arguments.get("summary", "")))
    expect = _expectation(arguments.get("expect") or {})
    if expect.is_empty():
        raise InvalidDecision("every action must say what should be true afterwards (expect)")
    if kind == "recheck":
        return Decision("recheck", expect=expect)
    intent = str(arguments.get("intent", "")).strip()
    if not intent:
        raise InvalidDecision("every action needs an intent")
    if kind == "key":
        key = str(arguments.get("key", ""))
        if key not in KEYS:
            raise InvalidDecision(f"key must be one of {list(KEYS)}")
        return Decision("key", intent=intent, key=key, expect=expect)
    element = str(arguments.get("element", ""))
    if not ELEMENT_ID.match(element):
        raise InvalidDecision(f"not an element id from the table: {element!r}")
    value = arguments.get("value") if kind in ("type", "select") else None
    return Decision(kind, intent=intent, element=element,
                    value=None if value is None else str(value), expect=expect)
