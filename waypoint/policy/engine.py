"""Configurable, deterministic action authorization; no model decides policy."""

from __future__ import annotations

import fnmatch
import posixpath
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias
from urllib.parse import unquote, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field

from waypoint.surface.ports import ACTION_KINDS, Action

Risk: TypeAlias = Literal["safe", "secret_write", "unknown", "irreversible"]
RISK_RANK = {"safe": 0, "secret_write": 1, "unknown": 2, "irreversible": 3}


class Allowlist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hosts: list[str]
    routes: list[str] = ["/", "/console", "/console/*"]
    actions: list[str] = sorted(ACTION_KINDS)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_actions_per_minute: int = Field(default=60, ge=1)
    max_steps_per_run: int = Field(default=25, ge=1)
    loop_breaker: int = Field(default=3, ge=1)


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowlist: Allowlist
    mutating_routes: list[str] = []
    readonly_routes: list[str] = []
    """Reviewed exceptions to ``mutating_routes`` - see policy.yaml for why they exist."""
    irreversible_verbs: list[str] = []
    unknown_state_policy: Literal["safe_only", "block"] = "safe_only"
    limits: Limits = Field(default_factory=Limits)

    @classmethod
    def load(cls, path: Path | None = None) -> PolicyConfig:
        path = path or Path(__file__).with_name("policy.yaml")
        return cls.model_validate(yaml.safe_load(path.read_text()))

    @classmethod
    def for_origin(cls, url: str) -> PolicyConfig:
        config = cls.load()
        config.allowlist.hosts = [urlsplit(url).netloc.lower()]
        return config


@dataclass(frozen=True)
class RunContext:
    unattended: bool = True
    state_known: bool = False
    declared_risk: Risk = "safe"
    declared_by_artifact: bool = False
    """True in replay, where ``declared_risk`` is a reviewed claim the page must live up
    to (R-RISK-7). In discovery nothing has been declared yet, so it is only a floor."""


@dataclass(frozen=True)
class ActionFacts:
    current_url: str
    target_url: str | None = None
    method: str = "GET"
    name: str = ""
    secret_field: bool = False
    labels: tuple[str, ...] = ()
    target_id: str = ""
    opaque_effect: bool = False


@dataclass(frozen=True)
class Allow:
    risk: Risk = "safe"


@dataclass(frozen=True)
class Block:
    reason: str


@dataclass(frozen=True)
class RequireApproval:
    reason: str
    risk: Risk
    approvable: bool = True
    """False when no inline approval may clear it: the artifact itself must be reviewed."""


Verdict: TypeAlias = Allow | Block | RequireApproval

APPROVAL_RISKS: frozenset[Risk] = frozenset({"irreversible", "unknown"})
"""Risks that need a human before they execute, attended or not."""


class PolicyEngine:
    def __init__(
        self, config: PolicyConfig | None = None, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.config = config or PolicyConfig.load()
        self.clock = clock
        self._times: list[float] = []
        self._steps = 0
        self._previous: tuple[str, str, str | None] | None = None
        self._repeats = 0

    def allowed_url(self, url: str) -> bool:
        try:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or parts.username or parts.password:
                return False
            if parts.netloc.lower() not in self.config.allowlist.hosts or "\\" in url:
                return False
            path = parts.path or "/"
            for _ in range(3):
                decoded = unquote(path)
                if decoded == path:
                    break
                path = decoded
            if "%" in path or "\\" in path:
                return False
            path = posixpath.normpath(path)
            return any(fnmatch.fnmatchcase(path, p) for p in self.config.allowlist.routes)
        except ValueError:
            return False

    def classify(self, action: Action, facts: ActionFacts) -> Risk:
        if action.kind in ("read", "assert", "wait_for", "finish"):
            return "safe"
        risk: Risk = "safe"
        if facts.target_url:
            raw_path = urlsplit(facts.target_url).path or "/"
            route = f"{facts.method.upper()} {unquote(raw_path)}"
            mutating = any(fnmatch.fnmatchcase(route, p) for p in self.config.mutating_routes)
            if mutating and not self._reviewed_readonly(facts.method, raw_path):
                risk = "irreversible"
        if action.kind in ("click", "dismiss") or (
            action.kind == "key" and action.value in ("Enter", "Space", " ")
        ):
            if any(
                re.search(rf"\b{re.escape(v)}\b", facts.name, re.I)
                for v in self.config.irreversible_verbs
            ):
                risk = "irreversible"
            elif not facts.name.strip() or facts.opaque_effect:
                risk = max((risk, "unknown"), key=lambda r: RISK_RANK[r])
        if action.kind in ("type", "select") and facts.secret_field:
            risk = max((risk, "secret_write"), key=lambda r: RISK_RANK[r])
        return risk

    def _reviewed_readonly(self, method: str, path: str) -> bool:
        """A reviewed read-only route, matched only on the literal, canonical raw path.

        An exemption is a statement about one literal route. A path that is encoded
        (``/console/%73earch``) or changes under normalisation (``/console/search/../x``)
        never borrows it, whatever it resolves to: the cost is one approval ping.
        """
        if path != posixpath.normpath(path) or "%" in path or "\\" in path:
            return False
        route = f"{method.upper()} {path}"
        return any(fnmatch.fnmatchcase(route, p) for p in self.config.readonly_routes)

    def check(self, action: Action, facts: ActionFacts, context: RunContext) -> Verdict:
        if action.kind not in self.config.allowlist.actions:
            return Block("action_not_allowed")
        # A first navigation may start at about:blank, but its destination is checked.
        if action.kind != "navigate" and not self.allowed_url(facts.current_url):
            return Block("current_location_not_allowed")
        if facts.target_url and not self.allowed_url(facts.target_url):
            return Block("target_location_not_allowed")
        if action.kind == "navigate" and not facts.target_url:
            return Block("missing_navigation_target")
        if facts.secret_field and action.kind in ("type", "select"):
            if not (action.value or "").startswith("$secrets."):
                return Block("secret_requires_broker")
        if (
            facts.secret_field
            and action.kind == "key"
            and action.value not in ("Enter", "Tab", "Shift+Tab")
        ):
            return Block("secret_requires_broker")
        now = self.clock()
        limits = self.config.limits
        if self._steps >= limits.max_steps_per_run:
            return Block("step_limit")
        if sum(t > now - 60 for t in self._times) >= limits.max_actions_per_minute:
            return Block("rate_limit")
        key = (action.kind, facts.target_id or facts.target_url or facts.current_url, action.value)
        if key == self._previous and self._repeats >= limits.loop_breaker - 1:
            return Block("loop_breaker")
        computed = self.classify(action, facts)
        if (
            context.declared_by_artifact
            and computed in APPROVAL_RISKS
            and RISK_RANK[computed] > RISK_RANK[context.declared_risk]
        ):
            # R-RISK-7: the page now does more than the reviewed artifact says - a renamed
            # control, a moved route. A drifted page must not run under a stale label, and
            # approving it inline would approve the drift, so nobody may. Only a computed
            # risk that changes the handling counts: typing a password computes
            # secret_write, which is handled exactly as a declared "safe" would be.
            return RequireApproval("risk_exceeds_declared", computed, approvable=False)
        risk = max((computed, context.declared_risk), key=lambda r: RISK_RANK[r])
        if not context.state_known:
            if self.config.unknown_state_policy == "block" or risk != "safe":
                return RequireApproval("unknown_state", risk)
        if risk in APPROVAL_RISKS:
            return RequireApproval("risky_action", risk)
        return Allow(risk)

    def record(self, action: Action, facts: ActionFacts) -> None:
        now = self.clock()
        self._times = [t for t in self._times if t > now - 60] + [now]
        self._steps += 1
        key = (action.kind, facts.target_id or facts.target_url or facts.current_url, action.value)
        self._repeats = self._repeats + 1 if key == self._previous else 1
        self._previous = key
