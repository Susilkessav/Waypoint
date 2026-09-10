"""Waypoint - record-once, replay-many automation for legacy UIs with no API.

Discovery uses an LLM to find a path through a UI once. The compiler turns that
transcript into a typed, versioned capability artifact. Replay executes the
artifact deterministically with no model in the decision loop. When replay meets
a state it does not recognise, control transfers to a human on the same live
session.

Execution rules are specified in PLAN.md section 6 and referenced from code by
their stable IDs (R-LOC-5, R-REC-3, ...).
"""

__version__ = "0.1.0"
