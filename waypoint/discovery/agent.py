"""The discovery loop: a goal in, a transcript out (PLAN.md 3.1, 6.10).

observe -> decide -> act, on the same surface, policy engine and redactor replay uses.
The decider (a live model, or a cassette of one) only ever *proposes*; this loop owns
every side effect:

* Element ids in a decision are prompt-local ("e12") and resolved here against the
  snapshot the decider was shown, so a decision can never reach an element it did not see.
* Values are resolved here too. ``$inputs.x`` and ``$secrets.x`` are the only forms the
  model is asked to use; a literal equal to a bound value is rewritten to its reference
  (and noted), and any other literal is typed but recorded only by length - it blocks
  approval until a human decides what it should be (PLAN.md 6.10).
* Every action goes through ``WebSurface.act``, so the policy engine sits under it
  exactly as in replay. Discovery is attended: a risky action is put to the operator's
  approval callback, and with no operator it is refused.
* Before acting, a locator bundle is synthesized for the target while it is still live
  - the compiler cannot do that afterwards.
* A finish is checked exactly as the compiler will check it - outputs that a later run
  can locate, a success condition true on this screen - and what fails goes back to the
  model as a correction, a bounded number of times. Found at the end of a run, the same
  problem could no longer be fixed.

The run stops on finish, give_up, a refusal, a cassette mismatch, the step limit, or a
screen that has not changed for ``stuck_after`` turns.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from waypoint.compiler.compile import finish_problems, locator_problem, nomination_holds
from waypoint.discovery.cassette import CassetteMismatch, Decider, DecisionContext
from waypoint.discovery.decisions import Decision
from waypoint.discovery.transcript import (
    BindingSpec,
    FinishRecord,
    OutputSpecDecl,
    Step,
    Transcript,
    snapshot_to_dict,
)
from waypoint.evidence import EvidenceWriter
from waypoint.policy.engine import PolicyConfig, PolicyEngine, RequireApproval, RunContext
from waypoint.policy.redactor import Redactor, binding_placeholder
from waypoint.policy.secrets import SecretBroker
from waypoint.surface.ports import (
    Action,
    ActionKind,
    ActionResult,
    Sensitivity,
    UIElement,
    UISnapshot,
)
from waypoint.surface.sensitivity import Binding
from waypoint.surface.web import WebSurface

_GOAL_VAR = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class InputBinding:
    name: str
    value: str = field(repr=False)
    type: str = "string"
    sensitivity: Sensitivity = "internal"


@dataclass
class DiscoveryOptions:
    capability_id: str
    goal: str
    """May reference inputs as {{name}}; rendered and scrubbed before model use or evidence."""
    entry: str
    inputs: Sequence[InputBinding]
    outputs: Sequence[OutputSpecDecl]
    secret_names: Sequence[str] = ("meridian_user", "meridian_password")
    max_steps: int = 20
    stuck_after: int = 3
    finish_corrections: int = 2
    """How many times a finish that would not compile goes back to the model to fix."""
    evidence_root: Path = Path("evidence/runs")
    headed: bool = False
    secrets: SecretBroker | None = None
    approve: Callable[[RequireApproval, Action], bool] | None = None
    """The attending operator. None refuses every action that needs approval."""


@dataclass(frozen=True)
class DiscoveryResult:
    transcript: Transcript
    run_dir: Path
    transcript_path: Path


class _Reject(Exception):
    """A decision that cannot be executed; the reason goes back to the model."""


def render_goal(goal: str, input_names: Sequence[str]) -> str:
    def swap(m: re.Match[str]) -> str:
        if m.group(1) not in input_names:
            raise ValueError(f"goal references undeclared input {m.group(1)!r}")
        return f"$inputs.{m.group(1)}"

    return _GOAL_VAR.sub(swap, goal)


def _describe(result: ActionResult) -> str:
    if result.ok:
        return "Done." + (" The page changed." if result.navigated else "")
    if result.error_code == "approval_required":
        return f"Not performed: it needs human approval ({result.error}). Find another way."
    if result.error_code == "policy_block":
        return f"Refused by policy ({result.error})."
    if result.error_code == "in_flight":
        return "Dispatched, but whether it completed is unknown. Do not repeat it."
    return f"Failed: {result.error or result.error_code}."


def discover(decider: Decider, options: DiscoveryOptions) -> DiscoveryResult:
    return _Discovery(decider, options).run()


class _Discovery:
    def __init__(self, decider: Decider, options: DiscoveryOptions) -> None:
        self.decider = decider
        self.opt = options
        self.values = {b.name: b.value for b in options.inputs}
        self.bindings = [Binding(b.name, b.value, b.sensitivity) for b in options.inputs]
        self.redactor = Redactor(self.bindings)
        self.rendered = {b.name: binding_placeholder(b.name) for b in options.inputs
                         if b.sensitivity != "public"}
        self.goal = self.redactor.scrub(
            render_goal(options.goal, [b.name for b in options.inputs]))
        self.run_id = EvidenceWriter.new_run_id()
        self.ev = EvidenceWriter(options.evidence_root, self.run_id, self.redactor)
        self.corrections = 0
        self.transcript = Transcript(
            run_id=self.run_id,
            capability_id=options.capability_id,
            goal=self.goal,
            entry=options.entry,
            model=decider.model,
            bindings=[BindingSpec(b.name, b.type, b.sensitivity) for b in options.inputs],
            expected_outputs=list(options.outputs),
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    # ------------------------------------------------------------------ run

    def run(self) -> DiscoveryResult:
        self.ev.json("meta.json", {
            "run_id": self.run_id, "kind": "discovery", "capability_id": self.opt.capability_id,
            "goal": self.goal, "entry": self.opt.entry, "model": self.decider.model,
            "inputs": [asdict(b) for b in self.transcript.bindings],
        }, scrub=False)
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(self.opt.entry))
        try:
            with WebSurface.launch(
                headed=self.opt.headed,
                bindings=self.bindings,
                policy=PolicyEngine(PolicyConfig.for_origin(origin)),
                # Attended: routine actions proceed, risky ones go to the operator.
                context=RunContext(unattended=False, state_known=True),
                secrets=self.opt.secrets or SecretBroker(),
                approve=self.opt.approve or (lambda _verdict, _action: False),
            ) as surface:
                self._loop(surface)
                self._capture(surface)
        except Exception as exc:  # the transcript and evidence must survive any failure
            self._end("error", self.redactor.error(exc))
        finally:
            self.transcript.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
            exchanges = getattr(self.decider, "exchanges", None)
            if exchanges:
                self.ev.json("llm_exchanges.json", exchanges)
            usage = getattr(self.decider, "usage", None)
            self.ev.event("finished", ending=self.transcript.ending,
                          detail=self.transcript.ending_detail, usage=usage)
            self.ev.close()
        path = self.ev.dir / "transcript.json"
        self.transcript.save(path)
        return DiscoveryResult(self.transcript, self.ev.dir, path)

    def _end(self, ending: Any, detail: str = "") -> None:
        if self.transcript.ending is None:
            self.transcript.ending = ending
            self.transcript.ending_detail = detail

    def _loop(self, surface: WebSurface) -> None:
        opened = surface.act(Action("navigate", url=self.opt.entry, intent="Open the entry page"))
        if not opened.ok:
            self._end("error", f"could not open the entry page: {opened.error}")
            return
        last_result: str | None = "Opened the entry page."
        previous_hash, unchanged, acted = None, 0, False
        for turn in range(self.opt.max_steps):
            snap = surface.observe()
            if self.transcript.steps and self.transcript.steps[-1].post_hash is None:
                self.transcript.steps[-1].post_hash = snap.hash
                last_result = self._unmet(self.transcript.steps[-1], snap, last_result)
            if acted:  # only an action can change the screen; a correction is not one
                unchanged = unchanged + 1 if snap.hash == previous_hash else 0
            previous_hash = snap.hash
            if unchanged >= self.opt.stuck_after:
                self._end("stuck", f"the screen did not change for {unchanged} turns")
                return
            ids = tuple((f"e{i}", e) for i, e in enumerate(snap.elements, 1))
            ctx = DecisionContext(
                turn=turn, goal=self.goal, snapshot=snap, elements=ids,
                input_names=tuple(self.values), secret_names=tuple(self.opt.secret_names),
                output_names=tuple(o.name for o in self.opt.outputs), last_result=last_result,
            )
            try:
                decision = self.decider.decide(ctx)
            except CassetteMismatch as exc:
                self._end("error", str(exc))
                return
            self.ev.event("decision", turn=turn, decision=decision.kind, intent=decision.intent,
                          element=decision.element, reason=decision.reason or None)
            if decision.kind == "give_up":
                refused = decision.reason.startswith("model_refusal")
                self._end("refused" if refused else "gave_up", decision.reason)
                return
            if decision.kind == "recheck":
                last_result, acted = self._recheck(decision, snap), False
                continue
            if decision.kind == "finish":
                feedback = self._finish(surface, decision, dict(ids), snap)
                if feedback is None:
                    return
                last_result, acted = feedback, False
                continue
            last_result, acted = self._act(surface, turn, decision, dict(ids), snap), True
        self._end("max_steps", f"stopped after {self.opt.max_steps} turns")

    # ---------------------------------------------------------------- steps

    def _value(self, raw: str | None) -> tuple[str | None, str | None, str | None]:
        """(recorded reference, provenance, value to type)."""
        if raw is None:
            return None, None, None
        if raw.startswith("$inputs."):
            name = raw.removeprefix("$inputs.")
            if name not in self.values:
                raise _Reject(f"there is no input named {name!r}")
            return raw, "input reference", self.values[name]
        if raw.startswith("$secrets."):
            if raw.removeprefix("$secrets.") not in self.opt.secret_names:
                raise _Reject(f"there is no credential named {raw!r}")
            return raw, "credential reference", raw  # the broker resolves it at type time
        for name, value in self.values.items():
            if raw.strip() == value.strip():
                return f"$inputs.{name}", f"literal matched input {name!r}; rewritten", value
        return None, f"unbound literal of {len(raw)} characters, not recorded", raw

    def _act(self, surface: WebSurface, turn: int, decision: Decision,
             ids: dict[str, UIElement], snap: Any) -> str:
        record = decision.to_dict()
        element = ids.get(decision.element or "")
        step = Step(turn=turn, pre=snapshot_to_dict(snap), decision=record, action=decision.kind)
        try:
            if decision.kind != "key" and element is None:
                raise _Reject(f"{decision.element!r} is not an element on this screen")
            value_ref, provenance, typed = self._value(decision.value)
        except _Reject as exc:
            step.error_code, step.error = "invalid_decision", str(exc)
            self.transcript.steps.append(step)
            return f"Not performed: {exc}."
        if decision.value is not None:
            record["value"] = value_ref or f"‹unbound literal: {len(decision.value)} chars›"
        step.value_ref, step.value_provenance = value_ref, provenance
        if element is not None:
            step.target = asdict(element)
            try:
                bundle = surface.synthesize(element.ref, self.values)
                step.bundle = bundle.model_dump(mode="json")
            except ValueError as exc:  # no unique, verified locator for this target
                step.bundle_error = locator_problem(exc)
        events_before = len(surface.events)
        kind = cast(ActionKind, decision.kind)  # click | type | select | key, checked above
        action = Action(
            kind,
            ref=None if decision.kind == "key" or element is None else element.ref,
            value=decision.key if decision.kind == "key" else typed,
            intent=decision.intent,
        )
        result = surface.act(action)
        step.ok, step.error_code, step.error = result.ok, result.error_code, result.error
        step.navigated = result.navigated
        for e in surface.events[events_before:]:
            if e.get("event") in ("action_approved", "approval_required") and e.get("risk"):
                step.risk = str(e["risk"])
        self.transcript.steps.append(step)
        self.ev.event("action", turn=turn, action=decision.kind, ok=result.ok,
                      error_code=result.error_code, risk=step.risk, value_ref=value_ref,
                      bundle=bool(step.bundle))
        return _describe(result)

    def _recheck(self, decision: Decision, snap: UISnapshot) -> str:
        """Restate the last action's expectation - kept only if it is true on this screen."""
        assert decision.expect is not None
        step = self.transcript.steps[-1] if self.transcript.steps else None
        if step is None or not step.ok:
            return "Nothing to restate: there is no completed action to attach it to."
        expect = asdict(decision.expect)
        if not nomination_holds(expect, snap, self.rendered):
            return ("Still not true on this screen. Name elements from the table below, with "
                    "the role and name exactly as shown, or continue without restating.")
        step.decision["expect"] = expect
        self.ev.event("expectation_restated", turn=step.turn)
        return "Recorded for the previous action. Carry on with the goal."

    def _unmet(self, step: Step, snap: UISnapshot, result: str | None) -> str | None:
        """Tell the model when its expectation turned out false, while later ones can improve.

        A false expectation compiles as an unverified checkpoint (R-PKG-5), which blocks
        approval; discovering that after the run is too late for anyone to fix it.
        """
        expect = step.decision.get("expect") or {}
        if not step.ok or not expect or nomination_holds(expect, snap, self.rendered):
            return result
        self.ev.event("expectation_unverified", turn=step.turn)
        return (
            f"{result or 'Done.'} The action itself was performed - do not repeat it - but "
            "what you said to expect is not true on this screen, so that step cannot be "
            "approved. Call recheck with an expectation that names elements from the table "
            "below, with the role and name exactly as shown."
        )

    def _finish(self, surface: WebSurface, decision: Decision, ids: dict[str, UIElement],
                snap: Any) -> str | None:
        """Record the finish; or, if it would not compile, the correction for the model."""
        outputs: dict[str, dict[str, Any]] = {}
        for spec in self.opt.outputs:
            eid = decision.outputs.get(spec.name)
            element = ids.get(eid or "")
            entry: dict[str, Any] = {"element": None, "bundle": None, "bundle_error": None}
            if element is None:
                entry["bundle_error"] = f"finish did not name an element for {spec.name!r}"
            else:
                entry["element"] = asdict(element)
                try:
                    entry["bundle"] = surface.synthesize(
                        element.ref, self.values, extraction=True
                    ).model_dump(mode="json")
                except ValueError as exc:
                    entry["bundle_error"] = locator_problem(exc)
            outputs[spec.name] = entry
        assert decision.expect is not None
        self.transcript.finish = FinishRecord(
            snapshot=snapshot_to_dict(snap), success=asdict(decision.expect),
            summary=decision.summary, outputs=outputs,
        )
        problems = finish_problems(self.transcript)
        if problems and self.corrections < self.opt.finish_corrections:
            self.corrections += 1
            self.transcript.finish = None
            self.ev.event("finish_rejected", correction=self.corrections, problems=problems)
            return (
                "Not finished - this would not compile: " + "; ".join(problems) + ". Name "
                "elements exactly as they appear on this screen (role, name, anchor), and for "
                "each output pick an element a later run can find again by a row label and a "
                "column header. Then call finish again."
            )
        self._end("finished", decision.summary)
        return None

    def _capture(self, surface: WebSurface) -> None:
        try:
            evidence = surface.capture_evidence()
            self.ev.screenshot("final.png", evidence.screenshot_png)
            self.ev.json("final_snapshot.json", asdict(evidence.snapshot))
        except Exception:  # evidence capture must never mask how the run ended
            self.ev.event("evidence_capture_failed")
