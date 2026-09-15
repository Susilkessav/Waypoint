"""The operator console is a view over the same rows the CLI works on - no extra authority.

Each action must land the store in the state the equivalent `waypoint intervene` command
would, and the console must never imply it can drive the browser (R-PROC-3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from waypoint.operator.console import create_console
from waypoint.session.escalation import Intervention, InterventionStore
from waypoint.session.intents import IntentStore
from waypoint.session.lease import LeaseStore
from waypoint.session.store import StateStore


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.fixture
def store(db: Path) -> StateStore:
    return StateStore(db)


@pytest.fixture
def client(db: Path):
    app = create_console(db)
    app.config.update(TESTING=True)
    return app.test_client()


def post(client, path, **kw):
    client.get("/")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    data = {**kw.pop("data", {}), "csrf_token": token}
    return client.post(path, data=data, **kw)


def escalate(store: StateStore, **kw) -> Intervention:
    return InterventionStore(store).open(
        session_id=kw.get("session_id", "sess-1"),
        run_id="run-1",
        capability_id="lookup_member_balance",
        version="1.2.0",
        step="steps[2]",
        reason_code="ambiguous_target",
        message="two controls match the recorded locator",
    )


def test_the_dashboard_lists_an_open_intervention(store, client) -> None:
    iv = escalate(store)
    page = client.get("/").get_data(as_text=True)
    assert iv.id in page
    assert "ambiguous_target" in page
    assert "lookup_member_balance" in page


def test_the_dashboard_says_it_cannot_drive_the_browser(store, client) -> None:
    """R-PROC-3 is not a footnote: the page a person acts from has to say it."""
    page = client.get("/").get_data(as_text=True)
    assert "R-PROC-3" in page
    assert "no remote-control channel" in page


def test_an_empty_queue_reads_as_empty(client) -> None:
    page = client.get("/").get_data(as_text=True)
    assert "Nothing is waiting for a person." in page


def test_the_detail_page_shows_why_the_run_stopped(store, client) -> None:
    iv = escalate(store)
    page = client.get(f"/interventions/{iv.id}").get_data(as_text=True)
    assert "two controls match the recorded locator" in page
    assert "steps[2]" in page


def test_an_unknown_intervention_redirects_instead_of_erroring(client) -> None:
    response = client.get("/interventions/nope")
    assert response.status_code == 302


class TestControlTransfer:
    """The same state a CLI take/return/abort would leave behind."""

    def test_take_moves_the_lease_to_the_human(self, store, client) -> None:
        iv = escalate(store)
        post(client, f"/interventions/{iv.id}/take", data={"operator": "dana"})

        assert InterventionStore(store).get(iv.id).status == "taken"
        assert InterventionStore(store).get(iv.id).operator == "dana"
        lease = LeaseStore(store).read("sess-1")
        assert lease is not None and lease.holder == "HUMAN"

    def test_return_releases_the_lease_and_bumps_the_generation(self, store, client) -> None:
        iv = escalate(store)
        post(client, f"/interventions/{iv.id}/take", data={"operator": "dana"})
        taken = LeaseStore(store).read("sess-1")
        post(client, f"/interventions/{iv.id}/return")

        assert InterventionStore(store).get(iv.id).status == "returned"
        released = LeaseStore(store).read("sess-1")
        assert released is not None and taken is not None
        assert released.holder == "NONE"
        assert released.generation > taken.generation, "every transfer bumps the generation"

    def test_abort_ends_the_run(self, store, client) -> None:
        iv = escalate(store)
        post(client, f"/interventions/{iv.id}/abort")
        assert InterventionStore(store).get(iv.id).status == "aborted"

    def test_a_refused_transition_is_a_message_not_a_traceback(self, store, client) -> None:
        iv = escalate(store)
        response = post(client, f"/interventions/{iv.id}/return", follow_redirects=True)
        assert response.status_code == 200
        assert "refused" in response.get_data(as_text=True)
        assert InterventionStore(store).get(iv.id).status == "open", "state is unchanged"


class TestIntents:
    def _intent(self, store: StateStore):
        return IntentStore(store).begin(
            run_id="run-1", capability_id="open_subaccount", version="1.0.0",
            step="steps[3]", inputs_digest="abc123",
        )

    def test_unresolved_intents_are_listed(self, store, client) -> None:
        intent = self._intent(store)
        page = client.get("/").get_data(as_text=True)
        assert intent.id in page
        assert "open_subaccount" in page

    def test_reconciling_records_who_decided_and_what_they_found(self, store, client) -> None:
        intent = self._intent(store)
        post(client, f"/intents/{intent.id}/reconcile",
                    data={"outcome": "completed", "operator": "dana"})

        settled = IntentStore(store).get(intent.id)
        assert settled.state == "reconciled"
        assert settled.resolution == "completed"
        assert settled.resolved_by == "dana"

    def test_an_invalid_outcome_is_refused(self, store, client) -> None:
        intent = self._intent(store)
        post(client, f"/intents/{intent.id}/reconcile", data={"outcome": "probably"})
        assert IntentStore(store).get(intent.id).state == "dispatching"


@pytest.mark.parametrize("path", ["/interventions/x/take", "/interventions/x/return",
                                  "/interventions/x/abort", "/intents/x/reconcile"])
def test_cross_origin_mutations_fail_even_with_session_token(client, path):
    response = post(client, path, headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    assert client.post(path).status_code == 403


def test_foreign_host_and_open_redirect_are_refused(client, store):
    assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 400
    iv = escalate(store)
    response = post(client, f"/interventions/{iv.id}/take",
                    headers={"Origin": "http://localhost", "Referer": "https://untrusted.example"})
    assert response.status_code == 302 and response.location == "/"


@pytest.mark.parametrize("ttl", ["nan", "inf", "-1", "0", "3601", "invalid"])
def test_invalid_lease_duration_does_not_take_control(client, store, ttl):
    iv = escalate(store)
    assert post(client, f"/interventions/{iv.id}/take", data={"ttl": ttl}).status_code == 400
    assert InterventionStore(store).get(iv.id).status == "open"
