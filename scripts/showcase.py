"""Curated evidence for the submission: one showcase run per demo scenario.

    uv run python scripts/showcase.py            # every scenario whose artifact is approved
    uv run python scripts/showcase.py --list     # what exists, and what each scenario needs

Each scenario replays an *approved* artifact - never a draft, never one approved in memory -
against a private instance of the target app, and its evidence directory becomes
``evidence/runs/showcase-<scenario>/``, which .gitignore keeps (nothing else under
``evidence/runs`` is committed). A scenario whose artifact is still a draft is skipped and
names the approval that unblocks it. ``evidence/README.md`` is rewritten to index every
showcase that exists, and every showcase is scanned for raw values before the script exits.

In the handoff scenarios the operator's part is played by this script, through the same
state store ``waypoint intervene`` uses; the README's demo path does it by hand.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from waypoint.artifact.approval import approval_status  # noqa: E402
from waypoint.artifact.schema import Capability, load  # noqa: E402
from waypoint.policy.secrets import SecretBroker  # noqa: E402
from waypoint.replay.engine import ReplayOptions, replay  # noqa: E402
from waypoint.replay.result import ReplayResult  # noqa: E402
from waypoint.session.escalation import Intervention, InterventionStore  # noqa: E402
from waypoint.session.store import StateStore  # noqa: E402
from waypoint.surface.ports import Action  # noqa: E402

RUNS = REPO / "evidence" / "runs"
CAPS = REPO / "capabilities"
#: The fixture's own, fictional credentials (.env.example); never real ones.
FIXTURE_CREDENTIALS = {"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}
CANARIES = ("operator1", "changeme", "12345", "Dolores Whitfield", "4,281.19", "250.00",
            "SA-12345", "CN-12345")
SUB_ACCOUNT = {"member_id": "12345", "account_type": "Money Market", "initial_deposit": "250.00"}

Person = Callable[[Any], None]


def content(surface: Any) -> Any:
    return next(f for f in surface.page.frames if f.name == "content")


def open_the_members_record(surface: Any) -> None:
    frame = content(surface)
    frame.locator('a[id$="_lnkView"][href$="member_id=12345"]').click()
    frame.wait_for_url("**/console/member?member_id=12345")


def confirm_by_hand(surface: Any) -> None:
    frame = content(surface)
    frame.locator("#ctl00_MainContent_lnkConfirm").click()
    frame.wait_for_url("**/console/subaccount/confirm**")


@dataclass(frozen=True)
class Scenario:
    name: str
    shows: str
    capability: str
    version: str
    inputs: dict[str, str] = field(default_factory=dict)
    inject: str | None = None
    person: Person | None = None


SCENARIOS = (
    Scenario("replay-success", "clean replay, no model: the balance and status",
             "lookup_member_balance", "1.2.0", {"member_id": "12345"}),
    Scenario("replay-another-member", "the same artifact, another member, another answer",
             "lookup_member_balance", "1.2.0", {"member_id": "67890"}),
    Scenario("replay-business-outcome", "no such member: a named outcome, exit 0",
             "lookup_member_balance", "1.2.0", {"member_id": "00000"}),
    Scenario("replay-recovered-interstitial", "a notice dismissed: success with recoveries",
             "lookup_member_balance", "1.2.0", {"member_id": "12345"}, inject="interstitial"),
    Scenario("replay-hard-failure", "a server error: failure, expected vs observed",
             "lookup_member_balance", "1.2.0", {"member_id": "12345"}, inject="500"),
    Scenario("replay-escalated-wrong-member", "the right screen for the wrong member: escalated",
             "lookup_member_balance", "1.2.0", {"member_id": "12345"}, inject="wrong_member"),
    Scenario("handoff-ambiguous", "escalation handed to a person, who opens the record; "
             "the run resumes - human/actions.jsonl and handoff1_diff.json",
             "lookup_member_balance", "1.2.0", {"member_id": "12345"}, inject="ambiguous",
             person=open_the_members_record),
    Scenario("irreversible-lost-response", "a person confirms, the response is lost; the "
             "engine reconciles from the accounts grid and adopts - one account",
             "open_sub_account", "1.0.0", SUB_ACCOUNT, inject="commit_then_drop",
             person=confirm_by_hand),
    Scenario("irreversible-stale-receipt", "a person confirms and gets someone else's receipt; "
             "reconciliation finds no such account and escalates",
             "open_sub_account", "1.0.0", SUB_ACCOUNT, inject="stale_confirmation",
             person=confirm_by_hand),
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def post(url: str) -> None:
    urllib.request.urlopen(urllib.request.Request(url, data=b"", method="POST"), timeout=10).read()


def approved(scenario: Scenario) -> Capability | None:
    path = CAPS / scenario.capability / f"{scenario.version}.json"
    if not path.exists():
        return None
    cap = load(path)
    return cap if approval_status(cap).approved else None


def run(scenario: Scenario, cap: Capability, base: str, state: Path) -> ReplayResult:
    post(f"{base}/_fixture/reset")
    store = InterventionStore(StateStore(state))

    def inject(surface: Any, origin: str) -> None:
        surface.act(Action("navigate", url=f"{origin}/console?inject={scenario.inject}"))

    def take(iv: Intervention) -> None:
        store.take(iv.id, "showcase-operator", 120)

    def operate(surface: Any, iv: Intervention) -> None:
        assert scenario.person is not None
        scenario.person(surface)
        store.give_back(iv.id)

    return replay(cap, scenario.inputs, ReplayOptions(
        base_url=base, evidence_root=RUNS, state_db=state,
        secrets=SecretBroker(environ=FIXTURE_CREDENTIALS),
        after_preconditions=inject if scenario.inject else None,
        handoff=scenario.person is not None, lease_poll_ms=100, wait_timeout_s=60,
        max_handoffs=1, notify=take if scenario.person else None,
        while_human=operate if scenario.person else None,
    ))


def write_index() -> None:
    rows = []
    for d in sorted(RUNS.glob("showcase-*")):
        result = d / "result.json"
        if result.exists():
            r = json.loads(result.read_text())
            what = next((s.shows for s in SCENARIOS if f"showcase-{s.name}" == d.name), "")
            code = (r.get("failure") or {}).get("code") or r.get("outcome") or ""
            rows.append(f"| [`{d.name}`](runs/{d.name}) | {r['capability_id']} "
                        f"{r['version']} | `{r['status']}` {code} | {what} |")
        else:
            expected = ("transcript.json", "cassette.json", "artifact.json")
            present = [n for n in expected if (d / n).exists()]
            missing = [n for n in expected if n not in present]
            note = f"; missing: {', '.join(missing)}" if missing else ""
            rows.append(f"| [`{d.name}`](runs/{d.name}) | discovery | - | a live Claude Haiku "
                        f"4.5 discovery run ({', '.join(present) or 'no run files'}{note}) |")
    (RUNS.parent / "README.md").write_text(
        "# Evidence\n\n"
        "These saved runs demonstrate discovery, deterministic replay, error handling and\n"
        "handoff against the local fixture. Start with `showcase-discovery-haiku`, then\n"
        "`showcase-replay-success` and `showcase-handoff-ambiguous`.\n\n"
        "## Files in each run\n\n"
        "- **Discovery:** `transcript.json`, `cassette.json`, the compiled draft in\n"
        "  `artifact.json`, metadata, events and the final screenshot and snapshot. These\n"
        "  recordings were made with a live Claude model; a cassette reproduces its decisions\n"
        "  without another model call. Discovery uses a transcript ending, not `result.json`.\n"
        "- **Replay:** the exact approved `artifact.json`, `meta.json`, `events.jsonl`,\n"
        "  `result.json`, per-step screenshots and sanitized snapshots. Failure runs include\n"
        "  the stopped screen and diagnostic context.\n"
        "- **Handoff and reconciliation:** human actions in `human/actions.jsonl`, a\n"
        "  `handoff1_diff.json`, and reconciliation evidence where applicable. The showcase\n"
        "  script plays the operator through the same lease and intervention store used by\n"
        "  the CLI. The [manual demo](../README.md#demo-path) uses a person in the live\n"
        "  browser.\n\n"
        "Sensitive outputs are redacted in saved evidence; replay returns full outputs only\n"
        "to the caller. The target app and credentials are fictional fixtures.\n\n"
        "## Artifact provenance\n\n"
        "The lookup discovery produced draft **1.1.0**. The lookup replay showcases use\n"
        "reviewed **1.2.0**, built from the handwritten base with outcomes and recovery.\n"
        "The [README demo](../README.md#demo-path) separately discovers, approves and replays\n"
        "the same newly generated artifact for two members. The sub-account showcases use\n"
        "**1.0.0**, derived from live discovery and hardened with a reconciliation probe by\n"
        "[the review script](../scripts/review_capabilities.py).\n\n"
        "## Run index\n\n"
        "| Run | Capability | Result | What it shows |\n|---|---|---|---|\n"
        + "\n".join(rows) + "\n\n"
        "## Regenerate replay evidence\n\n"
        "From the repository root:\n\n"
        "```bash\n"
        "uv run python scripts/showcase.py --list\n"
        "uv run python scripts/showcase.py\n"
        "```\n\n"
        "The second command starts a private fixture, replaces the replay showcases and\n"
        "rebuilds this index. Scenarios with unapproved artifacts are skipped. It preserves\n"
        "the two original live discovery recordings and makes no model API calls.\n")


def leaks() -> list[str]:
    found = []
    for d in RUNS.glob("showcase-*"):
        for f in d.rglob("*"):
            if f.is_file() and f.suffix != ".png":
                text = f.read_text(errors="replace")
                found += [f"{f.relative_to(RUNS.parent)}: {c!r}" for c in CANARIES if c in text]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="show scenarios and their state")
    args = parser.parse_args()

    blocked = [s for s in SCENARIOS if approved(s) is None]
    if args.list:
        for s in SCENARIOS:
            state = "ready" if approved(s) else f"needs {s.capability} {s.version} approved"
            exists = "exists" if (RUNS / f"showcase-{s.name}").exists() else "not yet run"
            print(f"{s.name:<32} {state:<44} {exists}")
        return 0

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = tempfile.NamedTemporaryFile(prefix="showcase-app-", suffix=".log", delete=False)
    app = subprocess.Popen([sys.executable, "-m", "target_app"], cwd=REPO,
                           env={**os.environ, "PORT": str(port), **FIXTURE_CREDENTIALS},
                           stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(f"{base}/health", timeout=1).read()
                break
            except OSError:
                time.sleep(0.25)
        with tempfile.TemporaryDirectory() as tmp:
            for s in SCENARIOS:
                cap = approved(s)
                if cap is None:
                    print(f"skipped  {s.name}: approve {s.capability} {s.version} first "
                          f"(waypoint approve {s.capability} --version {s.version})")
                    continue
                result = run(s, cap, base, Path(tmp) / f"{s.name}.db")
                target = RUNS / f"showcase-{s.name}"
                shutil.rmtree(target, ignore_errors=True)
                Path(result.evidence_dir or "").rename(target)
                code = result.failure.code if result.failure else (result.outcome or "")
                print(f"ran      {s.name}: {result.status} {code}")
    finally:
        app.terminate()
        app.wait(timeout=10)
    write_index()
    found = leaks()
    for line in found:
        print(f"LEAK     {line}")
    print(f"{len(SCENARIOS) - len(blocked)} run, {len(blocked)} waiting for approval; "
          "index: evidence/README.md")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
