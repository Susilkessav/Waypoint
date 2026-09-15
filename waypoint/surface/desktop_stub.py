"""A Surface over native desktop applications - deliberately not implemented.

The seam is the deliverable (REPORT.md §7). This class exists to prove the
Surface port can be implemented by something other than a browser: it satisfies
the same Protocol as the web adapter, and each method documents the operating
system accessibility calls it would make.

The one structural difference from the web is the *ref bridge*. On the web an
accessibility node is not actionable, so perception stamps a ``data-wp-ref``
attribute to get back to a clickable DOM element. On the desktop the
accessibility element already *is* the actionable handle - a UIA
``IUIAutomationElement`` or an AXAPI ``AXUIElementRef`` - so a ref maps straight
to a cached element and nothing is mutated. Everything above the Surface,
including sensitivity classification and redaction, is unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from waypoint.surface.locators import LocatorBundle, Resolution
from waypoint.surface.ports import Action, ActionResult, Evidence, Quiescence, UISnapshot

if TYPE_CHECKING:
    from waypoint.surface.ports import Surface

_SEAM = "DesktopSurface is a documented seam, not an implementation (REPORT.md §7)."


class DesktopSurface:
    def resolve(self, bundle: LocatorBundle, inputs: Mapping[str, str] | None = None) -> Resolution:
        """Map semantic candidates to UIA FindAll / AXAPI tree queries; verify identity."""
        raise NotImplementedError(f"resolve: UIA FindAll / AXAPI tree queries. {_SEAM}")

    def observe(self) -> UISnapshot:
        """Walk the OS accessibility tree of the target window.

        UIA (Windows): ``IUIAutomation.ElementFromHandle(hwnd)``, then a
            ControlViewWalker. role <- ControlType, name <- Name,
            value <- ValuePattern.Value, enabled <- IsEnabled,
            bbox <- BoundingRectangle.
        AXAPI (macOS): ``AXUIElementCreateApplication(pid)``, recursing
            kAXChildrenAttribute. role <- kAXRoleAttribute,
            name <- kAXTitleAttribute or kAXDescriptionAttribute,
            value <- kAXValueAttribute, enabled <- kAXEnabledAttribute,
            bbox <- kAXPositionAttribute + kAXSizeAttribute.
        """
        raise NotImplementedError(
            f"observe: UIA ControlViewWalker / AXAPI kAXChildrenAttribute. {_SEAM}"
        )

    def act(self, action: Action) -> ActionResult:
        """Dispatch through the element's own accessibility patterns.

        click  -> UIA InvokePattern.Invoke()        | AXAPI AXUIElementPerformAction(kAXPress)
        type   -> UIA ValuePattern.SetValue()       | AXAPI set kAXValueAttribute
        select -> UIA SelectionItemPattern.Select() | AXAPI set kAXSelectedAttribute
        key    -> SendInput                         | CGEventPost
        """
        raise NotImplementedError(
            f"act({action.kind}): UIA control patterns / AXAPI AXUIElementPerformAction. {_SEAM}"
        )

    def extract_raw(self, ref: str) -> str | None:
        """UIA ValuePattern.Value or Name | AXAPI kAXValueAttribute, on the cached element."""
        raise NotImplementedError(
            f"extract_raw: UIA ValuePattern.Value / AXAPI kAXValueAttribute. {_SEAM}"
        )

    def capture_evidence(self) -> Evidence:
        """UIA + PrintWindow | AXAPI + CGWindowListCreateImage, black-boxed as on the web."""
        raise NotImplementedError(
            f"capture_evidence: UIA + PrintWindow / AXAPI + CGWindowListCreateImage. {_SEAM}"
        )

    def quiesce(self, timeout_ms: int = 5000) -> Quiescence:
        """Wait for UIA StructureChanged events / AXAPI AXObserver notifications to subside."""
        raise NotImplementedError(
            f"quiesce: UIA StructureChanged events / AXAPI AXObserver notifications. {_SEAM}"
        )


if TYPE_CHECKING:
    _conforms: Surface = DesktopSurface()
