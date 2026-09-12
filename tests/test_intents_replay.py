"""R-REC-4 end to end: write-ahead intents for an irreversible step.

The fixture capability marks a member for review - a GET that changes state, behind a
link no verb list would flag. An attended run asks before it, and the intent is written
only once the action is approved. A run whose effect was never confirmed blocks the
next attempt at the same operation until an operator reconciles it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from waypoint.artifact.approval import approve
from waypoint.artifact.schema import Capability
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.replay.result import ReplayResult
from waypoint.session.intents import IntentStore
from waypoint.session.store import StateStore

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parents[1]
LOOKUP = REPO / "capabilities" / "lookup_member_balance" / "1.0.0.json"
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}
ALL_STATES = ("dispatching", "dispatched", "observed", "reconciled")


def flag_capability(*, unattended: bool, flagged_text: str = "FLAGGED") -> Capability:
    """lookup_member_balance's first three steps, then "Mark for Review" (irreversible)."""
    body = json.loads(LOOKUP.read_text())
    sigs = body["signatures"]
    this_member = sigs["member_detail_loaded"]["match"]["all"][1]

    def review_flag(text: str) -> dict[str, Any]:
        return {"description": f"THIS member's review flag reads {text}.", "match": {"all": [
            this_member,
            {"element_exists": {"role": "LayoutTableCell", "name": text,
                                "anchor": "Review flag", "frame": ["main", "content"]}},
        ]}}

    sigs["member_flagged"] = review_flag(flagged_text)
    sigs["member_not_flagged"] = review_flag("none")
    body.update(
        capability_id="flag_member_for_review", name="Flag a member for review",
        description="R-REC-4 fixture: one irreversible step.",
        outputs={"type": "object", "properties": {}},
        postconditions=[{"signature": "member_flagged"}],
        provenance={},
    )
    body["steps"] = [*body["steps"][:3], {
        "intent": "Mark THIS member for review", "action": "click", "risk": "irreversible",
        "target": {"recorded_tier": 1, "candidates": [{
            "tier": 1, "kind": "role_name", "frame_path": ["main", "content"],
            "role": "link", "name": "Mark for Review"}]},
        "checkpoint": {"signature": "member_flagged"}, "timeout_ms": 1500,
        "reconcile": {"completed_when": "member_flagged",
                      "not_completed_when": "member_not_flagged", "identity": ["member_id"]},
    }]
    body["policy"]["unattended"] = unattended
    return approve(Capability.model_validate(body), approver="test")


def run(cap: Capability, live_server: str, tmp_path: Path,
        asked: list[str] | None = None) -> ReplayResult:
    def operator_says_yes(verdict: Any, action: Any) -> bool:
        if asked is not None:
            asked.append(verdict.risk)
        return True

    return replay(cap, {"member_id": "12345"}, ReplayOptions(
        base_url=live_server, evidence_root=tmp_path / "runs",
        secrets=SecretBroker(environ=CREDENTIALS), state_db=tmp_path / "state.db",
        approve=operator_says_yes,
    ))


def intents(tmp_path: Path) -> IntentStore:
    return IntentStore(StateStore(tmp_path / "state.db"))


def test_an_approved_irreversible_step_is_written_ahead_and_confirmed(live_server, tmp_path
                                                                      ) -> None:
    asked: list[str] = []
    result = run(flag_capability(unattended=False), live_server, tmp_path, asked)
    assert result.status == "success", result.failure
    assert len(asked) == 1, "only the irreversible step asks"
    [intent] = intents(tmp_path).list(ALL_STATES)
    assert (intent.step, intent.state, intent.run_id) == ("steps[3]", "observed", result.run_id)
    assert result.telemetry["unresolved_intents"] == []


def test_unattended_it_escalates_before_anything_is_written(live_server, tmp_path) -> None:
    result = run(flag_capability(unattended=True), live_server, tmp_path)
    assert result.status == "escalated" and result.failure is not None
    assert (result.failure.code, result.failure.step) == ("approval_required", "steps[3]")
    assert intents(tmp_path).list(ALL_STATES) == []


def test_an_unconfirmed_effect_blocks_the_operation_until_reconciled(live_server, tmp_path
                                                                     ) -> None:
    unconfirmable = flag_capability(unattended=False, flagged_text="FLAGGED-NEVER-SHOWN")
    first = run(unconfirmable, live_server, tmp_path)
    assert first.failure is not None and first.failure.code == "checkpoint_not_met"
    [intent] = intents(tmp_path).list(ALL_STATES)
    assert intent.state == "dispatched"
    assert first.telemetry["unresolved_intents"] == [intent.id]

    good = flag_capability(unattended=False)
    blocked = run(good, live_server, tmp_path)
    assert blocked.status == "escalated" and blocked.failure is not None
    assert blocked.failure.code == "reconciliation_required"
    assert intent.id in blocked.failure.message
    assert not list(Path(blocked.evidence_dir or "").glob("*.png")), "no browser was started"

    exe = str(Path(sys.executable).parent / "waypoint")

    def operator(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([exe, "intervene", *args, "--db", str(tmp_path / "state.db")],
                              capture_output=True, text=True, env=dict(os.environ), timeout=60)

    listed = operator("intents")
    assert intent.id in listed.stdout and "dispatched" in listed.stdout
    assert operator("reconcile", intent.id, "--outcome", "maybe").returncode == 2
    done = operator("reconcile", intent.id, "--outcome", "completed", "--operator", "op1")
    assert done.returncode == 0, done.stderr
    assert "no unresolved intents" in operator("intents").stdout
    assert "completed by op1" in operator("intents", "--all").stdout

    again = run(good, live_server, tmp_path)
    assert again.status == "success", again.failure
