"""The Surface port is implementable by something other than a browser (REPORT.md §4)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from waypoint.surface.desktop_stub import DesktopSurface
from waypoint.surface.locators import Candidate, LocatorBundle
from waypoint.surface.ports import ACTION_KINDS, Action, Surface, max_sensitivity

CALLS: dict[str, Callable[[DesktopSurface], Any]] = {
    "observe": lambda s: s.observe(),
    "act": lambda s: s.act(Action("click", ref="r1")),
    "extract_raw": lambda s: s.extract_raw("r1"),
    "capture_evidence": lambda s: s.capture_evidence(),
    "quiesce": lambda s: s.quiesce(),
    "resolve": lambda s: s.resolve(
        LocatorBundle(
            recorded_tier=1,
            candidates=(Candidate(tier=1, kind="role_name", role="button", name="View"),),
        )
    ),
}


def test_desktop_stub_satisfies_the_surface_port() -> None:
    assert isinstance(DesktopSurface(), Surface)


@pytest.mark.parametrize("method", sorted(CALLS))
def test_every_desktop_method_names_its_os_equivalents(method: str) -> None:
    with pytest.raises(NotImplementedError) as exc:
        CALLS[method](DesktopSurface())
    assert "UIA" in str(exc.value) and "AXAPI" in str(exc.value)


def test_the_action_set_is_closed() -> None:
    assert ACTION_KINDS == {
        "navigate",
        "click",
        "type",
        "select",
        "key",
        "wait_for",
        "read",
        "assert",
        "dismiss",
        "finish",
    }
    with pytest.raises(ValueError, match="unsupported"):
        Action("execute_arbitrary_code")  # type: ignore[arg-type]


def test_sensitivity_ordering() -> None:
    assert max_sensitivity("public", "pii", "internal") == "pii"
    assert max_sensitivity("secret", "public") == "secret"
