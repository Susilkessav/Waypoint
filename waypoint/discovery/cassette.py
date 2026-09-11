"""Record and replay the model's decisions, so a discovery run reproduces offline.

A cassette stores, per turn, the sanitized snapshot hash the model saw and the
decision it made. Replaying feeds the same decisions back **only while the screen
still hashes the same**: if the application has drifted, the cassette stops with
``CassetteMismatch`` rather than clicking a stale decision into a different screen.

This is what lets a reviewer without an API key run discovery end to end, and it is
why the README can explain "how to run without live services". A cassette contains
no raw values: hashes are over sanitized text, and decisions reference bound values
as ``$inputs.x`` / ``$secrets.x``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from waypoint.discovery.decisions import Decision
from waypoint.surface.ports import UIElement, UISnapshot


@dataclass(frozen=True)
class DecisionContext:
    """Everything a decider may look at for one turn - all of it sanitized."""

    turn: int
    goal: str
    snapshot: UISnapshot
    elements: tuple[tuple[str, UIElement], ...]
    """Prompt-local ids ("e1"...) in snapshot order, and the element each names."""
    input_names: tuple[str, ...]
    secret_names: tuple[str, ...]
    output_names: tuple[str, ...]
    last_result: str | None = None


class Decider(Protocol):
    model: str

    def decide(self, ctx: DecisionContext) -> Decision: ...


class CassetteMismatch(RuntimeError):
    pass


@dataclass
class Cassette:
    model: str
    goal: str
    turns: list[dict[str, Any]] = field(default_factory=list)
    provenance: str = "recorded from a live model run"

    def save(self, path: Path) -> None:
        body = {"model": self.model, "goal": self.goal, "provenance": self.provenance,
                "turns": self.turns}
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def load(path: Path) -> Cassette:
        data = json.loads(path.read_text())
        return Cassette(data["model"], data["goal"], data["turns"],
                        data.get("provenance", "recorded from a live model run"))


class RecordingDecider:
    """Wraps a live decider and writes every decision to a cassette."""

    def __init__(self, inner: Decider, cassette: Cassette) -> None:
        self.inner = inner
        self.cassette = cassette
        self.model = inner.model

    def decide(self, ctx: DecisionContext) -> Decision:
        decision = self.inner.decide(ctx)
        self.cassette.turns.append(
            {"turn": ctx.turn, "snapshot_hash": ctx.snapshot.hash, "decision": decision.to_dict()}
        )
        return decision


class CassetteDecider:
    """Replays a cassette, refusing to continue once the screen stops matching."""

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette
        self.model = f"cassette:{cassette.model}"
        self._by_turn = {t["turn"]: t for t in cassette.turns}

    def decide(self, ctx: DecisionContext) -> Decision:
        entry = self._by_turn.get(ctx.turn)
        if entry is None:
            raise CassetteMismatch(f"the cassette has no decision for turn {ctx.turn}")
        if entry["snapshot_hash"] != ctx.snapshot.hash:
            raise CassetteMismatch(
                f"turn {ctx.turn}: the screen no longer matches the recording "
                f"({ctx.snapshot.hash} != {entry['snapshot_hash']}); the application may "
                "have changed - re-run discovery live"
            )
        return Decision.from_dict(entry["decision"])
