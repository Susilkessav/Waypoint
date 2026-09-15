"""Measuring a capability instead of trusting it, and gating unattended use on the result.

A sweep replays the declared cases, checks each against what it was supposed to produce,
and reports what the runs support. An artifact may then declare the reliability it must be
measured at before it runs unattended; until the runs exist, it does not run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from waypoint.artifact.approval import approve
from waypoint.artifact.schema import Capability, content_hash, load
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.replay.ledger import MIN_RUNS, Ledger
from waypoint.replay.stability import Case, StabilityRefused, load_cases, run_sweep
from waypoint.session.store import StateStore

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parents[1]
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}


@pytest.fixture(scope="module")
def approved() -> Capability:
    return approve(load(REPO / "capabilities" / "lookup_member_balance" / "1.2.0.json"),
                   approver="test")


def options(live_server: str, tmp_path: Path, **kw: object) -> ReplayOptions:
    return ReplayOptions(base_url=live_server, evidence_root=tmp_path / "runs",
                         state_db=tmp_path / "state.db", ledger_db=tmp_path / "state.db",
                         secrets=SecretBroker(environ=CREDENTIALS), capture_steps=False,
                         **kw)  # type: ignore[arg-type]


def test_a_sweep_reports_what_the_runs_support(approved: Capability, live_server: str,
                                               tmp_path: Path) -> None:
    cases = [
        Case(inputs={"member_id": "12345"}, name="found", expect_status="success"),
        Case(inputs={"member_id": "00000"}, name="missing", expect_status="business_outcome",
             expect_outcome="member_not_found"),
    ]
    report = run_sweep(approved, cases, runs=3, options=options(live_server, tmp_path),
                       report_root=tmp_path / "stability")

    assert report.runs == 6 and report.passed == 6
    assert report.verdict == "stable", report.confidence
    assert [c.case for c in report.cases] == ["found", "missing"]
    assert all(c.same_outputs for c in report.cases), "same inputs, same answer, every time"
    assert all(not c.mismatches for c in report.cases)
    assert report.cases[0].median_ms > 0 and report.cases[0].p95_ms >= report.cases[0].median_ms
    assert report.confidence["runs"] == 6 and report.confidence["clean"] == 6

    [directory] = list((tmp_path / "stability").iterdir())
    assert (directory / "report.json").exists()
    markdown = (directory / "report.md").read_text()
    assert "# Stability: lookup_member_balance 1.2.0" in markdown
    assert "found" in markdown and "missing" in markdown

    ledger = Ledger(StateStore(tmp_path / "state.db"))
    assert ledger.confidence(approved).runs == 6, "every run was recorded against this content"


def test_the_wrong_kind_of_answer_counts_as_a_failure(approved: Capability, live_server: str,
                                                      tmp_path: Path) -> None:
    """A run that 'succeeded' when the case expected a business outcome is not a pass."""
    cases = [Case(inputs={"member_id": "12345"}, name="mislabelled",
                  expect_status="business_outcome", expect_outcome="member_not_found")]
    report = run_sweep(approved, cases, runs=2, options=options(live_server, tmp_path),
                       report_root=tmp_path / "stability")
    assert report.passed == 0 and report.verdict == "broken"
    assert len(report.cases[0].mismatches) == 2
    assert "expected business_outcome, got success" in report.cases[0].mismatches[0]


def test_an_injected_failure_is_reported_but_never_counted(approved: Capability,
                                                           live_server: str,
                                                           tmp_path: Path) -> None:
    cases = [Case(inputs={"member_id": "12345"}, name="server-error", inject="500",
                  expect_status="failure")]
    report = run_sweep(approved, cases, runs=2, options=options(live_server, tmp_path),
                       report_root=tmp_path / "stability")
    assert report.cases[0].statuses == {"failure": 2}
    assert not report.cases[0].mismatches, "it failed exactly as the case said it should"
    ledger = Ledger(StateStore(tmp_path / "state.db"))
    assert ledger.confidence(approved).runs == 0, "breaking the app on purpose proves nothing"
    recorded = ledger.runs(approved.capability_id, approved.version, content_hash(approved),
                           include_injected=True)
    assert len(recorded) == 2 and all(r.injected == "500" for r in recorded)


def test_a_capability_that_commits_is_not_swept_by_accident(live_server: str,
                                                            tmp_path: Path) -> None:
    cap = approve(load(REPO / "capabilities" / "open_sub_account" / "1.0.0.json"),
                  approver="test")
    with pytest.raises(StabilityRefused, match="irreversible"):
        run_sweep(cap, [Case(inputs={"member_id": "12345", "account_type": "Money Market",
                                     "initial_deposit": "250.00"})],
                  runs=2, options=options(live_server, tmp_path),
                  report_root=tmp_path / "stability")


def test_unattended_replay_waits_for_the_confidence_it_asks_for(approved: Capability,
                                                                live_server: str,
                                                                tmp_path: Path) -> None:
    demanding = approve(
        approved.model_copy(update={
            "policy": approved.policy.model_copy(update={"min_confidence": 0.5}),
            "provenance": approved.provenance.model_copy(update={"approval": {"base": "draft"}}),
        }),
        approver="test",
    )
    refused = replay(demanding, {"member_id": "12345"}, options(live_server, tmp_path))
    assert refused.status == "failure"
    assert refused.failure is not None and refused.failure.code == "confidence_too_low"
    assert "0.50" in refused.failure.message

    report = run_sweep(demanding, [Case(inputs={"member_id": "12345"})], runs=MIN_RUNS,
                       options=options(live_server, tmp_path),
                       report_root=tmp_path / "stability")
    assert report.verdict == "stable"

    allowed = replay(demanding, {"member_id": "12345"}, options(live_server, tmp_path))
    assert allowed.status == "success", allowed.failure


def test_cases_can_be_declared_in_a_file(tmp_path: Path) -> None:
    path = tmp_path / "cases.yaml"
    path.write_text(
        "cases:\n"
        "  - name: found\n    inputs: {member_id: 12345}\n    expect_status: success\n"
        "  - name: missing\n    inputs: {member_id: '00000'}\n"
        "    expect_status: business_outcome\n    expect_outcome: member_not_found\n"
    )
    found, missing = load_cases(path)
    assert found.inputs == {"member_id": "12345"} and found.expect_status == "success"
    assert missing.expect_outcome == "member_not_found" and missing.label == "missing"
