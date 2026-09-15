"""A7 end to end: an escalation hands the live session to a person and back.

The ``ambiguous`` injection renders two identical View links in the searched member's
row, so steps[2] escalates. A stand-in operator takes control through the shared state
store (or the real CLI), acts on the same browser, and returns it; the run continues
only where the return ladder says it may (REPORT.md §5, R-PROC, R-RESUME).

T8  - returning on a screen no declared resume point accepts escalates again.
T19 - the run cannot act while the person holds control.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from waypoint.artifact.approval import approve
from waypoint.artifact.schema import Capability, load
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.replay.result import ReplayResult
from waypoint.session.escalation import Intervention, InterventionError, InterventionStore
from waypoint.session.lease import LeaseLost
from waypoint.session.store import StateStore
from waypoint.surface.ports import Action

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "capabilities" / "lookup_member_balance" / "1.0.0.json"
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}
BALANCE = {"savings_balance": "$4,281.19", "account_status": "active"}

Human = Callable[[Any, Intervention, InterventionStore], None]


@pytest.fixture(scope="module")
def approved() -> Capability:
    return approve(load(ARTIFACT), approver="test")


def content(surface: Any) -> Any:
    return next(f for f in surface.page.frames if f.name == "content")


def open_record(member_id: str) -> Human:
    """What a person would do: click the View link in the right row, then hand back."""
    def human(surface: Any, iv: Intervention, store: InterventionStore) -> None:
        frame = content(surface)
        frame.locator(f'a[id$="_lnkView"][href$="member_id={member_id}"]').click()
        frame.wait_for_url(f"**/console/member?member_id={member_id}")
        store.give_back(iv.id)
    return human


def hand_back_untouched(surface: Any, iv: Intervention, store: InterventionStore) -> None:
    store.give_back(iv.id)


def run(cap: Capability, live_server: str, tmp_path: Path, human: Human | None, *,
        take_ttl: float = 60, notify: Callable[[Intervention], None] | None = None,
        **kw: Any) -> tuple[ReplayResult, list[Intervention], InterventionStore]:
    db = tmp_path / "state.db"
    store = InterventionStore(StateStore(db))
    opened: list[Intervention] = []

    def take(iv: Intervention) -> None:
        opened.append(iv)
        store.take(iv.id, "tester", take_ttl)

    def navigate_to_ambiguity(surface: Any, origin: str) -> None:
        surface.act(Action("navigate", url=f"{origin}/console?inject=ambiguous"))

    kw.setdefault("wait_timeout_s", 30)  # fail fast if nobody ever takes control
    options = ReplayOptions(
        base_url=live_server, evidence_root=tmp_path, secrets=SecretBroker(environ=CREDENTIALS),
        after_preconditions=navigate_to_ambiguity, handoff=True, state_db=db,
        lease_poll_ms=100, notify=notify or take,
        while_human=(lambda s, iv: human(s, iv, store)) if human else None, **kw,
    )
    return replay(cap, {"member_id": "12345"}, options), opened, store


def human_log(result: ReplayResult) -> list[dict[str, Any]]:
    path = Path(result.evidence_dir or "") / "human" / "actions.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_person_resolves_the_ambiguity_and_the_run_finishes(approved, live_server, tmp_path
                                                              ) -> None:
    result, opened, store = run(approved, live_server, tmp_path, open_record("12345"))
    assert result.status == "success", result.failure
    assert result.outputs == BALANCE and result.exit_code == 0

    [iv] = opened
    assert (iv.reason_code, iv.step, iv.status) == ("ambiguous_locator", "steps[2]", "open")
    assert iv.screenshot and Path(iv.screenshot).exists()
    assert store.get(iv.id).status == "resolved"
    assert store.leases.read(result.run_id).effective_holder(store.store.clock()) == "NONE"

    [handoff] = result.telemetry["handoffs"]
    assert handoff["ladder"] == "resume:checkpoint:steps[3]"
    assert handoff["operator"] == "tester" and handoff["human_actions"] >= 1

    log = human_log(result)
    transfers = [e for e in log if e["event"] == "control_transfer"]
    assert [e["direction"] for e in transfers] == ["to_human", "to_agent"]
    assert transfers[0]["generation"] < transfers[1]["generation"], "a new grant, not the old"
    assert {"click", "navigation"} <= {e["event"] for e in log}

    run_dir = Path(result.evidence_dir or "")
    diff = json.loads((run_dir / "handoff1_diff.json").read_text())
    assert diff["screen_changed"] and diff["ladder"] == handoff["ladder"]
    for path in [*run_dir.glob("*.json*"), *run_dir.glob("human/*")]:
        text = path.read_text()
        for secret in ("4,281.19", "operator1", "changeme", "Dolores"):
            assert secret not in text, f"{secret!r} leaked into {path.name}"


def test_t8_returning_without_reaching_a_resume_point_escalates_again(approved, live_server,
                                                                       tmp_path) -> None:
    """The results page satisfies steps[1]'s checkpoint, which is not a resume point."""
    result, _, _ = run(approved, live_server, tmp_path, hand_back_untouched, max_handoffs=1)
    assert result.status == "escalated" and result.outputs is None
    assert result.failure is not None
    assert result.failure.code == "unrecognized_state_after_handoff"
    assert [h["ladder"] for h in result.telemetry["handoffs"]] == ["unrecognized"]


def test_a_second_handoff_can_finish_what_the_first_did_not(approved, live_server, tmp_path
                                                            ) -> None:
    calls: list[str] = []

    def human(surface: Any, iv: Intervention, store: InterventionStore) -> None:
        calls.append(iv.reason_code)
        (hand_back_untouched if len(calls) == 1 else open_record("12345"))(surface, iv, store)

    result, opened, _ = run(approved, live_server, tmp_path, human)
    assert result.status == "success", result.failure
    assert calls == ["ambiguous_locator", "unrecognized_state_after_handoff"]
    assert len({iv.id for iv in opened}) == 2
    assert [h["ladder"] for h in result.telemetry["handoffs"]] == [
        "unrecognized", "resume:checkpoint:steps[3]"]


def test_the_wrong_record_is_not_accepted_after_a_handoff(approved, live_server, tmp_path
                                                         ) -> None:
    result, _, _ = run(approved, live_server, tmp_path, open_record("67890"), max_handoffs=1)
    assert result.status == "escalated" and result.outputs is None
    assert result.failure is not None
    assert result.failure.code == "unrecognized_state_after_handoff"


def test_t19_the_run_cannot_act_while_the_person_holds_control(approved, live_server, tmp_path
                                                              ) -> None:
    refused: list[str] = []

    def human(surface: Any, iv: Intervention, store: InterventionStore) -> None:
        with pytest.raises(LeaseLost) as exc:
            surface.act(Action("navigate", url=surface.page.url))
        refused.append(str(exc.value))
        open_record("12345")(surface, iv, store)

    result, _, _ = run(approved, live_server, tmp_path, human)
    assert refused and result.status == "success", result.failure


def test_an_abort_ends_the_run_and_releases_control(approved, live_server, tmp_path) -> None:
    def human(surface: Any, iv: Intervention, store: InterventionStore) -> None:
        store.abort(iv.id)

    result, [iv], store = run(approved, live_server, tmp_path, human)
    assert result.status == "escalated" and result.exit_code == 3
    assert result.failure is not None and result.failure.code == "aborted_by_operator"
    assert store.get(iv.id).status == "aborted"
    assert store.leases.read(result.run_id).effective_holder(store.store.clock()) == "NONE"


def test_an_expired_operator_lease_escalates_and_a_late_return_is_refused(
        approved, live_server, tmp_path) -> None:
    result, [iv], store = run(approved, live_server, tmp_path, None, take_ttl=0.3)
    assert result.failure is not None and result.failure.code == "lease_timeout"
    assert store.get(iv.id).status == "expired"
    with pytest.raises(InterventionError):
        store.give_back(iv.id)


def test_nobody_taking_control_times_out(approved, live_server, tmp_path) -> None:
    result, _, store = run(approved, live_server, tmp_path, None, notify=lambda iv: None,
                           wait_timeout_s=0.5)
    assert result.failure is not None and result.failure.code == "intervention_timeout"
    assert [iv.status for iv in store.list(("expired",))] == ["expired"]


def test_the_cli_takes_and_returns_control(approved, live_server, tmp_path) -> None:
    exe = Path(sys.executable).parent / "waypoint"
    db = tmp_path / "state.db"

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(exe), "intervene", *args, "--db", str(db)],
                              capture_output=True, text=True, env=dict(os.environ), timeout=60)

    assert "no interventions" in cli("list").stdout  # before any state exists
    seen: dict[str, Any] = {}

    def take(iv: Intervention) -> None:
        seen["take"] = cli("take", iv.id, "--operator", "cli-op", "--ttl", "60")
        seen["list"] = cli("list")
        seen["show"] = cli("show", iv.id)

    def human(surface: Any, iv: Intervention, store: InterventionStore) -> None:
        frame = content(surface)
        frame.locator('a[id$="_lnkView"][href$="member_id=12345"]').click()
        frame.wait_for_url("**/console/member?member_id=12345")
        seen["return"] = cli("return", iv.id)

    result, _, store = run(approved, live_server, tmp_path, human, notify=take)
    assert result.status == "success", result.failure
    for name in ("take", "list", "show", "return"):
        assert seen[name].returncode == 0, (name, seen[name].stderr)
    assert "you have control" in seen["take"].stdout
    assert "taken" in seen["list"].stdout and "cli-op" in seen["list"].stdout
    shown = json.loads(seen["show"].stdout)
    assert "operator_token" not in shown and shown["lease"]["holder"] == "HUMAN"
    [iv] = store.list(("resolved",))
    again = cli("take", iv.id)
    assert again.returncode == 1 and "resolved" in again.stderr


def test_the_replay_cli_announces_a_handoff_and_obeys_the_operator(live_server, tmp_path
                                                                    ) -> None:
    """`replay --handoff` through two real CLIs: announce, take, return, ask again, abort."""
    root = tmp_path / "capabilities"
    (root / "lookup_member_balance").mkdir(parents=True)
    shutil.copy(ARTIFACT, root / "lookup_member_balance" / "1.0.0.json")
    exe = str(Path(sys.executable).parent / "waypoint")
    db = tmp_path / "state.db"
    env = {**os.environ, **CREDENTIALS}

    def operator(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([exe, "intervene", *args, "--db", str(db)], capture_output=True,
                              text=True, env=env, timeout=60)

    proc = subprocess.Popen(
        [exe, "replay", "lookup_member_balance", "--input", "member_id=12345",
         "--base-url", live_server, "--inject", "ambiguous", "--handoff", "--headless",
         "--state-db", str(db), "--evidence-root", str(tmp_path / "runs"), "--root", str(root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    announced: list[str] = []

    def next_intervention() -> str:
        assert proc.stderr is not None
        found = None
        for line in proc.stderr:
            announced.append(line)
            match = re.search(r"intervention (\w+) is open", line)
            found = match.group(1) if match else found
            if found and "end the run:" in line:
                return found
        raise AssertionError("no intervention was announced:\n" + "".join(announced))

    try:
        first = next_intervention()
        assert any(f"waypoint intervene take {first} --db {db}" in line for line in announced)
        assert operator("take", first).returncode == 0
        assert operator("return", first).returncode == 0  # nothing fixed: it must ask again
        second = next_intervention()
        assert second != first
        assert operator("abort", second).returncode == 0
        out, _ = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 3
    result = json.loads(out)
    assert result["failure"]["code"] == "aborted_by_operator"
    assert [h["ladder"] for h in result["telemetry"]["handoffs"]] == ["unrecognized"]
