"""Meridian Servicing Console - a deliberately hostile legacy web fixture.

This is the system under test, NOT part of Waypoint. Nothing under `waypoint/`
may import it; tests/test_boundaries.py enforces that.

Milestone A1 provides only a health endpoint. The console screens (frameset,
results table with eight identically-named View links, postback tabs, nested
iframe) arrive in A2, and the sub-account flow in B1.
"""

from target_app.app import create_app

__all__ = ["create_app"]
