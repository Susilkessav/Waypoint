"""The compiler on small synthetic transcripts - fast, no browser.

T15 - an extraction bundle keying on its own value is rejected at compile.
Also: pruning, unbound literals, weak checkpoints, redacted data, identity.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from waypoint.compiler.compile import CompileError, compile_transcript
from waypoint.discovery.transcript import (
    BindingSpec,
    FinishRecord,
    OutputSpecDecl,
    Step,
    Transcript,
    snapshot_to_dict,
)
from waypoint.surface.ports import UIElement, UISnapshot

PH = "‹$inputs.member_id›"
C = ("main", "content")


def el(role: str, name: str, *, anchors: tuple[str, ...] = (), value: str | None = None
       ) -> UIElement:
    return UIElement(f"r{role}{name}", role, name, value, True, C, None, anchors,
                     "public", "public")


SEARCH = UISnapshot("http://h/console", "M", (
    el("LayoutTableCell", "Servicing"),
    el("textbox", "", anchors=("Member ID",), value=""),
    el("button", "Search"),
), "", "A")
RESULTS = UISnapshot("http://h/console", "M", (
    el("LayoutTableCell", "Servicing"),
    el("cell", PH, anchors=("Member ID",)),
    el("link", "View", anchors=(PH, "BR-014")),
), "", "B")
DETOUR = UISnapshot("http://h/console", "M", (el("LayoutTableCell", "Servicing"),
                                               el("LayoutTableCell", "Reports")), "", "C")
DETAIL = UISnapshot("http://h/console", "M", (
    el("LayoutTableCell", "Servicing"),
    el("LayoutTableCell", "Member Profile"),
    el("LayoutTableCell", PH, anchors=("Member ID",)),
    el("columnheader", "Balance"),
    el("cell", "‹redacted:9 chars›", anchors=("Balance", "Savings")),
), "", "D")

BY_ROLE = {"recorded_tier": 1, "candidates": [
    {"tier": 1, "kind": "role_name", "frame_path": list(C), "role": "button", "name": "Search"}]}
BY_COLUMN = {"recorded_tier": 3, "candidates": [
    {"tier": 3, "kind": "anchored", "frame_path": list(C), "role": "cell",
     "anchor": {"role": "cell", "text": "Savings"}, "column": "Balance"}]}
KEYED_ON_VALUE = {"recorded_tier": 1, "candidates": [
    {"tier": 1, "kind": "role_name", "frame_path": list(C), "role": "cell", "name": "$4,281.19"}]}


def expect(*elements: tuple[str, str, str], text: str = "") -> dict:
    return {"elements": [{"role": r, "name": n, "anchor": a} for r, n, a in elements],
            "text": text}


def step(turn: int, pre: UISnapshot, post: UISnapshot, *, ok: bool = True,
         nominated: dict | None = None, navigated: bool = True, **kw) -> Step:
    return Step(turn=turn, pre=snapshot_to_dict(pre), action=kw.pop("action", "click"),
                decision={"kind": "click", "intent": kw.pop("intent", "Search"),
                          "expect": nominated or expect(("link", "View", PH))},
                target=kw.pop("target", {"role": "button", "anchors": [], "frame_path": list(C)}),
                bundle=kw.pop("bundle", BY_ROLE), ok=ok, navigated=navigated,
                post_hash=post.hash, **kw)


def transcript(steps: list[Step], *, final: UISnapshot = RESULTS,
               success: dict | None = None, bundle: dict | None = None,
               ending: str = "finished") -> Transcript:
    return Transcript(
        run_id="run-1", capability_id="lookup", goal="Look up $inputs.member_id",
        entry="http://h/console", model="test", ending=ending,  # type: ignore[arg-type]
        bindings=[BindingSpec("member_id", "string", "internal")],
        expected_outputs=[OutputSpecDecl("balance", format="money", sensitivity="pii")],
        steps=steps,
        finish=FinishRecord(snapshot_to_dict(final), success or expect(("link", "View", PH)),
                            "done", {"balance": {"element": None, "bundle": bundle or BY_COLUMN}}),
    )


def test_a_clean_run_compiles_with_no_open_gates() -> None:
    report = compile_transcript(transcript([step(0, SEARCH, RESULTS)]))
    cap = report.capability
    assert report.open_gates == ()
    assert len(cap.steps) == 1 and cap.steps[0].resume_point
    assert cap.signatures[cap.steps[0].checkpoint.signature].refs() == {"member_id"}
    assert cap.provenance.approval == {"base": "draft"}


def test_t15_extraction_keyed_on_its_own_value_is_rejected() -> None:
    with pytest.raises(CompileError, match="R-SENS-7"):
        compile_transcript(transcript([step(0, SEARCH, RESULTS)], bundle=KEYED_ON_VALUE))


def test_an_unfinished_run_does_not_compile() -> None:
    with pytest.raises(CompileError, match="did not finish"):
        compile_transcript(transcript([step(0, SEARCH, RESULTS)], ending="gave_up"))


def test_failed_actions_and_detours_are_pruned() -> None:
    steps = [
        step(0, SEARCH, SEARCH, ok=False, error_code="policy_block"),
        step(1, SEARCH, DETOUR, intent="Open reports"),
        step(2, DETOUR, SEARCH, intent="Go back"),
        step(3, SEARCH, RESULTS),
    ]
    report = compile_transcript(transcript(steps))
    assert [s.intent for s in report.capability.steps] == ["Search"]
    assert any("detour" in n for n in report.notes)
    assert any("policy_block" in n for n in report.notes)


def test_an_unbound_literal_becomes_an_input_that_blocks_approval() -> None:
    typed = step(0, SEARCH, RESULTS, action="type", value_ref=None,
                 value_provenance="unbound literal of 5 characters, not recorded",
                 target={"role": "textbox", "anchors": ["Member ID"], "frame_path": list(C)},
                 bundle=BY_ROLE)
    report = compile_transcript(transcript([replace(typed, decision={
        **typed.decision, "kind": "type", "value": "‹unbound literal: 5 chars›"})]))
    assert "unbound_0" in report.capability.inputs.properties
    assert report.capability.steps[0].unreviewed_literal
    assert any("unreviewed literal" in g for g in report.open_gates)


def test_a_nomination_true_on_every_screen_is_weak() -> None:
    always = step(0, SEARCH, DETOUR, nominated=expect(("LayoutTableCell", "Servicing", "")))
    report = compile_transcript(transcript(
        [always], final=DETOUR, success=expect(("LayoutTableCell", "Reports", ""))))
    assert report.capability.steps[0].checkpoint.weak
    assert any("weak" in g for g in report.open_gates)


def test_redacted_data_in_a_nomination_is_dropped() -> None:
    nominated = expect(("cell", "‹redacted:5 chars›", ""), ("link", "View", PH))
    report = compile_transcript(transcript([step(0, SEARCH, RESULTS, nominated=nominated)]))
    assert "redacted" not in report.capability.to_json().split('"signatures"')[1].split(
        '"postconditions"')[0]
    assert any("redacted data" in n for n in report.notes)


def test_identity_is_added_when_the_model_forgets_which_record() -> None:
    forgot = step(0, SEARCH, DETAIL, nominated=expect(("LayoutTableCell", "Member Profile", "")))
    report = compile_transcript(transcript([forgot], final=DETAIL,
                                           success=expect(("columnheader", "Balance", ""))))
    cap = report.capability
    assert cap.signatures[cap.steps[0].checkpoint.signature].refs() == {"member_id"}
    assert any("identity" in n for n in report.notes)


@pytest.mark.parametrize("written", ["\n2039$inputs.member_id\n203a",
                                     "\\u2039$inputs.member_id\\u203a", "$inputs.member_id"])
def test_a_placeholder_written_without_proper_marks_still_asserts_identity(written: str) -> None:
    """The live open_sub_account run wrote its Confirm expectation with a broken escape,
    and a correct check compiled as unverified - an open gate nobody could close."""
    nominated = expect(("cell", written, "Member ID"))
    report = compile_transcript(transcript([step(0, SEARCH, RESULTS, nominated=nominated)]))
    checkpoint = report.capability.steps[0].checkpoint
    assert not checkpoint.unverified
    signature = report.capability.signatures[checkpoint.signature].model_dump_json()
    assert '"name_ref":"$inputs.member_id"' in signature


def test_a_redacted_value_beside_a_label_keeps_the_label() -> None:
    """ "The value beside Balance" is about the step; the value itself is one record's."""
    nominated = expect(("cell", "‹redacted:9 chars›", "Balance"))
    report = compile_transcript(transcript(
        [step(0, RESULTS, DETAIL, nominated=nominated)], final=DETAIL,
        success=expect(("LayoutTableCell", "Member Profile", ""))))
    checkpoint = report.capability.steps[0].checkpoint
    signature = report.capability.signatures[checkpoint.signature].model_dump_json()
    assert not checkpoint.unverified
    assert '"anchor":"Balance"' in signature and "redacted" not in signature
    assert any("without its redacted value" in n for n in report.notes)
