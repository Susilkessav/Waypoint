"""Submission regressions: goal privacy, repeat discovery, and the documented demo chain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.test_claude_decider import FakeClient, reply, tool_use
from waypoint.artifact.approval import approve
from waypoint.artifact.schema import Provenance, load, locate
from waypoint.cli import app
from waypoint.discovery import claude
from waypoint.surface.web import WebSurface

REPO = Path(__file__).resolve().parents[1]
RUNNER = CliRunner()
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}


def goal_run(monkeypatch, tmp_path, entry, goal):
    client = FakeClient(reply(tool_use("stop", "give_up", {"reason": "probe complete"})))
    decider = claude.ClaudeDecider(client=client)
    monkeypatch.setattr(claude, "ClaudeDecider", lambda **kw: decider)
    result = RUNNER.invoke(app, [
        "discover", "--capability-id", "lookup_member_balance", "--entry", entry,
        "--goal", goal, "--bind", "member_id=12345:string:internal",
        "--bind", "contact=Avery Example:string:pii",
        "--bind", "token=fixture-goal-secret:string:secret",
        "--bind", "account_type=Savings:string:public",
        "--evidence-root", str(tmp_path), "--no-compile",
    ])
    assert result.exit_code == 1, result.output  # deliberately stopped, with evidence
    run = next(tmp_path.iterdir())
    payloads = {p.name: p.read_text() for p in run.glob("*.json*")}
    assert {"meta.json", "transcript.json", "cassette.json"} <= payloads.keys()
    for text in [json.dumps(client.calls), result.output, *payloads.values()]:
        for raw in ("12345", "Avery Example", "fixture-goal-secret", "111-22-3333"):
            assert raw not in text
    goals = [json.loads(payloads[p])["goal"]
             for p in ("meta.json", "transcript.json", "cassette.json")]
    assert len(set(goals)) == 1
    assert "$inputs.member_id" in goals[0] and "$inputs.contact" in goals[0]
    return client, goals[0]


@pytest.mark.browser
@pytest.mark.parametrize("templated", [False, True], ids=["literal", "template"])
def test_goal_is_private_in_model_requests_and_all_discovery_files(
    monkeypatch, tmp_path, live_server, templated,
):
    goal = ("Look up {{member_id}} for {{contact}} using {{token}}; Savings; SSN 111-22-3333"
            if templated else
            "Look up 12345 for Avery Example using fixture-goal-secret; Savings; SSN 111-22-3333")
    client, sanitized = goal_run(monkeypatch, tmp_path, f"{live_server}/console", goal)
    assert len(client.calls) == 1
    assert "Savings" in sanitized


def test_cassette_goal_is_private_even_when_no_model_turn_runs(monkeypatch, tmp_path):
    def unavailable(**kw):
        raise RuntimeError("browser unavailable")

    monkeypatch.setattr(WebSurface, "launch", unavailable)
    client, _ = goal_run(monkeypatch, tmp_path, "http://127.0.0.1/console",
                         "Look up 12345 for Avery Example using fixture-goal-secret")
    assert client.calls == []


def test_repeated_compilation_allocates_free_versions_and_preserves_existing_files(tmp_path):
    root = tmp_path / "capabilities"
    folder = root / "lookup_member_balance"
    folder.mkdir(parents=True)
    base = load(REPO / "capabilities/lookup_member_balance/1.0.0.json")
    (folder / "1.0.0.json").write_text(approve(base, approver="test").to_json())
    draft = base.model_copy(update={"version": "1.1.0", "provenance": Provenance()})
    (folder / "1.1.0.json").write_text(draft.to_json())
    before = {p: p.read_bytes() for p in folder.iterdir()}
    source = tmp_path / "transcript.json"
    recorded = REPO / "evidence/runs/showcase-discovery-haiku/transcript.json"
    source.write_bytes(recorded.read_bytes())
    for version in ("1.2.0", "1.3.0"):
        result = RUNNER.invoke(app, ["compile", str(source), "--root", str(root)])
        assert result.exit_code == 0, result.output
        assert load(folder / f"{version}.json").version == version
    assert all(p.read_bytes() == body for p, body in before.items())
    assert locate(root, base.capability_id).name == "1.0.0.json"
    refused = RUNNER.invoke(app, ["compile", str(source), "--root", str(root),
                                  "--version", "1.1.0"])
    assert refused.exit_code == 1 and "refusing to overwrite" in refused.output
    assert all(p.read_bytes() == body for p, body in before.items())


@pytest.mark.browser
@pytest.mark.parametrize("member_goal", ["{{member_id}}", "12345"], ids=["template", "literal"])
def test_documented_cassette_approval_replay_chain_uses_the_generated_artifact(
    monkeypatch, tmp_path, live_server, member_goal,
):
    for name, value in CREDENTIALS.items():
        monkeypatch.setenv(name, value)
    root = tmp_path / "capabilities"
    runs = tmp_path / "runs"
    result = RUNNER.invoke(app, [
        "discover", "--capability-id", "lookup_member_balance",
        "--goal", f"Look up member {member_goal} and read their current savings balance",
        "--entry", f"{live_server}/console", "--bind", "member_id=12345:string:internal",
        "--expect-output", "savings_balance:money:pii",
        "--expect-output", "account_status:string:internal", "--llm", "cassette",
        "--cassette", str(REPO / "evidence/runs/showcase-discovery-haiku/cassette.json"),
        "--root", str(root), "--version", "1.0.0", "--evidence-root", str(runs),
    ])
    assert result.exit_code == 0, result.output
    source = root / "lookup_member_balance/1.0.0.json"
    discovered = load(source)
    assert discovered.provenance.discovered_by["model"].startswith("cassette:")
    assert "12345" not in discovered.description
    assert "$inputs.member_id" in discovered.description
    result = RUNNER.invoke(app, ["approve", "lookup_member_balance", "--root", str(root),
                                 "--version", "1.0.0", "--approver", "test"])
    assert result.exit_code == 0, result.output
    for member, balance, status in (("12345", "$4,281.19", "active"),
                                     ("67890", "$912.04", "dormant")):
        result = RUNNER.invoke(app, ["replay", "lookup_member_balance", "--root", str(root),
                                     "--version", "1.0.0", "--input", f"member_id={member}",
                                     "--base-url", live_server, "--evidence-root", str(runs)])
        assert result.exit_code == 0, result.output
        output = json.loads(result.stdout)
        assert output["status"] == "success"
        assert output["outputs"] == {"savings_balance": balance, "account_status": status}
        artifact = Path(output["evidence_dir"]) / "artifact.json"
        assert artifact.read_bytes() == source.read_bytes()
