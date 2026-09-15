"""Handing the live session to a person and back, for discovery and replay alike.

One implementation of the control-transfer seam (R-PROC, R-RESUME-1, R-RESUME-6), so both
drivers pause, cede and resume the same way:

* the run holds a lease under an owner token, checked by the surface before every action;
* handing over quiesces the page, captures the screen, opens an intervention carrying
  enough context to act on, and *releases* the lease - from then on the run holds no
  token at all (R-PROC-4);
* while the person works, the run keeps the browser open and polls shared state
  (R-PROC-2), recording what they do through the run's redactor (R-RESUME-6);
* on return it re-acquires the lease under a new generation and reports the screen
  before and after. What happens next is the driver's decision: replay walks its return
  ladder, discovery hands the new screen back to the model.

A handoff that cannot be completed - the operator aborts, their lease lapses, nobody
takes it, or an action was still in flight - raises ``HandoffEnded`` with the code the
run should end with.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from waypoint.evidence import EvidenceWriter
from waypoint.policy.redactor import Redactor
from waypoint.session.escalation import Intervention, InterventionStore
from waypoint.session.human_log import HumanRecorder
from waypoint.session.lease import LeaseError, LeaseLost, LeaseStore, LeaseToken
from waypoint.session.store import DEFAULT_DB, StateStore
from waypoint.surface.ports import InFlight, UISnapshot
from waypoint.surface.web import WebSurface


@dataclass
class HandoffSettings:
    state_db: Path = DEFAULT_DB
    lease_ttl_s: float = 600.0
    wait_timeout_s: float = 1800.0
    """How long an open intervention may wait for someone to take it."""
    lease_poll_ms: int = 500
    notify: Callable[[Intervention], None] | None = None
    """Called once an intervention is open; the CLI prints how to take control."""
    while_human: Callable[[WebSurface, Intervention], None] | None = None
    """Fixtures and demos only: called once, in this thread, after a person takes control."""


@dataclass(frozen=True)
class Request:
    """What the intervention tells the operator: which task, where, and why it stopped."""

    capability_id: str
    version: str
    reason_code: str
    message: str
    step: str | None = None
    intent: str | None = None
    expected_signature: str | None = None
    observed_signatures: tuple[str, ...] = ()


@dataclass(frozen=True)
class Returned:
    intervention: Intervention
    number: int
    """1 for the run's first handoff; evidence files are numbered with it."""
    before: UISnapshot
    after: UISnapshot
    human_actions: int
    started_at: datetime


class HandoffEnded(Exception):
    """The handoff could not complete; the run ends escalated with this code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def capture(surface: WebSurface, ev: EvidenceWriter, name: str) -> tuple[str | None, str | None]:
    """A screenshot and sanitized snapshot. Evidence must never mask the real outcome."""
    try:
        evidence = surface.capture_evidence()
        return (
            ev.screenshot(f"{name}.png", evidence.screenshot_png),
            ev.json(f"{name}_snapshot.json", asdict(evidence.snapshot)),
        )
    except Exception:  # noqa: BLE001
        return None, None


class ControlSession:
    """The run's side of the control lease and of every handoff it makes."""

    def __init__(self, run_id: str, redactor: Redactor, ev: EvidenceWriter,
                 settings: HandoffSettings) -> None:
        self.run_id, self.redactor, self.ev, self.settings = run_id, redactor, ev, settings
        self.state = StateStore(settings.state_db)
        self.leases = LeaseStore(self.state)
        self.interventions = InterventionStore(self.state, self.leases)
        self.owner = uuid.uuid4().hex
        self.token: LeaseToken | None = None
        self.recorder: HumanRecorder | None = None
        self.count = 0

    # ----------------------------------------------------------------- lease

    def take(self, surface: WebSurface) -> None:
        self.token = self.leases.acquire(self.run_id, "AGENT", self.owner,
                                         self.settings.lease_ttl_s)
        surface.lease_guard = self.guard
        self.ev.event("lease", holder="AGENT", generation=self.token.generation)

    def guard(self) -> None:
        if self.token is None:
            raise LeaseLost("this run has released control")
        self.leases.assert_held(self.token)

    def renew(self) -> None:
        if self.token is not None:
            self.leases.renew(self.token, self.settings.lease_ttl_s)

    def release(self) -> None:
        if self.token is not None:
            try:
                self.leases.release(self.token)
            except LeaseError:
                pass  # already moved on; nothing of ours to release
            self.token = None

    # --------------------------------------------------------------- handoff

    def recorder_for(self, surface: WebSurface) -> HumanRecorder:
        if self.recorder is None:
            self.recorder = HumanRecorder(surface.page, WebSurface.frame_path, self.redactor,
                                          self.ev.dir / "human" / "actions.jsonl")
            self.recorder.install()
        return self.recorder

    def hand_over(self, surface: WebSurface, request: Request) -> Returned:
        n = self.count + 1
        if isinstance(surface.quiesce(), InFlight):  # R-RESUME-1/2: never mid-action
            raise HandoffEnded("indeterminate",
                               "an action was still in flight; a person must reconcile it")
        self.count = n
        before = surface.observe()
        shot, snap_path = capture(surface, self.ev, f"handoff{n}_before")
        iv = self.interventions.open(
            session_id=self.run_id, run_id=self.run_id, capability_id=request.capability_id,
            version=request.version, reason_code=request.reason_code, message=request.message,
            step=request.step, intent=request.intent,
            expected_signature=request.expected_signature,
            observed_signatures=request.observed_signatures,
            screenshot=str(self.ev.dir / shot) if shot else None,
            snapshot=str(self.ev.dir / snap_path) if snap_path else None,
            evidence_dir=str(self.ev.dir),
        )
        self.ev.event("intervention_opened", intervention=iv.id, reason=request.reason_code,
                      step=request.step)
        started_at = datetime.fromtimestamp(iv.created_at, UTC)
        recorder = self.recorder_for(surface)
        actions_before = recorder.actions
        assert self.token is not None
        released = self.leases.release(self.token)
        self.token = None  # anything still holding the old grant is now stale (R-PROC-4)
        surface.human_in_control = True  # their navigation is theirs, recorded, not policed
        recorder.active = True  # record from the moment control is released
        self.ev.event("lease", holder="NONE", generation=released.generation)
        if self.settings.notify is not None:
            self.settings.notify(iv)
        try:
            iv = self._await_return(surface, iv, recorder)
        except HandoffEnded:
            recorder.drain()  # keep what the person did before the run ended
            recorder.active = False
            surface.human_in_control = False
            raise
        self.token = self.leases.acquire(self.run_id, "AGENT", self.owner,
                                         self.settings.lease_ttl_s)
        surface.human_in_control = False
        recorder.stop(self.token.generation)
        self.interventions.close(iv.id, "resolved")
        self.ev.event("lease", holder="AGENT", generation=self.token.generation)
        return Returned(iv, n, before, surface.observe(), recorder.actions - actions_before,
                        started_at)

    def write_diff(self, returned: Returned, **extra: object) -> None:
        before, after = returned.before, returned.after
        self.ev.json(f"handoff{returned.number}_diff.json", {
            "intervention": returned.intervention.id,
            "before": {"url": before.url, "hash": before.hash,
                       "frames": [u for _, u in before.frame_urls]},
            "after": {"url": after.url, "hash": after.hash,
                      "frames": [u for _, u in after.frame_urls]},
            "screen_changed": before.hash != after.hash,
            "human_actions": returned.human_actions,
            **extra,
        })

    def _await_return(self, surface: WebSurface, iv: Intervention,
                      recorder: HumanRecorder) -> Intervention:
        """R-PROC-2: keep the browser open and poll the shared state until control returns."""
        deadline = time.monotonic() + self.settings.wait_timeout_s
        seen_human = hooked = False
        while True:
            surface.page.wait_for_timeout(self.settings.lease_poll_ms)  # delivers page events
            recorder.drain()
            iv = self.interventions.get(iv.id)
            if iv.status == "returned":
                return iv
            if iv.status == "aborted":
                raise HandoffEnded("aborted_by_operator", "the operator ended the run")
            lease = self.leases.read(self.run_id)
            if iv.status == "taken" and lease is not None:
                if lease.effective_holder(self.state.clock()) == "NONE":  # R-PROC-5
                    self.interventions.close(iv.id, "expired")
                    raise HandoffEnded(
                        "lease_timeout",
                        "the operator's lease expired before control was returned")
                if not seen_human:
                    seen_human = True
                    recorder.start(lease.generation)
                    self.ev.event("lease", holder="HUMAN", generation=lease.generation,
                                  operator=iv.operator)
                if self.settings.while_human is not None and not hooked:
                    hooked = True
                    self.settings.while_human(surface, iv)
            elif iv.status == "open" and time.monotonic() > deadline:
                self.interventions.close(iv.id, "expired")
                raise HandoffEnded("intervention_timeout", "nobody took control in time")
