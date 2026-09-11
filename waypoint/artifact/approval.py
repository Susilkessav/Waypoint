"""Draft to approved, bound to content (PLAN.md R-PKG-2, R-PKG-3).

Approval is a human decision about *these exact contents*. It records a content
hash; ``approval_status()`` recomputes that hash and every gate each time it is
asked, so an artifact edited after approval - by hand, by a tool, by a merge - is
simply not approved any more. Nothing here trusts a stored flag on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from waypoint.artifact.schema import Capability, approval_gates, content_hash


class ApprovalBlocked(Exception):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("approval blocked:\n  - " + "\n  - ".join(reasons))


@dataclass(frozen=True)
class ApprovalStatus:
    approved: bool
    reasons: tuple[str, ...]


def approval_status(cap: Capability, variant: str = "base") -> ApprovalStatus:
    """Approved only if flagged approved, unchanged since, and still passing every gate."""
    reasons: list[str] = []
    if cap.provenance.approval.get(variant) != "approved":
        reasons.append(f"variant {variant!r} is not approved")
    elif cap.provenance.approval_hash.get(variant) != content_hash(cap, variant):
        reasons.append("content changed since approval: hash mismatch (R-PKG-2)")
    reasons += approval_gates(cap)
    return ApprovalStatus(approved=not reasons, reasons=tuple(reasons))


def approve(
    cap: Capability,
    *,
    approver: str,
    note: str | None = None,
    variant: str = "base",
    now: datetime | None = None,
) -> Capability:
    """Return an approved copy, or raise ApprovalBlocked listing every open gate."""
    gates = approval_gates(cap)
    if gates:
        raise ApprovalBlocked(gates)
    prov = cap.provenance
    stamped = prov.model_copy(
        update={
            "approval": {**prov.approval, variant: "approved"},
            "approval_hash": {**prov.approval_hash, variant: content_hash(cap, variant)},
            "approved_by": approver,
            "approved_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
            "approval_note": note,
        }
    )
    return cap.model_copy(update={"provenance": stamped})
