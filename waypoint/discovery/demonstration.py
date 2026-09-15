"""Turning what a person demonstrates during discovery into steps replay can repeat.

When discovery gets stuck it hands the live browser to a person (R-PROC, R-RESUME). What
they do next is exactly the knowledge the run was missing - but a click that has already
happened cannot be turned into a locator afterwards: the screen it was on is gone.

So each interaction is *held* rather than watched:

1. An injected script cancels the person's click while the element is still on screen and
   reports it, tagging the element.
2. The engine thread observes the page, which stamps every element it can act on, finds
   the tagged one, and synthesizes a locator bundle for it exactly as it does for the
   model's own actions (R-LOC-1, R-LOC-5).
3. It then performs that click itself, through ``WebSurface.act`` - so the policy engine,
   the redactor and the evidence log treat it like any other action. A step the policy
   would refuse is let through as the person's own click and recorded as a gap, which
   blocks approval rather than compiling into something replay would repeat unchecked.

Typing is not held - a keystroke cannot be undone - so a field change is recorded when it
settles. The value reaches this process in memory only, to decide whether it equals an
input (``$inputs.member_id``) or is an unbound literal; it is never written anywhere.
Passwords report no value at all and become gaps.

Trust: the injected script holds a per-run nonce in a closure and its entry points are
non-writable, so page script cannot forge a demonstrated step or read the nonce. Events
are accepted only while a person actually holds the control lease.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame

from waypoint.compiler.compile import diff_expectation, locator_problem
from waypoint.discovery.transcript import Step, snapshot_to_dict
from waypoint.session.handoff import ControlSession
from waypoint.session.human_log import HumanRecorder
from waypoint.session.lease import LeaseLost
from waypoint.surface.ports import Action, UIElement, UISnapshot
from waypoint.surface.web import WebSurface

BINDING = "__wpDemo"

_INIT_JS = """
(() => {
  if (window.__wpDemoInstalled) return;
  const NONCE = "%(nonce)s";
  const binding = window.%(binding)s;
  if (!binding) return;
  let armed = false, passing = false, counter = 0;
  const define = (name, value) =>
    Object.defineProperty(window, name, {value, writable: false, configurable: false});
  define("__wpDemoInstalled", true);
  define("__wpDemoArm", (n) => { if (n === NONCE) armed = true; });
  define("__wpDemoDisarm", (n) => { if (n === NONCE) armed = false; });
  define("__wpDemoPass", (n) => { if (n === NONCE) passing = true; });
  Object.defineProperty(window, "__wpDemoPassing", {get: () => passing, configurable: false});
  const ACTIONABLE = 'a, button, input[type=submit], input[type=button], input[type=image],' +
    ' [role=button], [role=tab], [role=link], [onclick], td[onclick], th[onclick], summary';
  const tag = (el) => {
    const id = String(++counter);
    el.setAttribute('data-wp-demo', id);
    return id;
  };
  const isField = (el) => el.matches &&
    el.matches('input:not([type=submit]):not([type=button]), textarea, select');
  document.addEventListener('click', (ev) => {
    if (passing) { passing = false; return; }        // our own re-send of their click
    if (!armed || !ev.isTrusted) return;
    const el = (ev.target.closest && ev.target.closest(ACTIONABLE)) || ev.target;
    if (!el || !el.tagName || isField(el)) return;   // a field takes its own events
    ev.preventDefault();
    ev.stopImmediatePropagation();
    binding({nonce: NONCE, type: 'click', demo_id: tag(el),
             role: el.getAttribute('role') || '', tag: el.tagName.toLowerCase()})
      .then((held) => { if (held === false) armed = false; });
  }, true);
  const change = (el) => binding({
    nonce: NONCE, type: 'change', demo_id: tag(el), tag: el.tagName.toLowerCase(),
    kind: (el.getAttribute('type') || '').toLowerCase(),
    secret: el.type === 'password',
    value: el.type === 'password' ? null : (el.value || ''),
  });
  document.addEventListener('change', (ev) => {
    if (!armed || !ev.isTrusted || !isField(ev.target)) return;
    change(ev.target);
  }, true);
  document.addEventListener('keydown', (ev) => {
    if (passing || !armed || !ev.isTrusted || ev.key !== 'Enter') return;
    const el = ev.target;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    if (isField(el) && el.type !== 'password') change(el);   // Enter submits before change
    binding({nonce: NONCE, type: 'key', key: 'Enter',
             demo_id: el && el.setAttribute ? tag(el) : null});
  }, true);
  binding({nonce: NONCE, type: 'hello'}).then((ok) => { armed = ok === true; });
})();
"""


@dataclass
class _Event:
    frame: Frame | None
    payload: dict[str, Any]


class DemonstrationCapture:
    """Installs the holding script and turns what it reports into transcript steps."""

    def __init__(self, surface: WebSurface, session: ControlSession, recorder: HumanRecorder,
                 record: Callable[[Step], None], next_turn: Callable[[], int],
                 value_of: Callable[[str | None], tuple[str | None, str | None, str | None]],
                 values: dict[str, str], rendered: dict[str, str],
                 note: Callable[..., None]) -> None:
        self.surface = surface
        self.session = session
        self.recorder = recorder
        self.record = record
        self.next_turn = next_turn
        self.value_of = value_of
        self.values = values
        """Input name -> value, for locator synthesis; never written anywhere."""
        self.rendered = rendered
        self.note = note
        self.nonce = secrets.token_hex(16)
        self.accepting = False
        self.pending: list[_Event] = []
        self.done: list[Step] = []
        self._installed = False

    # ------------------------------------------------------------- lifecycle

    def install(self) -> None:
        if self._installed:
            return
        script = _INIT_JS % {"nonce": self.nonce, "binding": BINDING}
        self.surface.page.context.expose_binding(BINDING, self._on_event)
        self.surface.page.context.add_init_script(script)
        for frame in self.surface.page.frames:
            try:
                frame.evaluate(script)
            except PlaywrightError:
                pass  # a detaching frame; the init script covers its replacement
        self._installed = True

    def begin(self) -> None:
        """Start accepting: from here the person's interactions become steps."""
        self.install()
        self.accepting = True
        self.done = []
        self._set_armed(True)

    def end(self, surface: WebSurface) -> list[Step]:
        """Drain what is left, stop accepting, and report the steps this handoff produced."""
        self.process(surface)
        self.accepting = False
        self._set_armed(False)
        return list(self.done)

    def _set_armed(self, armed: bool) -> None:
        """Only while a person has control: an armed script would hold the model's clicks too."""
        call = "__wpDemoArm" if armed else "__wpDemoDisarm"
        for frame in self.surface.page.frames:
            try:
                frame.evaluate(f"(n) => window.{call} && window.{call}(n)", self.nonce)
            except PlaywrightError:
                pass

    def _on_event(self, source: dict[str, Any], payload: Any) -> bool:
        """Playwright dispatch thread: never call back into the driver from here."""
        if not isinstance(payload, dict) or payload.get("nonce") != self.nonce:
            return False
        if not self.accepting:
            return False
        if payload.get("type") != "hello":
            self.pending.append(_Event(source.get("frame"), payload))
        return True

    # -------------------------------------------------------------- draining

    def process(self, surface: WebSurface) -> None:
        """Engine thread only: hold-and-resend everything the person has done so far."""
        while self.pending:
            event = self.pending.pop(0)
            try:
                self._one(surface, event)
            except LeaseLost:
                # Control moved on mid-drain: keep it for after the lease comes back.
                self.pending.insert(0, event)
                return
            except PlaywrightError as exc:
                self._gap(surface, f"a person's action could not be repeated: "
                                   f"{surface.redactor.error(exc)}")
        if self.accepting:
            self._set_armed(True)  # frames that navigated carry a fresh, unarmed document

    def _one(self, surface: WebSurface, event: _Event) -> None:
        kind = event.payload.get("type")
        if kind == "click":
            self._click(surface, event)
        elif kind == "change":
            self._change(surface, event)
        elif kind == "key":
            self._key(surface, event)

    # ----------------------------------------------------------------- steps

    def _locate(self, surface: WebSurface, event: _Event
                ) -> tuple[UISnapshot, UIElement | None, str | None]:
        """Observe (which stamps the page), then find the element the script tagged."""
        snap = surface.observe()
        demo_id = event.payload.get("demo_id")
        ref: str | None = None
        if demo_id is not None and event.frame is not None:
            try:
                ref = event.frame.evaluate(
                    """(id) => {
                        const el = document.querySelector(`[data-wp-demo="${id}"]`);
                        if (!el) return null;
                        const holder = el.closest('[data-wp-ref]') ||
                                       el.querySelector('[data-wp-ref]');
                        return holder ? holder.getAttribute('data-wp-ref') : null;
                    }""",
                    demo_id,
                )
            except PlaywrightError:
                ref = None
        element = next((e for e in snap.elements if ref and e.ref == ref), None)
        return snap, element, ref

    def _step(self, action: str, intent: str, snap: UISnapshot, element: UIElement | None,
              bundle: dict[str, Any] | None, bundle_error: str | None) -> Step:
        step = Step(turn=self.next_turn(), pre=snapshot_to_dict(snap),
                    decision={"kind": action, "intent": intent, "performed_by": "human"},
                    action=action, performed_by="human", bundle=bundle,
                    bundle_error=bundle_error,
                    target=None if element is None else asdict(element))
        return step

    def _keep(self, step: Step, post: UISnapshot | None, pre: UISnapshot) -> None:
        if post is not None and step.action != "gap":
            step.decision["expect"] = diff_expectation(pre, post, self.rendered)
            step.post_hash = post.hash
        self.record(step)
        self.done.append(step)

    def _gap(self, surface: WebSurface, what: str, snap: UISnapshot | None = None) -> None:
        snap = snap if snap is not None else surface.observe()
        step = self._step("gap", f"A person acted here: {what}", snap, None, None, None)
        step.unrecorded, step.ok = what, True
        self.note("demonstration_gap", detail=what)
        self._keep(step, None, snap)

    def _bundle(self, surface: WebSurface, element: UIElement,
                extraction: bool = False) -> tuple[dict[str, Any] | None, str | None]:
        try:
            return surface.synthesize(element.ref, self.values,
                                      extraction=extraction).model_dump(mode="json"), None
        except ValueError as exc:
            return None, locator_problem(exc)

    # ------------------------------------------------------------ one action

    def _click(self, surface: WebSurface, event: _Event) -> None:
        snap, element, ref = self._locate(surface, event)
        if element is None or ref is None:
            self._release(event)
            self._gap(surface, "clicked a control that perception does not expose", snap)
            return
        name = element.name or f"a {element.role}"
        intent = f"A person clicked {element.role} {name!r}"
        bundle, error = self._bundle(surface, element)
        self.recorder.drain()
        self.recorder.suppress_clicks += 1  # their click is already in the human log
        self._pass(event)
        with self.session.acting_for_the_person(surface):
            result = surface.act(Action("click", ref=ref, intent=intent))
        if not result.ok:
            self.recorder.suppress_clicks = max(0, self.recorder.suppress_clicks - 1)
            self._release(event)
            self._gap(surface, f"{intent}: policy would not repeat it ({result.error_code})",
                      snap)
            return
        step = self._step("click", intent, snap, element, bundle, error)
        step.ok, step.navigated = True, result.navigated
        step.risk = self._risk(surface)
        self._keep(step, surface.observe(), snap)

    def _change(self, surface: WebSurface, event: _Event) -> None:
        snap, element, _ = self._locate(surface, event)
        payload = event.payload
        label = (element.anchors[0] if element and element.anchors else "a field")
        if payload.get("secret") or payload.get("value") is None:
            self._gap(surface, f"typed a credential into {label!r}", snap)
            return
        if element is None:
            self._gap(surface, "typed into a field that perception does not expose", snap)
            return
        value_ref, provenance, _ = self.value_of(str(payload.get("value")))
        action = "select" if payload.get("tag") == "select" else "type"
        intent = f"A person filled in {label!r}"
        bundle, error = self._bundle(surface, element)
        step = self._step(action, intent, snap, element, bundle, error)
        step.ok, step.value_ref, step.value_provenance = True, value_ref, provenance
        self._keep(step, surface.observe(), snap)

    def _key(self, surface: WebSurface, event: _Event) -> None:
        snap, element, ref = self._locate(surface, event)
        intent = "A person pressed Enter"
        self._pass(event)
        with self.session.acting_for_the_person(surface):
            result = surface.act(Action("key", ref=ref, value="Enter", intent=intent))
        if not result.ok:
            self._gap(surface, f"{intent}: policy would not repeat it ({result.error_code})",
                      snap)
            return
        step = self._step("key", intent, snap, element, None, None)
        step.decision["key"] = "Enter"
        step.ok, step.navigated = True, result.navigated
        step.risk = self._risk(surface)
        self._keep(step, surface.observe(), snap)

    # ----------------------------------------------------------------- page

    def _risk(self, surface: WebSurface) -> str:
        for event in reversed(surface.events):
            if event.get("event") in ("action_approved", "approval_required") and event.get("risk"):
                return str(event["risk"])
        return "safe"

    def _pass(self, event: _Event) -> None:
        """Let our own re-send through the holding script once."""
        if event.frame is not None:
            try:
                event.frame.evaluate("(n) => window.__wpDemoPass && window.__wpDemoPass(n)",
                                     self.nonce)
            except PlaywrightError:
                pass

    def _release(self, event: _Event) -> None:
        """Nothing replayable came of it: let the person's own click happen for real."""
        demo_id = event.payload.get("demo_id")
        if event.frame is None or demo_id is None:
            return
        self._pass(event)
        try:
            event.frame.evaluate(
                """(id) => {
                    const el = document.querySelector(`[data-wp-demo="${id}"]`);
                    if (el) el.click();
                }""",
                demo_id,
            )
        except PlaywrightError:
            pass
