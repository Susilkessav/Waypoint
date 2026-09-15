"""Deciding a discovery run is going nowhere, and nominating a checkpoint for a person's step.

Both are pure: no browser, no model. "Stuck" is what routes a run to a person
(REPORT.md §5), so it must be sharp - an unchanged screen, two screens alternating, or
actions that keep being refused - and must not fire on a run that is merely slow.
"""

from __future__ import annotations

from waypoint.compiler.compile import diff_expectation
from waypoint.discovery.agent import DiscoveryOptions, _Discovery
from waypoint.discovery.cassette import Decider, DecisionContext
from waypoint.discovery.decisions import Decision
from waypoint.surface.ports import UIElement, UISnapshot


class _Silent:
    model = "none"

    def decide(self, ctx: DecisionContext) -> Decision:  # pragma: no cover - never called
        raise AssertionError("the loop is not run here")


def loop() -> _Discovery:
    decider: Decider = _Silent()
    return _Discovery(decider, DiscoveryOptions(
        capability_id="c", goal="g", entry="http://127.0.0.1:1/console", inputs=[], outputs=[],
        evidence_root=__import__("pathlib").Path("/tmp/waypoint-unused"),
    ))


def element(role: str, name: str) -> UIElement:
    return UIElement(ref=f"ref_{name}", role=role, name=name, value=None, enabled=True,
                     frame_path=("main",), bbox=None, anchors=(), sensitivity="public",
                     name_sensitivity="public")


def snapshot(*names: tuple[str, str], url: str = "http://x/1") -> UISnapshot:
    elements = tuple(element(role, name) for role, name in names)
    return UISnapshot(url, "t", elements, "digest", "hash-" + "-".join(n for _, n in names), ())


def test_an_unchanged_screen_is_stuck() -> None:
    d = loop()
    assert d._going_nowhere(3, ["a", "b", "c", "d"], 0) == (
        "discovery_stuck", "the screen did not change for 3 turns")
    assert d._going_nowhere(2, ["a", "b", "c", "d"], 0) is None


def test_two_screens_alternating_is_stuck() -> None:
    d = loop()
    code, _ = d._going_nowhere(0, ["a", "b", "a", "b"], 0) or ("", "")
    assert code == "discovery_oscillating"
    assert d._going_nowhere(0, ["a", "b", "a", "c"], 0) is None, "progress, not a loop"
    assert d._going_nowhere(0, ["a", "a", "a", "a"], 0) is None, "that is the unchanged case"


def test_actions_that_keep_being_refused_are_stuck() -> None:
    d = loop()
    assert d._going_nowhere(0, [], 3) == (
        "discovery_blocked", "3 actions in a row could not be performed")
    assert d._going_nowhere(0, [], 2) is None


def test_a_persons_step_is_checked_by_what_it_made_appear() -> None:
    before = snapshot(("link", "View"), ("cell", "BR-014"))
    after = snapshot(("heading", "Member Profile"), ("link", "Mark for Review"),
                     ("cell", "BR-014"))
    expect = diff_expectation(before, after, {})
    assert [e["name"] for e in expect["elements"]] == ["Member Profile", "Mark for Review"]


def test_redacted_data_is_never_nominated_for_a_persons_step() -> None:
    before = snapshot(("link", "View"))
    after = snapshot(("cell", "‹redacted:12 chars›"), ("columnheader", "Balance"))
    expect = diff_expectation(before, after, {})
    assert [e["name"] for e in expect["elements"]] == ["Balance"]


def test_an_input_placeholder_is_nominated_first_so_the_check_says_which_record() -> None:
    before = snapshot(("link", "View"))
    after = snapshot(("cell", "BR-014"), ("LayoutTableCell", "‹$inputs.member_id›"),
                     ("heading", "Member Profile"))
    expect = diff_expectation(before, after, {"member_id": "‹$inputs.member_id›"})
    assert expect["elements"][0]["name"] == "‹$inputs.member_id›"


def test_a_screen_that_added_nothing_nominates_nothing() -> None:
    same = snapshot(("link", "View"))
    assert diff_expectation(same, same, {}) == {}
