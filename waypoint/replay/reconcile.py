"""The three-way verdict on an irreversible step (PLAN.md R-REC-1..5).

"Did it happen?" has three answers, never two, and the rules for reaching each are the
point of the module:

* **completed** needs positive evidence *bound to this operation*: ``completed_when``
  asserts the identity inputs (this member, this account type), and - when the artifact
  declares ``recency`` - some matching record was created at or after the attempt. A
  confirmation belonging to someone else, or to an older identical operation, is not
  ours to adopt (R-REC-2).
* **not_completed** needs positive evidence of absence: the authoritative screen for
  these inputs is visibly present and shows no such record, or shows only records older
  than the attempt. A missing confirmation page is *not* that - a server that committed
  and then lost the response looks exactly like one that never received the request
  (R-REC-3).
* **unknown** is everything else, including contradictory evidence and times that
  cannot be read. Unknown escalates; nothing executes on it.

Pure: signatures, a sanitized snapshot, the creation times the engine read through the
raw layer, and the moment of the attempt. The engine does the navigating and reading.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from waypoint.artifact.schema import Reconcile
from waypoint.signatures.recognizers import Signature
from waypoint.surface.ports import UISnapshot

Verdict = Literal["completed", "not_completed", "unknown"]


class _Predates:
    """A record the application shows with no creation time: it predates its own records."""

    def __repr__(self) -> str:
        return "PREDATES"


PREDATES: Final = _Predates()
_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\s*(?:UTC|Z|\+00:00))?$")
_NO_TIME = frozenset({"", "-", "—", "n/a", "N/A"})


@dataclass(frozen=True)
class Reconciliation:
    verdict: Verdict
    reason: str


def parse_time(raw: str) -> datetime | _Predates | None:
    """A UTC creation time; PREDATES for an explicit "no time recorded"; None if unreadable."""
    text = raw.strip()
    if text in _NO_TIME:
        return PREDATES
    found = _STAMP.match(text)
    if found is None:
        return None
    return datetime.fromisoformat(f"{found.group(1)}T{found.group(2)}").replace(tzinfo=UTC)


def decide(
    reconcile: Reconcile,
    signatures: Mapping[str, Signature],
    snap: UISnapshot,
    rendered: Mapping[str, str],
    times: Sequence[datetime | _Predates | None] | None,
    attempted_at: datetime,
) -> Reconciliation:
    completed = signatures[reconcile.completed_when].evaluate(snap, rendered)
    absent = signatures[reconcile.not_completed_when].evaluate(snap, rendered)
    if completed and absent:
        return Reconciliation("unknown", "both conditions hold: the evidence contradicts itself")
    if absent:
        return Reconciliation("not_completed",
                              "the authoritative screen for these inputs shows no such record")
    if not completed:
        return Reconciliation("unknown", "the probe screen matches neither condition")
    if reconcile.recency is None:
        return Reconciliation("completed", "a record matching these inputs is present")
    if not times:
        return Reconciliation("unknown", "no creation time was found for the matching records")
    floor = attempted_at - timedelta(seconds=reconcile.recency.skew_s)
    readable = [t for t in times if isinstance(t, datetime)]
    if any(t >= floor for t in readable):
        return Reconciliation("completed",
                              "a record matching these inputs was created at or after the attempt")
    if any(t is None for t in times):
        return Reconciliation("unknown", "a matching record's creation time could not be read")
    return Reconciliation("not_completed",
                          "every record matching these inputs predates the attempt")
