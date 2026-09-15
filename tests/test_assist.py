"""The one place a model is allowed back into replay, and everything that keeps it narrow.

Under `drift_search` the search control keeps its id and loses its name, so the recorded tier-1
locator finds nothing. With the artifact's permission and the caller's, a model may say
which control it is now - once, for a safe step, and only if its answer passes the same
deterministic checks the ladder would have applied. The run is marked assisted, the
evidence records the whole exchange, and a repaired draft is written for review.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from waypoint.artifact.approval import approve
from waypoint.artifact.schema import Capability, load
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.assist import AssistRequest, Choice, ScriptedAssistant, check, recorded_shape
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.replay.ledger import Ledger
from waypoint.session.store import StateStore
from waypoint.surface.ports import Action

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parents[1]
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}
BALANCE = {"savings_balance": "$4,281.19", "account_status": "active"}


def capability(*, assisted: bool) -> Capability:
    cap = load(REPO / "capabilities" / "lookup_member_balance" / "1.2.0.json")
    return approve(cap.model_copy(update={
        "policy": cap.policy.model_copy(update={"assisted_fallback": assisted}),
        "provenance": cap.provenance.model_copy(update={"approval": {"base": "draft"}}),
    }), approver="test")


def drifted(live_server: str, tmp_path: Path, **kw: Any) -> ReplayOptions:
    def inject(surface: Any, origin: str) -> None:
        surface.act(Action("navigate", url=f"{origin}/console?inject=drift_search"))

    return ReplayOptions(base_url=live_server, evidence_root=tmp_path / "runs",
                         secrets=SecretBroker(environ=CREDENTIALS), after_preconditions=inject,
                         capture_steps=False, **kw)


def renamed_search() -> ScriptedAssistant:
    """What a model should answer: the submit control that is now called 'Continue'."""
    return ScriptedAssistant(pick=[("button", "Continue")])


def test_without_the_fallback_a_renamed_control_escalates(live_server: str,
                                                          tmp_path: Path) -> None:
    result = replay(capability(assisted=False), {"member_id": "12345"},
                    drifted(live_server, tmp_path, assist=renamed_search()))
    assert result.status == "escalated"
    assert result.failure is not None and result.failure.code == "locator_not_found"
    assert result.telemetry["assisted"] == []


def test_the_artifact_must_allow_it_and_the_caller_must_ask(live_server: str,
                                                            tmp_path: Path) -> None:
    """The artifact permits it, but no assistant was passed: nothing changes."""
    result = replay(capability(assisted=True), {"member_id": "12345"},
                    drifted(live_server, tmp_path))
    assert result.status == "escalated" and result.telemetry["assisted"] == []


def test_an_assisted_step_finishes_the_run_and_is_never_silent(live_server: str,
                                                               tmp_path: Path) -> None:
    assistant = renamed_search()
    result = replay(capability(assisted=True), {"member_id": "12345"},
                    drifted(live_server, tmp_path, assist=assistant,
                            ledger_db=tmp_path / "state.db"))

    assert result.status == "success", result.failure
    assert result.outputs == BALANCE, "the capability still returns what it promised"

    [assisted] = result.telemetry["assisted"]
    assert assisted["model"] == "scripted-assistant"
    assert assisted["code"] == "locator_not_found" and assisted["accepted"] is True

    [request] = assistant.requests
    assert request.recorded["role"] == "button", "it was told what was recorded"
    assert all("$4,281.19" not in line for line in
               [f"{e.name}{e.value}" for _, e in request.elements]), "redacted, as always"

    run_dir = Path(result.evidence_dir or "")
    [record] = list(run_dir.glob("assist_*.json"))
    body = json.loads(record.read_text())
    assert body["accepted"] and body["chose"] and body["offered"]

    proposal = next(iter((run_dir / "proposal").glob("*.json")))
    repaired = load(proposal)
    assert repaired.version == "1.2.1" and repaired.provenance.approval == {"base": "draft"}
    assert "review the new locator" in (repaired.provenance.approval_note or "")

    confidence = Ledger(StateStore(tmp_path / "state.db")).confidence(capability(assisted=True))
    assert confidence.runs == 1 and confidence.clean == 0, "an assisted run is never clean"


def test_a_model_pointing_at_the_wrong_kind_of_control_is_refused(live_server: str,
                                                                  tmp_path: Path) -> None:
    result = replay(capability(assisted=True), {"member_id": "12345"},
                    drifted(live_server, tmp_path,
                            assist=ScriptedAssistant(pick=[("textbox", "")])))
    assert result.status == "escalated", "the escalation stands when the answer is not usable"
    assert result.telemetry["assisted"] == []


def test_a_model_that_says_it_cannot_tell_leaves_the_escalation_alone(live_server: str,
                                                                      tmp_path: Path) -> None:
    result = replay(capability(assisted=True), {"member_id": "12345"},
                    drifted(live_server, tmp_path, assist=ScriptedAssistant(answer=None)))
    assert result.status == "escalated" and result.telemetry["assisted"] == []


def test_an_invented_element_is_refused(live_server: str, tmp_path: Path) -> None:
    result = replay(capability(assisted=True), {"member_id": "12345"},
                    drifted(live_server, tmp_path, assist=ScriptedAssistant(answer="e999")))
    assert result.status == "escalated" and result.telemetry["assisted"] == []


def test_the_recorded_shape_is_described_without_values() -> None:
    cap = load(REPO / "capabilities" / "lookup_member_balance" / "1.2.0.json")
    step = next(s for s in cap.steps if s.target is not None)
    shape = recorded_shape(step.target)
    assert set(shape) <= {"role", "name", "label", "anchor", "recorded_tier"}
    assert "$4,281.19" not in json.dumps(shape)


def test_identity_is_checked_before_a_choice_is_used() -> None:
    """A model may not move a step onto another member's row (R-LOC-5)."""
    from waypoint.surface.ports import UIElement, UISnapshot

    cap = load(REPO / "capabilities" / "lookup_member_balance" / "1.2.0.json")
    anchored = next(s for s in cap.steps
                    if s.target is not None
                    and any(c.identity is not None or (c.anchor is not None and c.anchor.text_ref)
                            for c in s.target.candidates))

    def element(name: str, anchors: tuple[str, ...]) -> UIElement:
        return UIElement(ref="ref_1", role="link", name=name, value=None, enabled=True,
                         frame_path=("main",), bbox=None, anchors=anchors, sensitivity="public",
                         name_sensitivity="public")

    ours = element("View", ("‹$inputs.member_id›",))
    theirs = element("View", ("‹redacted:5 chars›",))
    snapshot = UISnapshot("u", "t", (ours, theirs), "d", "h", ())
    request = AssistRequest(where="steps[2]", intent=anchored.intent, action="click",
                            reason="several matches", recorded=recorded_shape(anchored.target),
                            snapshot=snapshot,
                            elements=(("e1", ours), ("e2", theirs)))
    rendered = {"member_id": "‹$inputs.member_id›"}

    assert check(Choice("e1", ""), request, anchored.target, rendered).ok
    refused = check(Choice("e2", ""), request, anchored.target, rendered)
    assert not refused.ok and "identified the record" in refused.reason
