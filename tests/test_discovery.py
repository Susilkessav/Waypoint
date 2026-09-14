"""A6 end to end: discover -> compile -> approve -> replay a *different* member.

The decider here is SCRIPTED - a deterministic stand-in for the model, not a model.
It chooses elements by role, name and nearby labels, exactly as the model is asked
to, so everything else runs for real: the loop, the policy engine under every action,
locator synthesis on the live page, the compiler, approval and replay. The genuine
model run the brief requires is a separate, recorded ClaudeDecider run.

T14 - a compiled artifact generalizes: discovered with 12345, it returns 67890's balance.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from target_app.data import MEMBERS
from waypoint.artifact.approval import approve
from waypoint.compiler.compile import CompileError, compile_transcript
from waypoint.discovery.agent import DiscoveryOptions, InputBinding, discover
from waypoint.discovery.cassette import Cassette, CassetteDecider, DecisionContext, RecordingDecider
from waypoint.discovery.decisions import Decision, Expectation, ExpectedElement
from waypoint.discovery.transcript import OutputSpecDecl
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions, replay

pytestmark = pytest.mark.browser

PH = "‹$inputs.member_id›"
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}
E = ExpectedElement


class ScriptedDecider:
    """NOT a model. Picks elements the way the model is instructed to, deterministically."""

    model = "scripted-test-decider"

    def decide(self, ctx: DecisionContext) -> Decision:
        def find(role: str, *, name: str | None = None, anchor: str | None = None,
                 empty: bool | None = None) -> str | None:
            for eid, e in ctx.elements:
                if e.role == role and (name is None or e.name == name) \
                        and (anchor is None or anchor in e.anchors) \
                        and (empty is None or (not e.value) == empty):
                    return eid
            return None

        def cell(column: str) -> str:
            return next(eid for eid, e in ctx.elements if e.role == "cell"
                        and e.anchors[:1] == (column,) and "Savings" in e.anchors)

        if find("button", name="Sign On"):
            for label, secret in (("User ID", "meridian_user"), ("Password", "meridian_password")):
                if box := find("textbox", anchor=label, empty=True):
                    return Decision("type", intent=f"Enter the operator {label}", element=box,
                                    value=f"$secrets.{secret}",
                                    expect=Expectation((E("textbox", "", label),)))
            return Decision("click", intent="Sign on", element=find("button", name="Sign On"),
                            expect=Expectation((E("link", "Sign Off"),)))
        if find("columnheader", name="Balance"):
            return Decision("finish", summary="Read the member's savings balance and status",
                            outputs={"savings_balance": cell("Balance"),
                                     "account_status": cell("Status")},
                            expect=Expectation((E("columnheader", "Balance"),
                                                E("LayoutTableCell", PH, "Member ID"))))
        if find("LayoutTableCell", name=PH) and (tab := find("cell", name="Accounts")):
            return Decision("click", intent="Open the Accounts tab", element=tab,
                            expect=Expectation((E("columnheader", "Balance"),)))
        if view := find("link", name="View", anchor=PH):
            return Decision("click", intent="Open this member's record", element=view,
                            expect=Expectation((E("LayoutTableCell", "Member Profile"),)))
        if box := find("textbox", anchor="Member ID", empty=True):
            return Decision("type", intent="Enter the member ID", element=box,
                            value="$inputs.member_id",
                            expect=Expectation((E("textbox", "", "Member ID"),)))
        if search := find("button", name="Search"):
            return Decision("click", intent="Run the search", element=search,
                            expect=Expectation((E("link", "View", PH),)))
        return Decision("give_up", reason="the scripted decider does not recognise this screen")


def options(live_server: str, root: Path) -> DiscoveryOptions:
    return DiscoveryOptions(
        capability_id="lookup_member_balance",
        goal="Look up member {{member_id}} and read their current savings balance",
        entry=f"{live_server}/console",
        inputs=[InputBinding("member_id", "12345", "string", "internal")],
        outputs=[OutputSpecDecl("savings_balance", format="money", sensitivity="pii"),
                 OutputSpecDecl("account_status", sensitivity="internal")],
        evidence_root=root,
        secrets=SecretBroker(environ=CREDENTIALS),
    )


@pytest.fixture(scope="module")
def discovered(live_server: str, tmp_path_factory: pytest.TempPathFactory):
    cassette = Cassette(model=ScriptedDecider.model, goal="lookup",
                        provenance="scripted test decider - not a model")
    result = discover(RecordingDecider(ScriptedDecider(), cassette),
                      options(live_server, tmp_path_factory.mktemp("discovery")))
    return result, cassette


def test_discovery_finishes_and_records_a_bundle_for_every_step(discovered) -> None:
    result, _ = discovered
    t = result.transcript
    assert t.ending == "finished", t.ending_detail
    assert all(s.ok and s.bundle for s in t.steps), [(s.turn, s.error) for s in t.steps]
    assert t.finish is not None and all(o["bundle"] for o in t.finish.outputs.values())


def test_the_transcript_holds_no_raw_values(discovered) -> None:
    result, _ = discovered
    run_dir = result.run_dir
    member = next(m for m in MEMBERS if m.member_id == "12345")
    for path in run_dir.glob("*.json*"):
        text = path.read_text()
        for raw in ("12345", member.name, "4,281.19", "operator1", "changeme"):
            assert raw not in text, f"{raw!r} leaked into {path.name}"


def test_t14_compiled_artifact_generalizes_to_another_member(discovered, live_server, tmp_path):
    result, _ = discovered
    report = compile_transcript(result.transcript)
    assert report.open_gates == (), report.open_gates
    approved = approve(report.capability, approver="test")
    run = replay(approved, {"member_id": "67890"}, ReplayOptions(
        base_url=live_server, evidence_root=tmp_path, secrets=SecretBroker(environ=CREDENTIALS)))
    assert run.status == "success", run.failure
    assert run.outputs == {"savings_balance": "$912.04", "account_status": "dormant"}


def test_a_cassette_reproduces_the_run_without_the_decider(discovered, live_server, tmp_path):
    result, cassette = discovered
    path = tmp_path / "cassette.json"
    cassette.save(path)
    again = discover(CassetteDecider(Cassette.load(path)), options(live_server, tmp_path))
    assert again.transcript.ending == "finished", again.transcript.ending_detail
    assert [s.pre["hash"] for s in again.transcript.steps] == \
           [s.pre["hash"] for s in result.transcript.steps]
    assert json.loads(path.read_text())["provenance"].startswith("scripted")


def test_cli_discover_from_a_cassette_writes_an_approvable_draft(discovered, live_server,
                                                                 tmp_path) -> None:
    """The README's "run without live services" path, through the installed CLI."""
    _, cassette = discovered
    tape = tmp_path / "cassette.json"
    cassette.save(tape)
    root = tmp_path / "capabilities"
    done = subprocess.run(
        [str(Path(sys.executable).parent / "waypoint"), "discover",
         "--capability-id", "lookup_member_balance",
         "--goal", "Look up member {{member_id}} and read their current savings balance",
         "--entry", f"{live_server}/console",
         "--bind", "member_id=12345:string:internal",
         "--expect-output", "savings_balance:money:pii",
         "--expect-output", "account_status:string:internal",
         "--llm", "cassette", "--cassette", str(tape),
         "--root", str(root), "--evidence-root", str(tmp_path / "runs")],
        capture_output=True, text=True, env={**os.environ, **CREDENTIALS},
        stdin=subprocess.DEVNULL, timeout=300,
    )
    assert done.returncode == 0, done.stderr
    assert (root / "lookup_member_balance" / "1.0.0.json").exists()
    assert "no open gates" in done.stdout


class _Sloppy(ScriptedDecider):
    """Finishes by naming a control that is not on the screen - as Haiku did on the live run.

    The compiler would reject it, so the loop must hand the reasons back while the page is
    still open. ``corrigible`` decides whether this stand-in takes the correction.
    """

    model = "scripted-sloppy-decider"
    corrigible = True

    def __init__(self) -> None:
        self.finishes = 0
        self.corrections: list[str] = []

    def decide(self, ctx: DecisionContext) -> Decision:
        decision = super().decide(ctx)
        if decision.kind != "finish":
            return decision
        self.finishes += 1
        if ctx.last_result and ctx.last_result.startswith("Not finished"):
            self.corrections.append(ctx.last_result)
        if self.corrigible and self.finishes > 1:
            return decision
        return Decision("finish", summary=decision.summary, outputs=decision.outputs,
                        expect=Expectation((E("cell", "\tstatus", "active"),)))


def test_a_finish_that_would_not_compile_goes_back_to_the_model(live_server, tmp_path) -> None:
    decider = _Sloppy()
    result = discover(decider, options(live_server, tmp_path))
    assert result.transcript.ending == "finished", result.transcript.ending_detail
    assert decider.finishes == 2, "the first finish should have been handed back"
    assert decider.corrections and "would not compile" in decider.corrections[0]
    assert "success condition was not true" in decider.corrections[0]
    assert compile_transcript(result.transcript).open_gates == ()


def test_corrections_are_bounded_and_the_evidence_survives(live_server, tmp_path) -> None:
    """An incorrigible model ends the run; compilation then fails for a stated reason."""
    decider = _Sloppy()
    decider.corrigible = False
    result = discover(decider, options(live_server, tmp_path))
    assert decider.finishes == 3  # the first, then two corrections
    assert result.transcript.ending == "finished"
    with pytest.raises(CompileError) as refused:
        compile_transcript(result.transcript)
    assert "finish: the success condition was not true on the final screen" in refused.value.reasons
    rejected = [json.loads(line) for line in
                (result.run_dir / "events.jsonl").read_text().splitlines()
                if "finish_rejected" in line]
    assert len(rejected) == 2


class _BadExpectation(ScriptedDecider):
    """Nominates a heading this application does not have; the loop must say so next turn."""

    model = "scripted-bad-expectation-decider"

    def __init__(self) -> None:
        self.told: list[str] = []
        self.actions = 0

    def decide(self, ctx: DecisionContext) -> Decision:
        if ctx.last_result:
            self.told.append(ctx.last_result)
        decision = super().decide(ctx)
        self.actions += 1
        if self.actions == 1:
            return Decision(decision.kind, intent=decision.intent, element=decision.element,
                            value=decision.value,
                            expect=Expectation((E("heading", "Meridian Servicing Console"),)))
        return decision


def test_an_expectation_that_turns_out_false_is_reported_to_the_model(live_server, tmp_path
                                                                      ) -> None:
    decider = _BadExpectation()
    result = discover(decider, options(live_server, tmp_path))
    assert result.transcript.ending == "finished", result.transcript.ending_detail
    assert any("not true on this screen" in told for told in decider.told)
    assert [t for t in decider.told if "not true" in t][0].startswith("Done")
    notes = compile_transcript(result.transcript).notes
    assert any("turn 0: checkpoint was not true" in n for n in notes)


def test_corrections_do_not_count_towards_being_stuck(live_server, tmp_path) -> None:
    """A rejected finish leaves the screen alone; only actions can be 'no progress'."""
    decider = _Sloppy()
    decider.corrigible = False
    opts = options(live_server, tmp_path)
    opts.finish_corrections = 4  # more corrections in a row than stuck_after (3)
    result = discover(decider, opts)
    assert result.transcript.ending == "finished", result.transcript.ending_detail
    assert decider.finishes == 5


class _Rechecks(_BadExpectation):
    """Told its expectation was false, it restates one - first a wrong one, then a true one."""

    model = "scripted-recheck-decider"

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def decide(self, ctx: DecisionContext) -> Decision:
        told = ctx.last_result or ""
        if ("not true on this screen" in told and self.attempts == 0) or "Still not true" in told:
            self.told.append(told)
            self.attempts += 1
            if self.attempts == 1:  # refused: no such element on this screen
                return Decision("recheck", expect=Expectation((E("heading", "Nope"),)))
            return Decision("recheck", expect=Expectation((E("textbox", "", "User ID"),)))
        return super().decide(ctx)


def test_recheck_replaces_a_false_expectation_only_when_the_new_one_is_true(live_server, tmp_path
                                                                            ) -> None:
    decider = _Rechecks()
    result = discover(decider, options(live_server, tmp_path))
    assert result.transcript.ending == "finished", result.transcript.ending_detail
    assert decider.attempts == 2, "the first restatement should have been refused"
    assert any("Still not true" in told for told in decider.told)

    restated = result.transcript.steps[0].decision["expect"]["elements"]
    assert list(restated) == [{"role": "textbox", "name": "", "anchor": "User ID"}]
    report = compile_transcript(result.transcript)
    assert not any("turn 0: checkpoint was not true" in n for n in report.notes)
    assert report.open_gates == (), report.open_gates
    events = (result.run_dir / "events.jsonl").read_text()
    assert "expectation_restated" in events


class _AssertsTheValue(ScriptedDecider):
    """Finishes by asserting the account status it just read - true for this member only."""

    model = "scripted-value-asserting-decider"

    def decide(self, ctx: DecisionContext) -> Decision:
        decision = super().decide(ctx)
        if decision.kind != "finish":
            return decision
        return Decision("finish", summary=decision.summary, outputs=decision.outputs,
                        expect=Expectation((E("columnheader", "Balance"),
                                            E("cell", "active", "Status"))))


def test_a_checkpoint_may_not_assert_an_outputs_own_value(live_server, tmp_path) -> None:
    """T14's real failure mode: "the status is active" fails for a dormant member."""
    result = discover(_AssertsTheValue(), options(live_server, tmp_path))
    assert result.transcript.ending == "finished", result.transcript.ending_detail
    report = compile_transcript(result.transcript)
    assert any("dropped an expectation on an output's own value" in n for n in report.notes)
    goal = report.capability.signatures[report.capability.postconditions[0].signature]
    assert "active" not in json.dumps(goal.model_dump(mode="json"))

    approved = approve(report.capability, approver="test")
    run = replay(approved, {"member_id": "67890"}, ReplayOptions(
        base_url=live_server, evidence_root=tmp_path / "runs",
        secrets=SecretBroker(environ=CREDENTIALS)))
    assert run.status == "success", run.failure
    assert run.outputs == {"savings_balance": "$912.04", "account_status": "dormant"}
