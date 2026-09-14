"""The live decider: Claude, called through a hand-written tool loop.

Hand-written rather than the SDK's Tool Runner because the loop, not the model, must
own every side effect: each decision is returned to the discovery agent, which puts it
through the policy engine before anything touches the page, and records it to the
cassette. The model only ever proposes.

Choices:

* The model defaults to Claude Haiku 4.5 (``claude-haiku-4-5``), the cheapest current
  model - chosen for cost. What makes discovery trustworthy does not live in the model:
  the policy engine gates every action, the compiler verifies every checkpoint, and a
  human approves the result. ``--model`` selects a more capable one.
* No ``thinking`` parameter is sent. On Haiku 4.5 that means no thinking, the cheapest
  option; on Claude Opus 5 it means adaptive thinking.
* One strict tool call per turn (``disable_parallel_tool_use``): the screen must be
  re-observed after every action, so parallel calls would act on stale refs.
* Refusal fallbacks (``fallbacks: "default"``, beta ``server-side-fallback-2026-07-01``)
  are sent only for models documented to use them (Opus 5, Fable 5.1), whose safety
  classifiers can decline a request; there it is re-run on Anthropic's recommended
  fallback. Other models do not get the parameter.
* The system prompt and tool list are identical on every turn and the history is
  append-only, so the growing prefix caches (top-level ``cache_control``).
"""

from __future__ import annotations

from typing import Any, cast

import anthropic
from anthropic.types.beta import (
    BetaCacheControlEphemeralParam,
    BetaMessageParam,
    BetaToolChoiceAutoParam,
    BetaToolParam,
)

from waypoint.discovery.cassette import DecisionContext
from waypoint.discovery.decisions import TOOLS, Decision, InvalidDecision, parse_tool_call
from waypoint.discovery.prompts import SYSTEM_PROMPT, render_turn

MODEL = "claude-haiku-4-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = frozenset({"claude-opus-5", "claude-fable-5-1"})
"""Models documented to use server-side refusal fallbacks."""
MAX_TOKENS = 16000
REQUEST_TIMEOUT_S = 60.0
"""Per request. A decision takes seconds; the SDK's default of ten minutes, retried, let one
stalled response freeze a live discovery run for over half an hour."""
MAX_RETRIES = 2


def default_client() -> anthropic.Anthropic:
    """A client that fails fast on a stalled connection instead of waiting it out."""
    return anthropic.Anthropic(timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES)


class ClaudeDecider:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str = MODEL,
        max_invalid: int = 2,
    ) -> None:
        self.client = client or default_client()
        self.model = model
        self.max_invalid = max_invalid
        self.messages: list[dict[str, Any]] = []
        self.exchanges: list[dict[str, Any]] = []
        """Sanitized request text and response per call - written to evidence."""
        self.usage: dict[str, int] = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                      "cache_read_input_tokens": 0}
        self._pending_tool_id: str | None = None

    def preflight(self) -> None:
        """Fail fast - before any browser starts - if the API cannot be used.

        A model lookup costs no tokens, and surfaces a missing or revoked credential,
        or a model this account cannot use, as a clear error at the start of the run
        rather than a generic one at the first decision.
        """
        self.client.models.retrieve(self.model)

    def decide(self, ctx: DecisionContext) -> Decision:
        content: list[dict[str, Any]] = []
        if self._pending_tool_id is not None:
            # Every tool_use must be answered before the next screen is shown.
            content.append({"type": "tool_result", "tool_use_id": self._pending_tool_id,
                            "content": ctx.last_result or "Done."})
            self._pending_tool_id = None
        content.append({"type": "text", "text": render_turn(ctx)})
        self.messages.append({"role": "user", "content": content})

        for _ in range(self.max_invalid + 1):
            response = self._call(ctx.turn)
            if response.stop_reason == "refusal":
                category = getattr(response.stop_details, "category", None)
                return Decision("give_up", reason=f"model_refusal:{category}")
            self.messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            if len(calls) != 1:
                reply: list[dict[str, Any]] = [
                    {"type": "tool_result", "tool_use_id": c.id, "is_error": True,
                     "content": "Not executed: call exactly one tool per turn."}
                    for c in calls
                ]
                reply.append({"type": "text", "text": "Call exactly one tool."})
                self.messages.append({"role": "user", "content": reply})
                continue
            call = calls[0]
            try:
                decision = parse_tool_call(call.name, dict(call.input))
            except InvalidDecision as exc:
                self.messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                     "content": f"Rejected before execution: {exc}"}]})
                continue
            # A finish can be handed back for correction, so it stays answerable; when it
            # is accepted the run ends and nothing asks again. give_up always ends the run.
            self._pending_tool_id = None if decision.kind == "give_up" else call.id
            return decision
        return Decision("give_up", reason="no_valid_decision_after_retries")

    def _call(self, turn: int) -> Any:
        tool_choice: BetaToolChoiceAutoParam = {"type": "auto", "disable_parallel_tool_use": True}
        cache: BetaCacheControlEphemeralParam = {"type": "ephemeral"}
        tools = cast(list[BetaToolParam], TOOLS)
        messages = cast(list[BetaMessageParam], self.messages)
        if self.model in FALLBACK_MODELS:
            response = self.client.beta.messages.create(
                model=self.model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT, tools=tools,
                tool_choice=tool_choice, messages=messages, cache_control=cache,
                betas=[FALLBACK_BETA], fallbacks="default",
            )
        else:
            response = self.client.beta.messages.create(
                model=self.model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT, tools=tools,
                tool_choice=tool_choice, messages=messages, cache_control=cache,
            )
        usage = response.usage
        self.usage["calls"] += 1
        self.usage["input_tokens"] += usage.input_tokens or 0
        self.usage["output_tokens"] += usage.output_tokens or 0
        self.usage["cache_read_input_tokens"] += usage.cache_read_input_tokens or 0
        last_user = self.messages[-1]["content"]
        self.exchanges.append({
            "turn": turn,
            "request": [b.get("text") or b.get("content") for b in last_user],
            "response": {"model": response.model, "stop_reason": response.stop_reason,
                         "content": [b.to_dict() for b in response.content]},
        })
        return response
