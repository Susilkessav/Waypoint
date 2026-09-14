"""B6 - the approval queue, and approving the version a reviewer means (no browser)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from waypoint.artifact.approval import approval_status, approve
from waypoint.artifact.schema import Provenance, load, locate
from waypoint.session.escalation import InterventionStore
from waypoint.session.store import StateStore

REPO = Path(__file__).resolve().parents[1]
EXE = str(Path(sys.executable).parent / "waypoint")


def draft(relative: str) -> str:
    """A committed artifact as an unapproved draft - whatever its approval in the repo is.

    Approval is a person's decision and changes over time; a test must set up the state it
    is about rather than inherit it.
    """
    cap = load(REPO / "capabilities" / relative)
    return cap.model_copy(update={"provenance": Provenance()}).to_json()


def capabilities(tmp_path: Path) -> Path:
    """An approved release plus drafts beside it."""
    root = tmp_path / "capabilities"
    lookup = root / "lookup_member_balance"
    (root / "open_sub_account").mkdir(parents=True)
    lookup.mkdir(parents=True)
    released = approve(load(REPO / "capabilities/lookup_member_balance/1.0.0.json"),
                       approver="test")
    (lookup / "1.0.0.json").write_text(released.to_json())
    (lookup / "1.2.0.json").write_text(draft("lookup_member_balance/1.2.0.json"))
    (root / "open_sub_account" / "1.0.0.json").write_text(draft("open_sub_account/1.0.0.json"))
    return root


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([EXE, *args], capture_output=True, text=True,
                          env=dict(os.environ), timeout=60)


def test_the_queue_lists_drafts_and_runs_waiting_for_a_person(tmp_path: Path) -> None:
    root = capabilities(tmp_path)
    db = tmp_path / "state.db"
    iv = InterventionStore(StateStore(db)).open(
        session_id="run-1", run_id="run-1", capability_id="open_sub_account", version="1.0.0",
        reason_code="approval_required", message="needs a person's approval: irreversible",
        step="steps[7]", intent="Confirm and create the sub-account")

    done = cli("approvals", "--root", str(root), "--db", str(db))
    assert done.returncode == 0, done.stderr
    out = done.stdout
    drafts, runs = out.split("runs waiting for approval:")
    assert "open_sub_account 1.0.0  approvable" in drafts
    assert "lookup_member_balance 1.2.0  approvable" in drafts
    assert "lookup_member_balance 1.0.0" not in drafts, "an approved release is not queued"
    assert iv.id in runs and "Confirm and create the sub-account" in runs
    assert f"waypoint intervene take {iv.id} --db {db}" in runs


def test_approve_without_a_version_approves_the_newest_draft(tmp_path: Path) -> None:
    """Replay defaults to the release in service; a reviewer means the draft in front of them."""
    root = capabilities(tmp_path)
    assert locate(root, "lookup_member_balance").name == "1.0.0.json"
    done = cli("approve", "lookup_member_balance", "--approver", "reviewer", "--root", str(root))
    assert done.returncode == 0, done.stderr
    assert "1.2.0" in done.stdout
    assert approval_status(load(root / "lookup_member_balance/1.2.0.json")).approved
    assert locate(root, "lookup_member_balance").name == "1.2.0.json", "now the release"


def test_an_empty_queue_says_so(tmp_path: Path) -> None:
    empty = tmp_path / "caps"
    empty.mkdir()
    done = cli("approvals", "--root", str(empty), "--db", str(tmp_path / "none.db"))
    assert done.returncode == 0 and done.stdout.count("  none") == 2
