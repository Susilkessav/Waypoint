"""What the human did while holding control (R-RESUME-6).

The brief requires recording the operator's actions, and in this console navigation
alone would record almost nothing: tabs are ``__doPostBack`` cells that never change
the URL. So a small script is injected into every frame - and re-installed on every
navigation - that reports clicks, field changes and form submits through an exposed
binding; navigations come from the browser itself.

Everything is recorded conservatively and passes through the run's redactor:

* A field change records which field and its length - **never the characters typed**
  - and no length at all for a password.
* A click on a plain table cell records only the length of its text: in this console a
  cell is usually data. Named controls (links, buttons, tabs) keep their scrubbed name.
* URLs go through the same URL redaction as evidence (R-SENS-4).

The recorder is live only while the human holds the lease, so the agent's own clicks
are never logged as human actions.

Callbacks never call back into Playwright. They run inside the driver's event
dispatch, and a synchronous round trip from there (resolving a frame's path needs
several) deadlocks. A callback only enqueues what it saw, with the frame's URL and the
time captured then; the engine thread drains the queue on every poll and before each
control-transfer marker, so the log stays in order.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame, Page

from waypoint.policy.redactor import Redactor

BINDING = "__wpHuman"

_INIT_JS = r"""
(() => {
  if (window.__wpHumanInstalled) return;
  window.__wpHumanInstalled = true;
  const send = (e) => { try { if (window.__wpHuman) window.__wpHuman(e); } catch (_) {} };
  const nameOf = (el) => (el.getAttribute('aria-label') || el.innerText ||
                          el.value || el.getAttribute('title') || '').trim().slice(0, 80);
  const isField = (el) => el.matches(
    'input:not([type=submit]):not([type=button]):not([type=image]), textarea, select');
  document.addEventListener('click', (ev) => {
    const el = (ev.target.closest && ev.target.closest(
      'a, button, input, select, textarea, td, th, [role], [onclick]')) || ev.target;
    if (!el || !el.tagName) return;
    const clickable = !!el.getAttribute('onclick') || el.matches(
      'a, button, [role=button], [role=tab], input[type=submit], input[type=button]');
    send({type: 'click', tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
          name: isField(el) ? '' : nameOf(el), id: el.id || '', clickable});
  }, true);
  document.addEventListener('change', (ev) => {
    const el = ev.target;
    if (!el || !el.matches || !el.matches('input, textarea, select')) return;
    const cell = el.closest('td');
    const label = cell && cell.previousElementSibling ?
      (cell.previousElementSibling.innerText || '').trim().slice(0, 60) : '';
    send({type: 'field_change', field: el.name || el.id || '', label,
          secret: el.type === 'password', length: (el.value || '').length});
  }, true);
  document.addEventListener('submit', (ev) => {
    const f = ev.target;
    send({type: 'submit', action: f.action || location.href,
          method: (f.method || 'get').toUpperCase()});
  }, true);
})();
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class _Seen:
    at: str
    frame: Any
    payload: dict[str, Any]
    """A binding payload, or ``{"type": "navigation", "url": ...}``."""


class HumanRecorder:
    def __init__(
        self,
        page: Page,
        frame_path: Callable[[Frame], tuple[str, ...]],
        redactor: Redactor,
        path: Path,
    ) -> None:
        self.page = page
        self.frame_path = frame_path
        self.redactor = redactor
        self.path = path
        self.active = False
        self.count = 0
        self.actions = 0
        """Records made by the person, excluding the control-transfer markers."""
        self._installed = False
        self._pending: deque[_Seen] = deque()
        self._last_url: dict[tuple[str, ...], str] = {}

    def install(self) -> None:
        """Once per browser context: binding, init script, and the already-loaded frames."""
        if self._installed:
            return
        self.page.context.expose_binding(BINDING, self._on_event)
        self.page.context.add_init_script(_INIT_JS)
        for frame in self.page.frames:
            try:
                frame.evaluate(_INIT_JS)
            except PlaywrightError:
                pass  # a detaching frame; the init script covers its replacement
        self.page.on("framenavigated", self._on_navigation)
        self._installed = True

    def start(self, generation: int) -> None:
        self.drain()
        self.active = True
        self._write({"event": "control_transfer", "direction": "to_human",
                     "actor": "operator", "generation": generation})

    def stop(self, generation: int) -> None:
        self.drain()
        self._write({"event": "control_transfer", "direction": "to_agent",
                     "actor": "replay", "generation": generation})
        self.active = False

    def drain(self) -> None:
        """Resolve and write what the callbacks queued. Engine thread only.

        Resolving a frame path dispatches further events, which may enqueue more;
        the loop takes those too.
        """
        while self._pending:
            seen = self._pending.popleft()
            record = self._record(seen)
            if record is not None:
                self._write(record, at=seen.at)

    # -------------------------------------------------------------- events

    def _on_event(self, source: dict[str, Any], payload: Any) -> None:
        if self.active and isinstance(payload, dict):
            self._pending.append(_Seen(_now(), source.get("frame"), payload))

    def _on_navigation(self, frame: Frame) -> None:
        if self.active:
            self._pending.append(_Seen(_now(), frame, {"type": "navigation", "url": frame.url}))

    def _path(self, frame: Any) -> tuple[str, ...]:
        try:
            return self.frame_path(frame) if frame is not None else ("?",)
        except PlaywrightError:
            return ("?",)  # detached since: the event still counts, its frame is unknown

    def _record(self, seen: _Seen) -> dict[str, Any] | None:
        payload = seen.payload
        kind = payload.get("type")
        where = self._path(seen.frame)
        if kind == "navigation":
            to_url = self.redactor.url(str(payload.get("url", "")))
            record = {"event": "navigation", "from_url": self._last_url.get(where),
                      "to_url": to_url, "frame_path": list(where)}
            self._last_url[where] = to_url
            return record
        if kind == "click":
            tag, name = str(payload.get("tag", ""))[:12], str(payload.get("name", ""))
            if tag in ("td", "th") and not payload.get("clickable"):
                name = f"‹cell text: {len(name)} chars›" if name else ""
            return {
                "event": "click", "tag": tag, "role": str(payload.get("role", ""))[:40],
                "name": self.redactor.scrub(name)[:80],
                "element_id": self.redactor.scrub(str(payload.get("id", "")))[:120],
                "frame_path": list(where),
            }
        if kind == "field_change":
            secret = bool(payload.get("secret"))
            return {
                "event": "field_change",
                "field": self.redactor.scrub(str(payload.get("field", "")))[:120],
                "label": self.redactor.scrub(str(payload.get("label", "")))[:60],
                "length": None if secret else payload.get("length"),
                "changed": True, "frame_path": list(where),
            }
        if kind == "submit":
            action = self.redactor.url(str(payload.get("action", "")))
            return {"event": "submit", "action": action,
                    "method": str(payload.get("method", "GET"))[:8], "frame_path": list(where)}
        return None

    def _write(self, record: dict[str, Any], at: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = {"at": at or _now(), **record}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.count += 1
        if record["event"] != "control_transfer":
            self.actions += 1
