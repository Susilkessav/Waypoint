"""Intervention requests: what a human is asked to do, and the state of that ask.

An intervention carries enough to act on without a transcript (PLAN.md 3.6): which
capability and version, which step and why it stopped, what was expected against
what was observed, and where the redacted screenshot and snapshot are. Taking and
returning control change the intervention *and* the lease in one transaction, so
the two can never disagree about who is in control.

    open --take--> taken --return--> returned --(replay re-acquires)--> resolved
      \\-------------abort------------> aborted          (lease expiry --> expired)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from waypoint.session.lease import LeaseError, LeaseStore
from waypoint.session.store import StateStore

Status = Literal["open", "taken", "returned", "aborted", "expired", "resolved"]
ACTIVE: tuple[Status, ...] = ("open", "taken", "returned")


class InterventionError(Exception):
    pass


@dataclass(frozen=True)
class Intervention:
    id: str
    session_id: str
    run_id: str
    capability_id: str
    version: str
    step: str | None
    intent: str | None
    reason_code: str
    message: str
    expected_signature: str | None
    observed_signatures: tuple[str, ...]
    screenshot: str | None
    snapshot: str | None
    evidence_dir: str | None
    status: Status
    operator: str | None
    operator_token: str | None
    created_at: float
    updated_at: float


def _row(row: sqlite3.Row) -> Intervention:
    data = dict(row)
    data["observed_signatures"] = tuple(json.loads(data["observed_signatures"] or "[]"))
    return Intervention(**data)


class InterventionStore:
    def __init__(self, store: StateStore, leases: LeaseStore | None = None) -> None:
        self.store = store
        self.leases = leases or LeaseStore(store)

    def open(self, *, session_id: str, run_id: str, capability_id: str, version: str,
             reason_code: str, message: str, step: str | None = None,
             intent: str | None = None, expected_signature: str | None = None,
             observed_signatures: Sequence[str] = (), screenshot: str | None = None,
             snapshot: str | None = None, evidence_dir: str | None = None) -> Intervention:
        now = self.store.clock()
        iid = uuid.uuid4().hex[:10]
        with self.store.transaction() as db:
            db.execute(
                """INSERT INTO interventions (id, session_id, run_id, capability_id, version,
                       step, intent, reason_code, message, expected_signature,
                       observed_signatures, screenshot, snapshot, evidence_dir, status,
                       created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
                (iid, session_id, run_id, capability_id, version, step, intent, reason_code,
                 message, expected_signature, json.dumps(list(observed_signatures)),
                 screenshot, snapshot, evidence_dir, now, now),
            )
        return self.get(iid)

    def get(self, intervention_id: str) -> Intervention:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM interventions WHERE id = ?",
                             (intervention_id,)).fetchone()
        if row is None:
            raise InterventionError(f"no intervention {intervention_id!r}")
        return _row(row)

    def list(self, statuses: Sequence[Status] = ACTIVE) -> list[Intervention]:
        marks = ", ".join("?" for _ in statuses)
        with self.store.connect() as db:
            rows = db.execute(f"SELECT * FROM interventions WHERE status IN ({marks}) "
                              "ORDER BY created_at", tuple(statuses)).fetchall()
        return [_row(r) for r in rows]

    def _move(self, db: sqlite3.Connection, iid: str, frm: Sequence[Status], to: Status,
              **fields: object) -> None:
        row = db.execute("SELECT status FROM interventions WHERE id = ?", (iid,)).fetchone()
        if row is None:
            raise InterventionError(f"no intervention {iid!r}")
        if row["status"] not in frm:
            wanted = " or ".join(frm)
            raise InterventionError(f"intervention {iid} is {row['status']}, not {wanted}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE interventions SET status = ?, updated_at = ?"
                   f"{', ' + sets if sets else ''} WHERE id = ?",
                   (to, self.store.clock(), *fields.values(), iid))

    def take(self, intervention_id: str, operator: str, ttl_s: float) -> Intervention:
        """The operator takes control: lease NONE -> HUMAN, under a fresh operator token."""
        token = uuid.uuid4().hex
        with self.store.transaction() as db:
            iv = _row(db.execute("SELECT * FROM interventions WHERE id = ?",
                                 (intervention_id,)).fetchone() or _missing(intervention_id))
            try:
                self.leases.transfer_in(db, iv.session_id, "HUMAN", token, ttl_s,
                                        require=("NONE",))
            except LeaseError as exc:
                raise InterventionError(f"cannot take control: {exc}") from None
            self._move(db, intervention_id, ("open",), "taken", operator=operator,
                       operator_token=token)
        return self.get(intervention_id)

    def give_back(self, intervention_id: str) -> Intervention:
        """The operator returns control: lease HUMAN -> NONE; the replay re-acquires it."""
        with self.store.transaction() as db:
            iv = _row(db.execute("SELECT * FROM interventions WHERE id = ?",
                                 (intervention_id,)).fetchone() or _missing(intervention_id))
            if iv.status != "taken" or iv.operator_token is None:
                raise InterventionError(f"intervention {iv.id} is {iv.status}, not taken")
            try:
                self.leases.transfer_in(db, iv.session_id, "NONE", iv.operator_token, 0,
                                        require=("HUMAN", "NONE"),
                                        require_owner=iv.operator_token)
            except LeaseError as exc:
                raise InterventionError(f"cannot return control: {exc}") from None
            self._move(db, intervention_id, ("taken",), "returned")
        return self.get(intervention_id)

    def abort(self, intervention_id: str) -> Intervention:
        """End the run. Control the operator holds is released in the same transaction."""
        with self.store.transaction() as db:
            iv = _row(db.execute("SELECT * FROM interventions WHERE id = ?",
                                 (intervention_id,)).fetchone() or _missing(intervention_id))
            self._move(db, intervention_id, ("open", "taken"), "aborted")
            if iv.status == "taken" and iv.operator_token is not None:
                try:
                    self.leases.transfer_in(db, iv.session_id, "NONE", iv.operator_token, 0,
                                            require=("HUMAN",), require_owner=iv.operator_token)
                except LeaseError:
                    pass  # already lapsed or moved on: nothing of the operator's to release
        return self.get(intervention_id)

    def close(self, intervention_id: str, status: Literal["resolved", "expired"]) -> None:
        with self.store.transaction() as db:
            self._move(db, intervention_id, ("open", "taken", "returned"), status)


def _missing(intervention_id: str) -> sqlite3.Row:
    raise InterventionError(f"no intervention {intervention_id!r}")
