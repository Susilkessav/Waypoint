"""Perception against real, recorded Chromium accessibility trees.

The fixtures under tests/fixtures/ax/ are genuine getFullAXTree output for the
target app's five screens (scripts/capture_ax_fixtures.py). Testing perception on
them - rather than on hand-written trees - is what caught Chromium naming container
cells after their whole contents, and exposing the tab strip as role=cell.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from target_app.data import branch_roster
from waypoint.policy.redactor import Redactor
from waypoint.surface.perception import FrameAX, Perceived, perceive
from waypoint.surface.ports import RawElement, RawSnapshot
from waypoint.surface.sensitivity import Binding, SensitivityClassifier

FIXTURES = Path(__file__).parent / "fixtures" / "ax"
MEMBER = Binding("member_id", "12345", "internal")
CONTENT = ("main", "content")


def load(name: str, bindings: tuple[Binding, ...] = ()) -> list[Perceived]:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    frames = [FrameAX(f["frame_id"], tuple(f["path"]), f["nodes"]) for f in data["frames"]]
    return perceive(frames, SensitivityClassifier(bindings), data["form_attrs"])


def elements(name: str, bindings: tuple[Binding, ...] = ()) -> list[RawElement]:
    return [p.element for p in load(name, bindings)]


def one(els: list[RawElement], role: str, name: str) -> RawElement:
    hits = [e for e in els if e.role == role and e.name.text == name]
    assert len(hits) == 1, f"expected one {role} {name!r}, found {len(hits)}"
    return hits[0]


class TestResultsGrid:
    def test_eight_identically_named_view_links_in_the_content_frame(self) -> None:
        views = [e for e in elements("results") if e.role == "link" and e.name.text == "View"]
        assert len(views) == 8
        assert {v.frame_path for v in views} == {CONTENT}

    def test_refs_are_unique(self) -> None:
        refs = [e.ref for e in elements("accounts")]
        assert len(refs) == len(set(refs))

    def test_each_view_link_is_anchored_by_its_own_row(self) -> None:
        views = [e for e in elements("results") if e.role == "link" and e.name.text == "View"]
        firsts = [v.anchors[0].text for v in views]
        assert firsts == [m.member_id for m in branch_roster("12345")]

    def test_the_bound_row_anchor_carries_the_binding(self) -> None:
        views = [e for e in elements("results", (MEMBER,)) if e.name.text == "View"]
        bound = [v for v in views if v.anchors[0].cls.binding == "member_id"]
        assert len(bound) == 1

    def test_chromium_duplicates_are_dropped(self) -> None:
        els = elements("results")
        assert [e.role for e in els if e.name.text == "12345"] == ["cell"]
        assert not [e for e in els if e.role == "cell" and e.name.text == "View"]

    def test_the_blank_header_is_dropped_and_real_headers_kept(self) -> None:
        headers = [e.name.text for e in elements("results") if e.role == "columnheader"]
        assert headers == ["Member ID", "Name", "Branch", "Status", "Joined"]

    @pytest.mark.parametrize(
        ("name", "level"),
        [("Dolores Whitfield", "pii"), ("12345", "internal"), ("active", "public")],
    )
    def test_cells_take_their_column_headers_level(self, name: str, level: str) -> None:
        cells = [e for e in elements("results") if e.role == "cell" and e.name.text == name]
        assert cells and {c.name.cls.level for c in cells} == {level}


class TestLayoutScreens:
    def test_label_less_textbox_is_labelled_by_the_cell_to_its_left(self) -> None:
        tb = one(elements("search"), "textbox", "")
        assert [a.text for a in tb.anchors] == ["Member ID"]
        assert tb.value is not None and tb.value.cls.level == "internal"

    def test_password_is_secret_via_dom_type(self) -> None:
        boxes = [e for e in elements("login") if e.role == "textbox"]
        levels = sorted(b.value.cls.level for b in boxes if b.value is not None)
        assert levels == ["internal", "secret"]

    def test_header_less_tab_strip_stays_readable(self) -> None:
        els = elements("detail")
        for tab in ("Summary", "Accounts", "Notes"):
            assert one(els, "cell", tab).name.cls.level == "public"

    def test_value_beside_a_name_label_is_pii(self) -> None:
        cell = one(elements("detail"), "LayoutTableCell", "Dolores Whitfield")
        assert (cell.name.cls.level, cell.name.cls.whole) == ("pii", True)

    def test_container_cells_are_not_elements(self) -> None:
        assert not [e for e in elements("detail") if e.name.text.startswith("Joined 2001")]

    def test_unlabelled_control_and_mutating_link_are_present(self) -> None:
        els = elements("detail")
        assert one(els, "link", "").role == "link"
        assert one(els, "link", "Mark for Review").enabled


class TestNestedIframe:
    def test_balance_cell_is_pii_with_header_and_row_context(self) -> None:
        cell = one(elements("accounts"), "cell", "$4,281.19")
        assert cell.frame_path == (*CONTENT, "iframe#ctl00_MainContent_ifrAccounts")
        assert cell.name.cls.level == "pii"
        assert cell.anchors[0].text == "Balance"
        assert "Savings" in [a.text for a in cell.anchors]


def test_real_results_page_sanitizes_without_leaking_any_member() -> None:
    """T25 end to end on a real tree: perception -> classification -> redaction."""
    raw = RawSnapshot(
        url="http://127.0.0.1:8080/console/search",
        title="Member Search Results",
        elements=tuple(elements("results", (MEMBER,))),
    )
    ui = Redactor([MEMBER]).snapshot(raw)
    fields = {k: v for k, v in dataclasses.asdict(ui).items() if k != "hash"}
    blob = json.dumps(fields, ensure_ascii=False)  # keep the placeholder glyphs readable
    roster = branch_roster("12345")
    leaked = [s for m in roster for s in (m.member_id, m.name) if s in blob]
    assert not leaked, f"raw member data leaked: {leaked}"
    assert blob.count("View") >= 8 and "‹$inputs.member_id›" in blob
