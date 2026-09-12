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
