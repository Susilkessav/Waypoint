"""The agent-facing surface: what is callable, what it promises, and what is refused.

A catalog entry is a contract an agent can rely on without reading the artifact: typed
arguments, named outputs, the business outcomes it may report instead, and whether it will
stop for a person. Drafts are invisible here - approval is a person's decision, and an
agent must not be able to route around it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from waypoint.artifact.approval import approve
from waypoint.artifact.schema import load
from waypoint.catalog.registry import (
    Catalog,
    Unavailable,
    UnknownCapability,
    result_for_agent,
)
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions
from waypoint.replay.ledger import Ledger
from waypoint.replay.result import FailureDetail, ReplayResult
from waypoint.session.store import StateStore

REPO = Path(__file__).resolve().parents[1]
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A capabilities directory holding one approved release and one draft."""
    root = tmp_path / "capabilities"
    (root / "lookup_member_balance").mkdir(parents=True)
    source = REPO / "capabilities" / "lookup_member_balance"
    shutil.copy(source / "1.2.0.json", root / "lookup_member_balance" / "1.2.0.json")
    shutil.copy(source / "1.1.0.json", root / "lookup_member_balance" / "1.1.0.json")
    return root


def test_only_approved_releases_are_callable(root: Path) -> None:
    [entry] = Catalog(root).entries()
    assert (entry.name, entry.version) == ("lookup_member_balance", "1.2.0")
    with pytest.raises(UnknownCapability, match="1.1.0"):
        Catalog(root).get("lookup_member_balance", "1.1.0")
    with pytest.raises(UnknownCapability):
        Catalog(root).get("transfer_funds")


def test_a_tool_definition_carries_the_artifacts_own_contract(root: Path) -> None:
    [tool] = Catalog(root).tools()
    assert tool["name"] == "lookup_member_balance"
    member = tool["input_schema"]["properties"]["member_id"]
    assert member["pattern"] == "^[0-9]{5}$", "the artifact's own constraint, not a guess"
    assert tool["input_schema"]["required"] == ["member_id"]
    assert tool["input_schema"]["additionalProperties"] is False
    assert "savings_balance" in tool["description"]
    assert "member_not_found" in tool["description"], "the caller can branch on it"

    [openai] = Catalog(root).tools("openai")
    assert openai["type"] == "function"
    assert openai["function"]["parameters"]["properties"]["member_id"]["pattern"]


def test_a_capability_that_commits_says_so(tmp_path: Path) -> None:
    root = tmp_path / "capabilities"
    (root / "open_sub_account").mkdir(parents=True)
    shutil.copy(REPO / "capabilities" / "open_sub_account" / "1.0.0.json",
                root / "open_sub_account" / "1.0.0.json")
    [entry] = Catalog(root).entries()
    assert entry.requires_human
    assert "cannot be undone" in Catalog(root).tools()[0]["description"]


def test_arguments_are_checked_before_anything_runs(root: Path) -> None:
    catalog = Catalog(root)
    with pytest.raises(ValueError, match="member_id"):
        catalog.invoke("lookup_member_balance", {"member_id": "not-a-member"})
    with pytest.raises(ValueError, match="branch_code"):
        catalog.invoke("lookup_member_balance", {"member_id": "12345", "branch_code": "BR-014"})


def test_a_capability_below_its_own_bar_is_listed_but_not_callable(root: Path,
                                                                   tmp_path: Path) -> None:
    path = root / "lookup_member_balance" / "1.2.0.json"
    cap = load(path)
    demanding = approve(cap.model_copy(update={
        "policy": cap.policy.model_copy(update={"min_confidence": 0.8}),
        "provenance": cap.provenance.model_copy(update={"approval": {"base": "draft"}}),
    }), approver="test")
    path.write_text(demanding.to_json())

    catalog = Catalog(root, Ledger(StateStore(tmp_path / "state.db")))
    [entry] = catalog.entries()
    assert not entry.available
    assert "below the 0.80" in (entry.unavailable_reason() or "")
    assert catalog.tools() == [], "an agent is not offered what it may not call"
    with pytest.raises(Unavailable, match="stability"):
        catalog.invoke("lookup_member_balance", {"member_id": "12345"})


def test_the_caller_is_told_what_happened_not_given_an_exception() -> None:
    success = ReplayResult(status="success", capability_id="c", version="1.0.0", run_id="r",
                           outputs={"savings_balance": "$4,281.19"})
    assert result_for_agent(success)["outputs"]["savings_balance"] == "$4,281.19"

    answer = ReplayResult(status="business_outcome", capability_id="c", version="1.0.0",
                          run_id="r", outcome="member_not_found")
    assert result_for_agent(answer) == {"status": "business_outcome", "capability": "c",
                                        "version": "1.0.0", "outcome": "member_not_found"}

    stopped = ReplayResult(status="escalated", capability_id="c", version="1.0.0", run_id="r",
                           failure=FailureDetail("approval_required", "needs a person",
                                                 step="steps[7]"),
                           telemetry={"handoffs": [{"intervention": "abc123"}]})
    body = result_for_agent(stopped)
    assert body["waiting_on_a_person"] is False
    assert body["requires_intervention"] is True
    assert body["intervention_history"] == ["abc123"]
    assert body["error"]["step"] == "steps[7]"


@pytest.mark.browser
def test_an_agent_calls_a_capability_by_name(root: Path, live_server: str,
                                             tmp_path: Path) -> None:
    catalog = Catalog(root, Ledger(StateStore(tmp_path / "state.db")))
    result = catalog.invoke(
        "lookup_member_balance", {"member_id": "67890"},
        options=ReplayOptions(base_url=live_server, evidence_root=tmp_path / "runs",
                              secrets=SecretBroker(environ=CREDENTIALS), capture_steps=False,
                              ledger_db=tmp_path / "state.db"),
    )
    assert result.status == "success", result.failure
    body = result_for_agent(result)
    assert body["outputs"] == {"savings_balance": "$912.04", "account_status": "dormant"}
    assert json.dumps(body), "the whole answer is JSON an agent can consume"

    ledger = Ledger(StateStore(tmp_path / "state.db"))
    assert ledger.confidence(load(root / "lookup_member_balance" / "1.2.0.json")).runs == 1
