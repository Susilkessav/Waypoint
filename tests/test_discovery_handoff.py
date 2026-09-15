"""Discovery that gets stuck asks a person, and what they demonstrate becomes a step.

The brief's escalation requirement covers being stuck during discovery, not only during
replay (REPORT.md §5). Here a scripted decider gives up at the search results - it "cannot
see" how to open the member's record - and a stand-in operator clicks View on the live
page. Control returns, the model finishes, and the compiled artifact is replayed for a
*different* member: the person's click has to generalize, or it was never a step.

Gaps are the other half: what a person does that cannot be recorded as a replayable step
compiles into a visible hole that blocks approval, rather than a flow with a silent jump.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from waypoint.artifact.approval import ApprovalBlocked, approve
from waypoint.compiler.compile import compile_transcript
from waypoint.discovery.agent import DiscoveryOptions, InputBinding, discover
from waypoint.discovery.decisions import Decision
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.session.escalation import Intervention, InterventionStore
from waypoint.session.handoff import HandoffSettings
from waypoint.session.store import StateStore
from waypoint.surface.web import WebSurface

from .test_discovery import CREDENTIALS, PH, ScriptedDecider

pytestmark = pytest.mark.browser


class _CannotOpenTheRecord(ScriptedDecider):
    """Does everything except open the member's record - that is what it asks a person for."""

    def __init__(self) -> None:
        self.gave_up = 0

    def decide(self, ctx: Any) -> Decision:
        on_results = any(e.role == "link" and e.name == "View" for _, e in ctx.elements)
        detail = any(e.name == PH and e.role == "LayoutTableCell" for _, e in ctx.elements)
        if on_results and not detail:
            self.gave_up += 1
            return Decision("give_up", reason="I cannot tell which View link is this member's")
        return super().decide(ctx)


def options(live_server: str, root: Path, db: Path, human: Any, **kw: Any) -> DiscoveryOptions:
    return DiscoveryOptions(
        capability_id="lookup_member_balance",
        goal="Look up member {{member_id}} and read their current savings balance",
        entry=f"{live_server}/console",
        inputs=[InputBinding("member_id", "12345", "string", "internal")],
        outputs=[],
        evidence_root=root,
        secrets=SecretBroker(environ=CREDENTIALS),
        handoff=True,
        handoff_settings=HandoffSettings(state_db=db, lease_poll_ms=100, wait_timeout_s=30,
                                         while_human=human, **kw),
    )


def taker(db: Path) -> tuple[Any, list[Intervention]]:
    """Takes control the moment an intervention opens, like an operator watching the queue."""
    store = InterventionStore(StateStore(db))
    seen: list[Intervention] = []

    def notify(iv: Intervention) -> None:
        seen.append(iv)
        store.take(iv.id, "tester", 60)

    return notify, seen


def open_record(member_id: str, db: Path) -> Any:
    store = InterventionStore(StateStore(db))

    def human(surface: WebSurface, iv: Intervention) -> None:
        frame = next(f for f in surface.page.frames if f.name == "content")
        view = frame.locator(f'a[id$="_lnkView"][href$="member_id={member_id}"]')
        if view.count():  # a second handoff would be from somewhere else entirely
            view.click()
        store.give_back(iv.id)

    return human


def test_a_person_demonstrates_the_step_and_it_replays_for_another_member(
    live_server: str, tmp_path: Path
) -> None:
    db = tmp_path / "state.db"
    notify, seen = taker(db)
    decider = _CannotOpenTheRecord()
    result = discover(decider, options(live_server, tmp_path / "runs", db,
                                       open_record("12345", db), notify=notify))

    t = result.transcript
    assert t.ending == "finished", t.ending_detail
    assert decider.gave_up == 1, "the model asked once and then carried on"

    [iv] = seen
    assert (iv.reason_code, iv.capability_id) == ("discovery_gave_up", "lookup_member_balance")
    [handoff] = result.handoffs
    assert handoff["operator"] == "tester"
    assert handoff["demonstrated_steps"] == ["A person clicked link 'View'"]

    demonstrated = [s for s in t.steps if s.performed_by == "human"]
    assert len(demonstrated) == 1
    step = demonstrated[0]
    assert step.ok and step.bundle is not None and step.bundle_error is None
    assert step.decision["expect"]["elements"], "a checkpoint was nominated from the screen"

    report = compile_transcript(t, version="1.0.0")
    cap = report.capability
    assert cap.provenance.demonstrated_steps, "the reviewer can see which steps a person made"
    [where] = cap.provenance.demonstrated_steps
    index = int(where.removeprefix("steps[").removesuffix("]"))
    assert cap.steps[index].action == "click"
    assert not any("could not be recorded" in g for g in report.open_gates)

    # The proof: recorded on 12345 by a person, replayed for 67890 by the engine.
    out = replay(approve(cap, approver="test"), {"member_id": "67890"}, ReplayOptions(
        base_url=live_server, evidence_root=tmp_path / "replays",
        secrets=SecretBroker(environ=CREDENTIALS)))
    assert out.status == "success", out.failure
    assert out.telemetry["steps"] == len(cap.steps)


class _CannotSignOn(ScriptedDecider):
    """Gives up at the sign-on screen, so a person signs in by hand."""

    def __init__(self) -> None:
        self.gave_up = 0

    def decide(self, ctx: Any) -> Decision:
        if any(e.role == "button" and e.name == "Sign On" for _, e in ctx.elements):
            self.gave_up += 1
            return Decision("give_up", reason="I will not handle this sign-on")
        return super().decide(ctx)


def test_what_cannot_be_recorded_becomes_a_gap_that_blocks_approval(
    live_server: str, tmp_path: Path
) -> None:
    """A person typing a password is real work that must never be captured (R-SENS-8).

    The run keeps going, but the artifact carries the hole where they typed it, and that
    hole blocks approval until someone authors the step.
    """
    db = tmp_path / "state.db"
    notify, _ = taker(db)
    store = InterventionStore(StateStore(db))

    def human(surface: WebSurface, iv: Intervention) -> None:
        page = surface.page
        page.locator("#ctl00_txtUserId").fill("operator1")
        page.locator("#ctl00_txtUserId").blur()
        page.locator("#ctl00_txtPassword").fill("changeme")
        page.locator("#ctl00_txtPassword").blur()
        page.locator("#ctl00_btnSignOn").click()
        store.give_back(iv.id)

    result = discover(_CannotSignOn(), options(live_server, tmp_path / "runs", db, human,
                                               notify=notify))
    t = result.transcript
    gaps = [s for s in t.steps if s.action == "gap"]
    assert gaps, [(s.action, s.decision.get("intent")) for s in t.steps]
    assert all(s.performed_by == "human" for s in gaps)
    assert any("credential" in (s.unrecorded or "") for s in gaps), [s.unrecorded for s in gaps]
    assert not any("changeme" in (s.unrecorded or "") for s in gaps), "never the value"

    report = compile_transcript(t, version="1.0.0")
    blocking = [g for g in report.open_gates if "could not be recorded" in g]
    assert blocking, report.open_gates
    with pytest.raises(ApprovalBlocked):
        approve(report.capability, approver="test")


def test_an_operator_who_aborts_ends_the_run(live_server: str, tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    store = InterventionStore(StateStore(db))

    def notify(iv: Intervention) -> None:
        store.take(iv.id, "tester", 60)
        store.abort(iv.id)

    result = discover(_CannotOpenTheRecord(), options(live_server, tmp_path / "runs", db,
                                                      None, notify=notify))
    assert result.transcript.ending == "blocked"
    assert "aborted_by_operator" in result.transcript.ending_detail
    assert result.handoffs == ()
