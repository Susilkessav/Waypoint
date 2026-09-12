"""Deterministic failure injection (PLAN.md section 7.2).

An injection is set with `?inject=<name>` on any console URL and is then held in
the session until `?inject=none`. Session scope rather than per-request query
string matters: the console is a frameset, so a run navigates several URLs the
harness never writes by hand, and a query parameter would be lost at the first
frame load. It also gives `waypoint replay --inject X` one place to set state.

Milestone A2 implemented the injections A4 and A7 needed; B1 adds the rest, which
belong to the sub-account flow: a slow page, a hard 500, an interstitial, an expired
session, a validation refusal, a renamed control, a commit whose response is dropped,
and a stale confirmation page.

Injections that would otherwise make a run impossible - the interstitial and the expired
session - fire **once** per session. An obstacle that never clears is not a recoverable
failure, it is a wall, and a remedy could never be shown to work against it.
"""

from __future__ import annotations

from collections.abc import Iterable

from flask import session

from target_app.data import RESTRICTED_MEMBER_ID, Member, branch_roster

SESSION_KEY = "wp_inject"
ONCE_KEY = "wp_inject_fired"
CLEAR_VALUE = "none"

SUPPORTED = frozenset(
    {
        # A2
        "ambiguous",  # duplicates a control so two elements match  -> escalate
        "row_missing",  # target row absent, no banner              -> tests R-LOC-5
        "reorder",  # rows re-sorted, positions shift               -> tests R-LOC-5
        "wrong_member",  # detail shows a different member          -> tests R-RESUME-5
        # B1
        "slow",  # the page takes seconds to settle                 -> recoverable
        "500",  # the server fails outright                         -> hard failure
        "interstitial",  # a notice stands in the way, once         -> recoverable
        "session",  # the sign-on lapses mid-flow, once             -> recoverable
        "validation",  # the application refuses the input          -> business outcome
        "drift",  # the submit control is renamed                   -> tier degradation
        "commit_then_drop",  # committed, response lost             -> tests R-REC-3
        "stale_confirmation",  # an older, unrelated confirmation   -> tests R-REC-2
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
        session.pop(ONCE_KEY, None)
    elif value in SUPPORTED and session.get(SESSION_KEY) != value:
        session[SESSION_KEY] = value
        session.pop(ONCE_KEY, None)  # a re-armed injection has not fired yet


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


def _fires_once(name: str) -> bool:
    """True the first time `name` is active in this session, False after that."""
    if not is_active(name):
        return False
    fired = set(session.get(ONCE_KEY, []))
    if name in fired:
        return False
    session[ONCE_KEY] = sorted(fired | {name})
    return True


def interstitial_due() -> bool:
    """A notice page stands between the operator and the screen they asked for."""
    return _fires_once("interstitial")


def session_lapses_now() -> bool:
    """The sign-on expires mid-flow; the next request lands on the login screen."""
    return _fires_once("session")


def delay_seconds() -> float:
    """How long the sub-account screens take to answer. Slow is not broken."""
    return 2.0 if is_active("slow") else 0.0


def fails_hard() -> bool:
    return is_active("500")


def refuses_input() -> bool:
    """The application rejects the request itself - a business outcome, not a fault."""
    return is_active("validation")


def submit_label(default: str) -> str:
    """Under `drift` the control keeps its id and loses its name: tier 1 stops matching."""
    return "Continue" if is_active("drift") else default


def drops_response_after_commit() -> bool:
    """The commit lands and the answer never arrives - indistinguishable, from outside,
    from a request that never arrived at all. That is the whole point (R-REC-3)."""
    return is_active("commit_then_drop")


def serves_stale_confirmation() -> bool:
    return is_active("stale_confirmation")


def authorized(member_id: str) -> bool:
    """One member nobody may service: a refusal that is an answer, not a failure."""
    return member_id != RESTRICTED_MEMBER_ID
