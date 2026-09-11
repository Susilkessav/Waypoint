"""A5 end to end: the hand-written artifact replays with no model in the loop.

T7  - the right screen for the wrong member escalates; nothing is extracted.
T16 - an unapproved or tampered artifact is refused before any browser starts.
T29 - "no such member" is a business outcome that exits 0, not a crash.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from waypoint.artifact.approval import approve
from waypoint.artifact.schema import Capability, Provenance, load
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.replay.result import ReplayResult
from waypoint.surface.ports import Action

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "capabilities" / "lookup_member_balance" / "1.0.0.json"
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}


@pytest.fixture(scope="module")
def approved() -> Capability:
    return approve(load(ARTIFACT), approver="test")


def run(cap: Capability, member_id: str, live_server: str, tmp_path: Path, **kw) -> ReplayResult:
    options = ReplayOptions(
        base_url=live_server,
        evidence_root=tmp_path,
        secrets=SecretBroker(environ=CREDENTIALS),
        **kw,
    )
    return replay(cap, {"member_id": member_id}, options)


def inject(name: str):
    return lambda surface, origin: surface.act(
        Action("navigate", url=f"{origin}/console?inject={name}")
    )


def test_cold_session_signs_in_and_returns_the_balance(approved, live_server, tmp_path) -> None:
    result = run(approved, "12345", live_server, tmp_path)
    assert result.status == "success", result.failure
    assert result.outputs == {"savings_balance": "$4,281.19", "account_status": "active"}
    assert result.exit_code == 0
    assert result.telemetry["tier_histogram"].get("3", 0) >= 1  # the anchored View link


def test_the_same_artifact_serves_another_member(approved, live_server, tmp_path) -> None:
    result = run(approved, "67890", live_server, tmp_path)
    assert result.status == "success", result.failure
    assert result.outputs == {"savings_balance": "$912.04", "account_status": "dormant"}


def test_unknown_member_is_a_business_outcome(approved, live_server, tmp_path) -> None:
    result = run(approved, "00000", live_server, tmp_path)
    assert (result.status, result.outcome, result.exit_code) == (
        "business_outcome", "member_not_found", 0,
    )
    assert result.outputs is None


def test_right_screen_wrong_member_escalates(approved, live_server, tmp_path) -> None:
    result = run(
        approved, "12345", live_server, tmp_path, after_preconditions=inject("wrong_member")
    )
    assert result.status == "escalated" and result.outputs is None
    assert result.failure is not None
    assert (result.failure.code, result.failure.step, result.failure.expected_signature) == (
        "checkpoint_not_met", "steps[2]", "member_detail_loaded",
    )
    assert "member_detail_loaded" not in result.failure.observed_signatures


def test_ambiguous_target_escalates_and_never_guesses(approved, live_server, tmp_path) -> None:
    result = run(approved, "12345", live_server, tmp_path, after_preconditions=inject("ambiguous"))
    assert result.status == "escalated"
    assert result.failure is not None and result.failure.code == "ambiguous_locator"


def test_unapproved_and_tampered_artifacts_are_refused(approved, live_server, tmp_path) -> None:
    unapproved = load(ARTIFACT).model_copy(update={"provenance": Provenance()})
    draft = run(unapproved, "12345", live_server, tmp_path / "draft")
    assert draft.failure is not None and draft.failure.code == "artifact_not_approved"
    body = json.loads(approved.to_json())
    body["steps"][0]["intent"] = "edited after approval"
    tampered = run(Capability.model_validate(body), "12345", live_server, tmp_path / "tampered")
    assert tampered.failure is not None and "hash mismatch" in tampered.failure.message
    for result in (draft, tampered):
        assert not list(Path(result.evidence_dir or "").glob("*.png")), "no browser was started"


def test_invalid_input_is_refused_without_echoing_it(approved, live_server, tmp_path) -> None:
    result = run(approved, "abcde", live_server, tmp_path)
    assert result.failure is not None and result.failure.code == "invalid_input"
    assert "abcde" not in result.failure.message


def test_evidence_is_complete_and_redacted(approved, live_server, tmp_path) -> None:
    result = run(approved, "12345", live_server, tmp_path)
    run_dir = Path(result.evidence_dir or "")
    for name in ("artifact.json", "meta.json", "events.jsonl", "result.json", "final.png"):
        assert (run_dir / name).exists(), name
    assert (run_dir / "artifact.json").read_text() == approved.to_json()
    recorded = json.loads((run_dir / "result.json").read_text())
    assert recorded["outputs"]["savings_balance"] == "‹redacted:9 chars›"
    for path in run_dir.glob("*.json*"):
        text = path.read_text()
        for secret in ("4,281.19", "operator1", "changeme", "Dolores"):
            assert secret not in text, f"{secret!r} leaked into {path.name}"


def test_cli_approve_then_replay(live_server, tmp_path) -> None:
    root = tmp_path / "capabilities"
    (root / "lookup_member_balance").mkdir(parents=True)
    shutil.copy(ARTIFACT, root / "lookup_member_balance" / "1.0.0.json")
    exe = Path(sys.executable).parent / "waypoint"
    env = {**os.environ, **CREDENTIALS}

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(exe), *args, "--root", str(root)], capture_output=True,
                              text=True, env=env, timeout=180)

    assert cli("approve", "lookup_member_balance", "--approver", "test").returncode == 0
    done = cli("replay", "lookup_member_balance", "--input", "member_id=12345",
               "--base-url", live_server, "--evidence-root", str(tmp_path / "runs"))
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["outputs"]["savings_balance"] == "$4,281.19"
