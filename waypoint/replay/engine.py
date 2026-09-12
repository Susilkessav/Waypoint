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

With ``handoff`` on (PLAN.md 3.6, R-PROC, R-RESUME), an escalation does not end the
run. The engine quiesces, opens an intervention and releases the control lease; a
person takes control of the same headed window through ``waypoint intervene`` and
hands it back. The engine re-acquires the lease under a new generation, diffs the
screen, and walks the return ladder - outcome, postcondition, the escalated step's
checkpoint, declared resume points - continuing only where one holds and escalating
again otherwise. It never blind-advances. Without ``handoff``, escalation ends the run.
"""

from __future__ import annotations

import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from waypoint.artifact.approval import approval_status
from waypoint.artifact.schema import Capability, Step, content_hash
from waypoint.evidence import EvidenceWriter
from waypoint.policy.engine import PolicyConfig, PolicyEngine, RequireApproval, RunContext
from waypoint.policy.redactor import Redactor, Sink
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.result import FailureDetail, ReplayResult, Status
from waypoint.replay.resume import LadderResult, Outcome, Resume, Success, describe, return_ladder
from waypoint.session.escalation import Intervention, InterventionStore
from waypoint.session.human_log import HumanRecorder
from waypoint.session.intents import IntentState, IntentStore, inputs_hash
from waypoint.session.lease import LeaseError, LeaseLost, LeaseStore, LeaseToken
from waypoint.session.store import DEFAULT_DB, StateStore
from waypoint.signatures.recognizers import rendered_inputs
from waypoint.surface.locators import Ambiguous, Found, NotFound
from waypoint.surface.ports import Action, InFlight, TextClass, UISnapshot
from waypoint.surface.sensitivity import Binding
from waypoint.surface.web import WebSurface

MONEY = re.compile(r"^-?\$?(?:\d{1,3}(?:,\d{3})*|\d+)\.\d{2}$")
_TEMPLATE_VAR = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_STEP_INDEX = re.compile(r"steps\[(\d+)\]")
NO_HANDOFF = frozenset({"in_flight", "indeterminate", "lease_lost", "lease_timeout",
                        "aborted_by_operator", "intervention_timeout"})
"""Escalations a handoff cannot resolve: an effect of unknown completion (R-RESUME-2),
or the control-transfer machinery itself ending the run."""


@dataclass
class ReplayOptions:
    base_url: str | None = None
    """Run against another origin than the artifact's entry (a test server, a tenant)."""
    evidence_root: Path = Path("evidence/runs")
    variant: str = "base"
    headed: bool = False
    require_approval: bool = True
    secrets: SecretBroker | None = None
    approve: Callable[[RequireApproval, Action], bool] | None = None
    """Asked before a risky action - attended capabilities only; unattended ones never ask."""
    poll_ms: int = 250
    on_launch: Callable[[WebSurface], None] | None = None
    after_preconditions: Callable[[WebSurface, str], None] | None = None
    """Called with the surface and the origin once signed in. Fixtures and demos only."""
    handoff: bool = False
    """Escalations pause for a person on the same live session instead of ending the run."""
    state_db: Path = DEFAULT_DB
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
        self.cap, self.inputs, self.opt = cap, inputs, opt
        self.sens = {n: spec.sensitivity for n, spec in cap.inputs.properties.items()}
        known = {n: v for n, v in inputs.items() if n in self.sens}
        self.bindings = [Binding(n, v, self.sens[n]) for n, v in known.items()]
        self.rendered = rendered_inputs(known, self.sens)
        self.redactor = Redactor(self.bindings)
        self.run_id = EvidenceWriter.new_run_id()
        self.ev = EvidenceWriter(opt.evidence_root, self.run_id, self.redactor)
        self.tiers: Counter[int] = Counter()
        self.steps_run = 0
        self.started = time.monotonic()
        entry = urlsplit(cap.surface.entry)
        self.origin = (opt.base_url or f"{entry.scheme}://{entry.netloc}").rstrip("/")
        self.entry_url = (
            self.origin + (entry.path or "/") + (f"?{entry.query}" if entry.query else "")
        )
        self.handoffs: list[dict[str, object]] = []
        self.owner = uuid.uuid4().hex
        self.token: LeaseToken | None = None
        self.recorder: HumanRecorder | None = None
        self.state: StateStore | None = None
        self.leases: LeaseStore | None = None
        self.interventions: InterventionStore | None = None
        self.intents: IntentStore | None = None
        self.digest = inputs_hash(cap.capability_id, inputs)
        self.open_intents: dict[str, str] = {}
        """This run's irreversible actions not yet confirmed: where -> intent id."""
        irreversible = any(s.risk == "irreversible" for _, s in cap.all_steps())
        if opt.handoff or irreversible:
            self.state = StateStore(opt.state_db)
        if opt.handoff and self.state is not None:
            self.leases = LeaseStore(self.state)
            self.interventions = InterventionStore(self.state, self.leases)
        if irreversible and self.state is not None:
            self.intents = IntentStore(self.state)

    # ------------------------------------------------------------- lifecycle

    def execute(self) -> ReplayResult:
        self.ev.text("artifact.json", self.cap.to_json())
        status = approval_status(self.cap, self.opt.variant)
        self.ev.json(
            "meta.json",
            {
                "run_id": self.run_id,
                "capability_id": self.cap.capability_id,
                "version": self.cap.version,
                "variant": self.opt.variant,
                "content_hash": content_hash(self.cap) if self.opt.variant == "base" else None,
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
            self._check_unreconciled()  # before any browser starts
            return self._finish("success", outputs=self._with_surface())
        except _Stop as stop:
            return self._finish(stop.status, detail=stop.detail, outcome=stop.outcome)
        finally:
            self.ev.close()

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
            if self.leases is not None:
                self._take_lease(surface)
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
                self._drop_lease()
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
            variant=self.opt.variant,
            outputs=outputs,
            outcome=outcome,
            failure=detail,
            telemetry={
                "steps": self.steps_run,
                "duration_ms": int((time.monotonic() - self.started) * 1000),
                "tier_histogram": {str(t): n for t, n in sorted(self.tiers.items())},
                "handoffs": self.handoffs,
                "unresolved_intents": sorted(self.open_intents.values()),
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
        if self.opt.after_preconditions is not None:
            self.opt.after_preconditions(surface, self.origin)
        index, pending = 0, None
        while True:
            try:
                if pending is not None:
                    detail, pending = pending, None
                    raise _Stop("escalated", detail)
                while index < len(self.cap.steps):
                    self._renew()
                    self._step(surface, f"steps[{index}]", self.cap.steps[index])
                    index += 1
                self._postconditions(surface)
                return self._extract(surface)
            except _Stop as stop:
                if not self._can_hand_off(stop):
                    raise
                assert stop.detail is not None
                ladder = self._handoff(surface, stop.detail, index)
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

        Until the probe of R-REC-5 exists (milestone B3) the answer is always Unknown,
        and Unknown escalates: the run stops before a browser starts.
        """
        if self.intents is None:
            return
        left = self.intents.unresolved(self.cap.capability_id, self.digest)
        if left:
            first = left[0]
            raise _Stop("escalated", FailureDetail(
                "reconciliation_required",
                f"run {first.run_id} left {first.step} {first.state}: whether it took effect "
                f"is unknown. Check the application, then: waypoint intervene reconcile "
                f"{first.id} --outcome completed|not-completed",
                step=first.step,
            ))

    def _intent_begin(self, where: str) -> None:
        assert self.intents is not None
        intent = self.intents.begin(run_id=self.run_id, capability_id=self.cap.capability_id,
                                    version=self.cap.version, step=where,
                                    inputs_digest=self.digest)
        self.open_intents[where] = intent.id
        self.ev.event("intent", where=where, intent_id=intent.id, state="dispatching")

    def _intent_advance(self, where: str, to: IntentState) -> None:
        assert self.intents is not None
        iid = self.open_intents[where]
        if to == "reconciled":
            self.intents.advance(iid, to, by="replay", resolution="confirmed_after_handoff")
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

    def _take_lease(self, surface: WebSurface) -> None:
        assert self.leases is not None
        self.token = self.leases.acquire(self.run_id, "AGENT", self.owner, self.opt.lease_ttl_s)
        surface.lease_guard = self._guard
        self.ev.event("lease", holder="AGENT", generation=self.token.generation)

    def _guard(self) -> None:
        if self.leases is None:
            return
        if self.token is None:
            raise LeaseLost("this run has released control")
        self.leases.assert_held(self.token)

    def _renew(self) -> None:
        if self.leases is not None and self.token is not None:
            self.leases.renew(self.token, self.opt.lease_ttl_s)

    def _drop_lease(self) -> None:
        if self.leases is not None and self.token is not None:
            try:
                self.leases.release(self.token)
            except LeaseError:
                pass  # already moved on; nothing of ours to release
            self.token = None

    def _can_hand_off(self, stop: _Stop) -> bool:
        return (
            self.opt.handoff
            and stop.status == "escalated"
            and stop.detail is not None
            and stop.detail.code not in NO_HANDOFF
            and len(self.handoffs) < self.opt.max_handoffs
        )

    def _recorder(self, surface: WebSurface) -> HumanRecorder:
        if self.recorder is None:
            self.recorder = HumanRecorder(surface.page, WebSurface.frame_path, self.redactor,
                                          self.ev.dir / "human" / "actions.jsonl")
            self.recorder.install()
        return self.recorder

    def _handoff(self, surface: WebSurface, detail: FailureDetail, index: int) -> LadderResult:
        assert self.leases is not None and self.interventions is not None
        n = len(self.handoffs) + 1
        if isinstance(surface.quiesce(), InFlight):  # R-RESUME-1/2: never hand over mid-action
            raise _Stop("escalated", replace(
                detail, code="indeterminate",
                message="an action was still in flight; a person must reconcile it"))
        before = surface.observe()
        shot, snap_path = self._capture(surface, f"handoff{n}_before")
        iv = self.interventions.open(
            session_id=self.run_id, run_id=self.run_id, capability_id=self.cap.capability_id,
            version=self.cap.version, reason_code=detail.code, message=detail.message,
            step=detail.step, intent=detail.intent,
            expected_signature=detail.expected_signature,
            observed_signatures=detail.observed_signatures,
            screenshot=str(self.ev.dir / shot) if shot else None,
            snapshot=str(self.ev.dir / snap_path) if snap_path else None,
            evidence_dir=str(self.ev.dir),
        )
        self.ev.event("intervention_opened", intervention=iv.id, reason=detail.code,
                      step=detail.step)
        recorder = self._recorder(surface)
        actions_before = recorder.actions
        assert self.token is not None
        released = self.leases.release(self.token)
        self.token = None  # anything still holding the old grant is now stale (R-PROC-4)
        recorder.active = True  # record from the moment control is released
        self.ev.event("lease", holder="NONE", generation=released.generation)
        if self.opt.notify is not None:
            self.opt.notify(iv)
        try:
            iv = self._await_return(surface, iv, detail, recorder)
        except _Stop:
            recorder.drain()  # keep what the person did before the run ended
            recorder.active = False
            raise
        self.token = self.leases.acquire(self.run_id, "AGENT", self.owner, self.opt.lease_ttl_s)
        recorder.stop(self.token.generation)
        self.interventions.close(iv.id, "resolved")
        self.ev.event("lease", holder="AGENT", generation=self.token.generation)
        after = surface.observe()
        ladder = return_ladder(self.cap, self.rendered, after, index)
        human_actions = recorder.actions - actions_before
        self.ev.json(f"handoff{n}_diff.json", {
            "intervention": iv.id,
            "before": {"url": before.url, "hash": before.hash,
                       "frames": [u for _, u in before.frame_urls]},
            "after": {"url": after.url, "hash": after.hash,
                      "frames": [u for _, u in after.frame_urls]},
            "screen_changed": before.hash != after.hash,
            "human_actions": human_actions,
            "ladder": describe(ladder),
        })
        self.handoffs.append({"intervention": iv.id, "reason": detail.code, "step": detail.step,
                              "operator": iv.operator, "human_actions": human_actions,
                              "ladder": describe(ladder)})
        self.ev.event("control_returned", intervention=iv.id, ladder=describe(ladder))
        return ladder

    def _await_return(self, surface: WebSurface, iv: Intervention, detail: FailureDetail,
                      recorder: HumanRecorder) -> Intervention:
        """R-PROC-2: keep the browser open and poll the shared state until control returns."""
        assert self.leases is not None and self.interventions is not None
        assert self.state is not None
        deadline = time.monotonic() + self.opt.wait_timeout_s
        seen_human = hooked = False
        while True:
            surface.page.wait_for_timeout(self.opt.lease_poll_ms)  # also delivers human events
            recorder.drain()
            iv = self.interventions.get(iv.id)
            if iv.status == "returned":
                return iv
            if iv.status == "aborted":
                raise _Stop("escalated", replace(
                    detail, code="aborted_by_operator", message="the operator ended the run"))
            lease = self.leases.read(self.run_id)
            if iv.status == "taken" and lease is not None:
                if lease.effective_holder(self.state.clock()) == "NONE":  # R-PROC-5
                    self.interventions.close(iv.id, "expired")
                    raise _Stop("escalated", replace(
                        detail, code="lease_timeout",
                        message="the operator's lease expired before control was returned"))
                if not seen_human:
                    seen_human = True
                    recorder.start(lease.generation)
                    self.ev.event("lease", holder="HUMAN", generation=lease.generation,
                                  operator=iv.operator)
                if self.opt.while_human is not None and not hooked:
                    hooked = True
                    self.opt.while_human(surface, iv)
            elif iv.status == "open" and time.monotonic() > deadline:
                self.interventions.close(iv.id, "expired")
                raise _Stop("escalated", replace(
                    detail, code="intervention_timeout", message="nobody took control in time"))

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
                    self._step(surface, f"{where}.remedy[{j}]", step)
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

    def _step(self, surface: WebSurface, where: str, step: Step) -> None:
        self._observe(surface, where)  # R-OUT-1: declared outcomes before every step
        # The previous checkpoint, precondition or remedy trigger held, so the state
        # is recognised; the step's declared risk is a floor the surface cannot lower.
        surface.context = RunContext(
            unattended=self.cap.policy.unattended, state_known=True, declared_risk=step.risk
        )
        attempts = step.retry.max + 1
        for attempt in range(1, attempts + 1):
            action, tier = self._action(surface, where, step)
            if action is None:  # not found: the page may still be arriving
                if attempt < attempts:
                    surface.page.wait_for_timeout(step.retry.backoff_ms)
                    continue
                raise self._escalate(
                    "locator_not_found", "target not found after retries", where, step=step
                )
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
                # Sent, with no clean answer: its effect is unknown, so it is never retried.
                raise self._escalate(
                    "reconciliation_required",
                    "the irreversible action was sent but did not complete cleanly; "
                    "whether it took effect is unknown",
                    where, step=step, tier=tier,
                )
            if code in ("approval_required", "in_flight"):  # never retried
                raise self._escalate(code, result.error or code, where, step=step, tier=tier)
            if code == "policy_block":
                raise self._fail(code, result.error or code, where, step=step, tier=tier)
            if attempt == attempts:
                raise self._fail("action_failed", result.error or code, where, step=step, tier=tier)
            surface.page.wait_for_timeout(step.retry.backoff_ms)
        self.steps_run += 1
        self._await_checkpoint(surface, where, step)
        if where in self.open_intents:
            self._intent_advance(where, "observed")

    def _action(
        self, surface: WebSurface, where: str, step: Step
    ) -> tuple[Action | None, int | None]:
        if step.action == "navigate":
            url = self.origin + render_template(step.url_template or "/", self.inputs)
            return Action("navigate", url=url, intent=step.intent), None
        if step.action == "wait_for":
            return Action("wait_for", intent=step.intent), None
        ref: str | None = None
        tier: int | None = None
        if step.target is not None:
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

    def _await_checkpoint(self, surface: WebSurface, where: str, step: Step) -> None:
        name = step.checkpoint.signature
        deadline = time.monotonic() + step.timeout_ms / 1000
        while True:
            snap = self._observe(surface, where)
            if self._holds(name, snap):
                self.ev.event("checkpoint_met", where=where, signature=name)
                return
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

    def _extract(self, surface: WebSurface) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, spec in self.cap.outputs.properties.items():
            where = f"outputs.{name}"
            res = surface.resolve(spec.extraction, self.inputs)
            if isinstance(res, Ambiguous):
                raise self._escalate("ambiguous_locator", res.reason, where, tier=res.tier)
            if not isinstance(res, Found):
                raise self._fail("extraction_failed", f"output {name!r} not found", where)
            self.tiers[res.tier] += 1
            value = (surface.extract_raw(res.ref) or "").strip()
            if not value:
                raise self._fail("extraction_failed", f"output {name!r} is empty", where)
            if spec.enum and value not in spec.enum:
                raise self._fail("output_invalid", f"output {name!r} is not one of the enum", where)
            if spec.format == "money" and not MONEY.match(value):
                raise self._fail("output_invalid", f"output {name!r} is not money", where)
            out[name] = value
        self.ev.event("outputs_extracted", names=sorted(out))
        return out

    # --------------------------------------------------------------- helpers

    def _observe(self, surface: WebSurface, where: str) -> UISnapshot:
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
        """Evidence must never mask the real outcome: capture failures are swallowed."""
        try:
            evidence = surface.capture_evidence()
            return (
                self.ev.screenshot(f"{name}.png", evidence.screenshot_png),
                self.ev.json(f"{name}_snapshot.json", asdict(evidence.snapshot)),
            )
        except Exception:  # noqa: BLE001
            return None, None
