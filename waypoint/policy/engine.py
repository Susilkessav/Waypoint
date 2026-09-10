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


Verdict: TypeAlias = Allow | Block | RequireApproval


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
            route = f"{facts.method.upper()} {unquote(urlsplit(facts.target_url).path) or '/'}"
            if any(fnmatch.fnmatchcase(route, p) for p in self.config.mutating_routes):
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
        risk = max(
            (self.classify(action, facts), context.declared_risk), key=lambda r: RISK_RANK[r]
        )
        if not context.state_known:
            if self.config.unknown_state_policy == "block" or risk != "safe":
                return RequireApproval("unknown_state", risk)
        if risk in ("irreversible", "unknown"):
            return RequireApproval("risky_action", risk)
        return Allow(risk)

    def record(self, action: Action, facts: ActionFacts) -> None:
        now = self.clock()
        self._times = [t for t in self._times if t > now - 60] + [now]
        self._steps += 1
        key = (action.kind, facts.target_id or facts.target_url or facts.current_url, action.value)
        self._repeats = self._repeats + 1 if key == self._previous else 1
        self._previous = key
