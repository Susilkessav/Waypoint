#!/usr/bin/env python
"""An AI agent answering a question by calling a Waypoint capability.

This is the whole point of the system in one script: the agent knows nothing about the
console, its frames, or how a balance is read. It sees a catalog of approved capabilities
as tools, picks one, calls it with typed arguments, and answers from what comes back.
Everything underneath is deterministic replay - no model decides which control to click.

    uv run python scripts/agent_demo.py --question "What is member 67890's savings balance?"
    uv run python scripts/agent_demo.py --cassette evidence/agent/lookup.json   # offline

A live run uses a bounded Haiku tool-use loop and records a cassette beside its evidence, so the
demonstration can be reproduced without an API key.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from waypoint.catalog.registry import Catalog, Unavailable, result_for_agent  # noqa: E402
from waypoint.envfile import load_dotenv  # noqa: E402
from waypoint.policy.secrets import SecretBroker  # noqa: E402
from waypoint.replay.engine import ReplayOptions  # noqa: E402
from waypoint.replay.ledger import Ledger  # noqa: E402
from waypoint.replay.stability import load_cases, run_sweep  # noqa: E402
from waypoint.session.store import StateStore  # noqa: E402

MODEL = "claude-haiku-4-5"
FIXTURE = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}
SYSTEM = """You are a bank's servicing assistant. You cannot browse; you call capabilities.

Call one when it answers the question, then reply in one short sentence. If a capability
reports a business outcome such as member_not_found, say that plainly - it is an answer,
not an error. Never invent a balance or an account number.
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_app(port: int, log: Path, tenant: str = "base") -> subprocess.Popen[bytes]:
    handle = log.open("wb")
    app = subprocess.Popen([sys.executable, "-m", "target_app"], cwd=REPO,
                           env={**os.environ, "PORT": str(port), **FIXTURE,
                                "WAYPOINT_NO_DOTENV": "1", "WAYPOINT_TENANT": tenant},
                           stdout=handle, stderr=subprocess.STDOUT)
    handle.close()
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/console", timeout=1)
            return app
        except urllib.error.HTTPError:
            return app
        except OSError:
            time.sleep(0.25)
    app.terminate()
    app.wait(timeout=10)
    raise SystemExit(f"the fixture did not start; see {log}")


def prepare_catalog(root: Path, state_db: Path, base_url: str, evidence: Path) -> Catalog:
    """Earn the lookup's confidence in this demo's own ledger, using declared fixture cases."""
    catalog = Catalog(root, Ledger(StateStore(state_db)))
    entry = catalog.get("lookup_member_balance")
    if not entry.available:
        cases = [case for case in load_cases(root / entry.name / "cases.yaml")
                 if case.inject is None]
        if not cases:
            raise Unavailable("the demo needs declared lookup cases to measure confidence")
        print("Measuring lookup cases against the private fixture before offering the tool…")
        report = run_sweep(entry.capability, cases, runs=2, options=ReplayOptions(
            base_url=base_url, evidence_root=evidence, state_db=state_db, ledger_db=state_db,
            capture_steps=False, secrets=SecretBroker(environ=FIXTURE)),
            report_root=state_db.parent / "stability")
        print(report.summary())
        if report.verdict == "broken" or not catalog.get(entry.name).available:
            raise Unavailable("lookup did not meet its confidence bar; inspect the demo report")
    return catalog


def call(catalog: Catalog, name: str, arguments: dict[str, Any], base_url: str,
         evidence: Path, state_db: Path) -> dict[str, Any]:
    result = catalog.invoke(name, arguments, options=ReplayOptions(
        base_url=base_url, evidence_root=evidence, state_db=state_db, ledger_db=state_db,
        capture_steps=False, secrets=SecretBroker(environ=FIXTURE),
    ))
    return result_for_agent(result)


def ask_model(question: str, tools: list[dict[str, Any]], run: Any) -> dict[str, Any]:
    """One tool-use loop: the model picks a capability, we run it, it answers."""
    import anthropic

    client = anthropic.Anthropic(timeout=60.0, max_retries=2)
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    transcript: dict[str, Any] = {"question": question, "model": MODEL, "calls": []}
    for _ in range(3):
        response = client.messages.create(model=MODEL, max_tokens=1024, system=SYSTEM,
                                          tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": response.content})
        calls = [block for block in response.content if block.type == "tool_use"]
        if not calls:
            transcript["answer"] = "".join(b.text for b in response.content if b.type == "text")
            return transcript
        results = []
        for block in calls:
            arguments = dict(block.input)
            outcome = run(block.name, arguments)
            transcript["calls"].append({"capability": block.name, "arguments": arguments,
                                        "result": outcome})
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(outcome)})
        messages.append({"role": "user", "content": results})
    transcript["answer"] = "(the model did not settle on an answer)"
    return transcript


def replay_cassette(path: Path, run: Any) -> dict[str, Any]:
    """Reproduce a recorded demonstration: the same calls, against a live fixture."""
    recorded = json.loads(path.read_text())
    transcript: dict[str, Any] = {"question": recorded["question"],
                                  "model": f"cassette:{recorded.get('model', 'unknown')}",
                                  "calls": []}
    matched = True
    for call_record in recorded["calls"]:
        outcome = run(call_record["capability"], call_record["arguments"])
        expected = call_record.get("result", {})
        call_matched = all(outcome.get(key) == expected.get(key)
                           for key in ("status", "outputs", "outcome", "error"))
        if not call_matched:
            matched = False
            print(f"  !! this run differs from the recording: expected "
                  f"{expected.get('status')} {expected.get('outputs')}")
        transcript["calls"].append({**call_record, "result": outcome,
                                    "as_recorded": call_matched})
    transcript["answer"] = recorded.get("answer", "") if matched else (
        "(the recorded answer is not repeated: this run did not reproduce it)")
    return transcript


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", default="What is member 67890's savings balance?")
    parser.add_argument("--cassette", type=Path,
                        help="Reproduce a recorded run instead of calling a model.")
    parser.add_argument("--record", type=Path, help="Live recording output; defaults to workspace.")
    parser.add_argument("--capabilities", type=Path, default=REPO / "capabilities")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--workspace", type=Path, help="Private demo state and output directory.")
    args = parser.parse_args()
    if args.cassette is None:
        load_dotenv()
    workspace = args.workspace or Path(tempfile.mkdtemp(prefix="waypoint-agent-"))
    workspace.mkdir(parents=True, exist_ok=True)
    evidence = args.evidence or workspace / "runs"
    recording = args.record or workspace / "agent.json"

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    state_db = workspace / "state.db"
    log = workspace / "app.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    app = start_app(port, log)
    try:
        catalog = prepare_catalog(args.capabilities, state_db, base_url, evidence)
        tools = catalog.tools()
        if not tools:
            print("no approved capabilities to offer; approve one first", file=sys.stderr)
            return 1
        print(f"catalog offers: {', '.join(t['name'] for t in tools)}\n")

        def run(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            print(f"  -> the agent calls {name}({json.dumps(arguments)})")
            outcome = call(catalog, name, arguments, base_url, evidence, state_db)
            print(f"  <- {json.dumps(outcome)}")
            return outcome

        if args.cassette is not None:
            transcript = replay_cassette(args.cassette, run)
        else:
            transcript = ask_model(args.question, tools, run)
            recording.parent.mkdir(parents=True, exist_ok=True)
            recording.write_text(json.dumps(transcript, indent=2, ensure_ascii=False) + "\n")
            print(f"\nrecorded: {recording}")
        (workspace / "result.json").write_text(json.dumps(transcript, indent=2) + "\n")
        print(f"\nQ: {transcript['question']}\nA: {transcript['answer']}")
        print(f"Demo evidence: {workspace}")
        return 0 if all(c.get("as_recorded", True) for c in transcript["calls"]) else 1
    except (Unavailable, KeyError, ValueError) as exc:
        print(f"demo stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        app.terminate()
        app.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
