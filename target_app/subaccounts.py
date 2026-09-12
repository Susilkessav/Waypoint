"""Sub-accounts opened during a session - the irreversible half of the fixture (PLAN.md 7.1).

Opening one is the operation Milestone B has to reconcile rather than repeat. Three
properties make it the right fixture:

* it is committed by a **GET** (`/console/subaccount/confirm`), so no "POST means
  mutation" rule catches it;
* it can commit while the caller never sees the answer (`inject=commit_then_drop`),
  which is exactly the state that must read as Unknown rather than NotCompleted;
* the confirmation page is the only evidence it happened, so that page carries the
  member, the type and the time - the three things a reconciliation must bind to
  before adopting it (R-REC-2).

State is **server-side**, as it would be in the real system. Per-session state would
isolate tests more cheaply, but it would also hide the one thing reconciliation exists
for: a fresh process, in a fresh browser, finding out what an earlier one committed. The
test suite resets it through a fixture-only route the automation cannot reach (it is off
the route allowlist).
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MINIMUM_DEPOSIT_CENTS = 2500
TYPES = ("Savings", "Checking", "Money Market")
_AMOUNT = re.compile(r"^\$?\s*(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{2}))?$")


@dataclass(frozen=True)
class SubAccount:
    account_id: str
    member_id: str
    kind: str
    balance_cents: int
    opened_at: str
    confirmation: str

    @property
    def balance(self) -> str:
        return f"${self.balance_cents // 100:,}.{self.balance_cents % 100:02d}"

    @property
    def status(self) -> str:
        return "active"

    @property
    def opened(self) -> str:
        return self.opened_at.replace("T", " ").removesuffix("+00:00") + " UTC"

    def to_dict(self) -> dict[str, str | int]:
        return {
            "account_id": self.account_id, "member_id": self.member_id, "kind": self.kind,
            "balance_cents": self.balance_cents, "opened_at": self.opened_at,
            "confirmation": self.confirmation,
        }


def parse_deposit(raw: str) -> int | None:
    """Cents, or None when the text is not an amount. Accepts '$1,000.00' and '25'."""
    found = _AMOUNT.match((raw or "").strip())
    if found is None:
        return None
    whole = int(found.group(1).replace(",", ""))
    return whole * 100 + int(found.group(2) or 0)


_LOCK = threading.Lock()
_STORE: list[SubAccount] = []


def reset() -> None:
    with _LOCK:
        _STORE.clear()


def _all() -> list[SubAccount]:
    with _LOCK:
        return list(_STORE)


def opened_for(member_id: str) -> list[SubAccount]:
    return [s for s in _all() if s.member_id == member_id]


def record(member_id: str, kind: str, deposit_cents: int) -> SubAccount:
    """Commit one sub-account. Called only from the confirm route - the mutation."""
    now = datetime.now(UTC).replace(microsecond=0)
    serial = uuid.uuid4().hex[:4].upper()
    with _LOCK:
        count = sum(1 for s in _STORE if s.member_id == member_id)
        opened = SubAccount(
            account_id=f"SA-{member_id}-{count + 1:02d}",
            member_id=member_id,
            kind=kind,
            balance_cents=deposit_cents,
            opened_at=now.isoformat(),
            confirmation=f"CN-{member_id}-{serial}",
        )
        _STORE.append(opened)
    return opened


def discard_last(member_id: str) -> SubAccount | None:
    """Delete this member's most recent sub-account. What the unlabelled icon does."""
    with _LOCK:
        index = next(
            (i for i in reversed(range(len(_STORE))) if _STORE[i].member_id == member_id), None
        )
        return None if index is None else _STORE.pop(index)


def stale() -> SubAccount:
    """Somebody else's confirmation, from days ago - what `stale_confirmation` serves.

    Adopting it would mean reporting success for an operation that never ran, which is
    why a reconciliation has to bind the confirmation to this member, this operation and
    this hour (R-REC-2) rather than merely finding a confirmation page.
    """
    when = (datetime.now(UTC) - timedelta(days=3)).replace(microsecond=0)
    return SubAccount(
        account_id="SA-10233-01", member_id="10233", kind="Savings", balance_cents=15000,
        opened_at=when.isoformat(), confirmation="CN-10233-4K2A",
    )
