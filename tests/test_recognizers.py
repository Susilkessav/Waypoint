"""Signatures read only the sanitized snapshot, yet still assert identity (R-RESUME-5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from waypoint.policy.redactor import Redactor
from waypoint.signatures.recognizers import (
    ElementPredicate,
    Predicate,
    Signature,
    rendered_inputs,
)
from waypoint.surface.perception import FrameAX, perceive
from waypoint.surface.ports import RawSnapshot, UIElement, UISnapshot
from waypoint.surface.sensitivity import Binding, SensitivityClassifier

CONTENT = ("main", "content")
RENDERED = rendered_inputs(
    {"member_id": "12345", "account_type": "savings"},
    {"member_id": "internal", "account_type": "public"},
)


def el(role: str, name: str, *, anchors: tuple[str, ...] = (), value: str | None = None,
       frame: tuple[str, ...] = CONTENT) -> UIElement:
    return UIElement(f"r-{role}-{name}", role, name, value, True, frame, None, anchors,
                     "public", "public")


def snap(*elements: UIElement, frame_urls: tuple[tuple[tuple[str, ...], str], ...] = ()
         ) -> UISnapshot:
    return UISnapshot("http://h/console", "Meridian", elements, "", "h", frame_urls)


def exists(**kw: object) -> Predicate:
    return Predicate(element_exists=ElementPredicate(**kw))  # type: ignore[arg-type]


class TestShape:
    def test_a_predicate_sets_exactly_one_key(self) -> None:
        with pytest.raises(ValidationError):
            Predicate(text_contains="a", url_matches="b")
        with pytest.raises(ValidationError):
            Predicate()

    def test_element_predicates_need_a_role_or_name(self) -> None:
        with pytest.raises(ValidationError):
            ElementPredicate(anchor="Member ID")

    def test_references_must_name_inputs(self) -> None:
        with pytest.raises(ValidationError):
            ElementPredicate(role="cell", name_ref="12345")

    def test_bad_regex_is_rejected_at_load(self) -> None:
        with pytest.raises(ValidationError):
            Predicate(url_matches="(unclosed")


class TestRendering:
    def test_sensitive_inputs_render_as_their_placeholder(self) -> None:
        assert RENDERED["member_id"] == "‹$inputs.member_id›"

    def test_public_inputs_render_verbatim(self) -> None:
        assert RENDERED["account_type"] == "savings"


class TestEvaluation:
    def test_identity_by_binding_placeholder(self) -> None:
        p = exists(role="LayoutTableCell", name_ref="$inputs.member_id", anchor="Member ID")
        right = snap(el("LayoutTableCell", "‹$inputs.member_id›", anchors=("Member ID",)))
        wrong = snap(el("LayoutTableCell", "‹redacted:5 chars›", anchors=("Member ID",)))
        assert p.evaluate(right, RENDERED) and not p.evaluate(wrong, RENDERED)

    def test_value_ref_and_filled(self) -> None:
        box = el("textbox", "", anchors=("Member ID",), value="‹$inputs.member_id›")
        assert exists(role="textbox", value_ref="$inputs.member_id").evaluate(snap(box), RENDERED)
        assert exists(role="textbox", filled=True).evaluate(snap(box), RENDERED)
        empty = el("textbox", "", anchors=("Member ID",))
        assert not exists(role="textbox", filled=True).evaluate(snap(empty), RENDERED)

    def test_frame_constraint(self) -> None:
        link = el("link", "Sign Off", frame=("main", "nav"))
        assert exists(name="Sign Off", frame=("main", "nav")).evaluate(snap(link), RENDERED)
        assert not exists(name="Sign Off", frame=CONTENT).evaluate(snap(link), RENDERED)

    def test_text_and_frame_urls(self) -> None:
        s = snap(el("StaticText", "No records found"),
                 frame_urls=((CONTENT, "http://h/console/search"),))
        assert Predicate(text_contains="No records").evaluate(s, RENDERED)
        assert Predicate(url_matches=r"/console/search$").evaluate(s, RENDERED)

    def test_combinators(self) -> None:
        s = snap(el("button", "Search"))
        yes, no = exists(name="Search"), exists(name="Delete")
        assert Predicate(all=(yes,)).evaluate(s, RENDERED)
        assert Predicate(any=(no, yes)).evaluate(s, RENDERED)
        assert Predicate(none=(no,)).evaluate(s, RENDERED)
        assert not Predicate(all=(yes, no)).evaluate(s, RENDERED)

    @pytest.mark.parametrize("key", ["element_exists", "element_absent"])
    def test_an_unbound_reference_is_an_error_never_a_verdict(self, key: str) -> None:
        p = Predicate(**{key: ElementPredicate(role="cell", name_ref="$inputs.other")})
        with pytest.raises(KeyError):
            p.evaluate(snap(el("cell", "x")), RENDERED)


class TestContentAssertion:
    """R-PKG-5 (c): a checkpoint must say what is on screen, not merely that it moved."""

    @pytest.mark.parametrize(
        ("predicate", "expected"),
        [
            (exists(role="cell"), True),
            (Predicate(text_contains="x"), True),
            (Predicate(url_matches="x"), False),
            (Predicate(element_absent=ElementPredicate(role="cell")), False),
            (Predicate(all=(Predicate(url_matches="x"), exists(role="cell"))), True),
            (Predicate(any=(Predicate(url_matches="x"), exists(role="cell"))), False),
            (Predicate(none=(exists(role="cell"),)), False),
        ],
    )
    def test_asserts_content(self, predicate: Predicate, expected: bool) -> None:
        assert predicate.asserts_content() is expected


MEMBER_DETAIL = Signature(
    description="Member detail for THIS member (R-RESUME-5)",
    match=Predicate(all=(
        exists(name="Member Profile", frame=CONTENT),
        exists(role="LayoutTableCell", name_ref="$inputs.member_id", anchor="Member ID",
               frame=CONTENT),
    )),
)


@pytest.mark.parametrize(("bound", "expected"), [("12345", True), ("67890", False)])
def test_wrong_member_correct_screen_on_a_real_tree(bound: str, expected: bool) -> None:
    """T7 at the recognizer: 12345's real detail page fails the check for 67890."""
    data = json.loads((Path(__file__).parent / "fixtures" / "ax" / "detail.json").read_text())
    frames = [FrameAX(f["frame_id"], tuple(f["path"]), f["nodes"]) for f in data["frames"]]
    binding = Binding("member_id", bound, "internal")
    raw = RawSnapshot("http://h/console", "Meridian", tuple(
        p.element for p in perceive(frames, SensitivityClassifier([binding]), data["form_attrs"])
    ))
    ui = Redactor([binding]).snapshot(raw)
    rendered = rendered_inputs({"member_id": bound}, {"member_id": "internal"})
    assert MEMBER_DETAIL.evaluate(ui, rendered) is expected
    assert MEMBER_DETAIL.refs() == {"member_id"}
