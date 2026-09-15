"""One row per replay, and the confidence computed from them.

A capability that replays is not the same as a capability that replays *reliably*. The
ledger keeps what every run cost and needed - how long it took, whether a locator had to
fall back, whether a declared recovery fired, whether a person was asked - so reliability
is measured rather than assumed, and a capability that starts degrading is visible before
it fails (R-LOC-4).

Two rules keep the measurement honest:

* **Bound to content, like approval (R-PKG-2).** Rows are keyed by the artifact's content
  hash, so evidence gathered for one version never speaks for an edited one.
* **Injected runs never count.** A stability sweep that deliberately breaks the
  application proves error handling, not unreliability; those rows are recorded and
  reported but excluded from the score. Nor do runs that never started - an unapproved
  artifact, a bad argument, an application that was not reachable at all.

The score is the lower bound of a 95% Wilson interval on "kept its contract", so a run of
three successes cannot present itself as certainty. ``clean`` is stricter than kept: no
fallback, no recovery, no handoff, no assisted step - the share of runs that needed
nothing but the artifact.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from waypoint.artifact.schema import Capability, content_hash
from waypoint.replay.result import ReplayResult
from waypoint.session.store import StateStore

Kind = Literal["call", "stability"]
Verdict = Literal["unproven", "stable", "flaky", "broken"]

KEPT = ("success", "business_outcome")
"""Statuses that kept the contract: a named business answer is a correct result (R-OUT-2)."""

MIN_RUNS = 5
"""Below this, no amount of success is evidence; the verdict is ``unproven``."""

DEFAULT_THRESHOLD = 0.8
"""Used when an artifact asks for a confidence gate without naming a bar."""


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    capability_id: str
    version: str
    variant: str
    content_hash: str
    status: str
    code: str | None
    outcome: str | None
    inputs_hash: str
    kind: Kind
    injected: str | None
    duration_ms: int
    steps: int
    degraded: int
    recoveries: int
    handoffs: int
    assisted: int
    outputs_hash: str | None
    at: float
    contract_ok: bool | None = None
    contract_reason: str | None = None

    @property
    def kept(self) -> bool:
        return (self.status in KEPT and self.contract_ok is not False
                and (self.kind != "stability" or self.contract_ok is True))

    @property
    def clean(self) -> bool:
        return self.kept and not (self.degraded or self.recoveries or self.handoffs
                                  or self.assisted)


@dataclass(frozen=True)
class Confidence:
    verdict: Verdict
    score: float
    """Lower bound of a 95% Wilson interval on the share of runs that kept the contract."""
    runs: int
    kept: int
    clean: int
    degraded_runs: int
    median_ms: int
    measured_at: float | None
    reasons: tuple[str, ...] = ()

    def meets(self, threshold: float) -> bool:
        return self.verdict in ("stable", "flaky") and self.score >= threshold

    def summary(self) -> str:
        if not self.runs:
            return "no runs recorded"
        return (f"{self.verdict} · {self.kept}/{self.runs} kept · {self.clean} clean · "
                f"score {self.score:.2f}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def outputs_hash(outputs: Mapping[str, str] | None) -> str | None:
    """Identifies *which* answer a run returned without keeping the answer itself."""
    if outputs is None:
        return None
    canonical = json.dumps(dict(sorted(outputs.items())), separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()[:32]


def wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    if total == 0:
        return 0.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denominator)


class Ledger:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def record(self, record: RunRecord) -> None:
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO runs (run_id, capability_id, version, variant, "
                "content_hash, status, code, outcome, inputs_hash, kind, injected, "
                "duration_ms, steps, degraded, recoveries, handoffs, assisted, outputs_hash, at, "
                "contract_ok, contract_reason)"
                " VALUES (:run_id, :capability_id, :version, :variant, :content_hash, :status, "
                ":code, :outcome, :inputs_hash, :kind, :injected, :duration_ms, :steps, "
                ":degraded, :recoveries, :handoffs, :assisted, :outputs_hash, :at, "
                ":contract_ok, :contract_reason)",
                asdict(record),
            )

    def evaluate(self, run_id: str, *, ok: bool, reason: str | None = None) -> None:
        """Store the case verdict beside the run; an unfinished sweep earns no confidence."""
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET contract_ok = ?, contract_reason = ? WHERE run_id = ?",
                       (ok, reason, run_id))

    def runs(self, capability_id: str, version: str, hash_: str, *,
             limit: int = 50, include_injected: bool = False) -> list[RunRecord]:
        sql = ("SELECT * FROM runs WHERE capability_id = ? AND version = ? "
               "AND content_hash = ?"
               + ("" if include_injected else " AND injected IS NULL")
               + " ORDER BY at DESC LIMIT ?")
        with self.store.connect() as db:
            rows = db.execute(sql, (capability_id, version, hash_, limit)).fetchall()
        return [RunRecord(**{**dict(row), "contract_ok":
                             None if row["contract_ok"] is None else bool(row["contract_ok"])})
                for row in rows]

    def confidence(self, cap: Capability, *, limit: int = 50) -> Confidence:
        return score(self.runs(cap.capability_id, cap.version, content_hash(cap), limit=limit))


def score(runs: Sequence[RunRecord]) -> Confidence:
    """The verdict these runs support - never more than they support."""
    total = len(runs)
    kept = sum(1 for r in runs if r.kept)
    clean = sum(1 for r in runs if r.clean)
    degraded = sum(1 for r in runs if r.kept and not r.clean)
    durations = sorted(r.duration_ms for r in runs)
    median = durations[len(durations) // 2] if durations else 0
    bound = wilson_lower_bound(kept, total)
    reasons: list[str] = []
    verdict: Verdict
    if total < MIN_RUNS:
        verdict = "unproven"
        reasons.append(f"{total} run(s) recorded for this content; {MIN_RUNS} is the minimum")
    elif kept < total:
        verdict = "broken"
        reasons.append(f"{total - kept} of {total} runs did not keep the contract")
    elif clean < total:
        verdict = "flaky"
        reasons.append(f"{degraded} of {total} runs needed a fallback, a recovery, a person "
                       "or an assisted step")
    else:
        verdict = "stable"
    return Confidence(verdict=verdict, score=round(bound, 4), runs=total, kept=kept, clean=clean,
                      degraded_runs=degraded, median_ms=median,
                      measured_at=max((r.at for r in runs), default=None),
                      reasons=tuple(reasons))


def record_for(result: ReplayResult, cap: Capability, *, inputs_hash: str, kind: Kind = "call",
               injected: str | None = None, outputs: Mapping[str, str] | None = None
               ) -> RunRecord:
    telemetry = result.telemetry
    return RunRecord(
        run_id=result.run_id,
        capability_id=result.capability_id,
        version=result.version,
        variant=result.variant,
        content_hash=content_hash(cap),
        status=result.status,
        code=result.failure.code if result.failure else None,
        outcome=result.outcome,
        inputs_hash=inputs_hash,
        kind=kind,
        injected=injected,
        duration_ms=int(telemetry.get("duration_ms") or 0),
        steps=int(telemetry.get("steps") or 0),
        degraded=int(telemetry.get("degradations") or 0),
        recoveries=len(telemetry.get("recoveries") or ()),
        handoffs=len(telemetry.get("handoffs") or ()),
        assisted=len(telemetry.get("assist_attempts") or telemetry.get("assisted") or ()),
        outputs_hash=outputs_hash(outputs if outputs is not None else result.outputs),
        at=time.time(),
    )
