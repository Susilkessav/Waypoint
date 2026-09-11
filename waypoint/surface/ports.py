"""The Surface port - the seam between perceiving/acting on an application and
everything above it (PLAN.md sections 4.1 and 3.7).

Discovery, the compiler and replay speak only the types in this module. The web
adapter and the desktop stub implement the same Protocol; that shared contract is
the answer to "how would this extend to a desktop app?".

Two snapshot types exist on purpose (PLAN.md R-SENS-6):

* ``RawSnapshot`` carries real values. It lives only inside a Surface, in memory;
  it is never serialized or logged, and is discarded on the next ``observe()``.
* ``UISnapshot`` is sanitized, and is the only snapshot allowed to leave a Surface.

Real values reach the replay engine through exactly one door: ``extract_raw``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, get_args, runtime_checkable

if TYPE_CHECKING:
    from waypoint.surface.locators import LocatorBundle, Resolution

Sensitivity = Literal["public", "internal", "pii", "secret"]
_RANK: dict[str, int] = {"public": 0, "internal": 1, "pii": 2, "secret": 3}


def rank(level: Sensitivity) -> int:
    return _RANK[level]


def max_sensitivity(*levels: Sensitivity) -> Sensitivity:
    """The most sensitive of ``levels``: public < internal < pii < secret."""
    return max(levels, key=rank)


ActionKind = Literal[
    "navigate", "click", "type", "select", "key", "wait_for", "read", "assert", "dismiss", "finish"
]
#: The closed action set (PLAN.md section 4.4). Closed on purpose: page content
#: can never become a novel operation.
ACTION_KINDS: frozenset[str] = frozenset(get_args(ActionKind))

BBox: TypeAlias = tuple[int, int, int, int]
"""x, y, width, height in CSS pixels, relative to the top-level viewport."""

FrameURL: TypeAlias = tuple[tuple[str, ...], str]
"""(frame path, URL). In a frameset the top-level URL is always the shell, so the
screen actually showing is only visible per frame."""


@dataclass(frozen=True)
class TextClass:
    """How sensitive one piece of text is, and how much of it is data."""

    level: Sensitivity
    whole: bool = True
    """True when the entire text is data (a table cell, a field value); False when
    only pattern-matched spans are sensitive (a money figure inside a label)."""
    binding: str | None = None
    """Name of the declared input whose value this text equals exactly, if any."""


PUBLIC = TextClass("public", whole=False)


@dataclass(frozen=True)
class RawText:
    text: str
    cls: TextClass


@dataclass(frozen=True)
class RawElement:
    """One perceived element with its REAL name and value. Never leaves a Surface."""

    ref: str
    role: str
    name: RawText
    value: RawText | None
    enabled: bool
    frame_path: tuple[str, ...]
    bbox: BBox | None
    anchors: tuple[RawText, ...]


@dataclass(frozen=True)
class RawSnapshot:
    url: str
    title: str
    elements: tuple[RawElement, ...]
    frame_urls: tuple[FrameURL, ...] = ()


@dataclass(frozen=True)
class UIElement:
    """One element as the model, the evidence and the logs may see it."""

    ref: str
    role: str
    name: str
    value: str | None
    enabled: bool
    frame_path: tuple[str, ...]
    bbox: BBox | None
    anchors: tuple[str, ...]
    sensitivity: Sensitivity
    name_sensitivity: Sensitivity


@dataclass(frozen=True)
class UISnapshot:
    """Sanitized for the model and for evidence on disk (PLAN.md R-SENS-3)."""

    url: str
    title: str
    elements: tuple[UIElement, ...]
    text_digest: str
    hash: str
    frame_urls: tuple[FrameURL, ...] = ()


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    ref: str | None = None
    value: str | None = field(default=None, repr=False)
    url: str | None = None
    intent: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError("unsupported action kind")


@dataclass(frozen=True)
class ActionResult:
    kind: ActionKind
    ok: bool
    ref: str | None = None
    navigated: bool = False
    error: str | None = None
    duration_ms: int = 0
    error_code: str | None = None
    quiescence: Quiescence | None = None


@dataclass(frozen=True)
class Settled:
    waited_ms: int


@dataclass(frozen=True)
class InFlight:
    waited_ms: int
    pending: tuple[str, ...]


Quiescence: TypeAlias = Settled | InFlight


@dataclass(frozen=True)
class Evidence:
    snapshot: UISnapshot
    screenshot_png: bytes
    """Sensitive regions are black-boxed before this is ever constructed."""


@runtime_checkable
class Surface(Protocol):
    """What every surface - web, desktop, anything - must offer.

    ``act`` gains a lease token in milestone A7 (PLAN.md R-PROC-4).
    """

    def observe(self) -> UISnapshot: ...
    def act(self, action: Action) -> ActionResult: ...
    def resolve(
        self, bundle: LocatorBundle, inputs: Mapping[str, str] | None = None
    ) -> Resolution: ...
    def extract_raw(self, ref: str) -> str | None: ...
    def capture_evidence(self) -> Evidence: ...
    def quiesce(self, timeout_ms: int = 5000) -> Quiescence: ...
