"""The three-way verdict and the gates around it (PLAN.md R-REC-1..5, R-PKG-3). No browser.

T12 - neither condition holds -> Unknown, never a guess either way.
Review findings: an earlier account sharing member and type is not this operation; a blank
creation date establishes nothing; an input the reconcile never checks blocks approval.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from waypoint.artifact.schema import Capability, approval_gates, load
from waypoint.replay.reconcile import (
    Reconciliation,
    Record,
    Stamp,
    decide,
    parse_stamp,
    same_value,
)
from waypoint.signatures.recognizers import rendered_inputs
from waypoint.surface.ports import UIElement, UISnapshot

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "capabilities" / "open_sub_account" / "1.0.0.json"
CAP = load(ARTIFACT)
RECONCILE = CAP.steps[-1].reconcile
assert RECONCILE is not None and RECONCILE.records is not None
INPUTS = {"member_id": "12345", "account_type": "Money Market", "initial_deposit": "250.00"}
RENDERED = rendered_inputs(
    INPUTS, {"member_id": "internal", "account_type": "public", "initial_deposit": "internal"})
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def el(role: str, name: str, *anchors: str) -> UIElement:
    return UIElement(f"r-{role}-{name}", role, name, None, True, ("main",), None, anchors,
                     "public", "public")


def grid(*types: str, member: str = "‹$inputs.member_id›", header: bool = True) -> UISnapshot:
    elements = [el("LayoutTableCell", member, "Member ID")]
    if header:
        elements.append(el("columnheader", "Opened"))
    elements += [el("cell", kind, "Type") for kind in types]
    return UISnapshot("http://h/console/member", "M", tuple(elements), "", "h")


def at(delta_s: float) -> str:
    """As the fixture shows it: to the millisecond."""
    return (NOW + timedelta(seconds=delta_s)).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"


def row(opened: str | None, balance: str | None = "$250.00",
        account: str = "SA-12345-01") -> Record:
    return Record({"Opened": opened, "Balance": balance, "Account": account})


def judge(records: list[Record], snap: UISnapshot | None = None,
          attempted: datetime | None = NOW) -> Reconciliation:
    assert RECONCILE is not None
    shown = snap if snap is not None else grid(*["Money Market"] * max(len(records), 1))
    return decide(RECONCILE, CAP.signatures, shown, RENDERED, records, INPUTS, attempted)


class TestWhichRecordIsThisOperation:
    def test_the_one_record_with_these_inputs_made_after_the_attempt_is_adopted(self) -> None:
        result = judge([row(at(3))])
        assert result.verdict == "completed" and result.record == row(at(3))

    def test_an_earlier_account_with_another_deposit_is_not_this_operation(self) -> None:
        """The review finding: a $100 Money Market account, then a failed $250 request.
        Member, type and a two-minute window matched; the operation did not."""
        result = judge([row(at(-30), balance="$100.00")])
        assert result.verdict == "not_completed"

    def test_the_right_record_is_adopted_beside_an_earlier_one(self) -> None:
        earlier, ours = row(at(-30), "$100.00", "SA-12345-01"), row(at(2), "$250.00",
                                                                     "SA-12345-02")
        result = judge([earlier, ours])
        assert result.verdict == "completed" and result.record == ours

    def test_a_record_with_these_inputs_made_just_before_the_attempt_is_unknown(self) -> None:
        """The skew window no longer widens adoption; it marks records that could be either."""
        assert judge([row(at(-30))]).verdict == "unknown"

    def test_two_records_that_could_both_be_this_operation_are_unknown(self) -> None:
        assert judge([row(at(1), account="SA-12345-01"),
                      row(at(4), account="SA-12345-02")]).verdict == "unknown"

    @pytest.mark.parametrize("blank", ["-", "", "   ", None, "n/a"])
    def test_a_blank_creation_date_establishes_nothing(self, blank: str | None) -> None:
        """The review finding: a missing date used to read as "predates the attempt", which
        is NotCompleted - and permission to run the operation again."""
        assert judge([row(blank)]).verdict == "unknown"

    def test_an_unreadable_value_is_unknown(self) -> None:
        assert judge([row(at(3), balance=None)]).verdict == "unknown"

    def test_only_readably_older_records_with_these_inputs_is_not_completed(self) -> None:
        assert judge([row(at(-3 * 86400))]).verdict == "not_completed"

    def test_a_whole_second_stamp_inside_the_attempts_second_is_unknown(self) -> None:
        """Review finding P1: "12:00:00" may mean 12:00:00.100, before an attempt at .700.
        Rounding the attempt down to the second adopted it."""
        result = judge([row("2026-09-12 12:00:00 UTC")],
                       attempted=NOW + timedelta(milliseconds=700))
        assert result.verdict == "unknown"

    def test_a_whole_second_stamp_wholly_after_the_attempt_is_completed(self) -> None:
        result = judge([row("2026-09-12 12:00:01 UTC")],
                       attempted=NOW + timedelta(milliseconds=700))
        assert result.verdict == "completed"

    def test_finer_stamps_place_records_on_either_side_of_the_attempt(self) -> None:
        attempted = NOW + timedelta(milliseconds=700)
        assert judge([row(at(0.9))], attempted=attempted).verdict == "completed"
        assert judge([row(at(0.1))], attempted=attempted).verdict == "unknown"

    def test_an_untrusted_attempt_time_adopts_nothing(self) -> None:
        """Review finding P2: a migrated intent's time may be any transition's. Evidence
        that needs it is Unknown; evidence that does not still decides."""
        assert judge([row(at(3))], attempted=None).verdict == "unknown"
        assert judge([row(at(3), balance="$100.00")], attempted=None).verdict == "not_completed"
        assert judge([], snap=grid("Savings"), attempted=None).verdict == "not_completed"

    def test_screens_shown_but_unreadable_rows_are_unknown(self) -> None:
        assert judge([], snap=grid("Money Market")).verdict == "unknown"


class TestScreens:
    def test_a_visible_grid_without_this_type_is_not_completed(self) -> None:
        assert judge([], snap=grid("Savings", "Checking")).verdict == "not_completed"

    def test_another_members_grid_proves_nothing(self) -> None:
        other = grid("Money Market", member="‹redacted:5 chars›")
        assert judge([row(at(3))], snap=other).verdict == "unknown"

    def test_t12_a_screen_matching_neither_condition_is_unknown(self) -> None:
        assert judge([], snap=grid(header=False)).verdict == "unknown"
        search_form = UISnapshot("http://h/console/content", "Search",
                                 (el("textbox", "", "Member ID"),), "", "h")
        assert judge([], snap=search_form).verdict == "unknown"


class TestValues:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("2026-09-12 01:02:03 UTC",
         Stamp(datetime(2026, 9, 12, 1, 2, 3, tzinfo=UTC), timedelta(seconds=1))),
        ("2026-09-12T01:02:03Z",
         Stamp(datetime(2026, 9, 12, 1, 2, 3, tzinfo=UTC), timedelta(seconds=1))),
        ("2026-09-12 01:02:03.456 UTC",
         Stamp(datetime(2026, 9, 12, 1, 2, 3, 456000, tzinfo=UTC), timedelta(milliseconds=1))),
        ("-", None), ("", None), (None, None), ("yesterday", None), ("12/09/2026", None),
    ])
    def test_a_displayed_time_is_an_interval_at_its_precision(self, raw: str | None,
                                                               expected: Any) -> None:
        assert parse_stamp(raw) == expected

    @pytest.mark.parametrize(("shown", "expected", "same"), [
        ("$250.00", "250.00", True), ("$1,250.00", "1250.00", True), ("250", "250.00", True),
        ("$100.00", "250.00", False), ("Money Market", "Money Market", True),
        ("Savings", "Money Market", False), (None, "250.00", None),
    ])
    def test_amounts_compare_as_amounts(self, shown: str | None, expected: str,
                                        same: bool | None) -> None:
        assert same_value(shown, expected) is same


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

    def test_a_reconcile_that_never_checks_an_input_cannot_be_approved(self) -> None:
        """The review finding, made structural: without the deposit, a $100 account would
        be adopted for a $250 request."""
        data = body()
        data["steps"][-1]["reconcile"]["records"]["fields"] = {}
        gates = approval_gates(Capability.model_validate(data))
        assert any("never checks ['initial_deposit']" in g for g in gates)

    def test_adopting_outputs_needs_row_level_records(self) -> None:
        data = body()
        data["steps"][-1]["reconcile"]["records"] = None
        gates = approval_gates(Capability.model_validate(data))
        assert any("adopting outputs needs records" in g for g in gates)

    def test_an_adopted_run_must_be_able_to_return_every_output(self) -> None:
        data = body()
        data["steps"][-1]["reconcile"]["records"]["outputs"] = {}
        gates = approval_gates(Capability.model_validate(data))
        assert any("could not return ['account_id']" in g for g in gates)

    def test_record_fields_must_name_inputs(self) -> None:
        data = body()
        data["steps"][-1]["reconcile"]["records"]["fields"] = {"Balance": "250.00"}
        with pytest.raises(ValidationError, match=r"must be \$inputs"):
            Capability.model_validate(data)

    def test_an_attended_artifact_may_omit_reconcile(self) -> None:
        """The gate is for unattended replay; attended, a person approves each Confirm."""
        data = body()
        data["steps"][-1]["reconcile"] = None
        data["policy"]["unattended"] = False
        assert approval_gates(Capability.model_validate(data)) == []
