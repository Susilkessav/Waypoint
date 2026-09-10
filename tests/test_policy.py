"""A4: R-RISK allowlists, effect classification, approval and bounded execution."""

from dataclasses import replace

import pytest

from waypoint.policy.engine import (
    ActionFacts,
    Allow,
    Block,
    PolicyConfig,
    PolicyEngine,
    RequireApproval,
    RunContext,
)
from waypoint.surface.ports import Action

BASE = "http://127.0.0.1:8080"
KNOWN = RunContext(state_known=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.test/console",
        BASE + "/logout",
        BASE + "/console/../logout",
        BASE + "/console/%2e%2e/logout",
        BASE + "/console/%252e%252e/logout",
        "file:///tmp/example",
        "http://user:password@127.0.0.1:8080/console",
        "http://127.0.0.1:8080.evil.test/console",
        BASE + "/console-extra",
    ],
)
def test_off_allowlist_is_blocked(url):
    engine = PolicyEngine()
    assert isinstance(
        engine.check(
            Action("navigate", url=url), ActionFacts("about:blank", target_url=url), KNOWN
        ),
        Block,
    )


@pytest.mark.parametrize("unattended", [False, True])
def test_mutating_get_never_becomes_safe_by_renaming_it(unattended):
    facts = ActionFacts(
        BASE + "/console/member", target_url=BASE + "/console/member/flag", name="Mark for Review"
    )
    verdict = PolicyEngine().check(
        Action("click", ref="one"), facts, RunContext(unattended=unattended, state_known=True)
    )
    assert isinstance(verdict, RequireApproval) and verdict.risk == "irreversible"


def test_unlabelled_click_requires_approval():
    verdict = PolicyEngine().check(
        Action("click", ref="one"), ActionFacts(BASE + "/console"), KNOWN
    )
    assert isinstance(verdict, RequireApproval) and verdict.risk == "unknown"


def test_enter_inherits_form_route_risk():
    facts = ActionFacts(BASE + "/console", target_url=BASE + "/console/search", method="POST")
    verdict = PolicyEngine().check(Action("key", value="Enter"), facts, KNOWN)
    assert isinstance(verdict, RequireApproval) and verdict.risk == "irreversible"


def test_unknown_state_only_allows_safe_actions():
    engine = PolicyEngine()
    context = RunContext()
    facts = ActionFacts(BASE + "/console")
    assert isinstance(engine.check(Action("read"), facts, context), Allow)
    assert isinstance(engine.check(Action("click", ref="x"), facts, context), RequireApproval)


def test_declared_risk_cannot_downgrade_runtime_risk():
    facts = ActionFacts(BASE + "/console", name="Confirm")
    verdict = PolicyEngine().check(Action("click", ref="x"), facts, KNOWN)
    assert isinstance(verdict, RequireApproval)
    safe = replace(facts, name="View")
    verdict = PolicyEngine().check(
        Action("click", ref="x"), safe, replace(KNOWN, declared_risk="irreversible")
    )
    assert isinstance(verdict, RequireApproval)


def test_literals_cannot_be_typed_into_secret_fields():
    verdict = PolicyEngine().check(
        Action("type", value="fixture-password"), ActionFacts(BASE + "/", secret_field=True), KNOWN
    )
    assert verdict == Block("secret_requires_broker")


def test_keystrokes_cannot_bypass_secret_broker():
    verdict = PolicyEngine().check(
        Action("key", value="a"), ActionFacts(BASE + "/", secret_field=True), KNOWN
    )
    assert verdict == Block("secret_requires_broker")


@pytest.mark.parametrize("key", ["Enter", "Space"])
def test_keyboard_activation_inherits_control_risk(key):
    verdict = PolicyEngine().check(
        Action("key", value=key), ActionFacts(BASE + "/console", name="Delete"), KNOWN
    )
    assert isinstance(verdict, RequireApproval) and verdict.risk == "irreversible"


def test_loop_breaker_counts_stable_target_across_observation_refs():
    engine = PolicyEngine()
    facts = ActionFacts(BASE + "/console", name="View", target_id="stable-node")
    for ref in ("g1-e1", "g2-e1"):
        action = Action("click", ref=ref)
        assert isinstance(engine.check(action, facts, KNOWN), Allow)
        engine.record(action, facts)
    assert engine.check(Action("click", ref="g3-e1"), facts, KNOWN) == Block("loop_breaker")


def test_rate_limit_recovers_but_total_step_limit_does_not():
    now = [0.0]
    config = PolicyConfig.load()
    config.limits.max_actions_per_minute = 2
    config.limits.max_steps_per_run = 3
    engine = PolicyEngine(config, clock=lambda: now[0])
    for i in range(2):
        engine.record(Action("read"), ActionFacts(BASE + "/console", target_id=str(i)))
    facts = ActionFacts(BASE + "/console", target_id="third")
    assert engine.check(Action("read"), facts, KNOWN) == Block("rate_limit")
    now[0] = 61.0
    assert isinstance(engine.check(Action("read"), facts, KNOWN), Allow)
    engine.record(Action("read"), facts)
    assert engine.check(Action("read"), facts, KNOWN) == Block("step_limit")
