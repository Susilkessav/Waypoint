"""B5 - redaction coverage across every sink R-SENS-5 names, as one matrix (T25).

One module-scoped world exercises every writer at once: a discovery run (with every prompt
the model would be sent), a replay of the irreversible capability through a lost response
and its reconciliation, and a handoff in which a person acts. Each sink is then scanned for
the raw values that must never reach it.

Two rules keep the matrix honest. A row whose sink was never written fails - otherwise a
broken writer would pass as perfectly redacted. And screenshots, which cannot be scanned as
text, are held to a structural rule instead: nothing writes a PNG except the masked
capture, whose pixels ``test_web_surface`` already proves black over classified cells.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.test_discovery import ScriptedDecider, options
from waypoint.artifact.approval import approve
from waypoint.artifact.schema import Capability, load
from waypoint.compiler.compile import compile_transcript
from waypoint.discovery.agent import discover
from waypoint.discovery.cassette import Cassette, DecisionContext, RecordingDecider
from waypoint.discovery.decisions import Decision
from waypoint.discovery.prompts import render_turn
from waypoint.policy.secrets import SecretBroker
from waypoint.replay.engine import ReplayOptions, replay
from waypoint.replay.result import ReplayResult
from waypoint.session.escalation import InterventionStore
from waypoint.session.store import StateStore
from waypoint.surface.ports import Action

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parents[1]
CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}
SUB_ACCOUNT = {"member_id": "12345", "account_type": "Money Market", "initial_deposit": "250.00"}
#: Every raw value in play: an internal ID, a name, a balance, both credentials, a deposit,
#: and the account and confirmation numbers the irreversible step creates.
CANARIES = ("12345", "Dolores Whitfield", "4,281.19", "operator1", "changeme", "250.00",
            "SA-12345", "CN-12345")
SINKS = ("llm_prompt", "llm_completion", "events", "transcript", "ax_snapshots", "screenshots",
         "human_action_log", "artifact", "run_records", "state_store")


@dataclass
class World:
    texts: dict[str, list[tuple[str, str]]]
    replay: ReplayResult
    handoff: ReplayResult


def inject(name: str) -> Any:
    return lambda surface, origin: surface.act(
        Action("navigate", url=f"{origin}/console?inject={name}"))


@pytest.fixture(scope="module")
def world(live_server: str, tmp_path_factory: pytest.TempPathFactory) -> World:
    root = tmp_path_factory.mktemp("coverage")
    urllib.request.urlopen(urllib.request.Request(f"{live_server}/_fixture/reset", data=b"",
                                                  method="POST"), timeout=10).read()
    texts: dict[str, list[tuple[str, str]]] = defaultdict(list)
    secrets = SecretBroker(environ=CREDENTIALS)

    # 1. Discovery - and the exact text of every prompt the model would receive.
    class Prompted(ScriptedDecider):
        def decide(self, ctx: DecisionContext) -> Decision:
            texts["llm_prompt"].append((f"turn {ctx.turn}", render_turn(ctx)))
            return super().decide(ctx)

    cassette = Cassette(model="scripted", goal="lookup", provenance="coverage")
    found = discover(RecordingDecider(Prompted(), cassette), options(live_server, root / "d"))
    cassette.save(found.run_dir / "cassette.json")
    texts["artifact"].append(("compiled draft",
                              compile_transcript(found.transcript).capability.to_json()))

    # 2. The irreversible capability, through a lost response and its reconciliation.
    data = json.loads((REPO / "capabilities/open_sub_account/1.0.0.json").read_text())
    data["provenance"], data["policy"]["unattended"] = {}, False
    sub_account = approve(Capability.model_validate(data), approver="test")
    state = root / "state.db"
    replayed = replay(sub_account, SUB_ACCOUNT, ReplayOptions(
        base_url=live_server, evidence_root=root / "r", state_db=state, secrets=secrets,
        approve=lambda verdict, action: True, after_preconditions=inject("commit_then_drop")))
    assert replayed.status == "success" and replayed.telemetry["adopted"], replayed.failure

    # 3. A handoff in which a person takes the browser and acts.
    store = InterventionStore(StateStore(state))

    def person(surface: Any, iv: Any) -> None:
        frame = next(f for f in surface.page.frames if f.name == "content")
        frame.locator('a[id$="_lnkView"][href$="member_id=12345"]').click()
        frame.wait_for_url("**/console/member?member_id=12345")
        store.give_back(iv.id)

    lookup = approve(load(REPO / "capabilities/lookup_member_balance/1.2.0.json"),
                     approver="test")
    handed = replay(lookup, {"member_id": "12345"}, ReplayOptions(
        base_url=live_server, evidence_root=root / "h", state_db=state, secrets=secrets,
        handoff=True, lease_poll_ms=100, wait_timeout_s=30, after_preconditions=inject(
            "ambiguous"), notify=lambda iv: store.take(iv.id, "tester", 60), while_human=person))
    assert handed.status == "success", handed.failure

    for run in (found.run_dir, Path(replayed.evidence_dir or ""), Path(handed.evidence_dir or "")):
        for f in sorted(run.rglob("*")):
            if not f.is_file():
                continue
            where = str(f.relative_to(root))
            if f.suffix == ".png":
                texts["screenshots"].append((where, ""))
                continue
            text = f.read_text()
            if f.name == "events.jsonl":
                texts["events"].append((where, text))
            elif f.name == "transcript.json":
                texts["transcript"].append((where, text))
            elif f.name in ("cassette.json", "llm_exchanges.json"):
                texts["llm_completion"].append((where, text))
            elif f.name.endswith("_snapshot.json"):
                texts["ax_snapshots"].append((where, text))
            elif f.parent.name == "human":
                texts["human_action_log"].append((where, text))
            elif f.name == "artifact.json":
                texts["artifact"].append((where, text))
            else:
                texts["run_records"].append((where, text))
    for f in (REPO / "capabilities").rglob("*.json"):
        texts["artifact"].append((str(f.relative_to(REPO)), f.read_text()))
    with sqlite3.connect(state) as db:  # persisted operator state, read by another process
        for table, columns in (("interventions", "reason_code, message, step, intent, "
                                "expected_signature, observed_signatures, operator"),
                               ("intents", "step, state, resolution, resolved_by")):
            for row in db.execute(f"SELECT {columns} FROM {table}"):
                texts["state_store"].append((table, json.dumps(row)))
    return World(dict(texts), replayed, handed)


@pytest.mark.parametrize("sink", SINKS)
def test_t25_no_raw_value_reaches_the_sink(world: World, sink: str) -> None:
    written = world.texts.get(sink, [])
    assert written, f"nothing was written to {sink}: this row would pass without testing"
    for where, text in written:
        for canary in CANARIES:
            assert canary not in text, f"{canary!r} reached {sink} in {where}"


def test_screenshots_are_only_ever_written_from_the_masked_capture() -> None:
    """Every evidence PNG passes through ``capture_evidence``, which masks what it must."""
    writers = []
    for path in (REPO / "waypoint").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "screenshot" and node.args):
                arg = ast.unparse(node.args[-1])
                writers.append((path.name, arg))
    evidence_writes = [(f, a) for f, a in writers if f != "web.py"]
    assert evidence_writes, "the scan found no evidence writers at all"
    assert all(a.endswith("screenshot_png") for _, a in evidence_writes), evidence_writes


def test_every_confirmed_step_leaves_a_screenshot_and_a_snapshot(world: World) -> None:
    run = Path(world.replay.evidence_dir or "")
    events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
    captured = [e for e in events if e["event"] == "step_captured"]
    confirmed = [e for e in events if e["event"] == "checkpoint_met"
                 and ".reconcile." not in e["where"]]
    assert captured and len(captured) >= len(confirmed) - 1
    for e in captured:
        assert (run / e["screenshot"]).exists() and (run / e["snapshot"]).exists()


def test_reconciliation_and_handoff_are_each_a_readable_trail(world: World) -> None:
    def kinds(result: ReplayResult) -> list[str]:
        path = Path(result.evidence_dir or "") / "events.jsonl"
        return [json.loads(line)["event"] for line in path.read_text().splitlines()]

    replayed = kinds(world.replay)
    for event in ("intent", "reconcile_started", "reconcile_verdict", "adopted"):
        assert event in replayed, event
    assert (Path(world.replay.evidence_dir or "") / "reconcile1.png").exists()

    handed = kinds(world.handoff)
    for event in ("intervention_opened", "lease", "control_returned"):
        assert event in handed, event
    assert (Path(world.handoff.evidence_dir or "") / "human" / "actions.jsonl").exists()
