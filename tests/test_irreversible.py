"""B3 and B6 end to end: an irreversible step is reconciled, never repeated.

Uses the reviewed ``open_sub_account`` 1.0.0 - discovered by Haiku 4.5, reconcile added at
review - approved in memory only, against a live app whose sub-accounts are server-side.

T10 - the server commits and the response is lost: no second submission, one sub-account.
T11 - a stale, unrelated confirmation page is not adopted.
T12 - a probe screen matching neither condition is Unknown and escalates.
T13 - an intent a crashed run left at ``dispatched`` is reconciled before anything runs.
R-RISK-7 - a step the page shows to be riskier than declared escalates; nobody may approve it.
Demo 8 - unattended, Confirm escalates; a person confirms by hand; the engine reconciles
         and adopts rather than clicking Confirm again.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

import pytest

from waypoint.artifact.approval import approve
from waypoint.artifact.schema import Capability
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.replay.result import ReplayResult
from waypoint.session.escalation import Intervention, InterventionStore
from waypoint.session.intents import Intent, IntentStore, inputs_hash
from waypoint.session.store import StateStore
from waypoint.surface.ports import Action

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "capabilities" / "open_sub_account" / "1.0.0.json"
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}
INPUTS = {"member_id": "12345", "account_type": "Money Market", "initial_deposit": "250.00"}
ALL = ("dispatching", "dispatched", "observed", "reconciled")


def artifact(*, unattended: bool = False, confirm_risk: str = "irreversible",
             probe_url: str | None = None) -> Capability:
    data = json.loads(ARTIFACT.read_text())
    data["provenance"] = {}
    data["policy"]["unattended"] = unattended
    data["steps"][-1]["risk"] = confirm_risk
    if probe_url is not None:  # a probe that lands somewhere that proves nothing
        probe = data["steps"][-1]["reconcile"]["probe"][0]
        probe["url_template"] = probe_url
        probe["checkpoint"]["signature"] = "search_form_here"
        data["signatures"]["search_form_here"] = {
            "description": "The member search form.",
            "match": {"element_exists": {"role": "textbox", "anchor": "Member ID"}}}
    return approve(Capability.model_validate(data), approver="test")


CONFIRM = f"steps[{len(json.loads(ARTIFACT.read_text())['steps']) - 1}]"


def run(cap: Capability, app: str, tmp_path: Path, *, inject: str | None = None,
        asked: list[str] | None = None, **kw: Any) -> ReplayResult:
    def operator(verdict: Any, action: Any) -> bool:  # the attending human says yes
        if asked is not None:
            asked.append(verdict.risk)
        return True

    def injection(surface: Any, origin: str) -> None:
        surface.act(Action("navigate", url=f"{origin}/console?inject={inject}"))

    return replay(cap, INPUTS, ReplayOptions(
        base_url=app, evidence_root=tmp_path / "runs", state_db=tmp_path / "state.db",
        secrets=SecretBroker(environ=CREDENTIALS), approve=operator,
        after_preconditions=injection if inject else None, **kw,
    ))


class App:
    """The application's own record, read the way a person would check it."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        body = b"txtUserId=operator1&txtPassword=changeme"
        self.opener.open(f"{base}/", body, timeout=10).read()

    def sub_accounts(self, member_id: str = "12345") -> list[str]:
        html = self.opener.open(f"{self.base}/console/member/accounts?member_id={member_id}",
                                timeout=10).read().decode()
        return re.findall(rf"SA-{member_id}-\d\d", html)

    def open_one_behind_the_engines_back(self) -> None:
        self.opener.open(f"{self.base}/console/subaccount/confirm?member_id=12345"
                         "&type=Money%20Market&cents=25000", timeout=10).read()


def intents(tmp_path: Path) -> IntentStore:
    return IntentStore(StateStore(tmp_path / "state.db"))


def left_by_a_crashed_run(tmp_path: Path) -> Intent:
    store = intents(tmp_path)
    intent = store.begin(run_id="crashed-run", capability_id="open_sub_account",
                         version="1.0.0", step=CONFIRM,
                         inputs_digest=inputs_hash("open_sub_account", INPUTS))
    store.advance(intent.id, "dispatched")
    return intent


def test_confirming_opens_one_sub_account_and_reads_its_id(fresh_app, tmp_path) -> None:
    asked: list[str] = []
    result = run(artifact(), fresh_app, tmp_path, asked=asked)
    assert result.status == "success", result.failure
    assert result.outputs == {"account_id": "SA-12345-01"}
    assert asked == ["irreversible"] and not result.telemetry["adopted"]
    assert App(fresh_app).sub_accounts() == ["SA-12345-01"]
    [intent] = intents(tmp_path).list(ALL)
    assert intent.state == "observed"


def test_t10_a_lost_response_is_reconciled_not_resubmitted(fresh_app, tmp_path) -> None:
    asked: list[str] = []
    result = run(artifact(), fresh_app, tmp_path, inject="commit_then_drop", asked=asked)
    assert result.status == "success", result.failure
    assert result.telemetry["adopted"] is True
    assert result.outputs == {"account_id": "SA-12345-01"}, "read from the account grid"
    assert [r["verdict"] for r in result.telemetry["reconciliations"]] == ["completed"]
    assert asked == ["irreversible"], "Confirm was sent exactly once"
    assert App(fresh_app).sub_accounts() == ["SA-12345-01"], "exactly one sub-account exists"
    [intent] = intents(tmp_path).list(ALL)
    assert (intent.state, intent.resolution, intent.resolved_by) == (
        "reconciled", "completed", "replay")


def test_t11_a_stale_confirmation_is_not_adopted(fresh_app, tmp_path) -> None:
    result = run(artifact(), fresh_app, tmp_path, inject="stale_confirmation")
    assert result.status == "failure" and result.outputs is None
    assert result.failure is not None
    assert result.failure.code == "irreversible_step_not_completed"
    assert App(fresh_app).sub_accounts() == []
    [intent] = intents(tmp_path).list(ALL)
    assert (intent.state, intent.resolution) == ("reconciled", "not_completed")


def test_t12_a_probe_screen_that_proves_nothing_escalates(fresh_app, tmp_path) -> None:
    cap = artifact(probe_url="/console/content")
    result = run(cap, fresh_app, tmp_path, inject="commit_then_drop")
    assert result.status == "escalated" and result.failure is not None
    assert result.failure.code == "reconciliation_unknown"
    assert "matches neither condition" in result.failure.message
    [intent] = intents(tmp_path).list(ALL)
    assert intent.state == "dispatched", "Unknown leaves the operation open for next time"
    assert App(fresh_app).sub_accounts() == ["SA-12345-01"], "committed once, never again"


def test_t13_an_operation_a_crashed_run_completed_is_adopted_without_running(
        fresh_app, tmp_path) -> None:
    left = left_by_a_crashed_run(tmp_path)
    App(fresh_app).open_one_behind_the_engines_back()  # the crashed run's commit landed
    asked: list[str] = []
    result = run(artifact(), fresh_app, tmp_path, asked=asked)
    assert result.status == "success", result.failure
    assert result.telemetry["adopted"] is True and result.outputs == {"account_id": "SA-12345-01"}
    assert asked == [], "nothing was dispatched - no step executed at all"
    assert App(fresh_app).sub_accounts() == ["SA-12345-01"]
    assert intents(tmp_path).get(left.id).resolution == "completed"


def test_t13_an_operation_a_crashed_run_never_completed_runs_once(fresh_app, tmp_path) -> None:
    left = left_by_a_crashed_run(tmp_path)
    asked: list[str] = []
    result = run(artifact(), fresh_app, tmp_path, asked=asked)
    assert result.status == "success", result.failure
    assert not result.telemetry["adopted"] and asked == ["irreversible"]
    assert [r["verdict"] for r in result.telemetry["reconciliations"]] == ["not_completed"]
    assert App(fresh_app).sub_accounts() == ["SA-12345-01"]
    assert intents(tmp_path).get(left.id).resolution == "not_completed"


def test_r_risk_7_a_page_riskier_than_declared_is_never_approved_inline(fresh_app, tmp_path
                                                                      ) -> None:
    """Confirm declared "safe" - a stale or wrong label. The route says irreversible."""
    asked: list[str] = []
    result = run(artifact(confirm_risk="safe"), fresh_app, tmp_path, asked=asked)
    assert result.status == "escalated" and result.failure is not None
    assert result.failure.code == "risk_exceeds_declared"
    assert asked == [], "the attending operator was never even asked"
    assert App(fresh_app).sub_accounts() == []


def handoff_run(app: str, tmp_path: Path, person: Callable[[Any], None]) -> tuple[
        ReplayResult, list[Intervention]]:
    store = InterventionStore(StateStore(tmp_path / "state.db"))
    opened: list[Intervention] = []

    def take(iv: Intervention) -> None:
        opened.append(iv)
        store.take(iv.id, "tester", 60)

    def act(surface: Any, iv: Intervention) -> None:
        person(surface)
        store.give_back(iv.id)

    result = run(artifact(unattended=True), app, tmp_path, handoff=True, lease_poll_ms=100,
                 wait_timeout_s=30, max_handoffs=1, notify=take, while_human=act)
    return result, opened


def confirm_by_hand(surface: Any) -> None:
    frame = next(f for f in surface.page.frames if f.name == "content")
    frame.locator("#ctl00_MainContent_lnkConfirm").click()
    frame.wait_for_url("**/console/subaccount/confirm**")


def test_demo_8_the_engine_reconciles_what_a_person_confirmed(fresh_app, tmp_path) -> None:
    result, [iv] = handoff_run(fresh_app, tmp_path, confirm_by_hand)
    assert (iv.reason_code, iv.step) == ("approval_required", CONFIRM)
    assert result.status == "success", result.failure
    assert result.telemetry["adopted"] is True and result.outputs == {"account_id": "SA-12345-01"}
    assert [r["verdict"] for r in result.telemetry["reconciliations"]] == ["completed"]
    assert App(fresh_app).sub_accounts() == ["SA-12345-01"], "the engine did not click again"
    assert intents(tmp_path).list(ALL) == [], "the engine itself never dispatched anything"


def test_returning_without_confirming_is_not_completed_and_escalates(fresh_app, tmp_path
                                                                      ) -> None:
    result, _ = handoff_run(fresh_app, tmp_path, lambda surface: None)
    assert result.status == "escalated" and result.failure is not None
    assert result.failure.code == "not_completed_after_handoff"
    assert App(fresh_app).sub_accounts() == []
