"""The return ladder on real recorded screens (R-RESUME-3, R-RESUME-4).

T8 - resume targets only declared resume points.
T9 - a resume point's checkpoint rejects an empty form.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from waypoint.artifact.schema import Capability, load
from waypoint.policy.redactor import Redactor
from waypoint.replay.resume import Outcome, Resume, Success, Unrecognized, return_ladder
from waypoint.signatures.recognizers import rendered_inputs
from waypoint.surface.perception import FrameAX, perceive
from waypoint.surface.ports import RawSnapshot, UIElement, UISnapshot
from waypoint.surface.sensitivity import Binding, SensitivityClassifier

REPO = Path(__file__).resolve().parents[1]
LOOKUP = load(REPO / "capabilities" / "lookup_member_balance" / "1.0.0.json")
RENDERED = rendered_inputs({"member_id": "12345"}, {"member_id": "internal"})


def screen(fixture: str, member: str = "12345") -> UISnapshot:
    """A real recorded screen, perceived and sanitized with ``member`` bound."""
    data = json.loads((REPO / "tests" / "fixtures" / "ax" / f"{fixture}.json").read_text())
    frames = [FrameAX(f["frame_id"], tuple(f["path"]), f["nodes"]) for f in data["frames"]]
    bound = Binding("member_id", member, "internal")
    elements = perceive(frames, SensitivityClassifier([bound]), data["form_attrs"])
    raw = RawSnapshot("http://h/console", "Meridian", tuple(p.element for p in elements),
                      tuple((tuple(f["path"]), f["url"]) for f in data["frames"]))
    return Redactor([bound]).snapshot(raw)


def el(role: str, name: str = "", *, anchors: tuple[str, ...] = (), value: str | None = None
       ) -> UIElement:
    return UIElement(f"r-{role}-{name}-{anchors}", role, name, value, True, ("main",), None,
                     anchors, "public", "public")


def snap(*elements: UIElement) -> UISnapshot:
    return UISnapshot("http://h/", "t", elements, "", "h")


class TestLadderOnTheLookupCapability:
    def test_rung_1_a_declared_outcome_wins(self) -> None:
        result = return_ladder(LOOKUP, RENDERED, snap(el("StaticText", "No records found")), 1)
        assert result == Outcome("member_not_found", "business")

    def test_rung_2_the_postcondition_means_success(self) -> None:
        assert return_ladder(LOOKUP, RENDERED, screen("accounts"), 2) == Success()

    def test_rung_3_the_escalated_steps_own_checkpoint(self) -> None:
        assert return_ladder(LOOKUP, RENDERED, screen("detail"), 2) == Resume(3, "checkpoint")

    def test_rung_4_a_declared_resume_point_further_on(self) -> None:
        """Escalated at the search; the human went on to the member's record."""
        assert return_ladder(LOOKUP, RENDERED, screen("detail"), 1) == Resume(3, "resume_point")

    def test_t8_a_satisfied_checkpoint_that_is_not_a_resume_point_is_not_resumed_to(self) -> None:
        results = screen("results")
        assert LOOKUP.signatures["member_in_results"].evaluate(results, RENDERED)
        assert not LOOKUP.steps[1].resume_point
        # "the highest satisfied checkpoint" would resume after steps[1]; the ladder refuses.
        assert isinstance(return_ladder(LOOKUP, RENDERED, results, 0), Unrecognized)

    def test_the_right_screen_for_the_wrong_member_is_unrecognised(self) -> None:
        wrong = rendered_inputs({"member_id": "67890"}, {"member_id": "internal"})
        assert isinstance(return_ladder(LOOKUP, wrong, screen("detail", member="67890"), 2),
                          Unrecognized)


def form_capability() -> Capability:
    def field(label: str) -> dict:
        return {"recorded_tier": 3, "candidates": [{
            "tier": 3, "kind": "anchored", "frame_path": ["main"], "role": "textbox",
            "anchor": {"role": "cell", "text": label}}]}

    def filled(label: str, ref: str) -> dict:
        return {"element_exists": {"role": "textbox", "anchor": label, "value_ref": ref}}

    review = {"recorded_tier": 1, "candidates": [{
        "tier": 1, "kind": "role_name", "frame_path": ["main"], "role": "button",
        "name": "Review"}]}
    return Capability.model_validate({
        "schema_version": "1.0.0", "capability_id": "fill_form", "version": "1.0.0",
        "name": "Fill a form", "description": "T9 fixture", "surface": {"entry": "http://h/f"},
        "inputs": {"required": ["name", "amount"], "properties": {
            "name": {"sensitivity": "internal"}, "amount": {"sensitivity": "internal"}}},
        "signatures": {
            "form_visible": {"match": {"element_exists": {"role": "button", "name": "Review"}}},
            "name_filled": {"match": filled("Name", "$inputs.name")},
            "form_complete": {"match": {"all": [filled("Name", "$inputs.name"),
                                                filled("Amount", "$inputs.amount")]}},
            "review_shown": {"match": {"text_contains": "Please review"}},
        },
        "postconditions": [{"signature": "review_shown"}],
        "steps": [
            {"intent": "Enter the name", "action": "type", "target": field("Name"),
             "value_ref": "$inputs.name", "checkpoint": {"signature": "name_filled"}},
            {"intent": "Enter the amount", "action": "type", "target": field("Amount"),
             "value_ref": "$inputs.amount", "checkpoint": {"signature": "form_complete"},
             "resume_point": True},
            {"intent": "Open the review", "action": "click", "target": review,
             "checkpoint": {"signature": "review_shown"}},
        ],
    })


FORM = form_capability()
FORM_INPUTS = rendered_inputs({"name": "x", "amount": "1.00"},
                              {"name": "internal", "amount": "internal"})


def form(name: str, amount: str) -> UISnapshot:
    return snap(el("textbox", anchors=("Name",), value=name),
                el("textbox", anchors=("Amount",), value=amount), el("button", "Review"))


class TestT9ResumePointsAreStateComplete:
    def test_a_generic_form_check_is_true_of_an_empty_form(self) -> None:
        """Why the ladder must not resume to just any satisfied checkpoint."""
        assert FORM.signatures["form_visible"].evaluate(form("", ""), FORM_INPUTS)

    @pytest.mark.parametrize(("name", "amount"), [("", ""), ("‹$inputs.name›", "")])
    def test_an_empty_or_half_filled_form_is_not_resumed_past(self, name: str, amount: str
                                                              ) -> None:
        assert isinstance(return_ladder(FORM, FORM_INPUTS, form(name, amount), 1), Unrecognized)

    def test_a_completed_form_resumes_after_the_resume_point(self) -> None:
        done = form("‹$inputs.name›", "‹$inputs.amount›")
        assert return_ladder(FORM, FORM_INPUTS, done, 1) == Resume(2, "checkpoint")
        assert return_ladder(FORM, FORM_INPUTS, done, 0) == Resume(1, "checkpoint")
