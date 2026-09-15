"""Deterministic replay: an approved artifact plus typed inputs, and no model.

Every decision below is made by the artifact or by a rule:

* Refuses an artifact that is not approved *as it now stands* (R-PKG-2, R-PKG-3).
* Establishes preconditions only through declared, bounded remedies, and only
  after positively recognising the state a remedy is written for.
* Before every step, checks the declared outcomes (R-OUT-1): "no such member" ends
  the run as a successful business outcome, not as an error.
* Resolves every target through the locator ladder; ambiguity escalates (R-LOC-2).
* After each action, polls the step's checkpoint. A state it does not recognise -
  including the right screen for the wrong member (R-RESUME-5) - escalates.
* Retries only an action that was never dispatched; never one whose effect is
  unknown (the surface reports ``in_flight``), and never an irreversible step.
* Reads outputs through the surface's one raw door (R-SENS-6), returning them in
  full to the caller while writing them redacted to evidence.

With ``handoff`` on (REPORT.md §5, R-PROC, R-RESUME), an escalation does not end the
run. The engine quiesces, opens an intervention and releases the control lease; a
person takes control of the same headed window through ``waypoint intervene`` and
hands it back. The engine re-acquires the lease under a new generation, diffs the
screen, and walks the return ladder - outcome, postcondition, the escalated step's
checkpoint, declared resume points - continuing only where one holds and escalating
again otherwise. It never blind-advances. Without ``handoff``, escalation ends the run.

An irreversible step is never repeated to find out whether it worked (R-REC). Its intent
is written immediately before dispatch; if nothing confirms it afterwards, the step's
``reconcile`` probe visits a read-only screen and answers completed (adopt the outputs
it shows), not completed (fail - the contract was not kept), or unknown (escalate). The
same probe settles an intent an earlier run left behind before this run executes
anything, and settles the step after a person handled it during a handoff.

Recovery is declared and bounded (R-OUT-3): a notice is dismissed, a slow page waited
for, a lapsed session re-entered - and the safe prefix replayed only if nothing
irreversible has been sent. Declared risk is a reviewed claim: a page that now computes
higher escalates rather than running under a stale label (R-RISK-7).
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote, urlsplit

from waypoint.artifact.approval import approval_status
from waypoint.artifact.schema import Capability, Reconcile, Step, content_hash
from waypoint.evidence import EvidenceWriter
from waypoint.policy.engine import PolicyConfig, PolicyEngine, RequireApproval, RunContext
from waypoint.policy.redactor import Redactor, Sink
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.assist import (
    ALLOWED_CODES,
    Assistant,
    AssistRequest,
    check,
    elements_for,
    recorded_shape,
)
from waypoint.replay.ledger import Kind, Ledger, record_for
from waypoint.replay.reconcile import Reconciliation, Record, decide
from waypoint.replay.result import FailureDetail, ReplayResult, Status
from waypoint.replay.resume import LadderResult, Outcome, Resume, Success, describe, return_ladder
from waypoint.session.escalation import Intervention
from waypoint.session.handoff import (
    ControlSession,
    HandoffEnded,
    HandoffSettings,
    Request,
    capture,
)
from waypoint.session.intents import Intent, IntentState, IntentStore, Resolution, inputs_hash
from waypoint.session.lease import LeaseLost
from waypoint.session.store import DEFAULT_DB, StateStore
from waypoint.signatures.recognizers import rendered_inputs
from waypoint.surface.locators import Ambiguous, Found, LocatorBundle, NotFound
from waypoint.surface.ports import Action, TextClass, UISnapshot
from waypoint.surface.sensitivity import Binding
from waypoint.surface.web import WebSurface

MONEY = re.compile(r"^-?\$?(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}$")
_TEMPLATE_VAR = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_STEP_INDEX = re.compile(r"steps\[(\d+)\]")
NOT_EVIDENCE = frozenset({"artifact_not_approved", "invalid_input", "confidence_too_low",
                          "confidence_unknown", "entry_unreachable"})
"""Runs that say nothing about how reliably this flow works: refusals decided before any
browser started, and an application that was not reachable at all. Both are facts about the
artifact, the caller or the environment, so neither enters the ledger (R-PKG-6)."""

NO_HANDOFF = frozenset({"in_flight", "indeterminate", "lease_lost", "lease_timeout",
                        "aborted_by_operator", "intervention_timeout"})
"""Escalations a handoff cannot resolve: an effect of unknown completion (R-RESUME-2),
or the control-transfer machinery itself ending the run."""


@dataclass
class ReplayOptions:
    base_url: str | None = None
    """Run against another origin than the artifact's entry (a test server, a tenant)."""
    evidence_root: Path = Path("evidence/runs")
    headed: bool = False
    require_approval: bool = True
    secrets: SecretBroker | None = None
    approve: Callable[[RequireApproval, Action], bool] | None = None
    """Asked before a risky action - attended capabilities only; unattended ones never ask."""
    poll_ms: int = 250
    capture_steps: bool = True
    """A screenshot and sanitized snapshot after every confirmed step (evidence depth)."""
    on_launch: Callable[[WebSurface], None] | None = None
    after_preconditions: Callable[[WebSurface, str], None] | None = None
    """Called with the surface and the origin once signed in. Fixtures and demos only."""
    handoff: bool = False
    """Escalations pause for a person on the same live session instead of ending the run."""
    state_db: Path = DEFAULT_DB
    ledger_db: Path | None = None
    """Where to record this run for confidence (REPORT.md §3). None records nothing."""
    kind: Kind = "call"
    """``stability`` for a measurement sweep; ``call`` for work someone asked for."""
    injected: str | None = None
    """A fixture failure injected on purpose: recorded, never counted against confidence."""
    require_confidence: bool = True
    """Honour an artifact's declared ``min_confidence`` before an unattended run."""
    assist: Assistant | None = None
    """A model asked, at most once, which control a step's lost target is now (R-ASSIST).
    Used only if the artifact also declares ``policy.assisted_fallback``."""
    lease_ttl_s: float = 600.0
    wait_timeout_s: float = 1800.0
    """How long an open intervention may wait for someone to take it."""
    max_handoffs: int = 2
    lease_poll_ms: int = 500
    notify: Callable[[Intervention], None] | None = None
    """Called once an intervention is open; the CLI prints how to take control."""
    while_human: Callable[[WebSurface, Intervention], None] | None = None
    """Fixtures and demos only: called once, in this thread, after a person takes control."""


class _Stop(Exception):
    def __init__(
        self, status: Status, detail: FailureDetail | None = None, outcome: str | None = None
    ) -> None:
        super().__init__(status)
        self.status, self.detail, self.outcome = status, detail, outcome


class _Reauth(Exception):
    """A reauth recovery: re-enter the application and replay the safe prefix (R-OUT-3)."""


class _Adopted(Exception):
    """An irreversible step reconciled as completed: its outputs are adopted, not re-made."""

    def __init__(self, where: str, outputs: dict[str, str]) -> None:
        super().__init__(where)
        self.where, self.outputs = where, outputs


def validate_inputs(cap: Capability, inputs: Mapping[str, str]) -> list[str]:
    """Problems with the caller's inputs. Messages never echo a value."""
    problems: list[str] = []
    props = cap.inputs.properties
    problems += [f"missing required input {n!r}" for n in cap.inputs.required if n not in inputs]
    for name, value in inputs.items():
        spec = props.get(name)
        if spec is None:
            problems.append(f"unknown input {name!r}")
            continue
        if spec.pattern and not re.fullmatch(spec.pattern, value):
            problems.append(f"input {name!r} does not match {spec.pattern}")
        if spec.enum and value not in spec.enum:
            problems.append(f"input {name!r} must be one of {list(spec.enum)}")
        if spec.format == "money" and not MONEY.match(value):
            problems.append(f"input {name!r} is not an amount of money")
    return problems


def render_template(template: str, inputs: Mapping[str, str]) -> str:
    return _TEMPLATE_VAR.sub(lambda m: quote(inputs[m.group(1)], safe=""), template)


def replay(
    cap: Capability, inputs: Mapping[str, str], options: ReplayOptions | None = None
) -> ReplayResult:
    return _Run(cap, dict(inputs), options or ReplayOptions()).execute()


class _Run:
    def __init__(self, cap: Capability, inputs: dict[str, str], opt: ReplayOptions) -> None:
        self.cap = cap
        self.inputs, self.opt = inputs, opt
        self.sens = {n: spec.sensitivity for n, spec in cap.inputs.properties.items()}
        known = {n: v for n, v in inputs.items() if n in self.sens}
        self.bindings = [Binding(n, v, self.sens[n]) for n, v in known.items()]
        self.rendered = rendered_inputs(known, self.sens)
        self.redactor = Redactor(self.bindings)
        self.run_id = EvidenceWriter.new_run_id()
        self.ev = EvidenceWriter(opt.evidence_root, self.run_id, self.redactor)
        self.tiers: Counter[int] = Counter()
        self.degradations = 0
        """Targets found below their recorded tier: the drift signal (R-LOC-4)."""
        self.steps_run = 0
        self.started = time.monotonic()
        entry = urlsplit(cap.surface.entry)
        self.origin = (opt.base_url or f"{entry.scheme}://{entry.netloc}").rstrip("/")
        self.entry_url = (
            self.origin + (entry.path or "/") + (f"?{entry.query}" if entry.query else "")
        )
        self.handoffs: list[dict[str, object]] = []
        self.recoveries: list[dict[str, object]] = []
        self.assisted: list[dict[str, object]] = []
        self.assist_attempts: list[dict[str, object]] = []
        self.recovery_counts: Counter[int] = Counter()
        self.reconciliations: list[dict[str, object]] = []
        self.adopted = False
        self.entered = False
        self.sent_irreversible = False
        self.intent_at: dict[str, float] = {}
        self.leftovers: list[Intent] = []
        self.handoff_started_at: datetime | None = None
        self.step_captures = 0
        self.state: StateStore | None = None
        self.control: ControlSession | None = None
        self.intents: IntentStore | None = None
        self.digest = inputs_hash(cap.capability_id, inputs)
        origin = urlsplit(self.origin)
        self.intent_scope = json.dumps([
            origin.scheme.lower(), (origin.hostname or "").lower(),
            origin.port or (443 if origin.scheme == "https" else 80),
        ], separators=(",", ":"))
        self.intent_contract = content_hash(cap)
        self.ledger = Ledger(StateStore(opt.ledger_db)) if opt.ledger_db is not None else None
        self.open_intents: dict[str, str] = {}
        """This run's irreversible actions not yet confirmed: where -> intent id."""
        irreversible = any(s.risk == "irreversible" for _, s in cap.all_steps())
        if opt.handoff:
            self.control = ControlSession(self.run_id, self.redactor, self.ev, HandoffSettings(
                state_db=opt.state_db, lease_ttl_s=opt.lease_ttl_s,
                wait_timeout_s=opt.wait_timeout_s, lease_poll_ms=opt.lease_poll_ms,
                notify=opt.notify, while_human=opt.while_human))
        if irreversible:
            self.state = StateStore(opt.state_db)
            self.intents = IntentStore(self.state)

    # ------------------------------------------------------------- lifecycle

    def execute(self) -> ReplayResult:
        result = self._execute()
        refused = result.failure is not None and result.failure.code in NOT_EVIDENCE
        if self.ledger is not None and not refused:
            self.ledger.record(record_for(result, self.cap, inputs_hash=self.digest,
                                          kind=self.opt.kind, injected=self.opt.injected))
        return result

    def _execute(self) -> ReplayResult:
        self.ev.text("artifact.json", self.cap.to_json())
        status = approval_status(self.cap)
        self.ev.json(
            "meta.json",
            {
                "run_id": self.run_id,
                "capability_id": self.cap.capability_id,
                "version": self.cap.version,
                "content_hash": content_hash(self.cap),
                "approved": status.approved,
                "approval_reasons": list(status.reasons),
                "inputs": {
                    n: self.redactor.text(v, TextClass(self.sens[n], binding=n), Sink.EVIDENCE)
                    for n, v in self.inputs.items()
                    if n in self.sens
                },
                "origin": self.origin,
                "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            scrub=False,
        )
        try:
            if self.opt.require_approval and not status.approved:
                reasons = "; ".join(status.reasons)
                raise _Stop("failure", FailureDetail("artifact_not_approved", reasons))
            problems = validate_inputs(self.cap, self.inputs)
            if problems:
                raise _Stop("failure", FailureDetail("invalid_input", "; ".join(problems)))
            self._check_confidence()
            self._check_unreconciled()  # before any browser starts
            return self._finish("success", outputs=self._with_surface())
        except _Stop as stop:
            return self._finish(stop.status, detail=stop.detail, outcome=stop.outcome)
        finally:
            self.ev.close()

    def _check_confidence(self) -> None:
        """An unattended capability may declare how reliable it must be measured to be.

        Approval says a person reviewed these contents; confidence says the contents have
        actually replayed. A capability that asks for a bar and cannot show it does not run
        unattended - it is not refused for attended use, where a person is watching.
        """
        threshold = self.cap.policy.min_confidence
        if threshold is None or not self.opt.require_confidence or not self.cap.policy.unattended:
            return
        if self.ledger is None:
            raise _Stop("failure", FailureDetail(
                "confidence_unknown",
                f"this capability requires measured confidence of {threshold:.2f} and this run "
                "records none; run `waypoint stability` or pass a ledger"))
        confidence = self.ledger.confidence(self.cap)
        self.ev.event("confidence", **confidence.to_dict())
        if not confidence.meets(threshold):
            raise _Stop("failure", FailureDetail(
                "confidence_too_low",
                f"{confidence.summary()}; this capability requires {threshold:.2f} "
                f"({'; '.join(confidence.reasons) or 'measured below the bar'})"))

    def _with_surface(self) -> dict[str, str]:
        config = PolicyConfig.for_origin(self.origin)
        config.allowlist.routes = list(self.cap.policy.allowed_routes)
        with WebSurface.launch(
            headed=self.opt.headed,
            bindings=self.bindings,
            policy=PolicyEngine(config),
            context=RunContext(unattended=self.cap.policy.unattended, state_known=False),
            secrets=self.opt.secrets or SecretBroker(),
            approve=self.opt.approve,
        ) as surface:
            if self.opt.on_launch is not None:
                self.opt.on_launch(surface)
            if self.control is not None:
                self.control.take(surface)
            try:
                outputs = self._drive(surface)
                self._capture(surface, "final")
                return outputs
            except _Stop as stop:
                shot, snap = self._capture(surface, "stop" if stop.detail else "final")
                if stop.detail is not None:
                    stop.detail = replace(stop.detail, screenshot=shot, snapshot=snap)
                raise
            except LeaseLost as exc:  # acted without control: never proceed (R-PROC-4)
                shot, snap = self._capture(surface, "stop")
                detail = FailureDetail("lease_lost", str(exc), screenshot=shot, snapshot=snap)
                raise _Stop("escalated", detail) from exc
            except Exception as exc:  # a bug in the engine or driver: fail loudly, with evidence
                shot, snap = self._capture(surface, "stop")
                message = self.redactor.error(exc)
                detail = FailureDetail("engine_error", message, screenshot=shot, snapshot=snap)
                raise _Stop("failure", detail) from exc
            finally:
                if self.control is not None:
                    self.control.release()
                for event in surface.events:
                    self.ev.event("surface", **event)

    def _finish(
        self,
        status: Status,
        *,
        outputs: dict[str, str] | None = None,
        detail: FailureDetail | None = None,
        outcome: str | None = None,
    ) -> ReplayResult:
        result = ReplayResult(
            status=status,
            capability_id=self.cap.capability_id,
            version=self.cap.version,
            run_id=self.run_id,
            outputs=outputs,
            outcome=outcome,
            failure=detail,
            telemetry={
                "steps": self.steps_run,
                "duration_ms": int((time.monotonic() - self.started) * 1000),
                "tier_histogram": {str(t): n for t, n in sorted(self.tiers.items())},
                "degradations": self.degradations,
                "handoffs": self.handoffs,
                "unresolved_intents": sorted(self.open_intents.values()),
                "recoveries": self.recoveries,
                "assisted": self.assisted,
                "assist_attempts": self.assist_attempts,
                "reconciliations": self.reconciliations,
                "adopted": self.adopted,
            },
            evidence_dir=str(self.ev.dir),
        )
        props = self.cap.outputs.properties
        redacted = {
            n: self.redactor.text(v, TextClass(props[n].sensitivity), Sink.EVIDENCE)
            for n, v in (outputs or {}).items()
        }
        self.ev.json("result.json", result.to_dict(outputs=redacted))
        self.ev.event("finished", status=status, outcome=outcome, code=detail and detail.code)
        return result

    # ---------------------------------------------------------------- driving

    def _drive(self, surface: WebSurface) -> dict[str, str]:
        self._enter(surface)
        adopted = self._reconcile_leftovers(surface)
        if adopted is not None:
            return adopted
        index, pending = 0, None
        while True:
            try:
                if pending is not None:
                    detail, pending = pending, None
                    raise _Stop("escalated", detail)
                while index < len(self.cap.steps):
                    if self.control is not None:
                        self.control.renew()
                    self._step(surface, f"steps[{index}]", self.cap.steps[index])
                    index += 1
                self._postconditions(surface)
                return self._extract(surface)
            except _Reauth:
                # Nothing irreversible has been sent (checked before raising), so the safe
                # prefix is replayed from the entry point.
                self._enter(surface)
                index = 0
            except _Adopted as settled:
                return self._adopt(settled.where, settled.outputs)
            except _Stop as stop:
                if not self._can_hand_off(stop):
                    raise
                assert stop.detail is not None
                self._record_handover(stop.detail)
                ladder = self._handoff(surface, stop.detail, index)
                handled = self._settle_after_handoff(surface, stop.detail)
                if handled is not None:
                    outputs, pending = handled
                    if outputs is not None:
                        return outputs
                    continue
                if isinstance(ladder, Resume):
                    self._confirm_intents(ladder.index)
                elif isinstance(ladder, Success):
                    self._confirm_intents(len(self.cap.steps))
                if isinstance(ladder, Success):
                    return self._extract(surface)
                if isinstance(ladder, Outcome):
                    if ladder.class_ == "business":
                        raise _Stop("business_outcome", outcome=ladder.name) from None
                    raise self._fail("hard_failure", f"recognised {ladder.name!r}",
                                     stop.detail.step) from None
                if isinstance(ladder, Resume):
                    index = ladder.index
                    continue
                pending = self._detail(
                    "unrecognized_state_after_handoff",
                    "after the handoff the screen matches no outcome, postcondition, "
                    "current checkpoint or declared resume point",
                    stop.detail.step, expected=stop.detail.expected_signature,
                    snap=ladder.snapshot,
                )

    def _enter(self, surface: WebSurface) -> None:
        """Open the entry point and establish preconditions - at the start, and again after
        a reauth, or after a leftover operation turned out never to have happened."""
        surface.context = RunContext(unattended=self.cap.policy.unattended, state_known=False)
        entry = Action("navigate", url=self.entry_url, intent="Open the entry point")
        result = surface.act(entry)
        self.ev.event(
            "action",
            where="entry",
            action="navigate",
            ok=result.ok,
            error_code=result.error_code,
            error=result.error,
        )
        if not result.ok:
            raise self._fail("entry_unreachable", result.error or "navigation failed", "entry")
        self._preconditions(surface)
        if not self.entered and self.opt.after_preconditions is not None:
            self.opt.after_preconditions(surface, self.origin)
        self.entered = True

    def _postconditions(self, surface: WebSurface) -> None:
        snap = self._observe(surface, "postconditions")
        for i, post in enumerate(self.cap.postconditions):
            if not self._holds(post.signature, snap):
                raise self._escalate(
                    "postcondition_unmet",
                    f"postcondition {post.signature!r} does not hold",
                    f"postconditions[{i}]",
                    expected=post.signature,
                    snap=snap,
                )

    # --------------------------------------------------------------- intents

    def _check_unreconciled(self) -> None:
        """R-REC-4: an earlier attempt at this operation may already have taken effect.

        When the step it names declares a reconcile probe, the run settles it first, in the
        browser, before executing anything. Otherwise the answer can only be Unknown, and
        the run escalates before a browser starts.
        """
        if self.intents is None:
            return
        left = self.intents.unresolved(self.cap.capability_id, self.digest,
                                       scope=self.intent_scope)
        for intent in left:
            step = self._step_at(intent.step)
            if (intent.scope != self.intent_scope
                    or intent.contract_hash != self.intent_contract
                    or step is None or step.reconcile is None):
                raise _Stop("escalated", FailureDetail(
                    "reconciliation_required",
                    f"run {intent.run_id} left {intent.step} {intent.state}: whether it took "
                    f"effect is unknown. Check the application, then: waypoint intervene "
                    f"reconcile {intent.id} --outcome completed|not-completed",
                    step=intent.step,
                ))
        self.leftovers = left

    def _intent_begin(self, where: str, *, handed_over: bool = False) -> None:
        assert self.intents is not None
        intent = self.intents.begin(run_id=self.run_id, capability_id=self.cap.capability_id,
                                    version=self.cap.version, step=where,
                                    inputs_digest=self.digest, scope=self.intent_scope,
                                    contract_hash=self.intent_contract)
        self.open_intents[where] = intent.id
        self.intent_at[where] = intent.attempted_at
        self.sent_irreversible = True
        self.ev.event("intent", where=where, intent_id=intent.id, state="dispatching",
                      handed_over=handed_over or None)

    def _record_handover(self, detail: FailureDetail) -> None:
        """Before a person gets control at an irreversible step, write its intent (R-REC-4).

        They may well perform the mutation themselves. If this run then dies before control
        comes back - a crash, an abort, a lapsed lease - an intent is what makes the next run
        find out whether it happened instead of doing it a second time.
        """
        where = detail.step or ""
        step = self._step_at(where)
        if (self.intents is None or step is None or step.risk != "irreversible"
                or where in self.open_intents):
            return
        self._intent_begin(where, handed_over=True)

    def _intent_advance(self, where: str, to: IntentState,
                        resolution: Resolution = "confirmed_after_handoff") -> None:
        assert self.intents is not None
        iid = self.open_intents[where]
        if to == "reconciled":
            self.intents.advance(iid, to, by="replay", resolution=resolution)
        else:
            self.intents.advance(iid, to)
        if to in ("observed", "reconciled"):
            del self.open_intents[where]
        self.ev.event("intent", where=where, intent_id=iid, state=to)

    def _confirm_intents(self, upto: int) -> None:
        """The return ladder moved past these steps because verified state shows them done."""
        for where in list(self.open_intents):
            found = _STEP_INDEX.fullmatch(where)
            if found is not None and int(found.group(1)) < upto:
                self._intent_advance(where, "reconciled")

    # --------------------------------------------------------------- handoff

    def _can_hand_off(self, stop: _Stop) -> bool:
        return (
            self.opt.handoff
            and stop.status == "escalated"
            and stop.detail is not None
            and stop.detail.code not in NO_HANDOFF
            and len(self.handoffs) < self.opt.max_handoffs
        )

    def _handoff(self, surface: WebSurface, detail: FailureDetail, index: int) -> LadderResult:
        assert self.control is not None
        try:
            returned = self.control.hand_over(surface, Request(
                capability_id=self.cap.capability_id, version=self.cap.version,
                reason_code=detail.code, message=detail.message, step=detail.step,
                intent=detail.intent, expected_signature=detail.expected_signature,
                observed_signatures=detail.observed_signatures,
            ))
        except HandoffEnded as ended:
            raise _Stop("escalated", replace(detail, code=ended.code, message=ended.message)
                        ) from None
        self.handoff_started_at = returned.started_at
        ladder = return_ladder(self.cap, self.rendered, returned.after, index)
        self.control.write_diff(returned, ladder=describe(ladder))
        iv = returned.intervention
        self.handoffs.append({"intervention": iv.id, "reason": detail.code, "step": detail.step,
                              "operator": iv.operator, "human_actions": returned.human_actions,
                              "ladder": describe(ladder)})
        self.ev.event("control_returned", intervention=iv.id, ladder=describe(ladder))
        return ladder

    def _preconditions(self, surface: WebSurface) -> None:
        snap = self._observe(surface, "preconditions")
        for i, pre in enumerate(self.cap.preconditions):
            where = f"preconditions[{i}]"
            if self._holds(pre.signature, snap):
                self.ev.event("precondition_met", where=where, signature=pre.signature)
                continue
            if pre.remedy is None:
                raise self._escalate(
                    "precondition_unmet",
                    f"{pre.signature!r} does not hold",
                    where,
                    expected=pre.signature,
                    snap=snap,
                )
            for _attempt in range(pre.remedy.max):
                if not self._holds(pre.remedy.when, snap):
                    raise self._escalate(
                        "unrecognized_state",
                        f"the remedy is written for {pre.remedy.when!r}, which does not hold",
                        where,
                        expected=pre.remedy.when,
                        snap=snap,
                    )
                for j, step in enumerate(pre.remedy.steps):
                    # A remedy IS the recovery for this state; recovering inside it would loop.
                    self._step(surface, f"{where}.remedy[{j}]", step, recover=False)
                snap = self._observe(surface, where)
                if self._holds(pre.signature, snap):
                    self.ev.event("precondition_met", where=where, signature=pre.signature)
                    break
            else:
                raise self._escalate(
                    "precondition_unmet",
                    f"{pre.signature!r} still does not hold",
                    where,
                    expected=pre.signature,
                    snap=snap,
                )

    def _step(self, surface: WebSurface, where: str, step: Step, *, recover: bool = True,
              capture: bool = True, probe: bool = False) -> None:
        if not probe:
            # R-OUT-1: declared outcomes before every step. Not before a reconcile probe: it
            # runs *because* of the screen it starts on (a lost response, a wrong receipt),
            # and exists to leave it. Its own screen is still checked once it arrives.
            snap = self._observe(surface, where)
            while recover and self._recover(surface, where, snap):
                snap = self._observe(surface, where)
        # The previous checkpoint, precondition or remedy trigger held, so the state is
        # recognised. The declared risk is a reviewed claim: if the page now computes a
        # higher one, the surface refuses and the run escalates (R-RISK-7).
        surface.context = RunContext(
            unattended=self.cap.policy.unattended, state_known=True, declared_risk=step.risk,
            declared_by_artifact=True,
        )
        attempts = step.retry.max + 1
        for attempt in range(1, attempts + 1):
            try:
                action, tier = self._action(surface, where, step)
            except _Stop as ambiguous:  # several matches: the ladder will not choose (R-LOC-2)
                assisted = self._assist(surface, where, step, ambiguous.detail)
                if assisted is None:
                    raise
                action, tier = assisted, None
            if action is None:  # not found: the page may still be arriving
                if attempt < attempts:
                    surface.page.wait_for_timeout(step.retry.backoff_ms)
                    continue
                lost = self._detail("locator_not_found", "target not found after retries",
                                    where, step=step)
                assisted = self._assist(surface, where, step, lost)
                if assisted is None:
                    raise _Stop("escalated", lost)
                action, tier = assisted, None
            if self.intents is not None and step.risk == "irreversible":
                # Written after policy and approval, immediately before dispatch (R-REC-4).
                surface.before_dispatch = lambda _action: self._intent_begin(where)
            try:
                result = surface.act(action)
            finally:
                surface.before_dispatch = None
            self.ev.event(
                "action",
                where=where,
                intent=step.intent,
                action=step.action,
                tier=tier,
                attempt=attempt,
                ok=result.ok,
                error_code=result.error_code,
                error=result.error,
                navigated=result.navigated,
                duration_ms=result.duration_ms,
            )
            if result.ok:
                if where in self.open_intents:
                    self._intent_advance(where, "dispatched")
                break
            code = result.error_code or "action_failed"
            if where in self.open_intents and code != "in_flight":
                # Sent, with no clean answer: reconciled, never retried (R-REC-1).
                self._settle(surface, where, step,
                             "the irreversible action was sent but did not complete cleanly")
            if code in ("approval_required", "in_flight"):  # never retried
                message = result.error or code
                if code == "approval_required":
                    risk = next((e.get("risk") for e in reversed(surface.events)
                                 if e.get("event") == "approval_required"), None)
                    message = f"needs a person's approval: {risk or 'risky'} ({message})"
                if result.error == "risk_exceeds_declared":
                    code = "risk_exceeds_declared"
                    message = (f"the page computes {risk or 'more risk'} for a step the artifact "
                               f"declares {step.risk}: review the artifact, not the action")
                raise self._escalate(code, message, where, step=step, tier=tier)
            if code == "policy_block":
                raise self._fail(code, result.error or code, where, step=step, tier=tier)
            if attempt == attempts:
                raise self._fail("action_failed", result.error or code, where, step=step, tier=tier)
            surface.page.wait_for_timeout(step.retry.backoff_ms)
        self.steps_run += 1
        try:
            self._await_checkpoint(surface, where, step, recover=recover)
        except _Stop as stop:
            if where in self.open_intents and stop.status in ("escalated", "failure"):
                seen = stop.detail.code if stop.detail else stop.status
                self._settle(surface, where, step,
                             f"the irreversible action was sent and not confirmed ({seen})")
            raise
        if where in self.open_intents:
            self._intent_advance(where, "observed")
        if capture and self.opt.capture_steps:
            self._capture_step(surface, where)

    def _action(
        self, surface: WebSurface, where: str, step: Step, ref_override: str | None = None
    ) -> tuple[Action | None, int | None]:
        if step.action == "navigate":
            url = self.origin + render_template(step.url_template or "/", self.inputs)
            return Action("navigate", url=url, intent=step.intent), None
        if step.action == "wait_for":
            return Action("wait_for", intent=step.intent), None
        ref: str | None = ref_override
        tier: int | None = None
        if step.target is not None and ref_override is None:
            res = surface.resolve(step.target, self.inputs)
            if isinstance(res, Ambiguous):
                raise self._escalate(
                    "ambiguous_locator", res.reason, where, step=step, tier=res.tier
                )
            if isinstance(res, NotFound):
                self.ev.event("locator_not_found", where=where, reason=res.reason)
                return None, None
            ref, tier = res.ref, res.tier
            self.tiers[res.tier] += 1
            if res.degraded:
                self.degradations += 1
                self.ev.event(
                    "locator_degradation",
                    where=where,
                    recorded_tier=step.target.recorded_tier,
                    resolved_tier=res.tier,
                )
        value: str | None = step.key
        if step.value_ref is not None:
            value = (
                step.value_ref
                if step.value_ref.startswith("$secrets.")
                else self.inputs[step.value_ref.removeprefix("$inputs.")]
            )
        return Action(step.action, ref=ref, value=value, intent=step.intent), tier

    def _assist(self, surface: WebSurface, where: str, step: Step,
                detail: FailureDetail | None) -> Action | None:
        """Ask a model which control this step's lost target is now - once, and bounded.

        Returns the action to take, or None to let the escalation stand. Every refusal
        below is deliberate: a capability that did not ask for this, a caller that did not
        enable it, a step that is not safe, anything irreversible already in flight, or a
        second attempt in one run.
        """
        if (self.opt.assist is None or not self.cap.policy.assisted_fallback or detail is None
                or detail.code not in ALLOWED_CODES or step.risk != "safe" or self.assist_attempts
                or self.open_intents or self.sent_irreversible):
            return None
        snap = self._observe(surface, where)
        request = AssistRequest(
            where=where, intent=step.intent, action=step.action, reason=detail.message,
            recorded=recorded_shape(step.target), snapshot=snap, elements=elements_for(snap),
        )
        record: dict[str, object] = {
            "where": where, "model": getattr(self.opt.assist, "model", "unknown"),
            "code": detail.code, "accepted": False,
        }
        self.assist_attempts.append(record)  # Consume the budget before entering the provider.
        try:
            choice = self.opt.assist.choose(request)
        except Exception as exc:
            record["error"] = self.redactor.error(exc)
            self.ev.event("assist_failed", **record)
            return None
        verdict = check(choice, request, step.target, self.rendered)
        model = getattr(self.opt.assist, "model", "unknown")
        record.update({
            "where": where, "model": model, "code": detail.code, "chose": choice.element,
            "reason": choice.reason, "accepted": verdict.ok, "check": verdict.reason,
        })
        self.ev.json(f"assist_{where.replace('[', '_').replace(']', '')}.json", {
            **record,
            "recorded": request.recorded,
            "offered": [f"{eid} {e.role} {e.name!r}" for eid, e in request.elements],
            "usage": choice.usage,
        })
        self.ev.event("assisted", **record)
        if not verdict.ok or verdict.element is None:
            return None
        record["proposal"] = self._propose_repair(surface, where, verdict.element)
        self.assisted.append(record)
        return self._action(surface, where, step, ref_override=verdict.element.ref)[0]

    def _propose_repair(self, surface: WebSurface, where: str, element: object) -> str | None:
        """Write the next version a person could approve, so the model is needed once.

        A draft, never an installed artifact: it goes to this run's evidence for review.
        """
        index = _STEP_INDEX.match(where)
        ref = getattr(element, "ref", None)
        if index is None or ref is None:
            return None
        try:
            bundle = surface.synthesize(str(ref), self.inputs)
        except ValueError as exc:
            self.ev.event("proposal_skipped", where=where, reason=self.redactor.error(exc))
            return None
        position = int(index.group(1))
        major, minor, patch = (int(p) for p in self.cap.version.split("-")[0].split("."))
        steps = list(self.cap.steps)
        steps[position] = steps[position].model_copy(update={"target": bundle})
        proposed = self.cap.model_copy(update={
            "version": f"{major}.{minor}.{patch + 1}",
            "steps": tuple(steps),
            "provenance": self.cap.provenance.model_copy(update={
                "approval": {"base": "draft"}, "approval_hash": {"base": None},
                "approved_by": None, "approved_at": None,
                "approval_note": f"proposed by an assisted run: {where} was relocated by "
                                 f"{getattr(self.opt.assist, 'model', 'a model')} in run "
                                 f"{self.run_id}; review the new locator before approving",
            }),
        })
        name = f"proposal/{proposed.capability_id}-{proposed.version}.json"
        (self.ev.dir / "proposal").mkdir(parents=True, exist_ok=True)
        self.ev.text(name, proposed.to_json())
        self.ev.event("proposal_written", where=where, version=proposed.version, path=name)
        return name

    def _await_checkpoint(self, surface: WebSurface, where: str, step: Step, *,
                          recover: bool = True) -> None:
        name = step.checkpoint.signature
        deadline = time.monotonic() + step.timeout_ms / 1000
        while True:
            snap = self._observe(surface, where, expected=name)
            if self._holds(name, snap):
                self.ev.event("checkpoint_met", where=where, signature=name)
                return
            if recover and self._recover(surface, where, snap):
                deadline = time.monotonic() + step.timeout_ms / 1000  # the obstacle took time
                continue
            if time.monotonic() >= deadline:
                break
            surface.page.wait_for_timeout(self.opt.poll_ms)
        raise self._escalate(
            "checkpoint_not_met",
            f"expected {name!r} within {step.timeout_ms} ms",
            where,
            step=step,
            expected=name,
            snap=snap,
        )

    def _adopted_outputs(self, surface: WebSurface, r: Reconcile,
                         record: Record | None) -> dict[str, str]:
        """Outputs of an adopted operation: from its own record, else from the probe screen."""
        out: dict[str, str] = {}
        if r.records is not None and record is not None:
            for name, column in r.records.outputs.items():
                out[name] = self._checked_output(name, record.values.get(column),
                                                 f"outputs.{name}")
        rest = {n: b for n, b in r.extract.items() if n not in out}
        if rest:
            out.update(self._extract(surface, rest))
        return out

    def _extract(
        self, surface: WebSurface, source: Mapping[str, LocatorBundle] | None = None
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, spec in self.cap.outputs.properties.items():
            if source is not None and name not in source:
                continue
            where = f"outputs.{name}"
            bundle = spec.extraction if source is None else source[name]
            res = surface.resolve(bundle, self.inputs)
            if isinstance(res, Ambiguous):
                raise self._escalate("ambiguous_locator", res.reason, where, tier=res.tier)
            if not isinstance(res, Found):
                raise self._fail("extraction_failed", f"output {name!r} not found", where)
            self.tiers[res.tier] += 1
            out[name] = self._checked_output(name, surface.extract_raw(res.ref), where)
        self.ev.event("outputs_extracted", names=sorted(out))
        return out

    def _checked_output(self, name: str, raw: str | None, where: str) -> str:
        spec = self.cap.outputs.properties[name]
        value = (raw or "").strip()
        if not value:
            raise self._fail("extraction_failed", f"output {name!r} is empty", where)
        if spec.enum and value not in spec.enum:
            raise self._fail("output_invalid", f"output {name!r} is not one of the enum", where)
        if spec.format == "money" and not MONEY.match(value):
            raise self._fail("output_invalid", f"output {name!r} is not money", where)
        return value

    # ------------------------------------------------------- recovery (R-OUT-3)

    def _recover(self, surface: WebSurface, where: str, snap: UISnapshot) -> bool:
        """Apply the declared recovery whose state holds - boundedly. True if one acted."""
        for i, rule in enumerate(self.cap.recovery):
            if not self._holds(rule.on, snap):
                continue
            self.recovery_counts[i] += 1
            attempt = self.recovery_counts[i]
            if attempt > rule.max:
                message = f"{rule.on!r} persisted after {rule.max} {rule.do} attempt(s)"
                if rule.else_ == "fail":
                    raise self._fail("recovery_exhausted", message, where, snap=snap)
                raise self._escalate("recovery_exhausted", message, where, snap=snap)
            record: dict[str, object] = {"on": rule.on, "do": rule.do, "where": where,
                                         "attempt": attempt}
            self.recoveries.append(record)
            self.ev.event("recovery", **record)
            if rule.do == "wait":
                surface.page.wait_for_timeout(rule.backoff_ms or self.opt.poll_ms)
                return True
            if rule.do == "dismiss":
                assert rule.target is not None
                res = surface.resolve(rule.target, self.inputs)
                if not isinstance(res, Found):
                    raise self._escalate("recovery_failed",
                                         f"nothing on screen dismisses {rule.on!r}", where,
                                         snap=snap)
                surface.context = RunContext(
                    unattended=self.cap.policy.unattended, state_known=True,
                    declared_risk="safe", declared_by_artifact=True,
                )
                result = surface.act(Action("click", ref=res.ref, intent=f"Dismiss {rule.on}"))
                if not result.ok:
                    raise self._escalate("recovery_failed", result.error or "dismiss failed",
                                         where, snap=snap)
                if rule.backoff_ms:
                    surface.page.wait_for_timeout(rule.backoff_ms)
                return True
            if self.sent_irreversible:
                raise self._escalate(
                    "reauth_after_irreversible",
                    "the session lapsed after an irreversible action was sent; replaying the "
                    "flow could repeat it", where, snap=snap,
                )
            raise _Reauth()
        return False

    # ------------------------------------------------------ reconciliation (R-REC)

    def _step_at(self, where: str) -> Step | None:
        found = _STEP_INDEX.fullmatch(where)
        if found is None:
            return None
        i = int(found.group(1))
        return self.cap.steps[i] if i < len(self.cap.steps) else None

    def _reconcile(
        self, surface: WebSurface, where: str, step: Step, attempted_at: datetime | None
    ) -> tuple[Reconciliation, dict[str, str] | None]:
        """Visit the probe screen and reach the three-way verdict. Never acts."""
        r = step.reconcile
        assert r is not None
        self.ev.event("reconcile_started", where=where,
                      attempted_at=attempted_at.isoformat(timespec="milliseconds")
                      if attempted_at else "untrusted")
        outputs: dict[str, str] | None = None
        try:
            for k, probe in enumerate(r.probe):
                self._step(surface, f"{where}.reconcile.probe[{k}]", probe,
                           recover=False, capture=False, probe=True)
        except (_Stop, _Reauth) as exc:
            seen = exc.detail.code if isinstance(exc, _Stop) and exc.detail else "reauth"
            verdict = Reconciliation("unknown", f"the probe could not reach its screen ({seen})")
        else:
            snap = surface.observe()
            records = None
            if r.records is not None:
                # Balances and creation dates are redacted from every snapshot; they are read
                # raw, row by row, compared, and dropped - only the verdict leaves this method.
                spec = r.records
                columns = [spec.created, *spec.fields, *spec.outputs.values()]
                records = [Record(surface.row_raw(e.ref, columns))
                           for e in snap.elements if spec.rows.matches(e, self.rendered)]
            verdict = decide(r, self.cap.signatures, snap, self.rendered, records, self.inputs,
                             attempted_at)
            if verdict.verdict == "completed":
                try:
                    outputs = self._adopted_outputs(surface, r, verdict.record)
                except _Stop as stop:
                    seen = stop.detail.message if stop.detail else stop.status
                    verdict = Reconciliation(
                        "unknown", f"completed, but its outputs could not be read ({seen})")
        record: dict[str, object] = {"where": where, "verdict": verdict.verdict,
                                     "reason": verdict.reason}
        self.reconciliations.append(record)
        self.ev.event("reconcile_verdict", **record)
        self._capture(surface, f"reconcile{len(self.reconciliations)}")
        return verdict, outputs

    def _settle(self, surface: WebSurface, where: str, step: Step, why: str) -> NoReturn:
        """An irreversible action was sent and nothing confirmed it: ask, never repeat."""
        if step.reconcile is None:
            raise self._escalate("reconciliation_required",
                                 f"{why}; whether it took effect is unknown", where, step=step)
        attempted = datetime.fromtimestamp(self.intent_at[where], UTC)
        verdict, outputs = self._reconcile(surface, where, step, attempted)
        if verdict.verdict == "completed":
            assert outputs is not None
            self._intent_advance(where, "reconciled", "completed")
            raise _Adopted(where, outputs)
        if verdict.verdict == "not_completed":
            self._intent_advance(where, "reconciled", "not_completed")
            raise self._fail("irreversible_step_not_completed",
                             f"{why}, and the application shows it did not happen: "
                             f"{verdict.reason}", where, step=step)
        raise self._escalate("reconciliation_unknown", f"{why}; {verdict.reason}", where,
                             step=step)

    def _reconcile_leftovers(self, surface: WebSurface) -> dict[str, str] | None:
        """Settle what an earlier run left unresolved, before this run executes anything."""
        if not self.leftovers:
            return None
        assert self.intents is not None
        for intent in self.leftovers:
            step = self._step_at(intent.step)
            assert step is not None
            attempted = (datetime.fromtimestamp(intent.attempted_at, UTC)
                         if intent.attempted_at_trusted else None)
            verdict, outputs = self._reconcile(surface, intent.step, step, attempted)
            if verdict.verdict == "unknown":
                raise self._escalate(
                    "reconciliation_unknown",
                    f"run {intent.run_id} left {intent.step} {intent.state}: {verdict.reason}",
                    intent.step, step=step,
                )
            resolution: Resolution = (
                "completed" if verdict.verdict == "completed" else "not_completed"
            )
            self.intents.advance(intent.id, "reconciled", by="replay", resolution=resolution)
            self.ev.event("intent", where=intent.step, intent_id=intent.id, state="reconciled",
                          resolution=resolution)
            if outputs is not None:
                return self._adopt(intent.step, outputs)
        self._enter(surface)  # none of it happened: run the flow from its start
        return None

    def _settle_after_handoff(
        self, surface: WebSurface, detail: FailureDetail
    ) -> tuple[dict[str, str] | None, FailureDetail | None] | None:
        """A person had control at an irreversible step: find out what they did, by probe.

        The return ladder would trust whatever screen they left; for a step that commits,
        only the authoritative record counts. None when the step is not irreversible.
        """
        where = detail.step or ""
        step = self._step_at(where)
        if step is None or step.risk != "irreversible" or step.reconcile is None:
            return None
        if where in self.intent_at:
            attempted = datetime.fromtimestamp(self.intent_at[where], UTC)
        else:
            assert self.handoff_started_at is not None
            attempted = self.handoff_started_at
        verdict, outputs = self._reconcile(surface, where, step, attempted)
        if verdict.verdict == "completed":
            assert outputs is not None
            if where in self.open_intents:
                self._intent_advance(where, "reconciled", "completed")
            return self._adopt(where, outputs), None
        if verdict.verdict == "not_completed" and where in self.open_intents:
            self._intent_advance(where, "reconciled", "not_completed")
        code = ("not_completed_after_handoff" if verdict.verdict == "not_completed"
                else "reconciliation_unknown")
        return None, self._detail(code, verdict.reason, where, step=step)

    def _adopt(self, where: str, outputs: dict[str, str]) -> dict[str, str]:
        """Return an adopted operation's outputs - only when nothing was left to do after it."""
        found = _STEP_INDEX.fullmatch(where)
        if found is None or int(found.group(1)) != len(self.cap.steps) - 1:
            raise self._escalate(
                "reconciled_mid_flow",
                "the operation was adopted, but steps remain after it; continuing past an "
                "adopted irreversible step is not supported", where,
            )
        self.adopted = True
        self.ev.event("adopted", where=where, outputs=sorted(outputs))
        return outputs

    def _capture_step(self, surface: WebSurface, where: str) -> None:
        self.step_captures += 1
        shot, snap = self._capture(surface, f"step{self.step_captures:02d}")
        self.ev.event("step_captured", where=where, screenshot=shot, snapshot=snap)

    # --------------------------------------------------------------- helpers

    def _observe(
        self, surface: WebSurface, where: str, expected: str | None = None
    ) -> UISnapshot:
        snap = surface.observe()
        for outcome in self.cap.outcomes:
            if self._holds(outcome.signature, snap):
                self.ev.event(
                    "outcome", where=where, outcome=outcome.name, outcome_class=outcome.class_
                )
                if outcome.class_ == "business":
                    raise _Stop("business_outcome", outcome=outcome.name)
                raise self._fail(
                    "hard_failure",
                    f"recognised {outcome.name!r}",
                    where,
                    snap=snap,
                    expected=expected,
                    observed=(outcome.signature,),
                )
        return snap

    def _holds(self, signature: str, snap: UISnapshot) -> bool:
        return self.cap.signatures[signature].evaluate(snap, self.rendered)

    def _matching(self, snap: UISnapshot) -> tuple[str, ...]:
        names = []
        for name, sig in self.cap.signatures.items():
            try:
                if sig.evaluate(snap, self.rendered):
                    names.append(name)
            except KeyError:
                continue
        return tuple(sorted(names))

    def _detail(
        self,
        code: str,
        message: str,
        where: str | None = None,
        *,
        step: Step | None = None,
        tier: int | None = None,
        expected: str | None = None,
        snap: UISnapshot | None = None,
        observed: tuple[str, ...] | None = None,
    ) -> FailureDetail:
        return FailureDetail(
            code=code,
            message=message,
            step=where,
            intent=step.intent if step else None,
            expected_signature=expected,
            observed_signatures=observed
            if observed is not None
            else (self._matching(snap) if snap else ()),
            resolved_tier=tier,
            url=snap.url if snap else None,
            frame_urls=tuple(u for _, u in snap.frame_urls) if snap else (),
        )

    def _escalate(self, code: str, message: str, where: str | None = None, **kw: object) -> _Stop:
        return _Stop("escalated", self._detail(code, message, where, **kw))  # type: ignore[arg-type]

    def _fail(self, code: str, message: str, where: str | None = None, **kw: object) -> _Stop:
        return _Stop("failure", self._detail(code, message, where, **kw))  # type: ignore[arg-type]

    def _capture(self, surface: WebSurface, name: str) -> tuple[str | None, str | None]:
        return capture(surface, self.ev, name)
