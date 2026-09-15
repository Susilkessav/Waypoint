"""Crash recovery: progress is recorded per step, and a dead run resumes from verified state.

An intent (R-REC-4) protects an irreversible action from repeating after a crash; it says
nothing about a safe step. These tests cover the other half: a row left at ``running`` is the
only signal ``resume-run`` needs, and the recorded step index is never trusted on its own -
the return ladder still has to recognise the live screen (R-RESUME-3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from waypoint.artifact.approval import approve
from waypoint.artifact.schema import Capability, load
from waypoint.policy.secrets import SecretBroker
from waypoint.replay import engine
from waypoint.replay.engine import ReplayOptions, ResumeRefused, replay, resume
from waypoint.session.progress import ProgressError, ProgressStore
from waypoint.session.store import StateStore

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "capabilities" / "lookup_member_balance" / "1.0.0.json"
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}


@pytest.fixture(scope="module")
def approved() -> Capability:
    return approve(load(ARTIFACT), approver="test")


def options(live_server: str, tmp_path: Path, **kw: Any) -> ReplayOptions:
    return ReplayOptions(
        base_url=live_server,
        evidence_root=tmp_path,
        secrets=SecretBroker(environ=CREDENTIALS),
        resume_db=tmp_path / "state.db",
        **kw,
    )


def crash_at(monkeypatch: pytest.MonkeyPatch, step_index: int) -> list[str]:
    """Simulate the process dying just before ``steps[step_index]``.

    ``finish`` is neutered rather than skipped by luck: a real crash never reaches the
    engine's ``finally``, and that is precisely what leaves the resume signal behind.
    Returns a list that will hold the dying run's id.
    """
    run_ids: list[str] = []
    started = ProgressStore.start

    def record(self: ProgressStore, **kw: Any) -> None:
        run_ids.append(str(kw["run_id"]))
        started(self, **kw)

    monkeypatch.setattr(ProgressStore, "start", record)
    monkeypatch.setattr(ProgressStore, "finish", lambda self, run_id: None)

    original = engine._Run._step

    def dying(self: Any, surface: Any, where: str, step: Any, **kw: Any) -> None:
        if where == f"steps[{step_index}]":
            raise KeyboardInterrupt("simulated process death")
        original(self, surface, where, step, **kw)

    monkeypatch.setattr(engine._Run, "_step", dying)
    return run_ids


def die_midway(approved: Capability, live_server: str, tmp_path: Path,
               monkeypatch: pytest.MonkeyPatch) -> str:
    """Run until steps[2], die, then undo the patches so the resume runs for real."""
    run_ids = crash_at(monkeypatch, 2)
    with pytest.raises(KeyboardInterrupt):
        replay(approved, {"member_id": "12345"}, options(live_server, tmp_path))
    monkeypatch.undo()
    return run_ids[-1]


def test_a_completed_run_records_progress_and_closes_it(approved, live_server, tmp_path) -> None:
    result = replay(approved, {"member_id": "12345"}, options(live_server, tmp_path))
    assert result.status == "success", result.failure
    recorded = ProgressStore(StateStore(tmp_path / "state.db")).get(result.run_id)
    assert recorded.status == "done"
    assert recorded.completed_index == len(approved.steps) - 1
    assert "12345" not in recorded.inputs_hash, "inputs are hashed here, never stored"


def test_a_dead_run_leaves_a_running_row_at_the_last_completed_step(
    approved, live_server, tmp_path, monkeypatch
) -> None:
    run_id = die_midway(approved, live_server, tmp_path, monkeypatch)
    recorded = ProgressStore(StateStore(tmp_path / "state.db")).get(run_id)
    assert recorded.status == "running", "a crash must leave the row open"
    assert recorded.completed_index == 1, "steps[0] and steps[1] completed; steps[2] died"


class TestResumeRefusals:
    """Every refusal happens before a browser starts."""

    def test_wrong_inputs_are_refused(self, approved, live_server, tmp_path, monkeypatch) -> None:
        run_id = die_midway(approved, live_server, tmp_path, monkeypatch)
        with pytest.raises(ResumeRefused, match="not the ones that run was started with"):
            resume(approved, {"member_id": "67890"}, run_id, options(live_server, tmp_path))

    def test_an_edited_artifact_is_refused(
        self, approved, live_server, tmp_path, monkeypatch
    ) -> None:
        run_id = die_midway(approved, live_server, tmp_path, monkeypatch)
        edited = approved.model_copy(update={"description": "edited after the run started"})
        with pytest.raises(ResumeRefused, match="artifact changed"):
            resume(edited, {"member_id": "12345"}, run_id, options(live_server, tmp_path))

    def test_a_finished_run_cannot_be_resumed(self, approved, live_server, tmp_path) -> None:
        result = replay(approved, {"member_id": "12345"}, options(live_server, tmp_path))
        with pytest.raises(ResumeRefused, match="nothing to resume"):
            resume(approved, {"member_id": "12345"}, result.run_id, options(live_server, tmp_path))

    def test_an_unknown_run_is_refused(self, approved, live_server, tmp_path) -> None:
        with pytest.raises(ProgressError, match="no progress recorded"):
            resume(approved, {"member_id": "12345"}, "nosuchrun", options(live_server, tmp_path))


def test_resume_reconstructs_safe_prefix_then_closes_both_records(
    approved, live_server, tmp_path, monkeypatch
) -> None:
    run_id = die_midway(approved, live_server, tmp_path, monkeypatch)
    result = resume(approved, {"member_id": "12345"}, run_id, options(live_server, tmp_path))
    assert result.status == "success", result.failure
    assert result.outputs["savings_balance"] == "$4,281.19"
    progress = ProgressStore(StateStore(tmp_path / "state.db"))
    assert progress.get(run_id).status == "done"
    assert progress.get(run_id).resumed_by == result.run_id
    assert progress.get(result.run_id).resumed_from == run_id
    assert progress.get(result.run_id).status == "done"
    events = (tmp_path / result.run_id / "events.jsonl").read_text()
    assert "resume_safe_prefix" in events
    with pytest.raises(ResumeRefused, match="nothing to resume"):
        resume(approved, {"member_id": "12345"}, run_id, options(live_server, tmp_path))


def test_reconstructed_prefix_can_hand_off_and_continue(
    approved, live_server, tmp_path, monkeypatch
) -> None:
    from waypoint.replay.stability import Case, case_injector
    from waypoint.session.escalation import InterventionStore

    ids = crash_at(monkeypatch, 3)
    with pytest.raises(KeyboardInterrupt):
        replay(approved, {"member_id": "12345"}, options(live_server, tmp_path))
    monkeypatch.undo()
    queue = InterventionStore(StateStore(tmp_path / "state.db"))

    def human(surface, iv):
        frame = next(f for f in surface.page.frames if f.name == "content")
        frame.locator('a[id$="_lnkView"][href$="member_id=12345"]').click()
        queue.give_back(iv.id)

    result = resume(approved, {"member_id": "12345"}, ids[-1], options(
        live_server, tmp_path, state_db=tmp_path / "state.db", handoff=True,
        notify=lambda iv: queue.take(iv.id, "regression", 60), while_human=human,
        lease_poll_ms=100, wait_timeout_s=30,
        after_preconditions=case_injector(Case({}, inject="ambiguous"))))
    assert result.status == "success", result.failure
    assert result.telemetry["handoffs"], "handoff remains available during prefix reconstruction"
