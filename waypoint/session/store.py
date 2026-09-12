"""Local state shared by the replay process and the operator CLI (PLAN.md 4.2).

One SQLite file holds the three things two processes must agree on: who may act
(``leases``), what a human has been asked to do (``interventions``), and which
irreversible actions were about to happen (``intents``). The replay process owns the
browser; ``waypoint intervene`` only ever touches this file (R-PROC-1..3).

WAL mode lets the paused replay poll while the CLI writes. Writes use
``BEGIN IMMEDIATE`` so concurrent writers serialize instead of racing, and
``synchronous=FULL`` because an intent record must be on disk *before* the click it
guards (R-REC-4). The file is local operational state - gitignored, never evidence.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB = Path(".waypoint/state.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
    session_id  TEXT PRIMARY KEY,
    holder      TEXT NOT NULL CHECK (holder IN ('AGENT', 'HUMAN', 'NONE')),
    owner_token TEXT NOT NULL,
    generation  INTEGER NOT NULL,
    expires_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS interventions (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    run_id              TEXT NOT NULL,
    capability_id       TEXT NOT NULL,
    version             TEXT NOT NULL,
    step                TEXT,
    intent              TEXT,
    reason_code         TEXT NOT NULL,
    message             TEXT NOT NULL,
    expected_signature  TEXT,
    observed_signatures TEXT NOT NULL DEFAULT '[]',
    screenshot          TEXT,
    snapshot            TEXT,
    evidence_dir        TEXT,
    status              TEXT NOT NULL CHECK (status IN
                            ('open', 'taken', 'returned', 'aborted', 'expired', 'resolved')),
    operator            TEXT,
    operator_token      TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS intents (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    version       TEXT NOT NULL,
    step          TEXT NOT NULL,
    inputs_hash   TEXT NOT NULL,
    nonce         TEXT NOT NULL,
    state         TEXT NOT NULL CHECK (state IN
                      ('dispatching', 'dispatched', 'observed', 'reconciled')),
    at            REAL NOT NULL,
    resolved_by   TEXT,
    resolution    TEXT CHECK (resolution IS NULL OR resolution IN
                      ('completed', 'not_completed', 'confirmed_after_handoff'))
);
CREATE INDEX IF NOT EXISTS intents_by_operation ON intents (capability_id, inputs_hash, state);
"""


class StateStore:
    def __init__(self, path: Path = DEFAULT_DB, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One serialized write: every check and update inside it is atomic."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
