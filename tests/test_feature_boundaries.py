"""Regression coverage for the submission review: integration and authority boundaries."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from waypoint.artifact.approval import approve
from waypoint.artifact.codegen import regression_test
from waypoint.artifact.schema import load
from waypoint.replay.assist import (
    AssistRequest,
    CassetteAssistant,
    RecordedChoice,
    ScriptedAssistant,
)
from waypoint.replay.engine import ReplayOptions, _Run
from waypoint.replay.ledger import Ledger, record_for
from waypoint.replay.result import FailureDetail, ReplayResult
from waypoint.replay.stability import Case
from waypoint.session.store import StateStore
from waypoint.surface.ports import UIElement, UISnapshot

REPO = Path(__file__).resolve().parents[1]


def cap_at(version="1.2.0", name="lookup_member_balance"):
    return load(REPO / "capabilities" / name / f"{version}.json")


def test_unresolved_intents_are_isolated_between_tenants(tmp_path):
    raw = cap_at("1.0.0", "open_sub_account").model_copy(
        update={"overrides": {"alpha": {}, "beta": {}}}
    )
    raw = approve(
        approve(raw, approver="review-probe", variant="alpha"),
        approver="review-probe",
        variant="beta",
    )
    inputs = {"member_id": "12345", "account_type": "Money Market", "initial_deposit": "250.00"}
    first = _Run(
        raw,
        inputs,
        ReplayOptions(
            variant="alpha",
            base_url="http://127.0.0.1:18111",
            state_db=tmp_path / "state.db",
            evidence_root=tmp_path / "runs",
        ),
    )
    second = _Run(
        raw,
        inputs,
        ReplayOptions(
            variant="beta",
            base_url="http://127.0.0.1:18222",
            state_db=tmp_path / "state.db",
            evidence_root=tmp_path / "runs",
        ),
    )
    try:
        first._intent_begin("steps[7]", handed_over=True)
        second._check_unreconciled()
        assert second.leftovers == [], "Tenant beta selected tenant alpha intent for reconciliation"
    finally:
        first.ev.close()
        second.ev.close()


def snapshot(hash_="screen-now", copies=1):
    elements = tuple(
        UIElement(
            ref=f"ref_{i}",
            role="button",
            name="Continue",
            value=None,
            enabled=True,
            frame_path=("main",),
            bbox=None,
            anchors=(),
            sensitivity="public",
            name_sensitivity="public",
        )
        for i in range(copies)
    )
    return UISnapshot(
        url="http://127.0.0.1/console",
        title="Search",
        elements=elements,
        text_digest="text",
        hash=hash_,
        frame_urls=(),
    )


def test_rejected_assist_consumes_the_one_call_budget(tmp_path, monkeypatch):
    raw = cap_at()
    raw = approve(
        raw.model_copy(
            update={"policy": raw.policy.model_copy(update={"assisted_fallback": True})}
        ),
        approver="review-probe",
    )
    assistant = ScriptedAssistant(answer="e999")
    run = _Run(raw, {"member_id": "12345"}, ReplayOptions(assist=assistant, evidence_root=tmp_path))
    monkeypatch.setattr(run, "_observe", lambda *a, **k: snapshot())
    try:
        for _ in range(2):
            run._assist(
                SimpleNamespace(),
                "steps[1]",
                raw.steps[1],
                FailureDetail("locator_not_found", "missing"),
            )
        assert len(assistant.requests) == 1, "Rejected model answers did not consume the budget"
    finally:
        run.ev.close()


@pytest.mark.parametrize("changed,duplicates", [(True, 1), (False, 2)])
def test_assist_cassette_rejects_changed_or_ambiguous_screen(changed, duplicates):
    snap = snapshot("changed" if changed else "recorded", duplicates)
    assistant = CassetteAssistant(
        [RecordedChoice("steps[1]", "recorded", "button", "Continue", "saved")]
    )
    request = AssistRequest(
        "steps[1]",
        "Search",
        "click",
        "missing",
        {"role": "button"},
        snap,
        tuple((f"e{i}", e) for i, e in enumerate(snap.elements, 1)),
    )
    assert assistant.choose(request).element is None, "Cassette chose a stale or ambiguous target"


@pytest.mark.parametrize(
    "version,case",
    [
        (
            "1.2.0",
            Case(
                inputs={"member_id": "12345"},
                name="server-error",
                inject="500",
                expect_status="failure",
            ),
        ),
        ("1.3.0", Case(inputs={"member_id": "12345"}, name="found", expect_status="success")),
    ],
)
@pytest.mark.browser
def test_generated_test_runs_the_declared_case(version, case, live_server, tmp_path, monkeypatch):
    monkeypatch.setenv("WAYPOINT_BASE_URL", live_server)
    monkeypatch.setenv("MERIDIAN_USER", "operator1")
    monkeypatch.setenv("MERIDIAN_PASS", "changeme")
    cap = cap_at(version)
    module = {"__name__": "generated_review_probe"}
    exec(compile(regression_test(cap), "<generated-test>", "exec"), module)
    module["test_case"](cap, case, tmp_path)


def test_failed_declared_contract_does_not_earn_unattended_confidence(tmp_path, monkeypatch):
    from waypoint.replay import stability

    raw = cap_at()
    raw = approve(
        raw.model_copy(update={"policy": raw.policy.model_copy(update={"min_confidence": 0.5})}),
        approver="review-probe",
    )
    ledger = Ledger(StateStore(tmp_path / "state.db"))
    sequence = []

    def replay_wrong_outcome(cap, inputs, options):
        sequence.append(1)
        result = ReplayResult(
            status="business_outcome",
            capability_id=cap.capability_id,
            version=cap.version,
            run_id=f"probe-{len(sequence)}",
            outcome="member_not_found",
            evidence_dir=str(tmp_path / f"probe-{len(sequence)}"),
            telemetry={},
        )
        # Same ordering as the engine: the replay records before the sweep checks its case.
        ledger.record(record_for(result, cap, inputs_hash="synthetic-probe", kind="stability"))
        return result

    monkeypatch.setattr(stability, "replay", replay_wrong_outcome)
    report = stability.run_sweep(
        raw,
        [
            Case(
                inputs={"member_id": "99999"},
                name="restricted",
                expect_status="business_outcome",
                expect_outcome="not_authorized",
            )
        ],
        runs=5,
        options=ReplayOptions(ledger_db=tmp_path / "state.db", evidence_root=tmp_path / "runs"),
        report_root=tmp_path / "reports",
    )
    assert report.verdict == "broken"
    assert not ledger.confidence(raw).meets(0.5), "A broken sweep still earned a passing score"
    from waypoint.replay.engine import replay
    refused = replay(raw, {"member_id": "99999"}, ReplayOptions(
        ledger_db=tmp_path / "state.db", evidence_root=tmp_path / "runs"))
    assert refused.failure.code == "confidence_too_low"


@pytest.mark.browser
def test_agent_demo_lookup_works_with_its_fresh_ledger(tmp_path):
    import json
    import os
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "scripts/agent_demo.py", "--cassette", "evidence/agent/lookup.json",
         "--workspace", str(tmp_path)], cwd=REPO, capture_output=True, text=True,
        env={**os.environ, "ANTHROPIC_API_KEY": "", "WAYPOINT_NO_DOTENV": "1"}, timeout=110)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["calls"] and all(call["as_recorded"] for call in result["calls"])
    assert "lookup_member_balance" in completed.stdout


def test_successful_resume_closes_the_original_crash_record(tmp_path, monkeypatch):
    from waypoint.artifact.schema import content_hash
    from waypoint.replay.engine import resume
    from waypoint.session.intents import inputs_hash
    from waypoint.session.progress import ProgressStore

    raw = cap_at()
    inputs = {"member_id": "12345"}
    progress = ProgressStore(StateStore(tmp_path / "state.db"))
    progress.start(
        run_id="dead-run",
        capability_id=raw.capability_id,
        version=raw.version,
        variant="base",
        content_hash=content_hash(raw),
        inputs_hash=inputs_hash(raw.capability_id, inputs),
        base_url="http://127.0.0.1",
    )
    monkeypatch.setattr(
        _Run,
        "execute",
        lambda self: ReplayResult(
            status="success",
            capability_id=raw.capability_id,
            version=raw.version,
            run_id="resumed-run",
        ),
    )
    result = resume(
        raw,
        inputs,
        "dead-run",
        ReplayOptions(resume_db=tmp_path / "state.db", evidence_root=tmp_path / "runs"),
    )
    assert result.status == "success"
    assert progress.get("dead-run").status != "running", "Old run can still be resumed again"


def test_operator_console_rejects_cross_origin_control_requests(tmp_path):
    from waypoint.operator.console import create_console
    from waypoint.session.escalation import InterventionStore
    from waypoint.session.lease import LeaseStore

    state_db = tmp_path / "state.db"
    store = StateStore(state_db)
    leases = LeaseStore(store)
    token = leases.acquire("synthetic-run", "AGENT", "review-probe", 60)
    leases.release(token)
    queue = InterventionStore(store)
    iv = queue.open(
        session_id="synthetic-run",
        run_id="synthetic-run",
        capability_id="lookup_member_balance",
        version="1.2.0",
        reason_code="probe",
        message="Synthetic review intervention",
    )
    client = create_console(state_db).test_client()
    response = client.post(
        f"/interventions/{iv.id}/take",
        data={"operator": "review-probe"},
        headers={"Origin": "https://untrusted.example", "Referer": "https://untrusted.example/"},
    )
    assert response.status_code in (400, 403), "Cross-origin POST changed control without a token"
    assert queue.get(iv.id).status == "open"


@pytest.mark.parametrize("variant,origin", [
    ("alpha", "http://127.0.0.1:18222"),
    ("beta", "http://127.0.0.1:18111"),
])
def test_intent_scope_separately_checks_origin_and_variant(tmp_path, variant, origin):
    raw = cap_at("1.0.0", "open_sub_account").model_copy(
        update={"overrides": {"alpha": {}, "beta": {}}})
    inputs = {"member_id": "12345", "account_type": "Money Market", "initial_deposit": "250.00"}
    first = _Run(raw, inputs, ReplayOptions(variant="alpha", base_url="http://127.0.0.1:18111",
                                          state_db=tmp_path / "state.db", evidence_root=tmp_path))
    second = _Run(raw, inputs, ReplayOptions(variant=variant, base_url=origin,
                                           state_db=tmp_path / "state.db", evidence_root=tmp_path))
    try:
        first._intent_begin("steps[7]")
        second._check_unreconciled()
        assert not second.leftovers
    finally:
        first.ev.close()
        second.ev.close()


@pytest.mark.parametrize("legacy", [True, False])
def test_legacy_or_changed_contract_intent_requires_operator(tmp_path, legacy):
    from waypoint.replay.engine import _Stop

    raw = cap_at("1.0.0", "open_sub_account")
    runner = _Run(raw, {}, ReplayOptions(state_db=tmp_path / "state.db", evidence_root=tmp_path))
    try:
        runner.intents.begin(run_id="old", capability_id=raw.capability_id, version="0.9.0",
                             step="steps[7]", inputs_digest=runner.digest,
                             scope=None if legacy else runner.intent_scope,
                             contract_hash=None if legacy else "an older contract")
        with pytest.raises(_Stop) as stopped:
            runner._check_unreconciled()
        assert stopped.value.detail.code == "reconciliation_required"
    finally:
        runner.ev.close()


def test_concurrent_resume_claim_has_one_winner_and_recoverable_successor(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from waypoint.session.progress import ProgressError, ProgressStore

    progress = ProgressStore(StateStore(tmp_path / "state.db"))
    progress.start(run_id="source", capability_id="cap", version="1.0.0", variant="base",
                   content_hash="hash", inputs_hash="inputs", base_url="http://localhost")
    progress.advance("source", 2)

    def claim(successor):
        try:
            progress.claim("source", successor)
            return successor
        except ProgressError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = [item for item in pool.map(claim, ["a", "b"]) if item]
    assert len(winners) == 1
    [winner] = winners
    assert progress.get("source").resumed_by == winner
    assert progress.get(winner).completed_index == 2
    progress.claim(winner, "retry-after-successor-crash")
    assert progress.get(winner).status == "done"


def test_failed_assist_provider_also_consumes_budget(tmp_path, monkeypatch):
    assistant = ScriptedAssistant()
    calls = []

    def fail(request):
        calls.append(request)
        raise RuntimeError("provider failed")

    monkeypatch.setattr(assistant, "choose", fail)
    raw = cap_at("1.3.0")
    runner = _Run(raw, {}, ReplayOptions(assist=assistant, evidence_root=tmp_path))
    monkeypatch.setattr(runner, "_observe", lambda *a, **kw: snapshot())
    try:
        for _ in range(2):
            assert runner._assist(None, "steps[1]", raw.steps[1],
                                  FailureDetail("locator_not_found", "missing")) is None
        assert len(calls) == len(runner.assist_attempts) == 1
        assert runner.assisted == []
        assert "error" in runner.assist_attempts[0]
    finally:
        runner.ev.close()


def test_resume_never_reconstructs_a_prefix_containing_a_mutation(tmp_path, monkeypatch):
    raw = cap_at("1.0.0", "open_sub_account")
    runner = _Run(raw, {}, ReplayOptions(state_db=tmp_path / "state.db", evidence_root=tmp_path))
    monkeypatch.setattr(runner, "_observe", lambda *a, **kw: snapshot())
    actions = []
    monkeypatch.setattr(runner, "_step", lambda *a: actions.append(a))
    try:
        index, detail = runner._resume_point(None, len(raw.steps))
        assert detail.code == "unrecognized_state_after_crash"
        assert actions == []
    finally:
        runner.ev.close()


def test_output_inconsistency_is_persisted_in_confidence(tmp_path, monkeypatch):
    from waypoint.replay import stability

    raw = cap_at()
    ledger = Ledger(StateStore(tmp_path / "state.db"))
    sequence = []

    def different_answers(cap, inputs, options):
        sequence.append(1)
        result = ReplayResult(status="success", capability_id=cap.capability_id,
                              version=cap.version, run_id=f"r{len(sequence)}",
                              outputs={"savings_balance": str(len(sequence))})
        ledger.record(record_for(result, cap, inputs_hash="test", kind="stability"))
        return result

    monkeypatch.setattr(stability, "replay", different_answers)
    report = stability.run_sweep(raw, [Case({"member_id": "12345"}, expect_status="success")],
        runs=5, options=ReplayOptions(ledger_db=tmp_path / "state.db"), report_root=tmp_path)
    assert report.verdict == "broken"
    assert ledger.confidence(raw).verdict == "broken"
    assert ledger.confidence(raw).kept == 0


def test_concurrent_state_open_serializes_schema_migration(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    ready = Barrier(4)

    def open_store(_):
        ready.wait()
        store = StateStore(tmp_path / "new.db")
        with store.connect() as db:
            return {row["name"] for row in db.execute("PRAGMA table_info(intents)")}

    with ThreadPoolExecutor(max_workers=4) as pool:
        columns = list(pool.map(open_store, range(4)))
    assert all({"scope", "contract_hash"} <= names for names in columns)
