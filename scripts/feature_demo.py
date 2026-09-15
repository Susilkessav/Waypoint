"""Rehearse retained extensions against private fixtures, then save sanitized evidence.

No model calls. Discovery uses an explicitly scripted decider; assisted relocation reuses
an existing live-model cassette. The script plays the operator using the public console
and shared lease store. Browser mutations only touch fictional fixture records.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.agent_demo import FIXTURE, free_port, prepare_catalog, start_app  # noqa: E402
from scripts.fixture_decider import PH, ScriptedDecider  # noqa: E402
from waypoint.artifact.approval import approve  # noqa: E402
from waypoint.artifact.schema import load  # noqa: E402
from waypoint.compiler.compile import compile_transcript  # noqa: E402
from waypoint.discovery.agent import DiscoveryOptions, InputBinding, discover  # noqa: E402
from waypoint.discovery.decisions import Decision  # noqa: E402
from waypoint.operator.console import create_console  # noqa: E402
from waypoint.policy.secrets import SecretBroker  # noqa: E402
from waypoint.replay.assist import CassetteAssistant  # noqa: E402
from waypoint.replay.engine import ReplayOptions, ResumeRefused, replay, resume  # noqa: E402
from waypoint.replay.stability import Case, case_injector  # noqa: E402
from waypoint.session.escalation import InterventionStore  # noqa: E402
from waypoint.session.handoff import HandoffSettings  # noqa: E402
from waypoint.session.progress import ProgressStore  # noqa: E402
from waypoint.session.store import StateStore  # noqa: E402


class NeedsDemonstration(ScriptedDecider):
    model = "scripted-demonstration-fixture"

    def decide(self, ctx: Any) -> Decision:
        results = any(e.role == "link" and e.name == "View" for _, e in ctx.elements)
        detail = any(e.role == "LayoutTableCell" and e.name == PH for _, e in ctx.elements)
        if results and not detail:
            return Decision("give_up", reason="Scripted stop: demonstrate the correct View link")
        return super().decide(ctx)


def options(base: str, workspace: Path, **kw: Any) -> ReplayOptions:
    return ReplayOptions(base_url=base, evidence_root=workspace / "runs",
                         state_db=workspace / "state.db", ledger_db=workspace / "state.db",
                         resume_db=workspace / "state.db", capture_steps=False,
                         secrets=SecretBroker(environ=FIXTURE), **kw)


def crash_worker(base: str, workspace: Path) -> None:
    advance = ProgressStore.advance

    def die(self: ProgressStore, run_id: str, completed_index: int) -> None:
        advance(self, run_id, completed_index)
        if completed_index == 1:
            os._exit(86)  # Deliberate process death after a durable, safe checkpoint.

    ProgressStore.advance = die  # type: ignore[method-assign]
    replay(load(REPO / "capabilities/lookup_member_balance/1.2.0.json"),
           {"member_id": "12345"}, options(base, workspace))
    raise RuntimeError("the crash checkpoint was not reached")


def rehearse(output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="waypoint-features-") as tmp:
        work = Path(tmp)
        output.mkdir(parents=True, exist_ok=True)
        base_port, tenant_port = free_port(), free_port()
        base, tenant = f"http://127.0.0.1:{base_port}", f"http://127.0.0.1:{tenant_port}"
        app = start_app(base_port, work / "app.log")
        other = None
        index: list[dict[str, str]] = []

        def retain(name: str, directory: Path, shows: str) -> None:
            target = output / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(directory, target)
            index.append({"name": name, "shows": shows, "path": name})
            print(f"PASS {name}: {shows}", flush=True)

        def save(name: str, result: Any, shows: str, status: str = "success") -> None:
            assert result.status == status, (name, result.failure)
            retain(name, Path(result.evidence_dir), shows)

        try:
            other = start_app(tenant_port, work / "tenant.log", "riverbank")
            catalog = prepare_catalog(REPO / "capabilities", work / "state.db", base,
                                      work / "runs")
            measurement = next((work / "stability").iterdir())
            measured = json.loads((measurement / "report.json").read_text())
            # Keep the measured runs with the report; replace temporary paths with portable
            # references to the retained copies. Runtime state and fixture logs stay private.
            references = []
            for path in measured["evidence"]:
                source = Path(path)
                shutil.copytree(source, measurement / source.name)
                references.append(source.name)
            measured["evidence"] = references
            (measurement / "report.json").write_text(json.dumps(measured, indent=2) + "\n")
            retain("confidence-measurement", measurement,
                   "Eight declared cases earn confidence in the same ledger used for invocation")
            cap = catalog.get("lookup_member_balance").capability
            save("catalog-base", catalog.invoke(cap.capability_id, {"member_id": "67890"},
                                                options=options(base, work)),
                 "Approved, measured tool invocation for another member")
            # Tenant execution is measured separately. This fixture demonstration explicitly
            # bypasses confidence while collecting its first evidence; approval remains on.
            save("tenant-riverbank", replay(cap, {"member_id": "67890"}, options(
                tenant, work, variant="riverbank", require_confidence=False)),
                "Same artifact and inputs; riverbank override finds the renamed Continue button")
            save("tenant-without-override", replay(cap, {"member_id": "67890"}, options(
                tenant, work, injected="tenant_layout")),
                 "Base target is refused on the changed tenant", "escalated")
            assist = CassetteAssistant.load(REPO / "evidence/agent/assist.json")
            result = replay(cap, {"member_id": "12345"}, options(
                base, work, assist=assist, injected="drift_search",
                after_preconditions=case_injector(Case({}, inject="drift_search"))))
            assert len(result.telemetry["assist_attempts"]) == 1
            assert len(result.telemetry["assisted"]) == 1
            save("assisted-relocation", result,
                 "Recorded live-model relocation matches this screen; one attempt, checked draft")

            queue = InterventionStore(StateStore(work / "state.db"))
            console = create_console(work / "state.db").test_client()

            def console_post(path: str) -> None:
                console.get("/")
                with console.session_transaction() as session:
                    token = session["csrf_token"]
                response = console.post(path, data={"csrf_token": token, "operator": "demo"},
                                        headers={"Origin": "http://localhost"})
                assert response.status_code == 302

            def take(iv: Any) -> None:
                console_post(f"/interventions/{iv.id}/take")
                assert queue.get(iv.id).status == "taken"

            def human(surface: Any, iv: Any) -> None:
                frame = next(f for f in surface.page.frames if f.name == "content")
                frame.locator('a[id$="_lnkView"][href$="member_id=12345"]').click()
                console_post(f"/interventions/{iv.id}/return")
                assert queue.get(iv.id).status == "returned"

            # Pin the established release so this injected operator exercise does not depend
            # on the new variant release's measurements or change its confidence.
            result = catalog.invoke(cap.capability_id, {"member_id": "12345"}, version="1.2.0",
                                    options=options(base, work, handoff=True, notify=take,
                                        while_human=human, lease_poll_ms=100, wait_timeout_s=30,
                                        injected="ambiguous", after_preconditions=case_injector(
                                            Case({}, inject="ambiguous"))))
            assert result.telemetry["handoffs"]
            save("catalog-console-handoff", result,
                 "Catalog invocation pauses; protected console takes/returns the same browser")

            discovery = discover(NeedsDemonstration(), DiscoveryOptions(
                capability_id="lookup_member_balance", goal="Read member {{member_id}} balance",
                entry=base + "/console", inputs=[InputBinding("member_id", "12345", "string",
                                                                              "internal")],
                outputs=[], evidence_root=work / "discovery", secrets=SecretBroker(environ=FIXTURE),
                handoff=True, handoff_settings=HandoffSettings(
                    state_db=work / "state.db", notify=take, while_human=human,
                    lease_poll_ms=100, wait_timeout_s=30)))
            assert discovery.transcript.ending == "finished", discovery.transcript.ending_detail
            compiled = compile_transcript(discovery.transcript, version="1.0.0")
            assert compiled.capability.provenance.demonstrated_steps
            # Explicit fixture review: approval applies only to this isolated generated copy.
            demonstrated = approve(compiled.capability, approver="fixture-demo-review")
            directory = next((work / "discovery").iterdir())
            (directory / "artifact.json").write_text(compiled.capability.to_json())
            retain("discovery-demonstration", directory,
                   "Scripted discovery stop; operator's click becomes a reusable input-bound step")
            save("demonstration-reused", replay(demonstrated, {"member_id": "67890"},
                                                options(base, work)),
                 "Human-demonstrated step replays for a different member")

            crashed = work / "crash"
            crashed.mkdir()
            child = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                    "--crash-worker", base, "--workspace", str(crashed)],
                                   cwd=REPO, env={**os.environ, "WAYPOINT_NO_DOTENV": "1"},
                                   capture_output=True, text=True, timeout=60)
            assert child.returncode == 86, child.stderr
            progress = ProgressStore(StateStore(crashed / "state.db"))
            with progress.store.connect() as db:
                source = db.execute(
                    "SELECT run_id FROM run_progress WHERE status='running'").fetchone()[0]
            retained = load(REPO / "capabilities/lookup_member_balance/1.2.0.json")
            result = resume(retained, {"member_id": "12345"}, source, options(base, crashed))
            assert progress.get(source).resumed_by == result.run_id
            assert progress.get(source).status == "done"
            try:
                resume(retained, {"member_id": "12345"}, source, options(base, crashed))
            except ResumeRefused:
                pass
            else:
                raise AssertionError("source was resumed twice")
            save("crash-resumed", result,
                 "Real worker exit; safe prefix reconstructed; source claimed once and closed")
            retain("crash-before-resume", crashed / "runs" / source,
                   "Interrupted worker's durable evidence before resumption (no terminal result)")
            (output / "manifest.json").write_text(json.dumps({
                "fixture_only": True, "live_model_calls": 0,
                "operator": "scripted through protected console routes", "scenarios": index,
            }, indent=2) + "\n")
            (output / "README.md").write_text(
                "# Feature demonstrations\n\nGenerated by `uv run python scripts/feature_demo.py`. "
                "These are private local fixtures; the script plays the operator through the "
                "protected console routes. No new model calls were made. Discovery decisions "
                "are explicitly scripted; assisted relocation reuses the saved "
                "live-model cassette.\n\n"
                + "| Evidence | Demonstrated behavior |\n|---|---|\n"
                + "\n".join(f"| [{r['name']}]({r['path']}) | {r['shows']} |" for r in index)
                + "\n\nSaved outputs and screenshots are redacted. "
                "Runtime databases remain private. "
                "Run IDs and captured paths describe the original execution; use the links above "
                "to inspect the retained copies.\n")
        finally:
            for process in (other, app):
                if process is not None:
                    process.terminate()
                    process.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / "evidence/features")
    parser.add_argument("--crash-worker", help=argparse.SUPPRESS)
    parser.add_argument("--workspace", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.crash_worker:
        crash_worker(args.crash_worker, args.workspace)
    else:
        rehearse(args.output)


if __name__ == "__main__":
    main()
