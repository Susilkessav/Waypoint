"""A7 state: leases with generations, interventions, intents. No browser.

T19 - a stale lease generation cannot act after a handoff.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from waypoint.session.escalation import Intervention, InterventionError, InterventionStore
from waypoint.session.intents import IntentError, IntentStore, inputs_hash
from waypoint.session.lease import LeaseError, LeaseLost, LeaseStore
from waypoint.session.store import StateStore


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> StateStore:
    return StateStore(tmp_path / "state.db", clock=clock)


class TestLease:
    def test_t19_a_stale_generation_cannot_act_after_a_handoff(self, store: StateStore) -> None:
        leases = LeaseStore(store)
        agent = leases.acquire("s1", "AGENT", "replay-run", 60)
        leases.assert_held(agent)
        leases.release(agent)
        human = leases.acquire("s1", "HUMAN", "operator", 60)
        with pytest.raises(LeaseLost, match="generation"):
            leases.assert_held(agent)
        leases.assert_held(human)
        leases.release(human)
        again = leases.acquire("s1", "AGENT", "replay-run", 60)  # same owner name, new grant
        assert again.generation > agent.generation
        with pytest.raises(LeaseLost):
            leases.assert_held(agent)  # the old grant stays dead
        leases.assert_held(again)

    def test_every_transfer_bumps_the_generation(self, store: StateStore) -> None:
        leases = LeaseStore(store)
        agent = leases.acquire("s", "AGENT", "a", 60)
        released = leases.release(agent)
        human = leases.acquire("s", "HUMAN", "h", 60)
        assert [agent.generation, released.generation, human.generation] == [1, 2, 3]

    def test_cannot_acquire_while_someone_else_holds(self, store: StateStore) -> None:
        leases = LeaseStore(store)
        leases.acquire("s", "AGENT", "a", 60)
        with pytest.raises(LeaseError, match="held by AGENT"):
            leases.acquire("s", "HUMAN", "h", 60)

    def test_an_expired_lease_counts_as_released(self, store: StateStore, clock: Clock) -> None:
        leases = LeaseStore(store)
        agent = leases.acquire("s", "AGENT", "a", 60)
        clock.now += 61
        with pytest.raises(LeaseLost, match="expired"):
            leases.assert_held(agent)
        leases.acquire("s", "HUMAN", "h", 60)

    def test_renew_extends_only_the_current_grant(self, store: StateStore, clock: Clock) -> None:
        leases = LeaseStore(store)
        agent = leases.acquire("s", "AGENT", "a", 60)
        clock.now += 50
        leases.renew(agent, 60)
        clock.now += 50
        leases.assert_held(agent)
        leases.release(agent)
        with pytest.raises(LeaseLost):
            leases.renew(agent, 60)

    def test_two_processes_share_one_truth(self, tmp_path: Path, clock: Clock) -> None:
        replay = LeaseStore(StateStore(tmp_path / "state.db", clock=clock))
        operator = LeaseStore(StateStore(tmp_path / "state.db", clock=clock))
        token = replay.acquire("s", "AGENT", "a", 60)
        seen = operator.read("s")
        assert seen is not None and (seen.holder, seen.generation) == ("AGENT", token.generation)


def open_request(store: StateStore) -> Intervention:
    return InterventionStore(store).open(
        session_id="s", run_id="r", capability_id="lookup", version="1.0.0",
        reason_code="ambiguous_locator", message="two View links match",
        step="steps[2]", observed_signatures=["member_in_results"],
    )


class TestInterventions:
    def test_take_and_return_move_the_lease_with_the_request(self, store: StateStore) -> None:
        leases = LeaseStore(store)
        agent = leases.acquire("s", "AGENT", "a", 60)
        requests = InterventionStore(store, leases)
        request = open_request(store)
        leases.release(agent)
        taken = requests.take(request.id, "sam", 60)
        lease = leases.read("s")
        assert lease is not None and lease.holder == "HUMAN"
        assert lease.owner_token == taken.operator_token
        requests.give_back(request.id)
        lease = leases.read("s")
        assert lease is not None and lease.holder == "NONE"
        assert requests.get(request.id).status == "returned"

    def test_a_refused_take_moves_nothing(self, store: StateStore) -> None:
        leases = LeaseStore(store)
        leases.acquire("s", "AGENT", "a", 60)
        request = open_request(store)
        with pytest.raises(InterventionError, match="held by AGENT"):
            InterventionStore(store, leases).take(request.id, "sam", 60)
        assert InterventionStore(store).get(request.id).status == "open"
        lease = leases.read("s")
        assert lease is not None and lease.holder == "AGENT"

    def test_cannot_take_twice_or_return_what_was_not_taken(self, store: StateStore) -> None:
        requests = InterventionStore(store)
        request = open_request(store)
        with pytest.raises(InterventionError):
            requests.give_back(request.id)
        requests.take(request.id, "sam", 60)
        with pytest.raises(InterventionError):
            requests.take(request.id, "alex", 60)

    def test_abort_and_listing(self, store: StateStore) -> None:
        requests = InterventionStore(store)
        request = open_request(store)
        assert [r.id for r in requests.list()] == [request.id]
        assert requests.get(request.id).observed_signatures == ("member_in_results",)
        requests.abort(request.id)
        assert requests.list() == []


class TestIntents:
    DIGEST = inputs_hash("open_sub_account", {"member_id": "12345", "account_type": "savings"})

    def begin(self, store: StateStore) -> tuple[IntentStore, str]:
        intents = IntentStore(store)
        intent = intents.begin(run_id="r", capability_id="open_sub_account", version="1.0.0",
                               step="steps[6]", inputs_digest=self.DIGEST)
        return intents, intent.id

    def test_unresolved_until_observed(self, store: StateStore) -> None:
        intents, iid = self.begin(store)
        assert [i.id for i in intents.unresolved("open_sub_account", self.DIGEST)] == [iid]
        intents.advance(iid, "dispatched")
        assert intents.unresolved("open_sub_account", self.DIGEST)
        intents.advance(iid, "observed")
        assert intents.unresolved("open_sub_account", self.DIGEST) == []

    def test_no_backward_transitions(self, store: StateStore) -> None:
        intents, iid = self.begin(store)
        intents.advance(iid, "dispatched")
        with pytest.raises(IntentError):
            intents.advance(iid, "dispatching")

    def test_the_digest_ignores_order_but_not_values(self) -> None:
        assert inputs_hash("c", {"a": "1", "b": "2"}) == inputs_hash("c", {"b": "2", "a": "1"})
        assert inputs_hash("c", {"a": "1"}) != inputs_hash("c", {"a": "2"})


def test_the_store_is_configured_for_durability(store: StateStore) -> None:
    with store.connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
