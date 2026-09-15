"""Tenant/multi-variant execution (R-PKG-4): overrides merge, hash, gate and actually run.

``Capability.overrides`` had no defined shape anywhere in the repo before this - these tests
pin the shape down: a flat map of ``where`` path -> replacement JSON, reusing the same
addressing ``all_steps``/``bundles`` already use, restricted to ``policy.allowed_routes`` and
per-step ``target``/``checkpoint``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from waypoint.artifact.approval import ApprovalBlocked, approval_status, approve
from waypoint.artifact.schema import (
    Capability,
    Provenance,
    apply_overrides,
    content_hash,
    load,
)
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.replay.ledger import Ledger
from waypoint.session.store import StateStore

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "capabilities" / "lookup_member_balance" / "1.0.0.json"
APPROVED_ARTIFACT = REPO / "capabilities" / "lookup_member_balance" / "1.2.0.json"
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}

RENAMED_CHECKPOINT = {"signature": "member_detail_loaded", "weak": True}
NARROWED_ROUTES = ["/", "/console"]  # drops "/console/*": /console/member is no longer allowed


def base() -> Capability:
    return load(ARTIFACT)


def draft() -> Capability:
    return base().model_copy(update={"provenance": Provenance(authored_by="test draft")})


def with_override(cap: Capability, variant: str, patch: dict[str, Any]) -> Capability:
    body = json.loads(cap.to_json())
    body["overrides"] = {**body["overrides"], variant: patch}
    return Capability.model_validate(body)


class TestApplyOverrides:
    def test_base_is_the_same_object_not_a_copy(self) -> None:
        cap = base()
        assert apply_overrides(cap, "base") is cap

    def test_unknown_variant_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown variant"):
            apply_overrides(base(), "nope")

    def test_unknown_path_is_refused(self) -> None:
        cap = with_override(base(), "tenant_x", {"steps[0].nonsense": {}})
        with pytest.raises(ValueError, match="not patchable"):
            apply_overrides(cap, "tenant_x")

    def test_step_target_is_replaced(self) -> None:
        cap = with_override(base(), "tenant_x", {"steps[2].checkpoint": RENAMED_CHECKPOINT})
        effective = apply_overrides(cap, "tenant_x")
        assert effective.steps[2].checkpoint.weak is True
        assert cap.steps[2].checkpoint.weak is False  # the raw artifact is untouched

    def test_policy_allowed_routes_is_replaced(self) -> None:
        cap = with_override(base(), "tenant_x", {"policy.allowed_routes": NARROWED_ROUTES})
        effective = apply_overrides(cap, "tenant_x")
        assert list(effective.policy.allowed_routes) == NARROWED_ROUTES
        assert list(cap.policy.allowed_routes) != NARROWED_ROUTES


class TestContentHash:
    def test_base_hash_is_unchanged_by_this_feature(self) -> None:
        """Regression pin: the approved fixture's stored hash must still match (R-PKG-2)."""
        approved = load(APPROVED_ARTIFACT)
        assert content_hash(approved) == approved.provenance.approval_hash["base"]

    def test_variant_hash_differs_from_base_when_the_override_is_real(self) -> None:
        cap = with_override(base(), "tenant_x", {"policy.allowed_routes": NARROWED_ROUTES})
        assert content_hash(cap, "tenant_x") != content_hash(cap, "base")

    def test_variant_hash_is_stable(self) -> None:
        cap = with_override(base(), "tenant_x", {"policy.allowed_routes": NARROWED_ROUTES})
        assert content_hash(cap, "tenant_x") == content_hash(cap, "tenant_x")


class TestApprovalGates:
    def test_an_override_introduced_problem_blocks_only_that_variant(self) -> None:
        cap = with_override(base(), "tenant_x", {"steps[2].checkpoint": RENAMED_CHECKPOINT})
        from waypoint.artifact.schema import approval_gates

        assert approval_gates(cap, "base") == []
        gates = approval_gates(cap, "tenant_x")
        assert any("checkpoint is weak" in g for g in gates)

    def test_a_broken_override_is_reported_not_raised(self) -> None:
        """A malformed variant must not take the queue - or the other variants - down."""
        cap = with_override(draft(), "broken", {"steps[0].target": {"not": "a bundle"}})
        cap = with_override(cap, "tenant_x", {"policy.allowed_routes": NARROWED_ROUTES})
        status = approval_status(cap, "broken")
        assert not status.approved
        assert any("cannot be applied" in r for r in status.reasons)
        # The healthy variant still answers.
        assert approval_status(cap, "tenant_x").reasons

    def test_an_override_naming_a_missing_step_is_reported(self) -> None:
        cap = with_override(draft(), "broken", {"steps[99].checkpoint": RENAMED_CHECKPOINT})
        status = approval_status(cap, "broken")
        assert not status.approved
        assert any("does not exist" in r for r in status.reasons)

    def test_a_broken_override_cannot_be_approved(self) -> None:
        cap = with_override(draft(), "broken", {"steps[0].target": {"not": "a bundle"}})
        with pytest.raises(ApprovalBlocked, match="cannot be applied"):
            approve(cap, approver="test", variant="broken")

    def test_variants_approve_independently(self) -> None:
        cap = with_override(draft(), "tenant_x", {"policy.allowed_routes": NARROWED_ROUTES})
        approved_base = approve(cap, approver="test", variant="base")
        assert approval_status(approved_base, "base").approved
        assert not approval_status(approved_base, "tenant_x").approved

        approved_both = approve(approved_base, approver="test", variant="tenant_x")
        assert approval_status(approved_both, "base").approved
        assert approval_status(approved_both, "tenant_x").approved

    def test_editing_shared_content_unapproves_every_variant(self) -> None:
        cap = with_override(draft(), "tenant_x", {"policy.allowed_routes": NARROWED_ROUTES})
        approved = approve(cap, approver="test", variant="base")
        approved = approve(approved, approver="test", variant="tenant_x")
        body = json.loads(approved.to_json())
        body["description"] = "edited after approval"
        edited = Capability.model_validate(body)
        assert not approval_status(edited, "base").approved
        assert not approval_status(edited, "tenant_x").approved

    def test_editing_one_variants_override_unapproves_every_variant(self) -> None:
        """Deliberately conservative: a variant's hash covers the whole file, not its slice.

        Editing tenant_y sends tenant_x and base back through review too. The reviewed unit
        is the file a person read, so the blast radius of an edit never depends on which
        subtree it landed in.
        """
        cap = with_override(draft(), "tenant_x", {"policy.allowed_routes": NARROWED_ROUTES})
        cap = with_override(cap, "tenant_y", {"policy.allowed_routes": ["/", "/other"]})
        approved = approve(cap, approver="test", variant="base")
        approved = approve(approved, approver="test", variant="tenant_x")
        assert approval_status(approved, "tenant_x").approved

        body = json.loads(approved.to_json())
        body["overrides"]["tenant_y"] = {"policy.allowed_routes": ["/", "/changed"]}
        edited = Capability.model_validate(body)
        assert not approval_status(edited, "base").approved
        assert not approval_status(edited, "tenant_x").approved


pytestmark_browser = pytest.mark.browser


@pytest.mark.browser
class TestVariantExecution:
    """Proves the engine runs the merged capability, not just checks it at approval time."""

    def _approved(self, patch: dict[str, Any]) -> Capability:
        cap = with_override(draft(), "restricted", patch)
        cap = approve(cap, approver="test", variant="base")
        return approve(cap, approver="test", variant="restricted")

    def test_base_succeeds_and_the_narrowed_variant_is_blocked(
        self, live_server: str, tmp_path: Path
    ) -> None:
        cap = self._approved({"policy.allowed_routes": NARROWED_ROUTES})

        base_result = replay(cap, {"member_id": "12345"}, ReplayOptions(
            base_url=live_server, evidence_root=tmp_path,
            secrets=SecretBroker(environ=CREDENTIALS), variant="base",
        ))
        assert base_result.status == "success", base_result.failure

        variant_result = replay(cap, {"member_id": "12345"}, ReplayOptions(
            base_url=live_server, evidence_root=tmp_path,
            secrets=SecretBroker(environ=CREDENTIALS), variant="restricted",
        ))
        assert variant_result.status != "success"
        assert variant_result.variant == "restricted"

    def test_runs_are_recorded_and_scored_per_variant(
        self, live_server: str, tmp_path: Path
    ) -> None:
        cap = self._approved({"policy.allowed_routes": list(base().policy.allowed_routes)})
        state_db = tmp_path / "state.db"
        ledger = Ledger(StateStore(state_db))

        for _ in range(2):
            replay(cap, {"member_id": "12345"}, ReplayOptions(
                base_url=live_server, evidence_root=tmp_path,
                secrets=SecretBroker(environ=CREDENTIALS), variant="restricted",
                ledger_db=state_db,
            ))

        restricted = ledger.confidence(cap, "restricted")
        assert restricted.runs == 2
        assert ledger.confidence(cap, "base").runs == 0


@pytest.mark.browser
def test_delivered_riverbank_override_reuses_the_changed_control(live_server, tmp_path):
    from waypoint.catalog.registry import Catalog
    from waypoint.replay.stability import Case, case_injector

    cap = load(REPO / "capabilities/lookup_member_balance/1.4.0.json")
    assert approval_status(cap, "base").approved
    assert approval_status(cap, "riverbank").approved
    assert Catalog(REPO / "capabilities", variant="riverbank").get(
        "lookup_member_balance").version == "1.4.0"
    options = ReplayOptions(base_url=live_server, evidence_root=tmp_path,
                            secrets=SecretBroker(environ=CREDENTIALS),
                            require_confidence=False, injected="drift_search",
                            after_preconditions=case_injector(Case({}, inject="drift_search")))
    refused = replay(cap, {"member_id": "67890"}, options)
    assert refused.status == "escalated"
    options.variant = "riverbank"
    result = replay(cap, {"member_id": "67890"}, options)
    assert result.status == "success", result.failure
    assert result.outputs == {"savings_balance": "$912.04", "account_status": "dormant"}
