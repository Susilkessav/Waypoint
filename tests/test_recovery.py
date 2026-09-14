"""B4 end to end: declared recovery, typed outcomes, and what drift does (R-OUT-1..3).

T30 - an interstitial is dismissed: ``success`` with ``recoveries`` non-empty.
T31 - a server error is a ``failure`` naming what was expected and what was seen.
T32 - an expired session is re-entered once and the run succeeds.
"""

from __future__ import annotations

import json
import re
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

import pytest

from waypoint.artifact.approval import approve
from waypoint.artifact.schema import Capability, load
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.replay.result import ReplayResult
from waypoint.surface.ports import Action

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parents[1]
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}
BALANCE = {"savings_balance": "$4,281.19", "account_status": "active"}
SUB_ACCOUNT = {"member_id": "12345", "account_type": "Money Market", "initial_deposit": "250.00"}


@pytest.fixture(scope="module")
def lookup() -> Capability:
    return approve(load(REPO / "capabilities" / "lookup_member_balance" / "1.2.0.json"),
                   approver="test")


@pytest.fixture(scope="module")
def open_sub_account() -> Capability:
    data = json.loads((REPO / "capabilities" / "open_sub_account" / "1.0.0.json").read_text())
    data["provenance"] = {}
    data["policy"]["unattended"] = False  # attended: the test approves Confirm inline
    return approve(Capability.model_validate(data), approver="test")


def run(cap: Capability, inputs: dict[str, str], app: str, tmp_path: Path,
        inject: str | None = None) -> ReplayResult:
    def injection(surface: Any, origin: str) -> None:
        surface.act(Action("navigate", url=f"{origin}/console?inject={inject}"))

    return replay(cap, inputs, ReplayOptions(
        base_url=app, evidence_root=tmp_path / "runs", state_db=tmp_path / "state.db",
        secrets=SecretBroker(environ=CREDENTIALS), approve=lambda verdict, action: True,
        after_preconditions=injection if inject else None,
    ))


def sub_accounts(base: str) -> list[str]:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    opener.open(f"{base}/", b"txtUserId=operator1&txtPassword=changeme", timeout=10).read()
    html = opener.open(f"{base}/console/member/accounts?member_id=12345", timeout=10).read()
    return re.findall(r"SA-12345-\d\d", html.decode())


def test_t30_an_interstitial_is_dismissed_and_the_run_succeeds(lookup, live_server, tmp_path
                                                              ) -> None:
    result = run(lookup, {"member_id": "12345"}, live_server, tmp_path, inject="interstitial")
    assert result.status == "success", result.failure
    assert result.outputs == BALANCE
    assert [(r["on"], r["do"]) for r in result.telemetry["recoveries"]] == [
        ("maintenance_interstitial", "dismiss")], "success with recoveries: the flake signal"


def test_t31_a_server_error_fails_with_expected_and_observed(lookup, live_server, tmp_path
                                                            ) -> None:
    result = run(lookup, {"member_id": "12345"}, live_server, tmp_path, inject="500")
    assert result.status == "failure" and result.exit_code == 1
    assert result.failure is not None
    assert result.failure.code == "hard_failure"
    assert result.failure.expected_signature == "member_detail_loaded"
    assert "server_error" in result.failure.observed_signatures
    assert (Path(result.evidence_dir or "") / "stop.png").exists()


def test_t32_an_expired_session_is_re_entered_once(lookup, live_server, tmp_path) -> None:
    result = run(lookup, {"member_id": "12345"}, live_server, tmp_path, inject="session")
    assert result.status == "success", result.failure
    assert result.outputs == BALANCE
    assert [r["do"] for r in result.telemetry["recoveries"]] == ["reauth"]


def test_a_slow_page_is_waited_for_not_failed(lookup, live_server, tmp_path) -> None:
    result = run(lookup, {"member_id": "12345"}, live_server, tmp_path, inject="slow")
    assert result.status == "success", result.failure
    assert result.telemetry["duration_ms"] >= 2000


def test_a_restricted_member_is_a_named_business_outcome(lookup, live_server, tmp_path) -> None:
    result = run(lookup, {"member_id": "99999"}, live_server, tmp_path)
    assert (result.status, result.outcome, result.exit_code) == (
        "business_outcome", "not_authorized", 0)


def test_a_refused_deposit_is_an_answer_and_commits_nothing(open_sub_account, fresh_app,
                                                           tmp_path) -> None:
    result = run(open_sub_account, SUB_ACCOUNT, fresh_app, tmp_path, inject="validation")
    assert (result.status, result.outcome) == ("business_outcome", "deposit_rejected")
    assert sub_accounts(fresh_app) == []
    assert result.telemetry["unresolved_intents"] == []


def test_a_renamed_control_escalates_rather_than_being_guessed(open_sub_account, fresh_app,
                                                               tmp_path) -> None:
    """drift: the Review button keeps its id and loses its name. The positional fallback
    asserts the name it was recorded with, so it refuses too - nothing is clicked."""
    result = run(open_sub_account, SUB_ACCOUNT, fresh_app, tmp_path, inject="drift")
    assert result.status == "escalated" and result.failure is not None
    assert result.failure.code == "locator_not_found"
    assert "Review" in (result.failure.intent or "") or "review" in (result.failure.intent or "")
    assert sub_accounts(fresh_app) == []
