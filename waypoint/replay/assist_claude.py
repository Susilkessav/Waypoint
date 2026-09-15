"""The live assistant: one Claude call, one tool, one element id back.

Separate from the discovery decider on purpose. Discovery asks "what should happen next?";
this asks the far smaller question "which of these elements is the control the artifact
already decided to use?" - so it gets its own prompt, its own single tool, and no history.
There is nothing to converse about: one call, or the run escalates.
"""

from __future__ import annotations

from typing import Any, cast

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaToolChoiceAnyParam, BetaToolParam

from waypoint.discovery.claude import MAX_RETRIES, MODEL, REQUEST_TIMEOUT_S
from waypoint.discovery.prompts import element_line
from waypoint.replay.assist import AssistRequest, Choice

SYSTEM_PROMPT = """You help a deterministic replay engine find one control that has moved.

A capability recorded a step against a specific control. On this screen the recorded
locator no longer finds exactly one match - most often because the application renamed the
control. Your only job is to say which element on the screen below is that same control, or
that you cannot tell.

Rules:
- You choose an element. You do not choose what happens to it: the action, any value and
  the next steps come from the capability, not from you.
- The recorded *name* may be stale; that is usually why you are being asked. Judge by what
  the step does, the kind of control it was, and where it sits on this screen. A submit
  button now called "Continue" can be the one that was recorded as "Search".
- The kind of control still has to match: a button for a button, a link for a link.
- If the step targets one record's row - a member, an account - the element must belong to
  that record. Values are redacted; match on the row's labels and the placeholders shown.
- If several elements could plausibly be it, or none fits what the step does, say you
  cannot tell. An escalation to a person is a good outcome; a wrong click is not.
"""

MAX_TOKENS = 1024

TOOLS: list[dict[str, Any]] = [
    {
        "name": "choose_element",
        "description": "Name the element that is the step's control, or none if unsure.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element": {
                    "type": "string",
                    "description": "An element id from the table, e.g. e12, or 'none'.",
                },
                "reason": {
                    "type": "string",
                    "description": "One short sentence a reviewer will read.",
                },
            },
            "required": ["element", "reason"],
            "additionalProperties": False,
        },
        "strict": True,
    }
]


def render(request: AssistRequest) -> str:
    lines = [
        f"Step: {request.where} - {request.intent}",
        f"Action the capability will take: {request.action}",
        f"The control as recorded (its name may since have changed): {request.described()}",
        f"Why the recorded locator failed here: {request.reason}",
        "",
        f"Screen: {request.snapshot.title!r}",
        *(element_line(eid, e) for eid, e in request.elements),
    ]
    return "\n".join(lines)


class ClaudeAssistant:
    """One call, one tool. No history, no second chance."""

    def __init__(self, client: anthropic.Anthropic | None = None, model: str = MODEL) -> None:
        self.client = client or anthropic.Anthropic(timeout=REQUEST_TIMEOUT_S,
                                                    max_retries=MAX_RETRIES)
        self.model = model
        self.exchanges: list[dict[str, Any]] = []

    def choose(self, request: AssistRequest) -> Choice:
        prompt = render(request)
        messages = cast(list[BetaMessageParam], [{"role": "user", "content": prompt}])
        tool_choice: BetaToolChoiceAnyParam = {"type": "any", "disable_parallel_tool_use": True}
        response = self.client.beta.messages.create(
            model=self.model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
            tools=cast(list[BetaToolParam], TOOLS), tool_choice=tool_choice, messages=messages,
        )
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        calls = [b for b in response.content if b.type == "tool_use"]
        self.exchanges.append({"prompt": prompt, "stop_reason": response.stop_reason,
                               "tool_calls": [dict(c.input) for c in calls], "usage": usage})
        if response.stop_reason == "refusal" or not calls:
            return Choice(None, "the model did not answer", usage)
        chosen = dict(calls[0].input)
        element = str(chosen.get("element", "")).strip()
        reason = str(chosen.get("reason", ""))[:200]
        if not element or element.lower() in ("none", "unknown", "unsure"):
            return Choice(None, reason or "the model was not sure", usage)
        return Choice(element, reason, usage)
