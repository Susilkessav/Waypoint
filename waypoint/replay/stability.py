"""Replaying a capability N times and saying, honestly, how reliable it is.

One green run proves a capability *can* work. Production needs to know whether it works
*every* time, and what it needed along the way. A sweep replays the declared cases, records
every run in the ledger, and reports:

* how many runs kept their contract, and how many needed nothing to do it;
* whether the same inputs produced the same outputs every time - the determinism claim,
  checked by comparing hashes of the outputs rather than keeping them;
* per step, whether the same locator tier resolved each time, and how close each checkpoint
  came to its timeout, so a step that is quietly getting slower shows up before it fails;
* what each case was expected to produce, when the cases declare it, so a run that
  "succeeded" with the wrong kind of answer counts as a failure.

Irreversible capabilities are refused unless the caller both passes ``allow_irreversible``
and points the sweep at a local fixture: repeating a step that moves money to measure
reliability is exactly the mistake this system exists to avoid (R-REC-1).
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from waypoint.artifact.schema import Capability, content_hash
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.replay.ledger import Confidence, Ledger, RunRecord, record_for, score
from waypoint.replay.result import ReplayResult
from waypoint.session.intents import inputs_hash
from waypoint.session.store import StateStore

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


class StabilityRefused(Exception):
    """The sweep would repeat something it must not repeat."""


@dataclass(frozen=True)
class Case:
    """One set of inputs, and what it is expected to produce."""

    inputs: dict[str, str]
    name: str = ""
    expect_status: str | None = None
    expect_outcome: str | None = None
    inject: str | None = None

    @property
    def label(self) -> str:
        return self.name or (self.inject or "default")

    def mismatch(self, result: ReplayResult) -> str | None:
        if self.expect_status and result.status != self.expect_status:
            return f"expected {self.expect_status}, got {result.status}"
        if self.expect_outcome and result.outcome != self.expect_outcome:
            return f"expected outcome {self.expect_outcome}, got {result.outcome or 'none'}"
        return None


@dataclass
class CaseReport:
    case: str
    runs: int = 0
    matched: int = 0
    """Runs that produced what the case declared - including failing where that was declared."""
    statuses: dict[str, int] = field(default_factory=dict)
    same_outputs: bool = True
    """Every run returned the same answer for the same inputs."""
    mismatches: list[str] = field(default_factory=list)
    median_ms: int = 0
    p95_ms: int = 0
    tier_changes: list[str] = field(default_factory=list)
    recoveries: int = 0
    degraded: int = 0


@dataclass
class StabilityReport:
    capability_id: str
    version: str
    content_hash: str
    started_at: str
    runs: int
    passed: int
    """Runs that produced what their case declared."""
    verdict: str
    confidence: dict[str, Any]
    cases: list[CaseReport]
    slow_steps: list[dict[str, Any]]
    """Steps whose checkpoint regularly waits out most of its allowance."""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        return (f"{self.verdict}: {self.passed}/{self.runs} runs did what their case declared "
                f"({self.confidence.get('verdict')}, score {self.confidence.get('score')})")


def _percentile(values: Sequence[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _step_facts(result: ReplayResult, evidence_root: Path
                ) -> tuple[dict[str, int], dict[str, int]]:
    """Per step: how long its checkpoint took, and which locator tier resolved it.

    Both come from the run's own event log, because one flow legitimately uses different
    tiers for different steps - what matters is whether *one* step changed tier between
    runs (R-LOC-4).
    """
    path = evidence_root / result.run_id / "events.jsonl"
    waits: dict[str, int] = {}
    tiers: dict[str, int] = {}
    started: dict[str, float] = {}
    if not path.exists():
        return waits, tiers
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        where = event.get("where")
        if not isinstance(where, str):
            continue
        at = datetime.fromisoformat(event["at"]).timestamp()
        if event.get("event") == "action":
            started[where] = at
            if isinstance(event.get("tier"), int):
                tiers[where] = int(event["tier"])
        elif event.get("event") == "checkpoint_met" and where in started:
            waits[where] = int((at - started[where]) * 1000)
    return waits, tiers


def _is_local(url: str) -> bool:
    return (urlsplit(url).hostname or "") in LOCAL_HOSTS


def run_sweep(cap: Capability, cases: Sequence[Case], *, runs: int, options: ReplayOptions,
              allow_irreversible: bool = False,
              report_root: Path = Path("evidence/stability")) -> StabilityReport:
    """Replay every case ``runs`` times, record each run, and report what they showed."""
    if any(step.risk == "irreversible" for _, step in cap.all_steps()):
        target = options.base_url or cap.surface.entry
        if not (allow_irreversible and _is_local(target)):
            raise StabilityRefused(
                f"{cap.capability_id} has an irreversible step: repeating it would repeat the "
                "operation. Pass allow_irreversible and point the sweep at a local fixture.")
    if options.ledger_db is None:
        raise StabilityRefused("a sweep must record its runs: set ledger_db")
    ledger = Ledger(StateStore(options.ledger_db))
    started = datetime.now(UTC)
    reports: list[CaseReport] = []
    records: list[RunRecord] = []
    evidence: list[str] = []
    waits: dict[str, list[tuple[int, int]]] = defaultdict(list)
    passed = 0

    for case in cases:
        report = CaseReport(case=case.label)
        statuses: Counter[str] = Counter()
        answers: set[str] = set()
        durations: list[int] = []
        tiers: dict[str, set[int]] = defaultdict(set)
        matched = 0
        case_start = len(records)
        for _ in range(runs):
            result = replay(cap, dict(case.inputs), ReplayOptions(
                **{**asdict_options(options), "kind": "stability",
                   "injected": case.inject or options.injected,
                   # Measuring is how a capability earns its confidence, so a sweep is never
                   # stopped by the bar it is measuring against.
                   "require_confidence": False,
                   "after_preconditions": case_injector(case) or options.after_preconditions}))
            record = record_for(result, cap, inputs_hash=inputs_hash(cap.capability_id,
                                                                    case.inputs),
                                kind="stability", injected=case.inject or options.injected)
            evidence.append(str(result.evidence_dir))
            statuses[result.status] += 1
            report.runs += 1
            report.recoveries += len(result.telemetry.get("recoveries") or ())
            report.degraded += int(result.telemetry.get("degradations") or 0)
            durations.append(record.duration_ms)
            answers.add(record.outputs_hash or "none")
            mismatch = case.mismatch(result)
            record = replace(record, contract_ok=mismatch is None, contract_reason=mismatch)
            records.append(record)
            ledger.evaluate(result.run_id, ok=mismatch is None, reason=mismatch)
            if mismatch:
                report.mismatches.append(f"{result.run_id}: {mismatch}")
            elif case.expect_status or record.kept:
                # It did what the case said it should - including failing on purpose.
                matched += 1
                passed += 1
            step_waits, step_tiers = _step_facts(result, Path(options.evidence_root))
            for where, waited in step_waits.items():
                step = next((s for w, s in cap.all_steps() if w == where), None)
                if step is not None:
                    waits[where].append((waited, step.timeout_ms))
            for where, tier in step_tiers.items():
                tiers[where].add(tier)
        report.statuses = dict(statuses)
        report.same_outputs = len(answers) <= 1
        if not report.same_outputs:
            for index in range(case_start, len(records)):
                record = records[index]
                reason = record.contract_reason or "same case returned inconsistent outputs"
                records[index] = replace(record, contract_ok=False, contract_reason=reason)
                ledger.evaluate(record.run_id, ok=False, reason=reason)
        report.median_ms = int(statistics.median(durations)) if durations else 0
        report.p95_ms = _percentile(durations, 0.95)
        report.matched = matched
        for where, seen in sorted(tiers.items()):
            if len(seen) > 1:
                report.tier_changes.append(
                    f"{where} resolved at different tiers across runs "
                    f"({', '.join(str(t) for t in sorted(seen))}): the recorded locator is not "
                    "matching every time")
        reports.append(report)

    slow = [
        {"step": where, "median_wait_ms": int(statistics.median(w for w, _ in samples)),
         "timeout_ms": samples[0][1],
         "note": "waits out most of its allowance; raise the timeout or expect flakes"}
        for where, samples in sorted(waits.items())
        if statistics.median(w for w, _ in samples) > 0.6 * samples[0][1]
    ]
    counted = [r for r in records if r.injected is None]
    confidence: Confidence = (score(counted) if counted
                              else ledger.confidence(cap, options.variant))
    verdict = ("broken" if any(r.mismatches for r in reports) or
               any(not r.same_outputs for r in reports) else confidence.verdict)
    sweep = StabilityReport(
        capability_id=cap.capability_id, version=cap.version,
        content_hash=content_hash(cap, options.variant),
        started_at=started.isoformat(timespec="seconds"), runs=len(records), passed=passed,
        verdict=verdict, confidence=confidence.to_dict(), cases=reports, slow_steps=slow,
        evidence=evidence,
    )
    write_report(sweep, report_root)
    return sweep


def asdict_options(options: ReplayOptions) -> dict[str, Any]:
    """A shallow copy of the options; the sweep overrides what each run needs."""
    return {f: getattr(options, f) for f in options.__dataclass_fields__
            if f not in ("kind", "injected", "after_preconditions", "require_confidence")}


def case_injector(case: Case) -> Callable[[Any, str], None] | None:
    """Turn on a case's fixture injection, the way the CLI's --inject does.

    The local fixture takes its failures from a query parameter; nothing here imports it,
    and a case without an injection leaves the caller's own hook alone.
    """
    if not case.inject:
        return None

    def apply(surface: Any, origin: str) -> None:
        from waypoint.surface.ports import Action

        surface.act(Action("navigate", url=f"{origin}/console?inject={quote(case.inject or '')}",
                           intent=f"fixture: inject {case.inject}"))

    return apply


def write_report(report: StabilityReport, root: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    directory = Path(root) / f"{report.capability_id}-{report.version}-{stamp}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
    (directory / "report.md").write_text(as_markdown(report))
    return directory


def as_markdown(report: StabilityReport) -> str:
    lines = [
        f"# Stability: {report.capability_id} {report.version}",
        "",
        f"- **Verdict:** {report.verdict}",
        f"- **Runs:** {report.runs} ({report.passed} did what their case declared)",
        f"- **Confidence:** {report.confidence.get('verdict')} · "
        f"score {report.confidence.get('score')} · {report.confidence.get('clean')} clean",
        f"- **Content hash:** `{report.content_hash}`",
        f"- **Started:** {report.started_at}",
        "",
        "| Case | Runs | As declared | Statuses | Same answer | Median | p95 | Recoveries |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in report.cases:
        statuses = ", ".join(f"{k} ×{v}" for k, v in sorted(case.statuses.items()))
        lines.append(
            f"| {case.case} | {case.runs} | {case.matched}/{case.runs} | {statuses} "
            f"| {'yes' if case.same_outputs else 'NO'} | {case.median_ms} ms | {case.p95_ms} ms "
            f"| {case.recoveries} |")
    problems = [m for case in report.cases for m in case.mismatches]
    problems += [c for case in report.cases for c in case.tier_changes]
    if problems:
        lines += ["", "## Problems", ""] + [f"- {p}" for p in problems]
    if report.slow_steps:
        lines += ["", "## Steps close to their timeout", ""]
        lines += [f"- `{s['step']}`: median {s['median_wait_ms']} ms of {s['timeout_ms']} ms - "
                  f"{s['note']}" for s in report.slow_steps]
    return "\n".join(lines) + "\n"


def load_cases(path: Path) -> list[Case]:
    """Cases as JSON or YAML: a list of {name, inputs, expect_status, expect_outcome, inject}."""
    import yaml

    raw = yaml.safe_load(path.read_text()) or []
    if isinstance(raw, dict):
        raw = raw.get("cases", [])
    cases = []
    for entry in raw:
        cases.append(Case(
            inputs={k: str(v) for k, v in (entry.get("inputs") or {}).items()},
            name=str(entry.get("name", "")),
            expect_status=entry.get("expect_status"),
            expect_outcome=entry.get("expect_outcome"),
            inject=entry.get("inject"),
        ))
    return cases
