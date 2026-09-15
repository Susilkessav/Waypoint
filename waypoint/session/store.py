"""Local state shared by the replay process and the operator CLI (R-PROC).

One SQLite file holds what two processes must agree on: who may act (``leases``), what a
human has been asked to do (``interventions``), and which irreversible actions were about
to happen (``intents``). It also keeps the ``runs`` ledger - one row per replay, the
evidence a capability's confidence is computed from (REPORT.md §3). The replay process owns the
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
    attempted_at  REAL NOT NULL,
    attempted_at_trusted INTEGER NOT NULL DEFAULT 0,
    updated_at    REAL NOT NULL,
    resolved_by   TEXT,
    resolution    TEXT CHECK (resolution IS NULL OR resolution IN
                      ('completed', 'not_completed', 'confirmed_after_handoff'))
);
CREATE INDEX IF NOT EXISTS intents_by_operation ON intents (capability_id, inputs_hash, state);
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL,
    version       TEXT NOT NULL,
    variant       TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    status        TEXT NOT NULL,
    code          TEXT,
    outcome       TEXT,
    inputs_hash   TEXT NOT NULL,
    kind          TEXT NOT NULL,
    injected      TEXT,
    duration_ms   INTEGER NOT NULL,
    steps         INTEGER NOT NULL,
    degraded      INTEGER NOT NULL DEFAULT 0,
    recoveries    INTEGER NOT NULL DEFAULT 0,
    handoffs      INTEGER NOT NULL DEFAULT 0,
    assisted      INTEGER NOT NULL DEFAULT 0,
    outputs_hash  TEXT,
    at            REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_by_artifact ON runs (capability_id, version, content_hash, at);
"""


def _migrate(db: sqlite3.Connection) -> None:
    """Bring an older state file up to this schema, in place.

    Intents used to keep one ``at`` column that every transition overwrote, so the time of
    the attempt - what reconciliation compares records against - was lost as soon as the
    intent moved on. It is now ``attempted_at``, written once, beside ``updated_at``.

    A migrated row's ``at`` may be any transition's time, so it is kept but marked untrusted
    (``attempted_at_trusted = 0``, the column default): reconciling such an intent may rely
    only on evidence that does not depend on when the attempt was made.
    """
    columns = {row["name"] for row in db.execute("PRAGMA table_info(intents)")}
    if "at" in columns and "attempted_at" not in columns:
        db.execute("ALTER TABLE intents RENAME COLUMN at TO attempted_at")
    if "updated_at" not in columns:
        db.execute("ALTER TABLE intents ADD COLUMN updated_at REAL")
        db.execute("UPDATE intents SET updated_at = attempted_at WHERE updated_at IS NULL")
    if "attempted_at_trusted" not in columns:
        db.execute("ALTER TABLE intents ADD COLUMN attempted_at_trusted INTEGER NOT NULL "
                   "DEFAULT 0")
    # Nullable scope marks legacy intents as unknown, never as belonging to a guessed tenant.
    additions = {
        "intents": {"scope": "TEXT", "contract_hash": "TEXT"},
        "runs": {"contract_ok": "INTEGER", "contract_reason": "TEXT"},
    }
    for table, fields in additions.items():
        present = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        for name, declaration in fields.items():
            if name not in present:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


class StateStore:
    def __init__(self, path: Path = DEFAULT_DB, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            try:
                _migrate(db)
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

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
