"""Which step a run last completed, for crash recovery.

An intent (``intents.py``) protects one irreversible action from repeating after a crash;
it says nothing about a safe step, so a process that dies partway through an ordinary flow
leaves nothing to resume from. ``ProgressStore`` closes that gap the same way: a row written
before the browser starts, advanced once per completed step, and finished in the run's own
``finally`` - so a row still at ``status = 'running'`` after the process is gone is the only
signal ``waypoint resume-run`` needs. Nothing here decides a process died; the operator does,
by choosing to run the command.

Like intents, raw inputs are never written here - only their hash. Resuming needs the actual
values again, verified against this hash before anything else happens (R-SENS).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from waypoint.session.store import StateStore

ProgressStatus = Literal["running", "done"]


class ProgressError(Exception):
    pass


@dataclass(frozen=True)
class RunProgress:
    run_id: str
    capability_id: str
    version: str
    variant: str
    content_hash: str
    inputs_hash: str
    base_url: str
    completed_index: int
    status: ProgressStatus
    updated_at: float
    resumed_by: str | None = None
    resumed_from: str | None = None


class ProgressStore:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def start(self, *, run_id: str, capability_id: str, version: str, variant: str,
              content_hash: str, inputs_hash: str, base_url: str) -> None:
        now = self.store.clock()
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO run_progress (run_id, capability_id, version, variant, "
                "content_hash, inputs_hash, base_url, completed_index, status, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, -1, 'running', ?)",
                (run_id, capability_id, version, variant, content_hash, inputs_hash, base_url,
                 now))

    def claim(self, source: str, successor: str) -> None:
        """Atomically retire the source and create its single resumable successor.

        If this process dies immediately after claiming, the successor remains discoverable;
        no second process can execute the same source checkpoint concurrently.
        """
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM run_progress WHERE run_id = ?", (source,)).fetchone()
            if row is None or row["status"] != "running" or row["resumed_by"] is not None:
                raise ProgressError("run is finished or has already been claimed for resumption")
            now = self.store.clock()
            db.execute("UPDATE run_progress SET status = 'done', resumed_by = ?, "
                       "updated_at = ? WHERE run_id = ?", (successor, now, source))
            db.execute(
                "INSERT INTO run_progress (run_id, capability_id, version, variant, "
                "content_hash, inputs_hash, base_url, completed_index, status, updated_at, "
                "resumed_from) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)",
                (successor, row["capability_id"], row["version"], row["variant"],
                 row["content_hash"], row["inputs_hash"], row["base_url"],
                 row["completed_index"], now, source))

    def advance(self, run_id: str, completed_index: int) -> None:
        with self.store.transaction() as db:
            db.execute(
                "UPDATE run_progress SET completed_index = ?, updated_at = ? WHERE run_id = ?",
                (completed_index, self.store.clock(), run_id))

    def finish(self, run_id: str) -> None:
        with self.store.transaction() as db:
            db.execute(
                "UPDATE run_progress SET status = 'done', updated_at = ? WHERE run_id = ?",
                (self.store.clock(), run_id))

    def get(self, run_id: str) -> RunProgress:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM run_progress WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ProgressError(f"no progress recorded for run {run_id!r}")
        return _progress(row)


def _progress(row: sqlite3.Row) -> RunProgress:
    return RunProgress(**dict(row))
