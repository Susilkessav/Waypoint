"""Small recording helper around the real CLI. No model calls or automatic approvals."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from waypoint.artifact.approval import approval_status
from waypoint.artifact.schema import load
from waypoint.session.escalation import InterventionStore
from waypoint.session.store import StateStore

REPO = Path(__file__).resolve().parents[1]
DEMO_HOME = REPO / ".waypoint" / "recording"
CURRENT = DEMO_HOME / "current.json"
COMMANDS = ("prepare", "app", "discover", "review", "draft", "approve", "replay", "other",
            "missing", "recover", "failure", "handoff", "commit", "take", "return", "abort",
            "evidence")
FIXTURE = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme", "WAYPOINT_NO_DOTENV": "1"}


def prepare() -> None:
    if CURRENT.exists():
        previous = json.loads(CURRENT.read_text())
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", previous["port"])) == 0:
                raise ValueError("Stop the previous APP with Control-C before ./demo prepare.")
        db = Path(previous["directory"]) / "state.db"
        if db.exists() and InterventionStore(StateStore(db)).list():
            raise ValueError("Finish or ./demo abort the previous handoff before preparing a take.")
    DEMO_HOME.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="take-", dir=DEMO_HOME))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    staging = DEMO_HOME / "current.tmp"
    staging.write_text(json.dumps({"directory": str(directory), "port": port}))
    staging.replace(CURRENT)
    print("Ready. In APP run: ./demo app\nThen start at scene 1 in docs/demo-script.md.")
    print(f"Recording files: {directory}")


def intervention_id(db: Path, action: str) -> str:
    if not db.exists():
        raise ValueError("No handoff yet. Run ./demo handoff in RUN and wait for it to pause.")
    statuses = ("taken",) if action == "return" else (("open", "taken") if action == "abort"
                                                     else ("open",))
    rows = InterventionStore(StateStore(db)).list(statuses)  # type: ignore[arg-type]
    if len(rows) != 1:
        raise ValueError(f"Expected one intervention for {action}; found {len(rows)}. "
                         "Wait for RUN to pause; do not start another replay.")
    return rows[0].id


def command_args(action: str, directory: Path, origin: str, headless: bool) -> list[str]:
    root = directory / "capabilities"
    runs = directory / "runs"
    db = directory / "state.db"
    own = ["--root", str(root), "--version", "1.0.0"]
    if action == "discover":
        if (root / "lookup_member_balance/1.0.0.json").exists():
            raise ValueError("Discovery already exists for this take. Continue with ./demo review.")
        args = ["discover", "--capability-id", "lookup_member_balance",
                "--goal", "Look up member {{member_id}} and read their current savings balance",
                "--entry", origin + "/console", "--bind", "member_id=12345:string:internal",
                "--expect-output", "savings_balance:money:pii",
                "--expect-output", "account_status:string:internal", "--llm", "cassette",
                "--cassette", str(REPO / "evidence/runs/showcase-discovery-haiku/cassette.json"),
                *own, "--evidence-root", str(runs)]
        return args if headless else [*args, "--headed"]
    if action == "approve":
        return ["approve", "lookup_member_balance", *own,
                "--note", "reviewed the generated flow and extraction targets"]
    if action in ("take", "return", "abort"):
        return ["intervene", action, intervention_id(db, action), "--db", str(db)]
    member = "67890" if action == "other" else ("00000" if action == "missing" else "12345")
    generated = action in ("draft", "replay", "other")
    cap = "open_sub_account" if action == "commit" else "lookup_member_balance"
    artifact = own if generated else ["--root", str(REPO / "capabilities"),
                                       "--version", "1.0.0" if action == "commit" else "1.2.0"]
    args = ["replay", cap, *artifact, "--input", f"member_id={member}",
            "--base-url", origin, "--state-db", str(db), "--evidence-root", str(runs)]
    injection = {"recover": "interstitial", "failure": "500", "handoff": "ambiguous",
                 "commit": "commit_then_drop"}.get(action)
    if injection:
        args += ["--inject", injection]
    if action in ("handoff", "commit"):
        args += ["--handoff"]
        if headless:
            args += ["--headless"]
    if action == "commit":
        args += ["--input", "account_type=Money Market", "--input", "initial_deposit=250.00"]
    return args


def show_result(text: str) -> None:
    result = json.loads(text)
    print(f"\nSTATUS: {result['status']}")
    print(f"ARTIFACT: {result['capability_id']} {result['version']}")
    for name, value in (result.get("outputs") or {}).items():
        print(f"{name}: {value}")
    if result.get("outcome"):
        print(f"OUTCOME: {result['outcome']}")
    failure = result.get("failure")
    if failure:
        for name in ("code", "step", "message", "expected_signature", "observed_signatures"):
            if failure.get(name):
                print(f"{name}: {failure[name]}")
    telemetry = result.get("telemetry") or {}
    if telemetry.get("recoveries"):
        print(f"RECOVERIES: {json.dumps(telemetry['recoveries'])}")
    if telemetry.get("adopted"):
        print("ADOPTED: true — reconciled the operation without repeating it")
    print(f"EVIDENCE: {result.get('evidence_dir')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=COMMANDS)
    parser.add_argument("--headless", action="store_true", help="for automated rehearsal")
    options = parser.parse_args()
    action = options.action
    if action == "prepare":
        prepare()
        return 0
    if not CURRENT.exists():
        raise ValueError("First run ./demo prepare in RUN.")
    settings = json.loads(CURRENT.read_text())
    directory = Path(settings["directory"])
    origin = f"http://127.0.0.1:{settings['port']}"
    env = {**os.environ, **FIXTURE}
    if action == "app":
        print(f"Demo app: {origin}\nLeave this terminal running.", flush=True)
        return subprocess.call([sys.executable, "-m", "target_app"], cwd=REPO,
                               env={**env, "PORT": str(settings["port"])})
    if action == "review":
        path = directory / "capabilities/lookup_member_balance/1.0.0.json"
        cap = load(path)
        print(f"ARTIFACT: {cap.capability_id} {cap.version}\nFILE: {path}")
        data = json.loads(cap.to_json())
        for section in ("inputs", "outputs"):
            print(f"{section.upper()}: " + ", ".join(data[section]["properties"]))
        for i, step in enumerate(cap.steps, 1):
            print(f"{i}. {step.action}: {step.intent}")
        print(f"APPROVED: {approval_status(cap).approved}")
        print("Review the artifact file before running ./demo approve.")
        return 0
    if action == "evidence":
        for run in sorted((directory / "runs").glob("*")):
            name = "result.json" if (run / "result.json").exists() else "transcript.json"
            if (run / name).exists():
                data = json.loads((run / name).read_text())
                print(f"{run.name}  {data.get('status', data.get('ending'))}")
        print(f"Open this folder in Finder: {directory / 'runs'}")
        return 0
    args = command_args(action, directory, origin, options.headless)
    if args[0] in ("discover", "replay"):
        try:
            urllib.request.urlopen(origin + "/health", timeout=2).close()
        except (OSError, urllib.error.URLError) as exc:
            raise ValueError("Start ./demo app in APP, then retry this scene.") from exc
    replay = args[0] == "replay"
    if action in ("handoff", "commit"):
        print("When RUN pauses: OPERATOR runs ./demo take, clicks in the browser, "
              "then runs ./demo return.", flush=True)
    result = subprocess.run([sys.executable, "-m", "waypoint.cli", *args], cwd=REPO, env=env,
                            stdout=subprocess.PIPE if replay else None, text=True)
    if replay and result.stdout:
        try:
            show_result(result.stdout)
        except (ValueError, KeyError):
            print(result.stdout)
    return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError) as error:
        print(f"Demo: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
