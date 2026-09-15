"""Who may act on the live session (R-PROC-4, R-PROC-5).

A lease has a holder (AGENT, HUMAN or NONE), an owner token naming the specific
process or operator that holds it, and a generation that increments on *every*
transfer. ``assert_held`` compares a caller's token against the row: a replay that
lost control during a handoff still holds a token from an older generation, so it
cannot act even if the holder label happens to read AGENT again. A controller label
alone cannot stop a stale actor; a generation counter can (T19).

An expired lease counts as released. The paused replay treats a human lease running
out as the end of the run (``escalated / timeout``) - it never silently takes back
control it was not handed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from waypoint.session.store import StateStore

Holder = Literal["AGENT", "HUMAN", "NONE"]


class LeaseError(Exception):
    """A transfer that the current lease state does not allow."""


class LeaseLost(LeaseError):
    """The caller is acting under a lease it no longer holds."""


@dataclass(frozen=True)
class Lease:
    session_id: str
    holder: Holder
    owner_token: str
    generation: int
    expires_at: float

    def effective_holder(self, now: float) -> Holder:
        return "NONE" if self.holder != "NONE" and now >= self.expires_at else self.holder


@dataclass(frozen=True)
class LeaseToken:
    """What a holder carries: proof of *which* grant it is acting under."""

    session_id: str
    owner_token: str
    generation: int


def _row_to_lease(row: sqlite3.Row) -> Lease:
    return Lease(row["session_id"], row["holder"], row["owner_token"], row["generation"],
                 row["expires_at"])


class LeaseStore:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def read(self, session_id: str) -> Lease | None:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM leases WHERE session_id = ?", (session_id,)).fetchone()
        return None if row is None else _row_to_lease(row)

    def transfer_in(
        self,
        db: sqlite3.Connection,
        session_id: str,
        to: Holder,
        owner_token: str,
        ttl_s: float,
        *,
        require: tuple[Holder, ...] | None = None,
        require_owner: str | None = None,
    ) -> Lease:
        """Transfer inside an open transaction, so it can share one with other writes."""
        now = self.store.clock()
        row = db.execute("SELECT * FROM leases WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            if require is not None and "NONE" not in require:
                raise LeaseError(f"session {session_id!r} has no lease")
            generation = 1
        else:
            current = _row_to_lease(row)
            holder = current.effective_holder(now)
            if require is not None and holder not in require:
                raise LeaseError(f"the lease is held by {holder}, not {' or '.join(require)}")
            if require_owner is not None and current.owner_token != require_owner:
                raise LeaseError("the lease belongs to someone else")
            generation = current.generation + 1
        expires = now + ttl_s if to != "NONE" else now
        db.execute(
            """INSERT INTO leases (session_id, holder, owner_token, generation, expires_at,
                                   updated_at) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (session_id) DO UPDATE SET holder = excluded.holder,
                   owner_token = excluded.owner_token, generation = excluded.generation,
                   expires_at = excluded.expires_at, updated_at = excluded.updated_at""",
            (session_id, to, owner_token, generation, expires, now),
        )
        return Lease(session_id, to, owner_token, generation, expires)

    def acquire(self, session_id: str, holder: Holder, owner_token: str, ttl_s: float,
                *, require: tuple[Holder, ...] = ("NONE",)) -> LeaseToken:
        with self.store.transaction() as db:
            lease = self.transfer_in(db, session_id, holder, owner_token, ttl_s, require=require)
        return LeaseToken(session_id, owner_token, lease.generation)

    def release(self, token: LeaseToken) -> Lease:
        """Give the lease up. Only the current holder of this exact generation may."""
        with self.store.transaction() as db:
            self._check_in(db, token)
            return self.transfer_in(db, token.session_id, "NONE", token.owner_token, 0)

    def renew(self, token: LeaseToken, ttl_s: float) -> None:
        with self.store.transaction() as db:
            self._check_in(db, token)
            db.execute("UPDATE leases SET expires_at = ?, updated_at = ? WHERE session_id = ?",
                       (self.store.clock() + ttl_s, self.store.clock(), token.session_id))

    def assert_held(self, token: LeaseToken) -> None:
        with self.store.connect() as db:
            self._check_in(db, token)

    def _check_in(self, db: sqlite3.Connection, token: LeaseToken) -> None:
        row = db.execute("SELECT * FROM leases WHERE session_id = ?",
                         (token.session_id,)).fetchone()
        if row is None:
            raise LeaseLost("this session has no lease")
        lease = _row_to_lease(row)
        if (lease.owner_token, lease.generation) != (token.owner_token, token.generation):
            raise LeaseLost(f"the lease has moved on: generation {lease.generation} is current, "
                            f"this caller holds generation {token.generation}")
        if lease.effective_holder(self.store.clock()) == "NONE":
            raise LeaseLost("the lease was released or has expired")
