"""ClaudeDecider's hand-written tool loop, against a fake client - no network, no cost.

The first real (paid) discovery run should not also be the loop's first test. These
pin the request shape and the protocol rules the loop depends on.
"""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

from waypoint.discovery.cassette import DecisionContext
from waypoint.discovery.claude import FALLBACK_BETA, MODEL, ClaudeDecider
from waypoint.surface.ports import UIElement, UISnapshot

C = ("main", "content")
EXPECT = {"elements": [{"role": "link", "name": "View", "anchor": ""}], "text": ""}


class Block(NS):
    def to_dict(self) -> dict[str, Any]:
        return dict(vars(self))


def tool_use(bid: str, name: str, arguments: dict[str, Any]) -> Block:
    return Block(type="tool_use", id=bid, name=name, input=arguments)


def reply(*content: Block, stop: str = "tool_use", details: Any = None) -> NS:
    return NS(content=list(content), stop_reason=stop, stop_details=details, model=MODEL,
              usage=NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=80))


class FakeClient:
    def __init__(self, *replies: NS) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.beta = NS(messages=NS(create=self._create))
        self.models = NS(retrieve=lambda model: NS(id=model))

    def _create(self, **kw: Any) -> NS:
        self.calls.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        return self.replies.pop(0)


def ctx(turn: int = 0, last: str | None = None) -> DecisionContext:
    e = UIElement("ns-g1-e1", "button", "Search", None, True, C, None, (), "public", "public")
    snap = UISnapshot("http://h/console", "M", (e,), "", "h")
    return DecisionContext(turn, "Look up $inputs.member_id", snap, (("e1", e),),
                           ("member_id",), ("meridian_user",), ("savings_balance",), last)


def click(bid: str, element: str = "e1") -> Block:
    return tool_use(bid, "click", {"element": element, "intent": "Search", "expect": EXPECT})


def test_request_shape() -> None:
    client = FakeClient(reply(click("t1")))
    ClaudeDecider(client=client).decide(ctx())  # type: ignore[arg-type]
    call = client.calls[0]
    assert call["model"] == "claude-haiku-4-5"  # the cheapest current model
    assert "fallbacks" not in call and "betas" not in call
    assert "thinking" not in call  # none on Haiku 4.5 - the cheapest option
    assert call["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert all(t["strict"] for t in call["tools"])
    assert "navigate" not in {t["name"] for t in call["tools"]}
    assert call["cache_control"] == {"type": "ephemeral"}
    text = call["messages"][0]["content"][-1]["text"]
    assert "$inputs.member_id" in text and "e1 button" in text


def test_opus_5_gets_refusal_fallbacks() -> None:
    client = FakeClient(reply(click("t1")))
    ClaudeDecider(client=client, model="claude-opus-5").decide(ctx())  # type: ignore[arg-type]
    call = client.calls[0]
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"


def test_each_tool_call_is_answered_before_the_next_screen() -> None:
    client = FakeClient(reply(click("t1")), reply(click("t2")))
    decider = ClaudeDecider(client=client)  # type: ignore[arg-type]
    assert decider.decide(ctx(0)).kind == "click"
    decider.decide(ctx(1, last="Done. The page changed."))
    second_user = client.calls[1]["messages"][-1]["content"]
    assert second_user[0] == {"type": "tool_result", "tool_use_id": "t1",
                              "content": "Done. The page changed."}
    assert second_user[1]["type"] == "text"


def test_an_invalid_decision_is_rejected_and_retried() -> None:
    client = FakeClient(reply(click("bad", element="not-an-id")), reply(click("good")))
    decision = ClaudeDecider(client=client).decide(ctx())  # type: ignore[arg-type]
    assert decision.element == "e1"
    rejection = client.calls[1]["messages"][-1]["content"][0]
    assert rejection["tool_use_id"] == "bad" and rejection["is_error"]


def test_parallel_calls_are_all_answered_then_retried() -> None:
    client = FakeClient(reply(click("a"), click("b")), reply(click("c")))
    ClaudeDecider(client=client).decide(ctx())  # type: ignore[arg-type]
    answered = [b["tool_use_id"] for b in client.calls[1]["messages"][-1]["content"]
                if b.get("type") == "tool_result"]
    assert answered == ["a", "b"]


def test_a_refusal_ends_discovery_cleanly() -> None:
    client = FakeClient(reply(stop="refusal", details=NS(category="cyber")))
    decision = ClaudeDecider(client=client).decide(ctx())  # type: ignore[arg-type]
    assert (decision.kind, decision.reason) == ("give_up", "model_refusal:cyber")


def test_no_valid_decision_after_retries_gives_up() -> None:
    client = FakeClient(*(reply(Block(type="text", text="hmm"), stop="end_turn")
                          for _ in range(3)))
    decision = ClaudeDecider(client=client, max_invalid=2).decide(ctx())  # type: ignore[arg-type]
    assert decision.kind == "give_up" and len(client.calls) == 3


def test_usage_and_exchanges_are_recorded_for_evidence() -> None:
    client = FakeClient(reply(click("t1")))
    decider = ClaudeDecider(client=client)  # type: ignore[arg-type]
    decider.decide(ctx())
    assert decider.usage == {"calls": 1, "input_tokens": 100, "output_tokens": 20,
                             "cache_read_input_tokens": 80}
    assert decider.exchanges[0]["response"]["stop_reason"] == "tool_use"


def test_preflight_looks_up_the_model() -> None:
    looked_up: list[str] = []
    client = FakeClient()
    client.models = NS(retrieve=lambda model: looked_up.append(model))
    ClaudeDecider(client=client).preflight()  # type: ignore[arg-type]
    assert looked_up == ["claude-haiku-4-5"]
