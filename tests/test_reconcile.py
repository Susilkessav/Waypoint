"""The three-way verdict and the gates around it (PLAN.md R-REC-1..5, R-PKG-3). No browser.

T12 - neither condition holds -> Unknown, never a guess either way.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from waypoint.artifact.schema import Capability, approval_gates, load
from waypoint.replay.reconcile import PREDATES, decide, parse_time
from waypoint.signatures.recognizers import rendered_inputs
from waypoint.surface.ports import UIElement, UISnapshot

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "capabilities" / "open_sub_account" / "1.0.0.json"
CAP = load(ARTIFACT)
RECONCILE = CAP.steps[-1].reconcile
assert RECONCILE is not None
RENDERED = rendered_inputs(
    {"member_id": "12345", "account_type": "Money Market", "initial_deposit": "250.00"},
    {"member_id": "internal", "account_type": "public", "initial_deposit": "internal"},
)
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def el(role: str, name: str, *anchors: str) -> UIElement:
    return UIElement(f"r-{role}-{name}", role, name, None, True, ("main",), None, anchors,
                     "public", "public")


def grid(*rows: str, member: str = "‹$inputs.member_id›", header: bool = True) -> UISnapshot:
    elements = [el("LayoutTableCell", member, "Member ID")]
    if header:
        elements.append(el("columnheader", "Opened"))
    elements += [el("cell", kind, "Type") for kind in rows]
    return UISnapshot("http://h/console/member", "M", tuple(elements), "", "h")


def verdict(snap: UISnapshot, times: list[Any] | None = None, at: datetime = NOW) -> str:
    assert RECONCILE is not None
    return decide(RECONCILE, CAP.signatures, snap, RENDERED, times, at).verdict


class TestVerdict:
    def test_a_recent_record_for_these_inputs_is_completed(self) -> None:
        assert verdict(grid("Money Market"), [NOW + timedelta(seconds=3)]) == "completed"

    def test_clock_skew_is_tolerated(self) -> None:
        assert verdict(grid("Money Market"), [NOW - timedelta(seconds=90)]) == "completed"

    def test_only_older_records_of_this_type_means_not_completed(self) -> None:
        """Positive evidence: the list is visible and every matching record predates us."""
        old = NOW - timedelta(days=3)
        assert verdict(grid("Money Market"), [old, PREDATES]) == "not_completed"

    def test_an_unreadable_time_with_no_recent_one_is_unknown(self) -> None:
        assert verdict(grid("Money Market"), [None]) == "unknown"
        assert verdict(grid("Money Market"), []) == "unknown"

    def test_a_readable_recent_time_wins_over_an_unreadable_one(self) -> None:
        assert verdict(grid("Money Market"), [None, NOW]) == "completed"

    def test_a_visible_grid_without_this_type_is_not_completed(self) -> None:
        assert verdict(grid("Savings", "Checking"), []) == "not_completed"

    def test_another_members_grid_proves_nothing(self) -> None:
        """R-REC-2: someone else's record is neither ours nor evidence ours is missing."""
        other = grid("Money Market", member="‹redacted:5 chars›")
        assert verdict(other, [NOW]) == "unknown"

    def test_t12_a_screen_matching_neither_condition_is_unknown(self) -> None:
        """No grid header: absence of a row on a page that shows no list is not evidence."""
        assert verdict(grid(header=False)) == "unknown"
        search_form = UISnapshot("http://h/console/content", "Search",
                                 (el("textbox", "", "Member ID"),), "", "h")
        assert verdict(search_form) == "unknown"


class TestParseTime:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("2026-09-12 01:02:03 UTC", datetime(2026, 9, 12, 1, 2, 3, tzinfo=UTC)),
        ("2026-09-12T01:02:03Z", datetime(2026, 9, 12, 1, 2, 3, tzinfo=UTC)),
        ("-", PREDATES), ("", PREDATES),
        ("yesterday", None), ("12/09/2026", None),
    ])
    def test_formats(self, raw: str, expected: Any) -> None:
        assert parse_time(raw) == expected


def body() -> dict[str, Any]:
    data = json.loads(ARTIFACT.read_text())
    data["provenance"] = {}
    return data


class TestGates:
    def test_the_reviewed_artifact_has_no_open_gates(self) -> None:
        assert approval_gates(CAP) == []

    def test_a_probe_may_not_act(self) -> None:
        data = body()
        probe = data["steps"][-1]["reconcile"]["probe"][0]
        data["steps"][-1]["reconcile"]["probe"][0] = {
            **probe, "action": "click", "url_template": None,
            "target": data["steps"][-1]["target"]}
        with pytest.raises(ValidationError, match="may only navigate and wait"):
            Capability.model_validate(data)

    def test_absence_that_asserts_nothing_on_screen_is_not_evidence(self) -> None:
        data = body()
        data["signatures"]["no_subaccount_for_inputs"]["match"] = {"element_absent": {
            "role": "cell", "name_ref": "$inputs.account_type", "anchor": "Type"}}
        gates = approval_gates(Capability.model_validate(data))
        assert any("absence is not evidence" in g for g in gates)

    def test_completed_when_must_assert_every_identity_input(self) -> None:
        data = body()
        data["steps"][-1]["reconcile"]["completed_when"] = "accounts_listed_for_member"
        gates = approval_gates(Capability.model_validate(data))
        assert any("does not assert the identity inputs" in g for g in gates)

    def test_an_adopted_run_must_be_able_to_return_every_output(self) -> None:
        data = body()
        data["steps"][-1]["reconcile"]["extract"] = {}
        gates = approval_gates(Capability.model_validate(data))
        assert any("could not return ['account_id']" in g for g in gates)

    def test_an_attended_artifact_may_omit_reconcile(self) -> None:
        """The gate is for unattended replay; attended, a person approves each Confirm."""
        data = body()
        data["steps"][-1]["reconcile"] = None
        data["policy"]["unattended"] = False
        assert approval_gates(Capability.model_validate(data)) == []
