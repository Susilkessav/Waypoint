"""What the run ledger will and will not claim. Pure: no browser, no model.

Confidence is evidence about *these exact contents* (R-PKG-2): rows are keyed by the
artifact's content hash, runs against a deliberately broken application never count, and a
handful of successes is never presented as certainty.
"""

from __future__ import annotations

import time
from pathlib import Path

from waypoint.replay.ledger import (
    MIN_RUNS,
    Ledger,
    RunRecord,
    outputs_hash,
    score,
    wilson_lower_bound,
)
from waypoint.session.store import StateStore


def record(**overrides: object) -> RunRecord:
    base: dict[str, object] = dict(
        run_id=f"run-{time.time_ns()}", capability_id="lookup_member_balance", version="1.0.0",
        variant="base", content_hash="sha256:aaa", status="success", code=None, outcome=None,
        inputs_hash="inputs-1", kind="call", injected=None, duration_ms=4000, steps=8,
        degraded=0, recoveries=0, handoffs=0, assisted=0, outputs_hash="sha256:out", at=time.time(),
    )
    base.update(overrides)
    return RunRecord(**base)  # type: ignore[arg-type]


def test_a_few_successes_are_not_certainty() -> None:
    assert wilson_lower_bound(0, 0) == 0.0
    assert wilson_lower_bound(3, 3) < 0.5, "three runs cannot support a strong claim"
    assert wilson_lower_bound(20, 20) > wilson_lower_bound(5, 5)
    assert wilson_lower_bound(9, 10) < wilson_lower_bound(10, 10)


def test_too_few_runs_is_unproven_however_good_they_look() -> None:
    result = score([record() for _ in range(MIN_RUNS - 1)])
    assert result.verdict == "unproven"
    assert result.kept == MIN_RUNS - 1
    assert not result.meets(0.1), "unproven never meets a bar"


def test_runs_that_all_kept_the_contract_without_help_are_stable() -> None:
    result = score([record() for _ in range(MIN_RUNS)])
    assert (result.verdict, result.kept, result.clean) == ("stable", MIN_RUNS, MIN_RUNS)
    assert result.meets(0.5) and not result.meets(0.99)


def test_a_business_outcome_kept_the_contract() -> None:
    runs = [record(status="business_outcome", outcome="member_not_found")
            for _ in range(MIN_RUNS)]
    assert score(runs).verdict == "stable"


def test_needing_a_fallback_or_a_person_is_flaky_not_stable() -> None:
    runs = [record() for _ in range(MIN_RUNS - 1)] + [record(degraded=1)]
    assert score(runs).verdict == "flaky"
    runs = [record() for _ in range(MIN_RUNS - 1)] + [record(recoveries=1)]
    assert score(runs).verdict == "flaky"
    runs = [record() for _ in range(MIN_RUNS - 1)] + [record(handoffs=1)]
    assert score(runs).verdict == "flaky"


def test_one_failure_makes_it_broken() -> None:
    runs = [record() for _ in range(MIN_RUNS)] + [record(status="failure", code="hard_failure")]
    result = score(runs)
    assert result.verdict == "broken" and not result.meets(0.0)


def test_evidence_does_not_cross_content_hashes_or_injections(tmp_path: Path) -> None:
    ledger = Ledger(StateStore(tmp_path / "state.db"))
    ledger.record(record())
    ledger.record(record(content_hash="sha256:edited"))
    ledger.record(record(injected="500", status="failure"))

    kept = ledger.runs("lookup_member_balance", "1.0.0", "sha256:aaa")
    assert len(kept) == 1, "another version's runs, and injected ones, say nothing about this"
    assert len(ledger.runs("lookup_member_balance", "1.0.0", "sha256:aaa",
                           include_injected=True)) == 2
    assert ledger.runs("lookup_member_balance", "9.9.9", "sha256:aaa") == []


def test_a_run_that_never_started_is_not_evidence_about_the_flow() -> None:
    """An unreachable application, or a refusal before the browser, says nothing about it."""
    from waypoint.replay.engine import NOT_EVIDENCE

    assert {"entry_unreachable", "artifact_not_approved", "invalid_input",
            "confidence_too_low"} <= NOT_EVIDENCE
    assert "hard_failure" not in NOT_EVIDENCE, "a real failure is exactly what must count"


def test_an_answer_is_identified_without_being_kept() -> None:
    first = outputs_hash({"savings_balance": "$4,281.19", "account_status": "active"})
    again = outputs_hash({"account_status": "active", "savings_balance": "$4,281.19"})
    other = outputs_hash({"savings_balance": "$912.04", "account_status": "dormant"})
    assert first == again != other
    assert first and "4,281" not in first and "active" not in first
    assert outputs_hash(None) is None


def test_an_unevaluated_stability_run_cannot_earn_confidence():
    rows = [record(kind="stability") for _ in range(MIN_RUNS)]
    assert not score(rows).meets(0.0)
