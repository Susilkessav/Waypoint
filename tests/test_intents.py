"""The intent store's operator side: listing, and reconciling with an audit trail."""

from __future__ import annotations

from pathlib import Path

import pytest

from waypoint.session.intents import Intent, IntentError, IntentStore, inputs_hash
from waypoint.session.store import StateStore


def store(tmp_path: Path) -> IntentStore:
    return IntentStore(StateStore(tmp_path / "state.db"))


def begin(intents: IntentStore, step: str = "steps[3]") -> Intent:
    return intents.begin(run_id="r1", capability_id="cap", version="1.0.0", step=step,
                         inputs_digest=inputs_hash("cap", {"member_id": "12345"}))


def test_list_defaults_to_unresolved_and_get_reads_one(tmp_path: Path) -> None:
    intents = store(tmp_path)
    open_one, done = begin(intents), begin(intents, "steps[4]")
    intents.advance(done.id, "dispatched")
    intents.advance(done.id, "observed")
    assert [i.id for i in intents.list()] == [open_one.id]
    assert {i.id for i in intents.list(("dispatching", "observed"))} == {open_one.id, done.id}
    assert intents.get(done.id).state == "observed"
    with pytest.raises(IntentError):
        intents.get("missing")


def test_reconciling_records_who_and_what_and_happens_once(tmp_path: Path) -> None:
    intents = store(tmp_path)
    intent = begin(intents)
    with pytest.raises(IntentError):  # a resolution without an author is refused
        intents.advance(intent.id, "reconciled")
    intents.advance(intent.id, "reconciled", by="op1", resolution="not_completed")
    got = intents.get(intent.id)
    assert (got.state, got.resolved_by, got.resolution) == ("reconciled", "op1", "not_completed")
    assert intents.unresolved("cap", intent.inputs_hash) == []
    with pytest.raises(IntentError):
        intents.advance(intent.id, "reconciled", by="op2", resolution="completed")


def test_only_reconciling_carries_an_author(tmp_path: Path) -> None:
    intents = store(tmp_path)
    intent = begin(intents)
    with pytest.raises(IntentError):
        intents.advance(intent.id, "dispatched", by="op1", resolution="completed")


def test_a_slow_transition_never_moves_the_attempt_time(tmp_path: Path) -> None:
    """P2: reconciliation dates records against the attempt. advance() used to overwrite
    it, so a transition 180 s late made a committed operation look attempted after its own
    record - and read as not completed."""
    clock = [1000.0]
    intents = IntentStore(StateStore(tmp_path / "state.db", clock=lambda: clock[0]))
    intent = begin(intents)
    clock[0] = 1180.0
    intents.advance(intent.id, "dispatched")
    clock[0] = 1300.0
    intents.advance(intent.id, "reconciled", by="replay", resolution="completed")
    got = intents.get(intent.id)
    assert (got.attempted_at, got.updated_at) == (1000.0, 1300.0)
    assert got.attempted_at_trusted is True


def test_an_older_state_file_is_migrated_in_place(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE intents (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, "
                   "capability_id TEXT NOT NULL, version TEXT NOT NULL, step TEXT NOT NULL, "
                   "inputs_hash TEXT NOT NULL, nonce TEXT NOT NULL, state TEXT NOT NULL, "
                   "at REAL NOT NULL, resolved_by TEXT, resolution TEXT)")
        db.execute("INSERT INTO intents VALUES ('i1', 'r', 'cap', '1.0.0', 'steps[3]', 'h', 'n', "
                   "'dispatched', 42.0, NULL, NULL)")
    intents = IntentStore(StateStore(path))
    got = intents.get("i1")
    assert (got.attempted_at, got.updated_at, got.state) == (42.0, 42.0, "dispatched")
    assert got.attempted_at_trusted is False, "a migrated time may be any transition's"
    assert begin(intents).attempted_at_trusted is True
    intents.advance("i1", "observed")
    assert intents.get("i1").attempted_at == 42.0
