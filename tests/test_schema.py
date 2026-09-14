"""The artifact as a contract: structural rules, approval gates, content-bound approval.

T16 - an artifact edited after approval is not approved.
T17 - editing the shared signature library cannot change an approved artifact.
T18 - approval gates are recomputed from content, never read from a stored counter.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from waypoint.artifact.approval import ApprovalBlocked, approval_status, approve
from waypoint.artifact.schema import (
    Capability,
    Provenance,
    approval_gates,
    content_hash,
    load,
    locate,
)
from waypoint.signatures.library import LIBRARY_PATH, inline, load_library

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "capabilities" / "lookup_member_balance" / "1.0.0.json"


def base() -> Capability:
    return load(ARTIFACT)


def draft() -> Capability:
    """The artifact with its approval stripped - independent of whether the committed
    file has been approved, which is a human decision these tests must not assume."""
    return base().model_copy(update={"provenance": Provenance(authored_by="test draft")})


def edited(change: Callable[[dict[str, Any]], None], cap: Capability | None = None) -> dict:
    body = json.loads((cap or base()).to_json())
    change(body)
    return body


class TestTheHandWrittenArtifact:
    def test_it_has_no_open_approval_gates(self) -> None:
        assert approval_gates(base()) == []

    def test_the_file_on_disk_is_canonical_json(self) -> None:
        assert ARTIFACT.read_text() == base().to_json()

    def test_every_step_after_the_search_asserts_which_member(self) -> None:
        cap = base()
        for step in cap.steps[1:]:
            assert "member_id" in cap.signatures[step.checkpoint.signature].refs(), step.intent

    def test_locate_finds_the_highest_release(self) -> None:
        """Which version is the release is a reviewer's decision; locate follows it."""
        found = locate(REPO / "capabilities", "lookup_member_balance")
        assert approval_status(load(found)).approved
        approved = [p for p in found.parent.glob("*.json") if approval_status(load(p)).approved]
        assert found == max(approved, key=lambda p: tuple(int(x) for x in p.stem.split(".")))


class TestStructuralRules:
    """Violations mean the artifact cannot be loaded at all."""

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda b: b["steps"][0].update(value_ref="12345"), "value_ref"),
            (lambda b: b["signatures"].pop("member_in_results"), "R-PKG-1"),
            (
                lambda b: b["steps"][2]["target"]["candidates"][0]["anchor"].update(
                    text_ref="$inputs.other"
                ),
                "undeclared input",
            ),
            (lambda b: b["steps"][3].update(risk="irreversible", retry={"max": 1}), "reconcile"),
            (lambda b: b.update(notes="anything"), "Extra inputs"),
            (
                lambda b: b["steps"].append(
                    {
                        "intent": "Deep link",
                        "action": "navigate",
                        "url_template": "/console/member?member_id=12345",
                        "checkpoint": {"signature": "member_detail_loaded"},
                    }
                ),
                "literal identifier",
            ),
        ],
    )
    def test_rejected_at_load(self, change: Callable[[dict], None], message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            Capability.model_validate(edited(change))


class TestApprovalGates:
    """T18: every gate is recomputed from content; no stored counter is believed."""

    def test_weak_checkpoint_blocks_even_when_the_cache_says_zero(self) -> None:
        def change(b: dict) -> None:
            b["steps"][1]["checkpoint"]["weak"] = True
            b["provenance"]["approval_gates"] = {"weak_checkpoints": 0}

        with pytest.raises(ApprovalBlocked, match="weak"):
            approve(Capability.model_validate(edited(change)), approver="test")

    def test_checkpoint_that_asserts_no_content_blocks(self) -> None:
        def change(b: dict) -> None:
            b["signatures"]["moved"] = {"match": {"url_matches": "/console"}}
            b["steps"][0]["checkpoint"]["signature"] = "moved"

        gates = approval_gates(Capability.model_validate(edited(change)))
        assert any("asserts nothing on screen" in g for g in gates)

    def test_extraction_keyed_on_its_own_value_blocks(self) -> None:
        def change(b: dict) -> None:
            b["outputs"]["properties"]["savings_balance"]["extraction"] = {
                "recorded_tier": 1,
                "candidates": [
                    {"tier": 1, "kind": "role_name", "role": "cell", "name": "$4,281.19"}
                ],
            }

        gates = approval_gates(Capability.model_validate(edited(change)))
        assert any("R-SENS-7" in g for g in gates)
        assert any("R-SENS-9" in g and "money" in g for g in gates)

    def test_unattended_irreversible_step_needs_reconcile(self) -> None:
        change = lambda b: b["steps"][3].update(risk="irreversible")  # noqa: E731
        gates = approval_gates(Capability.model_validate(edited(change)))
        assert any("R-REC" in g for g in gates)

    def test_sensitive_literal_anywhere_blocks(self) -> None:
        change = lambda b: b.update(description="Escalate to 123-45-6789")  # noqa: E731
        gates = approval_gates(Capability.model_validate(edited(change)))
        assert any("R-SENS-9" in g and "ssn" in g for g in gates)


class TestApprovalBindsToContent:
    """T16: approval is a statement about exact contents."""

    def test_a_draft_is_not_approved(self) -> None:
        status = approval_status(draft())
        assert not status.approved and "not approved" in status.reasons[0]

    def test_approve_then_status(self) -> None:
        assert approval_status(approve(draft(), approver="test")).approved

    def test_editing_after_approval_revokes_it(self) -> None:
        approved = approve(base(), approver="test")
        tampered = Capability.model_validate(
            edited(lambda b: b["steps"][0].update(intent="something else"), approved)
        )
        status = approval_status(tampered)
        assert not status.approved and any("hash mismatch" in r for r in status.reasons)

    def test_approval_metadata_is_not_part_of_the_content(self) -> None:
        assert content_hash(approve(base(), approver="test")) == content_hash(base())


class TestSelfContained:
    """T17: artifacts carry copies; the library is an authoring convenience only."""

    def test_library_edits_cannot_change_an_approved_artifact(self, tmp_path: Path) -> None:
        approved = approve(base(), approver="test")
        before = content_hash(approved)
        loosened = LIBRARY_PATH.read_text().replace(
            "name_ref: $inputs.member_id", "name_contains: ''"
        )
        (tmp_path / "library.yaml").write_text(loosened)
        library = load_library(tmp_path / "library.yaml")
        assert library["member_detail_loaded"] != approved.signatures["member_detail_loaded"]
        assert content_hash(approved) == before
        assert approval_status(approved).approved
        assert approved.signatures["member_detail_loaded"].refs() == {"member_id"}

    def test_inline_copies_rather_than_references(self) -> None:
        library = load_library()
        copy = inline(["authenticated"], library)["authenticated"]
        assert copy == library["authenticated"] and copy is not library["authenticated"]

    def test_nothing_on_the_execution_path_reads_the_library(self) -> None:
        for folder in ("replay", "artifact"):
            for path in (REPO / "waypoint" / folder).glob("*.py"):
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        assert "signatures.library" not in node.module, path.name


def test_the_default_version_is_the_current_release_not_a_newer_draft(tmp_path: Path) -> None:
    """A discovered draft must not hijack `waypoint replay <id>` before anyone approves it."""
    from waypoint.artifact.approval import approve
    from waypoint.artifact.schema import load, locate

    folder = tmp_path / "lookup_member_balance"
    folder.mkdir(parents=True)
    released = approve(load(REPO / "capabilities" / "lookup_member_balance" / "1.0.0.json"),
                       approver="test")
    (folder / "1.0.0.json").write_text(released.to_json())
    draft = released.model_copy(update={"version": "1.1.0", "provenance": Provenance()})
    (folder / "1.1.0.json").write_text(draft.to_json())

    assert locate(tmp_path, "lookup_member_balance").name == "1.0.0.json"
    assert locate(tmp_path, "lookup_member_balance", "1.1.0").name == "1.1.0.json"

    (folder / "1.0.0.json").unlink()  # nothing approved: the newest, which then refuses
    assert locate(tmp_path, "lookup_member_balance").name == "1.1.0.json"
