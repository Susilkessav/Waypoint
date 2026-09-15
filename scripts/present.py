"""A recording presenter: Enter advances through the real CLI, using isolated demo state."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import demo  # noqa: E402
from waypoint.session.escalation import InterventionStore  # noqa: E402
from waypoint.session.store import StateStore  # noqa: E402

SCENES = (
    ("discover", "1 · Discover the workflow from recorded live-model decisions", 0),
    ("review", "2 · Review the generated inputs, outputs and steps", 0),
    ("draft", "3 · Prove an unapproved draft is refused", 1),
    ("approve", "4 · Approve the reviewed artifact", 0),
    ("replay", "5 · Replay without a model", 0),
    ("other", "6 · Reuse the exact artifact for another member", 0),
    ("missing", "7 · A missing member is a named business answer", 0),
    ("recover", "8 · Recover from a declared maintenance notice", 0),
    ("failure", "9 · A server error produces a structured failure", 1),
    ("handoff", "10 · Hand the same browser to a person", 0),
    ("commit", "11 · Reconcile a lost confirmation without repeating the commit", 0),
    ("evidence", "12 · Inspect the saved evidence", 0),
)


def command(action: str, headless: bool) -> list[str]:
    return [sys.executable, "-m", "scripts.demo", action, *(["--headless"] if headless else [])]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rehearse", action="store_true",
                        help="Run automatic scenes headless; handoffs have a separate rehearsal.")
    args = parser.parse_args()
    env = {**os.environ, **demo.FIXTURE}
    demo.prepare()
    settings = json.loads(demo.CURRENT.read_text())
    directory = Path(settings["directory"])
    log = (directory / "app.log").open("w")
    app = subprocess.Popen(command("app", args.rehearse), cwd=REPO, env=env,
                           stdout=log, stderr=subprocess.STDOUT)
    active: subprocess.Popen[str] | None = None
    queue = InterventionStore(StateStore(directory / "state.db"))

    def pause(message: str = "Enter = next scene · q = stop") -> None:
        if not args.rehearse and input(f"\n{message}\n").strip().lower() == "q":
            raise KeyboardInterrupt

    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{settings['port']}/health", timeout=1).close()
                break
            except OSError:
                time.sleep(0.25)
        else:
            raise RuntimeError(f"fixture failed to start; inspect {directory / 'app.log'}")
        print("Ready. Start recording. Your private fixture stops when this presenter exits.")
        for action, title, expected in SCENES:
            if args.rehearse and action in {"handoff", "commit"}:
                print(f"MANUAL SCENE: {action}; automated coverage: scripts/showcase.py")
                continue
            pause("Review complete? Enter approves this artifact · q = stop"
                  if action == "approve" else "Enter = next scene · q = stop")
            print(f"\n{title}\n$ ./demo {action}\n", flush=True)
            if action not in {"handoff", "commit"}:
                result = subprocess.run(command(action, args.rehearse), cwd=REPO, env=env)
                if result.returncode != expected:
                    raise RuntimeError(
                        f"{action}: expected exit {expected}, got {result.returncode}")
                continue
            active = subprocess.Popen(command(action, False), cwd=REPO, env=env, text=True)
            while active.poll() is None and not queue.list(("open",)):
                time.sleep(0.2)
            if active.poll() is not None:
                raise RuntimeError(f"{action} ended before opening its intervention")
            pause("Enter = take control of the paused browser · q = abort")
            subprocess.run(command("take", False), cwd=REPO, env=env, check=True)
            instruction = ("Click View in member 12345's row" if action == "handoff"
                           else "Click Confirm once; a lost response is expected")
            pause(f"In the browser: {instruction}. Then Enter = return control · q = abort")
            subprocess.run(command("return", False), cwd=REPO, env=env, check=True)
            try:
                finished = active.wait(timeout=60)
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"{action} is still waiting for a person: control was probably returned "
                    "before the click. Start a fresh take with docs/present.sh.") from None
            if finished != expected:
                raise RuntimeError(f"{action} did not finish as expected; inspect its evidence")
            active = None
        print(f"\nRecording flow complete. Evidence: {directory}")
        print("Optional: uv run python scripts/agent_demo.py --cassette evidence/agent/lookup.json")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nPresenter stopped. Saved evidence remains available.")
        return 130
    finally:
        if active is not None and active.poll() is None:
            for iv in queue.list(("open", "taken")):
                queue.abort(iv.id)
            try:
                active.wait(timeout=5)
            except subprocess.TimeoutExpired:
                active.terminate()
                active.wait(timeout=10)
        app.terminate()
        app.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
