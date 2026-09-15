"""The three-way verdict on an irreversible step (R-REC-1..5).

"Did it happen?" has three answers, never two, and the rules for reaching each are the
point of the module:

* **completed** needs positive evidence bound to *this* operation. Screen signatures can
  say "this member has a Money Market account"; only the record's own values can say
  "...opened with 250.00, after we tried". So when an artifact declares ``records``, each
  candidate record is judged on its raw values: it must show every operation input the
  artifact names (the deposit, not just the type), it must have been created at or after
  the attempt, and it must be the only one that does. An earlier account that merely
  shares the member and type is somebody else's operation (R-REC-2).
* **not_completed** needs positive evidence of absence: the authoritative list for these
  inputs is visibly present and holds no record of this kind, or every record showing
  these inputs is readably older than the attempt (R-REC-3).
* **unknown** is everything else: contradictory screens, a record whose values or creation
  time cannot be read (a blank date establishes nothing about age), a matching record whose
  displayed time cannot be placed on either side of the attempt - within the clock-skew
  window, or inside the same displayed second when the application shows only seconds -
  more than one record that could be this operation, or an attempt time that is itself not
  trustworthy. Unknown escalates; nothing executes on it.

Pure: signatures, a sanitized snapshot, the record values the engine read through the raw
layer, the raw inputs, and the moment of the attempt. Nothing here is logged.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from waypoint.artifact.schema import Reconcile, Records
from waypoint.signatures.recognizers import Signature
from waypoint.surface.ports import UISnapshot

Verdict = Literal["completed", "not_completed", "unknown"]

_STAMP = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d{1,6})?(?:\s*(?:UTC|Z|\+00:00))?$")
_AMOUNT = re.compile(r"^\$?\s*(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)$")


@dataclass(frozen=True)
class Record:
    """One on-screen record that could be this operation: column -> raw text (None if unread)."""

    values: Mapping[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Reconciliation:
    verdict: Verdict
    reason: str
    record: Record | None = None
    """The one record adopted, when completed was decided record by record."""


@dataclass(frozen=True)
class Stamp:
    """A displayed creation time is an interval, not an instant: ``start`` plus the finest
    unit the application shows. "12:00:00" means some moment in [12:00:00, 12:00:01)."""

    start: datetime
    width: timedelta


def parse_stamp(raw: str | None) -> Stamp | None:
    """A UTC creation time at the precision shown, or None - including for a blank or
    placeholder date, which establishes nothing about when the record was made."""
    found = _STAMP.match((raw or "").strip())
    if found is None:
        return None
    fraction = found.group(3) or ""
    start = datetime.fromisoformat(f"{found.group(1)}T{found.group(2)}{fraction}")
    width = timedelta(seconds=10 ** -(len(fraction) - 1)) if fraction else timedelta(seconds=1)
    return Stamp(start.replace(tzinfo=UTC), width)


def same_value(shown: str | None, expected: str) -> bool | None:
    """Whether a record shows an input's value; None when the record's value is unreadable.

    Amounts compare as amounts ("$250.00" shows "250.00"); anything else compares exactly.
    """
    if shown is None:
        return None
    a, b = _amount(shown), _amount(expected)
    if a is not None and b is not None:
        return a == b
    return shown.strip() == expected.strip()


def _amount(text: str) -> Decimal | None:
    found = _AMOUNT.match(text.strip())
    if found is None:
        return None
    try:
        return Decimal(found.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def decide(
    reconcile: Reconcile,
    signatures: Mapping[str, Signature],
    snap: UISnapshot,
    rendered: Mapping[str, str],
    records: Sequence[Record] | None,
    inputs: Mapping[str, str],
    attempted_at: datetime | None,
) -> Reconciliation:
    """``attempted_at`` is None when the attempt time is not trustworthy (migrated state):
    the verdict may then rest only on evidence that does not depend on it."""
    completed = signatures[reconcile.completed_when].evaluate(snap, rendered)
    absent = signatures[reconcile.not_completed_when].evaluate(snap, rendered)
    if completed and absent:
        return Reconciliation("unknown", "both conditions hold: the evidence contradicts itself")
    if absent:
        return Reconciliation("not_completed",
                              "the authoritative screen for these inputs shows no such record")
    if not completed:
        return Reconciliation("unknown", "the probe screen matches neither condition")
    if reconcile.records is None:
        return Reconciliation("completed", "a record matching these inputs is present")
    return _judge(reconcile.records, records or (), inputs, attempted_at)


def _judge(spec: Records, records: Sequence[Record], inputs: Mapping[str, str],
           attempted_at: datetime | None) -> Reconciliation:
    if not records:
        return Reconciliation("unknown", "records of this kind are shown but none could be read")
    candidates: list[Record] = []
    for record in records:
        matches = [same_value(record.values.get(column), inputs[ref.removeprefix("$inputs.")])
                   for column, ref in spec.fields.items()]
        if any(m is None for m in matches):
            return Reconciliation("unknown", "a record's values could not be read")
        if all(matches):
            candidates.append(record)
    if not candidates:
        return Reconciliation("not_completed",
                              "no record shows this operation's inputs; the others are not ours")
    if attempted_at is None:
        return Reconciliation("unknown", "a record shows these inputs, and without a "
                                         "trustworthy attempt time nothing says whose it is")
    window = attempted_at - timedelta(seconds=spec.skew_s)
    new: list[Record] = []
    for record in candidates:
        stamp = parse_stamp(record.values.get(spec.created))
        if stamp is None:
            return Reconciliation("unknown", "a record showing these inputs has no readable "
                                             "creation time, so its age cannot be established")
        if stamp.start >= attempted_at:
            new.append(record)  # every moment the display allows is after the attempt
        elif stamp.start + stamp.width > window:
            return Reconciliation("unknown", "a record showing these inputs was created too "
                                             "close to the attempt to tell - within the skew "
                                             "window, or in the same displayed moment")
    if len(new) > 1:
        return Reconciliation("unknown", "more than one record could be this operation")
    if new:
        return Reconciliation("completed", "exactly one record shows these inputs, created at "
                                           "or after the attempt", record=new[0])
    return Reconciliation("not_completed",
                          "every record showing these inputs predates the attempt")
