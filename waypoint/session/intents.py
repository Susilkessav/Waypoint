"""Write-ahead intent records for irreversible actions (R-REC-4).

Before the engine dispatches an irreversible action it writes - and, because the
store runs ``synchronous=FULL``, flushes - a row saying it is about to. The row moves
``dispatching -> dispatched -> observed`` as the action returns and its checkpoint
confirms. An operation left at ``dispatching`` or ``dispatched`` means nobody knows
whether it happened; until it is reconciled, nothing may run it again - not this
process after a handoff, and not a fresh process after a crash.

``reconciled`` always records who decided and what they found: an operator through
``waypoint intervene reconcile`` (``completed`` / ``not_completed``), or the replay
itself when a handoff's return ladder moved past the step on verified state
(``confirmed_after_handoff``). The automatic probe of R-REC-5 arrives in milestone B3.

Operations are keyed by capability and a hash of the inputs. The hash lives only in
this local state file, never in evidence.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from waypoint.session.store import StateStore

IntentState = Literal["dispatching", "dispatched", "observed", "reconciled"]
UNRESOLVED: tuple[IntentState, ...] = ("dispatching", "dispatched")
Resolution = Literal["completed", "not_completed", "confirmed_after_handoff"]
_FORWARD: dict[str, tuple[str, ...]] = {
    "dispatching": ("dispatched", "reconciled"),
    "dispatched": ("observed", "reconciled"),
    "observed": (),
    "reconciled": (),
}


class IntentError(Exception):
    pass


@dataclass(frozen=True)
class Intent:
    id: str
    run_id: str
    capability_id: str
    version: str
    step: str
    inputs_hash: str
    nonce: str
    state: IntentState
    attempted_at: float
    """When the action was about to be sent (or handed to a person). Written once: it is
    what reconciliation dates records against, so no transition may move it."""
    updated_at: float | None = None
    resolved_by: str | None = None
    resolution: Resolution | None = None
    attempted_at_trusted: bool = True
    """False for rows migrated from an older state file, whose time may be a transition's."""
    scope: str | None = None
    contract_hash: str | None = None


def inputs_hash(capability_id: str, inputs: Mapping[str, str]) -> str:
    body = json.dumps({"capability": capability_id, "inputs": dict(sorted(inputs.items()))},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class IntentStore:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def begin(self, *, run_id: str, capability_id: str, version: str, step: str,
              inputs_digest: str, scope: str | None = None,
              contract_hash: str | None = None) -> Intent:
        now = self.store.clock()
        intent = Intent(uuid.uuid4().hex[:12], run_id, capability_id, version, step,
                        inputs_digest, uuid.uuid4().hex, "dispatching", now, now,
                        scope=scope, contract_hash=contract_hash)
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO intents (id, run_id, capability_id, version, step, inputs_hash, "
                "nonce, state, attempted_at, attempted_at_trusted, updated_at, scope, "
                "contract_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (intent.id, intent.run_id, intent.capability_id, intent.version, intent.step,
                 intent.inputs_hash, intent.nonce, intent.state, now, now, scope, contract_hash))
        return intent

    def advance(self, intent_id: str, to: IntentState, *, by: str | None = None,
                resolution: Resolution | None = None) -> None:
        if (to == "reconciled") != (by is not None and resolution is not None):
            raise IntentError("reconciling, and only reconciling, records who and what was found")
        with self.store.transaction() as db:
            row = db.execute("SELECT state FROM intents WHERE id = ?", (intent_id,)).fetchone()
            if row is None:
                raise IntentError(f"no intent {intent_id!r}")
            if to not in _FORWARD[row["state"]]:
                raise IntentError(f"intent {intent_id} is {row['state']}; it cannot become {to}")
            # attempted_at is deliberately absent: a slow transition must not make a committed
            # operation look as if it was attempted after its own record was created.
            db.execute("UPDATE intents SET state = ?, updated_at = ?, resolved_by = ?, "
                       "resolution = ? WHERE id = ?",
                       (to, self.store.clock(), by, resolution, intent_id))

    def get(self, intent_id: str) -> Intent:
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM intents WHERE id = ?", (intent_id,)).fetchone()
        if row is None:
            raise IntentError(f"no intent {intent_id!r}")
        return _intent(row)

    def list(self, states: Sequence[IntentState] = UNRESOLVED) -> builtins.list[Intent]:
        marks = ", ".join("?" for _ in states)
        with self.store.connect() as db:
            rows = db.execute(f"SELECT * FROM intents WHERE state IN ({marks}) "
                              "ORDER BY attempted_at",
                              tuple(states)).fetchall()
        return [_intent(r) for r in rows]

    def unresolved(self, capability_id: str, inputs_digest: str, *,
                   scope: str | None = None) -> builtins.list[Intent]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM intents WHERE capability_id = ? AND inputs_hash = ? "
                "AND state IN (?, ?) ORDER BY attempted_at",
                (capability_id, inputs_digest, *UNRESOLVED),
            ).fetchall()
        return [_intent(r) for r in rows if scope is None or r["scope"] in (None, scope)]


def _intent(row: sqlite3.Row) -> Intent:
    data = dict(row)
    data["attempted_at_trusted"] = bool(data["attempted_at_trusted"])
    return Intent(**data)
