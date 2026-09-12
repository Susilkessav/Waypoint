"""The human review of capability artifacts, written down (milestones B2 and B4).

Discovery proposes; a reviewer decides. Doing that review as a script rather than by
hand-editing JSON means every difference between what the model produced and what is put
up for approval is one reviewable diff, and re-running it reproduces the artifacts
exactly. Neither output is approved here - approval stays a human act:

    waypoint approve open_sub_account --version 1.0.0 --note "..."
    waypoint approve lookup_member_balance --version 1.2.0 --note "..."

open_sub_account 1.0.0, from the live Haiku 4.5 discovery run in
evidence/runs/showcase-discovery-open-sub-account/:

1. Sign-on becomes a precondition with a remedy instead of three steps, so a warm session
   skips it and a lapsed one can be re-entered by recovery.
2. Inputs get their contracts: a five-digit member ID, an enumerated account type, a
   money-formatted deposit.
3. Resume points are cut to the one state-complete screen - this member's record. The
   compiler proposes one wherever a check names the member; a half-filled form does too.
4. The irreversible Confirm gets its reconcile block (the gate discovery left open): a
   read-only probe of the member's accounts grid, identity bound to member and type,
   recency bound to when the attempt was made, and where to read the account ID from.
5. Outcomes and recovery from the shared recognizers.

lookup_member_balance 1.2.0 is the hand-written 1.0.0 plus the same outcomes and
recovery, so demo commands 6 and 7 meet an interstitial and a server error it expects.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from waypoint.artifact.schema import Capability, approval_gates  # noqa: E402
from waypoint.signatures.library import inline, load_library  # noqa: E402

LIBRARY = load_library()
DRAFT = REPO / "evidence" / "runs" / "showcase-discovery-open-sub-account" / "draft-1.0.0.json"
LOOKUP = REPO / "capabilities" / "lookup_member_balance" / "1.0.0.json"
ACCOUNT_TYPES = ["Savings", "Checking", "Money Market"]
SIGN_ON = ("authenticated", "login_form_visible", "user_id_filled", "password_filled")

CONTINUE_LINK: dict[str, Any] = {"recorded_tier": 1, "candidates": [{
    "tier": 1, "kind": "role_name", "frame_path": ["main", "content"],
    "role": "link", "name": "Continue"}]}
RECOVERY: list[dict[str, Any]] = [
    {"on": "maintenance_interstitial", "do": "dismiss", "target": CONTINUE_LINK, "max": 2},
    {"on": "login_form_visible", "do": "reauth", "max": 1},
]


def library(*names: str) -> dict[str, Any]:
    return {n: s.model_dump(mode="json") for n, s in inline(names, LIBRARY).items()}


def outcome(name: str, cls: str, signature: str, description: str) -> dict[str, Any]:
    return {"name": name, "class": cls, "signature": signature, "description": description}


def lookup_member_balance() -> Capability:
    body = json.loads(LOOKUP.read_text())
    body["version"] = "1.2.0"
    body["signatures"].update(library("maintenance_interstitial", "server_error",
                                      "not_authorized_banner"))
    body["outcomes"] = [
        *body["outcomes"],
        outcome("not_authorized", "business", "not_authorized_banner",
                "This operator may not service the member."),
        outcome("server_error", "hard_failure", "server_error",
                "The application failed; nothing about the member is known."),
    ]
    body["recovery"] = RECOVERY
    body["provenance"] = {"authored_by": "hand-written 1.0.0, hardened for milestone B4 by "
                                         "scripts/review_capabilities.py"}
    return Capability.model_validate(body)


def open_sub_account() -> Capability:
    body = json.loads(DRAFT.read_text())
    lookup = json.loads(LOOKUP.read_text())
    steps: list[dict[str, Any]] = body["steps"]

    # 1. Sign-on: a precondition with a remedy, not three steps.
    assert [s.get("value_ref") for s in steps[:2]] == ["$secrets.meridian_user",
                                                       "$secrets.meridian_password"]
    assert steps[2]["target"]["candidates"][0].get("name") == "Sign On"
    dropped = {s["checkpoint"]["signature"] for s in steps[:3]}
    steps = steps[3:]
    still_used = {s["checkpoint"]["signature"] for s in steps} | {
        p["signature"] for p in body["postconditions"]}
    for name in dropped - still_used:
        del body["signatures"][name]
    body["preconditions"] = lookup["preconditions"]
    body["signatures"].update({n: lookup["signatures"][n] for n in SIGN_ON})

    # 2. The contract.
    body["name"] = "Open a sub-account"
    body["description"] = ("Opens a sub-account of the given type for a member, with an "
                           "initial deposit, and returns the new account's ID.")
    body["surface"] = {"kind": "web", "entry": "http://127.0.0.1:8080/console",
                       "app_fingerprint": lookup["surface"]["app_fingerprint"]}
    props = body["inputs"]["properties"]
    props["member_id"].update(pattern="^[0-9]{5}$", description="Five-digit Meridian member ID")
    props["account_type"].update(enum=ACCOUNT_TYPES, description="Sub-account type")
    props["initial_deposit"].update(format="money",
                                    description="Opening deposit, an amount of money; the "
                                                "application enforces its own minimum")

    # 3. Resume points: only the state-complete screen - this member's record (R-RESUME-4).
    for step in steps:
        step["resume_point"] = False
    view = next(s for s in steps if any(c.get("name") == "View"
                                        for c in (s.get("target") or {}).get("candidates", [])))
    view["resume_point"] = True

    # 4. The irreversible step: reconcile, never repeat.
    confirm = steps[-1]
    assert confirm["risk"] == "irreversible" and confirm["retry"]["max"] == 0
    confirm["timeout_ms"] = 5000
    confirm["reconcile"] = {
        "probe": [{
            "intent": "Open this member's accounts grid (read-only)",
            "action": "navigate",
            "url_template": "/console/member?member_id={member_id}&tab=accounts",
            "checkpoint": {"signature": "accounts_listed_for_member"},
            "timeout_ms": 10000,
        }],
        "completed_when": "subaccount_present_for_inputs",
        "not_completed_when": "no_subaccount_for_inputs",
        "identity": ["member_id", "account_type"],
        "recency": {"cells": {"role": "cell", "anchor": "Opened",
                              "anchor_ref": "$inputs.account_type"}},
        "extract": {"account_id": {"recorded_tier": 3, "candidates": [{
            "tier": 3, "kind": "anchored",
            "frame_path": ["main", "iframe#ctl00_MainContent_ifrAccounts"],
            "role": "cell", "column": "Account",
            "anchor": {"role": "cell", "text_ref": "$inputs.account_type"},
        }]}},
    }
    body["steps"] = steps
    body["signatures"].update(library(
        "accounts_listed_for_member", "subaccount_present_for_inputs",
        "no_subaccount_for_inputs", "maintenance_interstitial", "server_error",
        "gateway_error", "not_authorized_banner", "deposit_rejected_banner",
        "no_records_banner"))

    # 5. Outcomes and recovery.
    body["outcomes"] = [
        outcome("member_not_found", "business", "no_records_banner",
                "No member exists with the supplied ID."),
        outcome("not_authorized", "business", "not_authorized_banner",
                "This operator may not service the member."),
        outcome("deposit_rejected", "business", "deposit_rejected_banner",
                "The application refused the initial deposit."),
        outcome("server_error", "hard_failure", "server_error",
                "The application failed before anything was committed."),
        outcome("response_lost", "hard_failure", "gateway_error",
                "The response was lost; if Confirm was sent, the step is reconciled."),
    ]
    body["recovery"] = RECOVERY
    body["provenance"] = {
        "discovered_by": body["provenance"]["discovered_by"],
        "compiled_at": body["provenance"]["compiled_at"],
        "authored_by": "reviewed for milestone B2 by scripts/review_capabilities.py",
    }
    return Capability.model_validate(body)


def main() -> None:
    for cap in (open_sub_account(), lookup_member_balance()):
        gates = approval_gates(cap)
        target = REPO / "capabilities" / cap.capability_id / f"{cap.version}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(cap.to_json())
        state = "approvable" if not gates else f"open gates: {gates}"
        print(f"wrote {target.relative_to(REPO)} - draft, {state}")


if __name__ == "__main__":
    main()
