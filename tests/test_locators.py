"""A4 T1–T6: exercise compiled locators against the hostile browser fixture."""

from urllib.parse import urljoin

import pytest
from pydantic import ValidationError

from tests.test_web_surface import find, login, open_member, search
from tests.test_web_surface import surface as surface
from waypoint.surface.locators import (
    Ambiguous,
    Candidate,
    Found,
    LocatorBundle,
    NotFound,
)
from waypoint.surface.ports import Action

pytestmark = pytest.mark.browser
INPUTS = {"member_id": "12345"}


def grid_bundle(s):
    login(s)
    snap = search(s, "12345")
    target = find(snap, "link", name="View", anchor="‹$inputs.member_id›")
    return s.synthesize(target.ref, INPUTS)


def inject_results(s, inject):
    content = s.page.frame(name="content")
    assert content is not None
    content.goto(
        urljoin(s.page.url, f"/console/search?ctl00$MainContent$txtMemberId=12345&inject={inject}")
    )
    s.quiesce()


def test_compilation_excludes_ambiguous_tier_and_round_trips_json(surface):
    bundle = grid_bundle(surface)
    assert bundle.recorded_tier == 3
    assert not any(c.tier == 1 for c in bundle.candidates)
    assert any(d.candidate.tier == 1 and d.matches_at_record == 8 for d in bundle.diagnostics)
    decoded = LocatorBundle.model_validate_json(bundle.model_dump_json())
    found = surface.resolve(decoded, INPUTS)
    assert isinstance(found, Found)
    assert surface.act(Action("click", ref=found.ref)).ok
    assert "member_id=12345" in surface.page.frame(name="content").url


def test_duplicate_anchor_cannot_be_rescued_by_position(surface):
    bundle = grid_bundle(surface)
    inject_results(surface, "ambiguous")
    result = surface.resolve(bundle, INPUTS)
    assert isinstance(result, Ambiguous)
    assert result.reason == "positional_blocked_by_unresolved_ambiguity"


def test_reordering_keeps_semantic_identity_but_invalidates_old_position(surface):
    bundle = grid_bundle(surface)
    position = next(c for c in bundle.candidates if c.tier == 4)
    inject_results(surface, "reorder")
    found = surface.resolve(bundle, INPUTS)
    assert isinstance(found, Found) and found.tier == 3
    positional_hits = surface.matcher.matches(position, INPUTS)
    assert len(positional_hits) == 1
    assert not surface.matcher.identity_matches(position, positional_hits[0], INPUTS)
    assert surface.act(Action("click", ref=found.ref)).ok
    assert "member_id=12345" in surface.page.frame(name="content").url


def test_missing_row_never_selects_its_replacement(surface):
    bundle = grid_bundle(surface)
    inject_results(surface, "row_missing")
    before = surface.page.frame(name="content").url
    result = surface.resolve(bundle, INPUTS)
    assert isinstance(result, NotFound)
    assert surface.page.frame(name="content").url == before
    assert any(e["event"] == "positional_identity_failed" for e in surface.events)


def test_anchor_is_exact_not_a_substring(surface):
    bundle = grid_bundle(surface)
    surface.page.frame(name="content").locator("td").filter(has_text="12345").first.evaluate(
        "e => e.textContent='123456'"
    )
    assert isinstance(surface.resolve(bundle, INPUTS), NotFound)


def test_name_drift_falls_back_to_context_and_emits_degradation(surface):
    surface.page.set_content(
        "<table><tr><td>Member ID</td><td><input aria-label='Search value'></td></tr></table>"
    )
    snap = surface.observe()
    target = find(snap, "textbox", name="Search value")
    bundle = surface.synthesize(target.ref)
    surface.page.locator("input").evaluate("e => e.setAttribute('aria-label','Renamed value')")
    result = surface.resolve(bundle)
    assert isinstance(result, Found) and result.degraded
    assert any(e["event"] == "locator_degradation" for e in surface.events)


def test_extraction_bundle_uses_header_and_row_not_balance_value(surface):
    detail = open_member(surface)
    tab = find(detail, "cell", name="Accounts")
    assert surface.act(Action("click", ref=tab.ref)).ok
    snap = surface.observe()
    balance = next(
        e
        for e in snap.elements
        if e.role == "cell" and "Balance" in e.anchors and "Savings" in e.anchors
    )
    bundle = surface.synthesize(balance.ref)
    assert "$4,281.19" not in bundle.model_dump_json()
    result = surface.resolve(bundle)
    assert isinstance(result, Found)
    assert surface.extract_raw(result.ref) == "$4,281.19"


def test_bundle_rejects_unverified_position():
    semantic = Candidate(tier=1, kind="role_name", role="button", name="View")
    positional = Candidate(tier=4, kind="structural", path="button:nth-child(2)")
    with pytest.raises(ValidationError, match="identity"):
        LocatorBundle(recorded_tier=1, candidates=(semantic, positional))


def test_a_value_beside_its_label_gets_a_semantic_extraction_locator(surface):
    """Label/value layout rows - every confirmation page - locate the value by its label.

    The row holds two cells, so a locator that took "the cells of the label's row" matched
    the label as well and could never be unique; outputs on such pages had no semantic
    locator at all (found by the live discovery of open_sub_account).
    """
    from tests.test_web_surface import open_member
    from waypoint.artifact.schema import keys_on_value

    detail = open_member(surface)
    joined = next(e for e in detail.elements
                  if e.role == "LayoutTableCell" and "Joined" in e.anchors)
    bundle = surface.synthesize(joined.ref, INPUTS, extraction=True)
    anchored = [c for c in bundle.candidates if c.kind == "anchored"]
    assert anchored and anchored[0].anchor.text == "Joined" and anchored[0].name is None
    assert not any(keys_on_value(c) for c in bundle.candidates)

    found = surface.resolve(bundle, INPUTS)
    assert isinstance(found, Found) and found.tier == 3  # refs are per-observation
    assert surface.extract_raw(found.ref) == "2001-07-22"
