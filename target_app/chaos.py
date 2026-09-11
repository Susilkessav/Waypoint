"""Deterministic failure injection (PLAN.md section 7.2).

An injection is set with `?inject=<name>` on any console URL and is then held in
the session until `?inject=none`. Session scope rather than per-request query
string matters: the console is a frameset, so a run navigates several URLs the
harness never writes by hand, and a query parameter would be lost at the first
frame load. It also gives `waypoint replay --inject X` one place to set state.

Milestone A2 implements the three injections that A4 and A7 need test fixtures
for. The remaining six arrive in B1.
"""

from __future__ import annotations

from collections.abc import Iterable

from flask import session

from target_app.data import Member, branch_roster

SESSION_KEY = "wp_inject"
CLEAR_VALUE = "none"

#: Implemented in A2. B1 adds: slow, 500, interstitial, session, validation,
#: drift, commit_then_drop, stale_confirmation.
SUPPORTED = frozenset(
    {
        "ambiguous",  # duplicates a control so two elements match  -> escalate
        "row_missing",  # target row absent, no banner              -> tests R-LOC-5
        "reorder",  # rows re-sorted, positions shift               -> tests R-LOC-5
        "wrong_member",  # detail shows a different member          -> tests R-RESUME-5
    }
)


def absorb_query_param(raw: str | None) -> None:
    """Record `?inject=` into the session. Unknown names are ignored, not errors.

    Ignoring unknown names keeps a typo in a demo command from masquerading as a
    working injection, while leaving B1's additions forward-compatible.
    """
    if raw is None:
        return
    value = raw.strip().lower()
    if value == CLEAR_VALUE:
        session.pop(SESSION_KEY, None)
    elif value in SUPPORTED:
        session[SESSION_KEY] = value


def active() -> str | None:
    return session.get(SESSION_KEY)


def is_active(name: str) -> bool:
    return active() == name


def apply_to_roster(rows: Iterable[Member], searched_member_id: str) -> list[Member]:
    """Transform the results grid according to the active injection."""
    result = list(rows)
    if is_active("row_missing"):
        # The searched member vanishes, and NO "no records" banner is shown. A
        # positional locator would then select whoever inherited the old row.
        result = [m for m in result if m.member_id != searched_member_id]
    elif is_active("reorder"):
        result.reverse()
    return result


def duplicates_view_link(member: Member, searched_member_id: str) -> bool:
    """True when this row should render two identical View links.

    Makes the anchored locator match more than one element, which must resolve
    to Ambiguous and escalate rather than pick one (PLAN.md R-LOC-2).
    """
    return is_active("ambiguous") and member.member_id == searched_member_id


def displayed_member_id(requested: str) -> str:
    """The member the detail page actually renders.

    Under ``wrong_member`` it is the next member of the same branch: the right screen
    for the wrong person. A checkpoint asking only "is this a Member Profile?" would
    pass; one that asserts *which* member must not (PLAN.md R-RESUME-5, T7).
    """
    if not is_active("wrong_member"):
        return requested
    roster = [m.member_id for m in branch_roster(requested)]
    if requested not in roster or len(roster) < 2:
        return requested
    return roster[(roster.index(requested) + 1) % len(roster)]
