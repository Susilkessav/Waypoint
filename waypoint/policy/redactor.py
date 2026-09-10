"""The one redactor (PLAN.md R-SENS-3, R-SENS-4, R-SENS-5).

Every sink renders sensitive text through this module: the model prompt, evidence
on disk, local stdout and the caller. The classifier decides *how sensitive* a
piece of text is, at perception time; this module decides only *how it looks at
a given sink*. There are no scattered ``if sensitive:`` checks anywhere else.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from enum import StrEnum
from urllib.parse import parse_qsl, unquote, urlsplit

from waypoint.surface.ports import (
    FrameURL,
    RawElement,
    RawSnapshot,
    Sensitivity,
    TextClass,
    UIElement,
    UISnapshot,
)
from waypoint.surface.sensitivity import (
    MIN_SUBSTRING_BINDING_LEN,
    PATTERNS,
    Binding,
    binding_pattern,
    redactable,
)


class Sink(StrEnum):
    MODEL = "model"
    EVIDENCE = "evidence"
    STDOUT = "stdout"
    CALLER = "caller"


#: R-SENS-3's table, as data: a level is shown in full only at the sinks listed.
VISIBLE_AT: dict[Sensitivity, frozenset[Sink]] = {
    "secret": frozenset(),
    "pii": frozenset({Sink.CALLER}),
    "internal": frozenset({Sink.STDOUT, Sink.CALLER}),
    "public": frozenset(Sink),
}

SECRET = "‹secret›"
SPAN = "‹redacted›"
DIGEST_LIMIT = 4000
#: A path segment that looks like an identifier: a digit run, a long hex id, an address.
ID_SEGMENT_RE = re.compile(r"\d{3,}|[0-9a-fA-F]{12,}|@")


def _literal(text: str) -> Callable[[re.Match[str]], str]:
    """A re.sub replacement inserting ``text`` verbatim - no backslash or group parsing."""
    return lambda _match: text


def visible(level: Sensitivity, sink: Sink) -> bool:
    return sink in VISIBLE_AT[level]


def whole_placeholder(text: str) -> str:
    return f"‹redacted:{len(text)} chars›"


def binding_placeholder(name: str) -> str:
    return f"‹$inputs.{name}›"


class Redactor:
    def __init__(self, bindings: Sequence[Binding] = ()) -> None:
        self._secrets = {b.value for b in bindings if b.sensitivity == "secret" and b.value}
        live = [b for b in bindings if redactable(b)]
        self._exact = {b.value.strip(): b for b in live}
        self._substring = sorted(
            (
                (b, binding_pattern(b))
                for b in live
                if len(b.value.strip()) >= MIN_SUBSTRING_BINDING_LEN
            ),
            key=lambda pair: len(pair[0].value),
            reverse=True,  # longest first, so one bound value never splits another
        )

    # ------------------------------------------------------------------ text

    def remember_secret(self, value: str) -> None:
        if value:
            self._secrets.add(value)

    def text(self, raw: str, cls: TextClass, sink: Sink = Sink.MODEL) -> str:
        if not raw:
            return raw
        if cls.level == "secret":
            return SECRET  # never shown, and no length: a password's length is a clue
        if cls.whole:
            if visible(cls.level, sink):
                return self._hide_secrets(raw)
            return binding_placeholder(cls.binding) if cls.binding else whole_placeholder(raw)
        return self.scrub(raw, sink)

    def scrub(self, raw: str, sink: Sink = Sink.MODEL) -> str:
        """Span-level redaction for chrome: bound values and content patterns only."""
        out = self._hide_secrets(raw)
        for binding, rx in self._substring:
            if not visible(binding.sensitivity, sink):
                out = rx.sub(_literal(binding_placeholder(binding.name)), out)
        if not visible("pii", sink):
            for rx in PATTERNS.values():
                out = rx.sub(SPAN, out)
        return out

    def _hide_secrets(self, raw: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            raw = raw.replace(secret, SECRET)
        return raw

    def error(self, exc: BaseException) -> str:
        """Exceptions are an evidence sink, including URLs echoed by Playwright."""
        raw = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raw = re.sub(r"https?://[^\s<>\"']+", lambda m: self.url(m.group()), raw)
        return self.scrub(raw, Sink.EVIDENCE)[:300]

    # ------------------------------------------------------------------- url

    def url(self, raw_url: str, sink: Sink = Sink.MODEL) -> str:
        """Query values, fragments and identifier-like path segments (R-SENS-4)."""
        parts = urlsplit(raw_url)
        show_internal = visible("internal", sink)

        def component(value: str) -> str:
            decoded = unquote(value)
            hidden = self._hide_secrets(decoded)
            return hidden if hidden != decoded else value

        path = "/".join(
            component(seg) if show_internal else self._segment(seg) for seg in parts.path.split("/")
        )
        query = "&".join(
            f"{component(k)}={component(v)}" if show_internal else self._query_pair(component(k), v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        )
        netloc = component(parts.netloc.rsplit("@", 1)[-1])
        out = f"{parts.scheme}://{netloc}" if parts.scheme else netloc
        out += path
        if query:
            out += f"?{query}"
        if parts.fragment:
            out += f"#{component(parts.fragment) if show_internal else SPAN}"
        return out

    def _segment(self, seg: str) -> str:
        decoded = unquote(seg)
        if self._hide_secrets(decoded) != decoded:
            return SECRET
        bound = self._exact.get(decoded)
        if bound is not None:
            return binding_placeholder(bound.name)
        return SPAN if seg and ID_SEGMENT_RE.search(decoded) else seg

    def _query_pair(self, key: str, value: str) -> str:
        if not value:
            return f"{key}="
        bound = self._exact.get(value.strip())
        return f"{key}={binding_placeholder(bound.name) if bound else SPAN}"

    # --------------------------------------------------------------- snapshot

    def element(self, e: RawElement, sink: Sink = Sink.MODEL) -> UIElement:
        return UIElement(
            ref=e.ref,
            role=e.role,
            name=self.text(e.name.text, e.name.cls, sink),
            value=None if e.value is None else self.text(e.value.text, e.value.cls, sink),
            enabled=e.enabled,
            frame_path=e.frame_path,
            bbox=e.bbox,
            anchors=tuple(self.text(a.text, a.cls, sink) for a in e.anchors),
            sensitivity=e.value.cls.level if e.value is not None else "public",
            name_sensitivity=e.name.cls.level,
        )

    def snapshot(self, raw: RawSnapshot) -> UISnapshot:
        """Sanitize for the model and for evidence on disk.

        One snapshot serves both because R-SENS-3's MODEL and EVIDENCE columns are
        identical for every level - a property tests/test_redactor.py pins down.
        """
        elements = tuple(self.element(e, Sink.MODEL) for e in raw.elements)
        url = self.url(raw.url, Sink.MODEL)
        frame_urls = tuple((path, self.url(u, Sink.MODEL)) for path, u in raw.frame_urls)
        return UISnapshot(
            url=url,
            title=self.scrub(raw.title, Sink.MODEL),
            elements=elements,
            text_digest=text_digest(elements),
            hash=snapshot_hash(url, elements, frame_urls),
            frame_urls=frame_urls,
        )


def text_digest(elements: Sequence[UIElement]) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for e in elements:
        if e.name and e.name not in seen:
            seen.add(e.name)
            parts.append(e.name)
    return " | ".join(parts)[:DIGEST_LIMIT]


def snapshot_hash(
    url: str, elements: Sequence[UIElement], frame_urls: Sequence[FrameURL] = ()
) -> str:
    """Stable identity of a screen's structure (PLAN.md section 6.1).

    Sorted (frame, role, name) triples plus the URL path: independent of element
    order, refs and bounding boxes. Computed over *sanitized* text on purpose - a
    hash of a raw five-digit member ID could be reversed by trying all 100,000.
    """
    rows = sorted(json.dumps([list(e.frame_path), e.role, e.name]) for e in elements)
    frames = sorted(json.dumps([list(p), urlsplit(u).path]) for p, u in frame_urls)
    payload = json.dumps({"path": urlsplit(url).path, "frames": frames, "elements": rows})
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
