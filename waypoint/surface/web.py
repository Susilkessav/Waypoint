"""The web Surface: Chrome DevTools Protocol for perception, Playwright for action.

observe()     Per-frame ``Accessibility.getFullAXTree`` (one call does not see child
              frames - the A3 spike), perceive, stamp refs, sanitize.
act()         Resolves a ref to the single element carrying it and acts through
              Playwright, which waits for actionability; then waits for quiet. It
              never waits on ``load``: a frame's load event can resolve against the
              document being replaced, which bit A3 twice.
extract_raw() The one door to real values, for the replay engine only (R-SENS-6).

Perception mutates the page: every perceived element gets a ``data-wp-ref``
attribute (space-separated when a text node and its element share one). It is
cleared and rewritten on every observe(), and never appears in an artifact.
Policy checks run inside ``act()``. The control lease is planned for A7.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame, Locator, Page, Request, sync_playwright

from waypoint.policy.engine import (
    ActionFacts,
    Block,
    PolicyEngine,
    RequireApproval,
    RunContext,
)
from waypoint.policy.redactor import Redactor, Sink, visible
from waypoint.policy.secrets import SecretBroker
from waypoint.surface.locators import LocatorBundle, NotFound, Resolution, resolve
from waypoint.surface.perception import FrameAX, Perceived, perceive
from waypoint.surface.ports import (
    Action,
    ActionResult,
    BBox,
    Evidence,
    FrameURL,
    InFlight,
    Quiescence,
    RawElement,
    RawSnapshot,
    Settled,
    UISnapshot,
)
from waypoint.surface.sensitivity import Binding, SensitivityClassifier
from waypoint.surface.web_locators import WebMatcher

if TYPE_CHECKING:
    from waypoint.surface.ports import Surface

REF_ATTR = "data-wp-ref"
FORM_TAGS = frozenset({"INPUT", "SELECT", "TEXTAREA"})
KEPT_FORM_ATTRS = ("type", "autocomplete", "name", "id")
IDLE_MS = 300
"""No request in flight for this long counts as quiet."""
POLL_MS = 25

_STAMP_JS = """function (ref) {
  const el = this.nodeType === 1 ? this : this.parentElement;
  if (!el) return false;
  const cur = el.getAttribute('data-wp-ref');
  el.setAttribute('data-wp-ref', cur ? cur + ' ' + ref : ref);
  return true;
}"""
_CLEAR_JS = (
    "() => document.querySelectorAll('[data-wp-ref]')"
    ".forEach(e => e.removeAttribute('data-wp-ref'))"
)
_RECTS_JS = """() => Array.from(document.querySelectorAll('[data-wp-ref]')).map(e => {
  const r = e.getBoundingClientRect();
  return [e.getAttribute('data-wp-ref'), r.x, r.y, r.width, r.height];
})"""


def frame_segment(name: str | None, element_id: str | None, tag: str) -> str:
    """How one frame is named inside a frame path; shared by every frame walk."""
    return name or (f"iframe#{element_id}" if element_id else tag.lower())


def index_dom(
    root: dict[str, Any],
) -> tuple[dict[str, tuple[str, ...]], dict[str, dict[str, str]]]:
    """From one pierced ``DOM.getDocument``: frame id -> frame path, and
    backendNodeId -> attributes of form controls (the AX tree omits input type)."""
    frame_paths: dict[str, tuple[str, ...]] = {}
    form_attrs: dict[str, dict[str, str]] = {}

    def attrs(node: dict[str, Any]) -> dict[str, str]:
        a = node.get("attributes", [])
        return dict(zip(a[::2], a[1::2], strict=False))

    def walk(node: dict[str, Any], path: tuple[str, ...]) -> None:
        if node.get("nodeName") in FORM_TAGS:
            a = attrs(node)
            form_attrs[str(node["backendNodeId"])] = {k: a[k] for k in KEPT_FORM_ATTRS if k in a}
        for child in node.get("children", []):
            walk(child, path)
        if "contentDocument" in node:
            a = attrs(node)
            sub = (*path, frame_segment(a.get("name"), a.get("id"), node["nodeName"]))
            if node.get("frameId"):
                frame_paths[node["frameId"]] = sub
            walk(node["contentDocument"], sub)

    walk(root, ("main",))
    return frame_paths, form_attrs


def _ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


class WebSurface:
    def __init__(
        self,
        page: Page,
        bindings: Sequence[Binding] = (),
        *,
        policy: PolicyEngine | None = None,
        context: RunContext | None = None,
        secrets: SecretBroker | None = None,
        approve: Callable[[RequireApproval, Action], bool] | None = None,
    ) -> None:
        self.page = page
        self.classifier = SensitivityClassifier(bindings)
        self.redactor = Redactor(bindings)
        self.policy = policy or PolicyEngine()
        self.context = context or RunContext()
        self.secrets = secrets or SecretBroker()
        self.approve = approve
        self.lease_guard: Callable[[], None] | None = None
        """Set by a holder of the control lease; act() calls it first (R-PROC-4)."""
        self.human_in_control = False
        """Set by the engine while a person holds the lease. Their navigation is recorded as
        theirs; only while the agent drives must each request be one it was cleared for."""
        self._authorized: tuple[object, ...] | None = None
        self._block_reason = ""
        self.before_dispatch: Callable[[Action], None] | None = None
        """Called once policy and any approval have passed, immediately before dispatch.
        The engine writes its intent record here (R-REC-4); raising stops the dispatch."""
        self.events: list[dict[str, object]] = []
        self.matcher = WebMatcher(self)
        self._cdp = page.context.new_cdp_session(page)
        self._cdp.send("DOM.enable")
        self._cdp.send("Accessibility.enable")
        self._navigation_blocked = False
        self._cdp.on("Fetch.requestPaused", self._guard_navigation)
        self._cdp.send(
            "Fetch.enable",
            {
                "patterns": [
                    {"urlPattern": "*", "resourceType": "Document", "requestStage": "Request"}
                ]
            },
        )
        self._raw: RawSnapshot | None = None
        self._perceived: dict[str, Perceived] = {}
        self._pending: set[Request] = set()
        self._last_activity = time.monotonic()
        self._navigations = 0
        self._unsettled = False
        self._ref_namespace = uuid.uuid4().hex[:12]
        self._generation = 0
        page.on("request", self._on_request_start)
        page.on("requestfinished", self._on_request_end)
        page.on("requestfailed", self._on_request_end)
        page.on("framenavigated", self._on_navigated)

    @classmethod
    @contextmanager
    def launch(
        cls,
        *,
        headed: bool = False,
        bindings: Sequence[Binding] = (),
        policy: PolicyEngine | None = None,
        context: RunContext | None = None,
        secrets: SecretBroker | None = None,
        approve: Callable[[RequireApproval, Action], bool] | None = None,
    ) -> Iterator[WebSurface]:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headed)
            try:
                yield cls(
                    browser.new_page(),
                    bindings,
                    policy=policy,
                    context=context,
                    secrets=secrets,
                    approve=approve,
                )
            finally:
                browser.close()

    # --------------------------------------------------------------- observe

    def observe(self) -> UISnapshot:
        self._generation += 1
        self._raw = None
        self._perceived = {}
        frames, form_attrs, frame_urls = self._read_trees()
        perceived = perceive(
            frames,
            self.classifier,
            form_attrs,
            ref_prefix=f"{self._ref_namespace}g{self._generation}-",
        )
        for frame in self.page.frames:
            self._evaluate(frame, _CLEAR_JS)
        for p in perceived:
            self._stamp(p.backend_id, p.element.ref)
        boxes = self._boxes()
        self._perceived = {}
        elements: list[RawElement] = []
        for p in perceived:
            element = dataclasses.replace(p.element, bbox=boxes.get(p.element.ref))
            self._perceived[element.ref] = dataclasses.replace(p, element=element)
            elements.append(element)
        self._raw = RawSnapshot(self.page.url, self.page.title(), tuple(elements), frame_urls)
        return self.redactor.snapshot(self._raw)

    def _read_trees(
        self,
    ) -> tuple[list[FrameAX], dict[str, dict[str, str]], tuple[FrameURL, ...]]:
        tree = self._cdp.send("Page.getFrameTree")["frameTree"]
        flat: list[dict[str, Any]] = []

        def flatten(ft: dict[str, Any]) -> None:
            flat.append(ft["frame"])
            for child in ft.get("childFrames", []):
                flatten(child)

        flatten(tree)
        dom = self._cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
        paths, form_attrs = index_dom(dom)
        paths[tree["frame"]["id"]] = ("main",)
        frames: list[FrameAX] = []
        urls: list[FrameURL] = []
        for f in flat:
            path = paths.get(f["id"])
            if path is None:
                continue  # detached, or not yet attached to its parent document
            try:
                nodes = self._cdp.send("Accessibility.getFullAXTree", {"frameId": f["id"]})
            except PlaywrightError:
                continue  # navigated away mid-read; the next observe() will see it
            frames.append(FrameAX(f["id"], path, nodes["nodes"]))
            urls.append((path, f["url"]))
        return frames, form_attrs, tuple(urls)

    def _stamp(self, backend_id: int, ref: str) -> None:
        try:
            resolved = self._cdp.send("DOM.resolveNode", {"backendNodeId": backend_id})
            object_id = resolved["object"]["objectId"]
            self._cdp.send(
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": _STAMP_JS,
                    "arguments": [{"value": ref}],
                },
            )
            self._cdp.send("Runtime.releaseObject", {"objectId": object_id})
        except PlaywrightError:
            pass  # vanished since perception; acting on the ref will then fail loudly

    def _boxes(self) -> dict[str, BBox]:
        """One script per frame, offset by the frame's own position in the viewport."""
        out: dict[str, BBox] = {}
        for frame in self.page.frames:
            try:
                ox, oy = self._frame_offset(frame)
                rows = frame.evaluate(_RECTS_JS)
            except PlaywrightError:
                continue
            for refs, x, y, w, h in rows:
                if w <= 0 or h <= 0:
                    continue  # not rendered
                box = (round(x + ox), round(y + oy), round(w), round(h))
                for ref in str(refs).split():
                    out[ref] = box
        return out

    @staticmethod
    def _frame_offset(frame: Frame) -> tuple[float, float]:
        if frame.parent_frame is None:
            return 0.0, 0.0
        box = frame.frame_element().bounding_box()  # relative to the main viewport
        return (box["x"], box["y"]) if box else (0.0, 0.0)

    @staticmethod
    def _evaluate(frame: Frame, script: str) -> Any:
        try:
            return frame.evaluate(script)
        except PlaywrightError:
            return None

    # ------------------------------------------------------------------- act

    def act(self, action: Action) -> ActionResult:
        if self.lease_guard is not None:
            # Raises LeaseLost for a caller acting under a grant that has moved on.
            # Deliberately outside the try below: a stale actor must not proceed.
            self.lease_guard()
        started, navigations = time.monotonic(), self._navigations
        self._navigation_blocked = False
        self._block_reason = ""
        try:
            if self._unsettled and action.kind not in ("read", "assert", "wait_for"):
                return ActionResult(
                    action.kind,
                    False,
                    error="previous_action_still_unsettled",
                    error_code="in_flight",
                )
            facts = self._action_facts(action)
            verdict = self.policy.check(action, facts, self.context)
            if isinstance(verdict, Block):
                self.events.append({"event": "policy_block", "reason": verdict.reason})
                return ActionResult(
                    action.kind, False, action.ref, error=verdict.reason, error_code="policy_block"
                )
            if isinstance(verdict, RequireApproval):
                if (
                    self.context.unattended
                    or not verdict.approvable
                    or self.approve is None
                    or not self.approve(verdict, action)
                ):
                    self.events.append(
                        {
                            "event": "approval_required",
                            "reason": verdict.reason,
                            "risk": verdict.risk,
                        }
                    )
                    return ActionResult(
                        action.kind,
                        False,
                        action.ref,
                        error=verdict.reason,
                        error_code="approval_required",
                    )
                # A reviewer may change the page while deciding. Re-check the effect.
                if self._action_facts(action) != facts:
                    return ActionResult(
                        action.kind,
                        False,
                        error="state_changed_during_approval",
                        error_code="approval_required",
                    )
                self.events.append({"event": "action_approved", "risk": verdict.risk})
            self.policy.record(action, facts)
            if self.before_dispatch is not None:
                self.before_dispatch(action)  # the last moment nothing has been sent
            # The one document request this action was classified - and, if risky, approved -
            # for. Any other mutating request it causes, a redirect hop included, is refused.
            self._authorized = (
                _request_key(facts.method, facts.target_url) if facts.target_url else None
            )
            self._dispatch(action)
        except (PlaywrightError, LookupError, ValueError) as exc:
            self._authorized = None
            return ActionResult(
                action.kind,
                ok=False,
                ref=action.ref,
                # A navigation the guard refused surfaces as a driver error; report why.
                error=f"navigation_{self._block_reason or 'location_not_allowed'}"
                if self._navigation_blocked
                else self.redactor.error(exc),
                error_code="policy_block" if self._navigation_blocked else "action_failed",
                duration_ms=_ms(started),
            )
        try:
            quiet = self.quiesce()
        finally:
            self._authorized = None
        return ActionResult(
            action.kind,
            ok=isinstance(quiet, Settled) and not self._navigation_blocked,
            ref=action.ref,
            navigated=self._navigations != navigations,
            duration_ms=_ms(started),
            quiescence=quiet,
            error_code="policy_block"
            if self._navigation_blocked
            else ("in_flight" if isinstance(quiet, InFlight) else None),
            error=(
                f"navigation_{self._block_reason or 'location_not_allowed'}"
                if self._navigation_blocked
                else (
                    "Action dispatched; completion is unknown. Do not retry automatically."
                    if isinstance(quiet, InFlight)
                    else None
                )
            ),
        )

    def _dispatch(self, a: Action) -> None:
        if a.kind == "navigate":
            if not a.url:
                raise ValueError("navigate needs a url")
            self.page.goto(a.url, wait_until="commit")
        elif a.kind in ("click", "dismiss"):
            self._locate(a).click()
        elif a.kind in ("type", "select"):
            loc = self._locate(a)

            def write(value: str) -> None:
                if a.kind == "select":
                    loc.select_option(value)
                else:
                    loc.fill(value)

            if (a.value or "").startswith("$secrets."):
                facts = self._action_facts(a)

                def inject(value: str) -> None:
                    self.redactor.remember_secret(value)
                    self.classifier.remember_secret(value)
                    write(value)

                self.secrets.inject(a.value or "", facts.labels, inject)
            else:
                write(a.value or "")
        elif a.kind == "key":
            if not a.value:
                raise ValueError("key needs a value, e.g. 'Enter'")
            if a.ref:
                self._locate(a).press(a.value)
            else:
                self.page.keyboard.press(a.value)
        # wait_for: the quiesce() in act() is the wait. read / assert / finish are
        # decided above the surface and never touch the page.

    def _locate(self, a: Action) -> Locator:
        if not a.ref:
            raise ValueError(f"{a.kind} needs a ref")
        if a.ref not in self._perceived:
            raise LookupError(f"unknown ref {a.ref!r}: not in the latest snapshot")
        selector = f'[{REF_ATTR}~="{a.ref}"]'
        hits: list[Locator] = []
        for frame in self.page.frames:
            try:
                n = frame.locator(selector).count()
            except PlaywrightError:
                continue
            hits += [frame.locator(selector)] * n
        if len(hits) != 1:
            raise LookupError(
                f"ref {a.ref!r} is stale: matched {len(hits)} elements; observe again"
            )
        return hits[0]

    def _action_facts(self, action: Action) -> ActionFacts:
        if action.kind == "navigate":
            return ActionFacts(self.page.url, target_url=action.url)
        if not action.ref and action.kind != "key":
            return ActionFacts(self.page.url)
        if action.ref:
            loc = self._locate(action)
            p = self._perceived[action.ref]
            labels = tuple(a.text for a in p.element.anchors) + (p.element.name.text,)
            field = p.element.value
            secret_field = field is not None and field.cls.level == "secret"
            target_id = str(p.backend_id)
        else:
            frame = next(
                (
                    f
                    for f in self.page.frames
                    if f.evaluate(
                        "document.hasFocus() && "
                        "!['IFRAME','FRAME'].includes(document.activeElement.tagName)"
                    )
                ),
                self.page.main_frame,
            )
            loc = frame.locator(":focus")
            if not loc.count():
                return ActionFacts(frame.url, opaque_effect=True)
            labels, secret_field, target_id = (), False, "focused_control"
        data = loc.evaluate(
            r"""(e, kind) => {
            const link = e.closest('a[href]');
            const form = e.form || e.closest('form');
            const isSubmit = x => (x.tagName === 'BUTTON' && x.type === 'submit') ||
                (x.tagName === 'INPUT' && ['submit','image'].includes(x.type));
            const submit = form && (kind === 'key' ||
                (['click','dismiss'].includes(kind) && isSubmit(e)));
            const submitter = submit && (kind === 'key' ?
                Array.from(form.elements).find(x => isSubmit(x) && !x.disabled) : e);
            // The WebForms idiom: an onclick that is exactly one __doPostBack call submits
            // its form. Anything more in the handler stays opaque, so it needs approval.
            const pbCall = new RegExp(
                "^\\s*(?:javascript:)?\\s*__doPostBack\\(\\s*'[^']*'\\s*,\\s*'[^']*'\\s*\\)" +
                "\\s*;?\\s*(?:return\\s+false\\s*;?)?\\s*$");
            const postback = ['click','dismiss'].includes(kind) &&
                pbCall.test(e.getAttribute('onclick') || '') &&
                (form || document.forms['aspnetForm'] || document.forms[0]) || null;
            const labelledBy = (e.getAttribute('aria-labelledby') || '').split(/\s+/)
                .map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
            const ownName = labelledBy || e.getAttribute('aria-label') || e.innerText ||
                (['submit','button'].includes(e.type) ? e.value : '');
            // Enter in a field activates the form's submitter, so the submitter's name
            // is the control whose effect is being classified. No submitter: unnamed.
            const keyName = submitter ? (submitter.getAttribute('aria-label') ||
                submitter.innerText || submitter.value || '') : '';
            return {url: location.href, name: kind === 'key' && submit ? keyName : ownName,
                destination: submit ? (submitter?.hasAttribute('formaction') ?
                    submitter.formAction : form.action) :
                    (postback ? postback.action : (link && link.href)),
                method: submit ? (submitter?.hasAttribute('formmethod') ?
                    submitter.formMethod : form.method).toUpperCase() :
                    (postback ? (postback.method || 'post').toUpperCase() : 'GET'),
                secret: e.type === 'password',
                opaque: !!e.getAttribute('onclick') && !link && !submit && !postback};
        }""",
            (
                action.kind
                if action.value == "Enter" or action.kind != "key"
                else ("click" if action.value in ("Space", " ") else "other")
            ),
        )
        destination = data["destination"] if action.kind in ("click", "dismiss", "key") else None
        return ActionFacts(
            current_url=data["url"],
            target_url=urljoin(data["url"], destination) if destination else None,
            method=data["method"],
            name=data["name"],
            labels=labels,
            secret_field=secret_field or data["secret"],
            target_id=target_id,
            opaque_effect=data["opaque"],
        )

    @staticmethod
    def frame_path(frame: Frame) -> tuple[str, ...]:
        if frame.parent_frame is None:
            return ("main",)
        element = frame.frame_element()
        segment = frame_segment(
            element.get_attribute("name"),
            element.get_attribute("id"),
            element.evaluate("e => e.tagName"),
        )
        return (*WebSurface.frame_path(frame.parent_frame), segment)

    def resolve(self, bundle: LocatorBundle, inputs: Mapping[str, str] | None = None) -> Resolution:
        try:
            self.observe()
            return resolve(bundle, self.matcher, inputs or {}, self.events.append)
        except (PlaywrightError, ValueError):
            self.events.append(
                {"event": "locator_error", "reason": "invalid_or_unavailable_locator"}
            )
            return NotFound("invalid_or_unavailable_locator")

    def synthesize(
        self, ref: str, inputs: Mapping[str, str] | None = None, *, extraction: bool = False
    ) -> LocatorBundle:
        return self.matcher.synthesize(ref, inputs or {}, extraction=extraction)

    # --------------------------------------------------------------- quiesce

    def _guard_navigation(self, event: dict[str, Any]) -> None:
        """Intercept every document request, including redirects, before dispatch.

        CDP Fetch reports redirect hops individually. Checking only goto()'s
        initial URL would allow a permitted page to redirect off the allowlist.
        """
        request_id = event["requestId"]
        url = event["request"]["url"]
        method = event["request"].get("method", "GET")
        if not self.policy.allowed_url(url):
            reason = "location_not_allowed"
        elif (
            not self.human_in_control
            and self.policy.mutates(method, url)
            and _request_key(method, url) != self._authorized
        ):
            # R-RISK-3 applies to every hop, not only the action's own target: an allowed
            # page that redirects into a commit would otherwise execute it unclassified,
            # unapproved and with no intent written.
            reason = "unauthorized_mutation"
        else:
            if (
                not self.human_in_control
                and self._authorized is not None
                and self.policy.mutates(method, url)
            ):
                # One approval, one request: consumed the moment it is sent. A 307 back to
                # the same URL, or any retry, would otherwise commit again under the same
                # approval; refused, its effect goes to reconciliation instead.
                self._authorized = None
            self._cdp.send("Fetch.continueRequest", {"requestId": request_id})
            return
        self._navigation_blocked = True
        self._block_reason = reason
        self.events.append({"event": "navigation_blocked", "reason": reason})
        self._cdp.send(
            "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
        )

    def quiesce(self, timeout_ms: int = 5000) -> Quiescence:
        started = time.monotonic()
        deadline = started + timeout_ms / 1000
        while time.monotonic() < deadline:
            self.page.wait_for_timeout(POLL_MS)  # also lets Playwright deliver events
            quiet_ms = (time.monotonic() - self._last_activity) * 1000
            if not self._pending and quiet_ms >= IDLE_MS:
                self._unsettled = False
                return Settled(_ms(started))
        pending = tuple(sorted(self.redactor.url(r.url) for r in self._pending))
        self._unsettled = True
        return InFlight(_ms(started), pending)

    def _on_request_start(self, request: Request) -> None:
        self._pending.add(request)
        self._last_activity = time.monotonic()

    def _on_request_end(self, request: Request) -> None:
        self._pending.discard(request)
        self._last_activity = time.monotonic()

    def _on_navigated(self, _frame: Frame) -> None:
        self._navigations += 1
        self._last_activity = time.monotonic()

    # ------------------------------------------------------ raw and evidence

    def extract_raw(self, ref: str) -> str | None:
        """Real value of ``ref`` from the latest snapshot (PLAN.md R-SENS-6).

        The replay engine calls this to fill a caller's outputs. Nothing that logs,
        prompts or persists may call it.
        """
        p = self._perceived.get(ref)
        if p is None:
            return None
        if p.element.name.cls.level == "secret" or (
            p.element.value is not None and p.element.value.cls.level == "secret"
        ):
            return None
        return p.element.value.text if p.element.value is not None else p.element.name.text

    def row_raw(self, ref: str, columns: Sequence[str]) -> dict[str, str | None]:
        """Raw text of ``ref``'s table row under each column header; None where unreadable.

        Engine-only, like ``extract_raw``: reconciliation compares these values - balances,
        creation dates - and nothing may log them. A secret cell is never read.
        """
        p = self._perceived.get(ref)
        if p is None or p.element.name.cls.level == "secret":
            return dict.fromkeys(columns)
        try:
            row = self._locate(Action("read", ref=ref)).locator("xpath=ancestor::tr[1]")
            table = row.locator("xpath=ancestor::table[1]")
            headers = [h.inner_text().strip()
                       for h in table.locator("tr").first.locator("th").all()]
            cells = row.locator(":scope > td, :scope > th").all()
        except (PlaywrightError, LookupError):
            return dict.fromkeys(columns)
        out: dict[str, str | None] = {}
        for column in columns:
            i = headers.index(column) if column in headers else -1
            out[column] = self._cell_raw(cells[i]) if 0 <= i < len(cells) else None
        return out

    def _cell_raw(self, cell: Locator) -> str | None:
        """One cell's raw text through the same door as ``extract_raw``.

        Every cell read is looked up in the classified snapshot: a secret anywhere in it is
        withheld, and a cell perception never classified is not read at all.
        """
        try:
            refs = (cell.get_attribute(REF_ATTR) or "").split()
        except PlaywrightError:
            return None
        perceived = [ref for ref in refs if ref in self._perceived]
        if not perceived:
            return None
        texts = [self.extract_raw(ref) for ref in perceived]
        if any(text is None for text in texts):
            return None
        return (texts[0] or "").strip()

    def capture_evidence(self) -> Evidence:
        """Screenshot with every element the evidence sink may not see blacked out.

        Re-observes first: masking from a stale snapshot could miss new data.
        """
        snapshot = self.observe()
        assert self._raw is not None
        masks = [
            loc
            for element in self._raw.elements
            if _hidden_from_evidence(element)
            for loc in self._locators(element.ref)
        ]
        png = self.page.screenshot(mask=masks, mask_color="#000000")
        return Evidence(snapshot=snapshot, screenshot_png=png)

    def _locators(self, ref: str) -> list[Locator]:
        selector = f'[{REF_ATTR}~="{ref}"]'
        out: list[Locator] = []
        for frame in self.page.frames:
            try:
                if frame.locator(selector).count():
                    out.append(frame.locator(selector))
            except PlaywrightError:
                continue
        return out


def _request_key(method: str, url: str) -> tuple[object, ...]:
    """A request's identity for authorization: method, origin, decoded path, sorted query."""
    parts = urlsplit(url)
    query = tuple(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return (method.upper(), parts.scheme, parts.netloc.lower(), unquote(parts.path or "/"),
            query)


def _hidden_from_evidence(element: RawElement) -> bool:
    if element.name.text and not visible(element.name.cls.level, Sink.EVIDENCE):
        return True
    value = element.value
    return value is not None and bool(value.text) and not visible(value.cls.level, Sink.EVIDENCE)


if TYPE_CHECKING:

    def _conforms(surface: WebSurface) -> Surface:
        return surface
