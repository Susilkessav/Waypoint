"""Discovery that gets stuck asks a person, on the same live browser (REPORT.md §5).

The brief's escalation requirement covers being stuck during discovery, not only during
replay. Here a scripted decider gives up at the search results - it "cannot see" which View
link is the member's - and a stand-in operator takes control, opens the record and hands
back. The model finishes from the screen the person left.

What the person did is not turned into a replayable step. It is logged, and it compiles into
a visible gap that blocks approval until someone authors the step, so a draft never silently
skips work a person did.
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
        frame.locator(f'a[id$="_lnkView"][href$="member_id={member_id}"]').click()
        frame.wait_for_url(f"**/console/member?member_id={member_id}")
        store.give_back(iv.id)

    return human


def test_a_stuck_discovery_is_finished_with_a_persons_help(live_server: str,
                                                          tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    notify, seen = taker(db)
    decider = _CannotOpenTheRecord()
    result = discover(decider, options(live_server, tmp_path / "runs", db,
                                       open_record("12345", db), notify=notify))

    t = result.transcript
    assert t.ending == "finished", t.ending_detail
    assert decider.gave_up == 1, "the model asked once, then carried on from the new screen"

    [iv] = seen
    assert (iv.reason_code, iv.capability_id) == ("discovery_gave_up", "lookup_member_balance")
    assert iv.screenshot and Path(iv.screenshot).exists(), "the operator sees where it stopped"
    [handoff] = result.handoffs
    assert handoff["operator"] == "tester" and handoff["human_actions"] >= 1
    assert handoff["gap_recorded"] is True

    actions = Path(result.run_dir) / "human" / "actions.jsonl"
    assert actions.exists() and "click" in actions.read_text(), "what the person did is logged"


def test_what_a_person_did_compiles_into_a_gap_that_blocks_approval(live_server: str,
                                                                    tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    notify, _ = taker(db)
    result = discover(_CannotOpenTheRecord(), options(live_server, tmp_path / "runs", db,
                                                      open_record("12345", db), notify=notify))
    t = result.transcript
    [gap] = [s for s in t.steps if s.action == "gap"]
    assert gap.unrecorded and "tester" in gap.unrecorded

    report = compile_transcript(t, version="1.0.0")
    gated = [s for s in report.capability.steps if s.unrecorded_human_action]
    assert len(gated) == 1 and gated[0].action == "wait_for"
    assert any("could not be recorded" in g for g in report.open_gates), report.open_gates
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
