"""Command-line entry point.

Every subcommand is declared here from the start so the command surface is
visible and documented, but each raises until its milestone lands. A stub that
exits loudly is preferable to a command that silently does nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from waypoint import __version__
from waypoint.session.store import DEFAULT_DB

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Record-once, replay-many automation for legacy UIs with no API.",
)


class NotYetImplemented(typer.Exit):
    """Exit code 2 with a message naming the milestone that will implement this."""

    def __init__(self, command: str, milestone: str) -> None:
        typer.secho(
            f"`waypoint {command}` is not implemented yet (arrives in milestone {milestone}).",
            fg=typer.colors.YELLOW,
            err=True,
        )
        super().__init__(code=2)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


CAPABILITIES = Path("capabilities")


@app.command()
def approve(
    capability_id: str,
    version: Annotated[str | None, typer.Option(help="Defaults to the newest version.")] = None,
    note: Annotated[str | None, typer.Option(help="Why this is approved.")] = None,
    approver: Annotated[str | None, typer.Option(help="Defaults to the OS user.")] = None,
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """Approve a draft artifact for replay, after checking every approval gate."""
    import getpass

    from waypoint.artifact.approval import ApprovalBlocked
    from waypoint.artifact.approval import approve as approve_artifact
    from waypoint.artifact.schema import content_hash, load, locate

    path = locate(root, capability_id, version, release=False)
    cap = load(path)
    try:
        approved = approve_artifact(
            cap, approver=approver or getpass.getuser(), note=note
        )
    except ApprovalBlocked as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    path.write_text(approved.to_json())
    typer.echo(f"approved {capability_id} {cap.version} {content_hash(approved)}")


_SENSITIVITIES = ("public", "internal", "pii", "secret")
_TYPES = ("string", "enum", "money")


def _parse_inputs(items: list[str]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name:
            typer.secho(f"--input expects name=value, got {name or item!r}", err=True)
            raise typer.Exit(code=2)
        values.append((name.strip(), value))
    return values


def _parse_bind(item: str) -> tuple[str, str, str, str]:
    """name=value[:type[:sensitivity]] - the value is never echoed back in errors."""
    name, sep, rest = item.partition("=")
    if not sep or not name.strip():
        raise typer.BadParameter("--bind expects name=value[:type[:sensitivity]]")
    value, typ, sens = rest, "string", "internal"
    parts = rest.rsplit(":", 2)
    if len(parts) == 3 and parts[1] in _TYPES and parts[2] in _SENSITIVITIES:
        value, typ, sens = parts
    elif len(parts) >= 2 and parts[-1] in _TYPES:
        value, typ = rest.rsplit(":", 1)
    return name.strip(), value, typ, sens


def _parse_output(item: str) -> tuple[str, str | None, str]:
    """name[:type[:sensitivity]] -> (name, format, sensitivity)."""
    name, typ, sens = ([*item.split(":"), "string", "internal"])[:3]
    if typ not in _TYPES or sens not in _SENSITIVITIES:
        raise typer.BadParameter(f"--expect-output expects name[:type[:sensitivity]], got {item!r}")
    return name, "money" if typ == "money" else None, sens


def _next_version(root: Path, capability_id: str) -> str:
    from waypoint.artifact.schema import locate

    try:
        highest = locate(root, capability_id, release=False).stem.partition("-")[0]
    except FileNotFoundError:
        return "1.0.0"
    major, minor, _ = (int(x) for x in highest.split("."))
    return f"{major}.{minor + 1}.0"


def _operator_approval(verdict: Any, action: Any) -> bool:
    """Discovery is attended: a person at a terminal decides; nobody there means no."""
    import sys

    if not sys.stdin.isatty():
        return False
    return bool(typer.confirm(
        f"Policy needs approval ({verdict.reason}, risk {verdict.risk}) to "
        f"{action.kind}: {action.intent!r}. Allow?", default=False, err=True))


def _announce_intervention(state_db: Path) -> Callable[[Any], None]:
    """Print how to take control, so the waiting run is self-explanatory on screen."""
    def announce(iv: Any) -> None:
        flag = _db_flag(state_db)
        typer.secho(f"paused at {iv.step} ({iv.reason_code}): {iv.message}",
                    fg=typer.colors.YELLOW, err=True)
        typer.secho(
            f"intervention {iv.id} is open and the browser stays up.\n"
            f"  take control:  waypoint intervene take {iv.id}{flag}\n"
            f"  hand it back:  waypoint intervene return {iv.id}{flag}\n"
            f"  end the run:   waypoint intervene abort {iv.id}{flag}",
            err=True,
        )
    return announce


def _compile_and_write(transcript: Any, run_dir: Path, root: Path, version: str | None) -> None:
    import json

    from waypoint.compiler.compile import CompileError, compile_transcript

    ver = version or _next_version(root, transcript.capability_id)
    target = root / transcript.capability_id / f"{ver}.json"
    if target.exists():
        typer.secho(f"refusing to overwrite {target}; pass another --version", err=True,
                    fg=typer.colors.RED)
        raise typer.Exit(code=1)
    try:
        report = compile_transcript(transcript, version=ver)
    except CompileError as exc:
        (run_dir / "compile_report.json").write_text(
            json.dumps({"errors": list(exc.reasons)}, indent=2) + "\n")
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.capability.to_json())
    (run_dir / "compile_report.json").write_text(json.dumps(
        {"artifact": str(target), "notes": list(report.notes),
         "open_gates": list(report.open_gates)}, indent=2) + "\n")
    typer.echo(f"draft written: {target}")
    for gate in report.open_gates:
        typer.secho(f"  open gate: {gate}", err=True, fg=typer.colors.YELLOW)
    if not report.open_gates:
        typer.echo(f"no open gates - review it, then: waypoint approve "
                   f"{transcript.capability_id} --version {ver}")


@app.command()
def discover(
    capability_id: Annotated[str, typer.Option(help="Identifier for the capability.")],
    goal: Annotated[str, typer.Option(help="The task; reference inputs as {{name}}.")],
    entry: Annotated[str, typer.Option(help="URL where the operator starts.")],
    bind: Annotated[
        list[str], typer.Option("--bind", help="name=value[:type[:sensitivity]]; repeatable.")
    ] = [],  # noqa: B006 - typer requires a literal default here
    expect_output: Annotated[
        list[str], typer.Option("--expect-output", help="name[:type[:sensitivity]]; repeatable.")
    ] = [],  # noqa: B006
    llm: Annotated[str, typer.Option(help="anthropic (live model) or cassette (offline).")] = (
        "anthropic"
    ),
    cassette: Annotated[Path | None, typer.Option(help="Cassette for --llm cassette.")] = None,
    model: Annotated[
        str, typer.Option(help="Model for --llm anthropic; defaults to the cheapest current one.")
    ] = "claude-haiku-4-5",
    no_compile: Annotated[bool, typer.Option("--no-compile", help="Stop at the transcript.")] = (
        False
    ),
    version: Annotated[str | None, typer.Option(help="Defaults to the next free version.")] = None,
    max_steps: Annotated[int, typer.Option(help="Turn limit.")] = 20,
    handoff: Annotated[
        bool,
        typer.Option(help="When the run gets stuck, or an action needs a person, open an "
                     "intervention and wait for an operator on the live browser. Implies "
                     "--headed unless --no-headed is given."),
    ] = False,
    state_db: Annotated[
        Path, typer.Option(help="Shared session state for --handoff (leases, interventions).")
    ] = DEFAULT_DB,
    headed: Annotated[bool, typer.Option(help="Show the browser.")] = False,
    evidence_root: Annotated[Path, typer.Option(help="Where run evidence goes.")] = Path(
        "evidence/runs"
    ),
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """Discover a flow with a model (or a recorded cassette) and compile a draft artifact.

    Discovery is attended: an action the policy marks risky is put to you at the
    terminal, and refused when there is no terminal. A live run records a cassette in
    its evidence directory, so it can be reproduced later with --llm cassette.
    """
    from waypoint.discovery.agent import DiscoveryOptions, InputBinding
    from waypoint.discovery.agent import discover as run_discovery
    from waypoint.discovery.cassette import Cassette, CassetteDecider, Decider, RecordingDecider
    from waypoint.discovery.transcript import OutputSpecDecl
    from waypoint.session.handoff import HandoffSettings

    inputs = [InputBinding(n, v, t, s) for n, v, t, s in map(_parse_bind, bind)]  # type: ignore[arg-type]
    outputs = [OutputSpecDecl(n, format=f, sensitivity=s)
               for n, f, s in map(_parse_output, expect_output)]
    recording: Cassette | None = None
    decider: Decider
    if llm == "cassette":
        if cassette is None:
            typer.secho("--llm cassette needs --cassette PATH", err=True)
            raise typer.Exit(code=2)
        decider = CassetteDecider(Cassette.load(cassette))
    elif llm == "anthropic":
        import anthropic

        from waypoint.discovery.claude import ClaudeDecider

        try:
            live = ClaudeDecider(model=model)
            live.preflight()  # a free model lookup, before any browser starts
        except (anthropic.AnthropicError, TypeError) as exc:
            typer.secho(f"cannot use the Anthropic API ({type(exc).__name__}): set "
                        "ANTHROPIC_API_KEY (exported, or in .env), or reproduce a recorded "
                        "run with --llm cassette", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from None
        # The discovery loop owns goal sanitization; never record the raw CLI argument.
        recording = Cassette(model=model, goal="")
        decider = RecordingDecider(live, recording)
    else:
        typer.secho("--llm must be 'anthropic' or 'cassette'", err=True)
        raise typer.Exit(code=2)

    result = run_discovery(decider, DiscoveryOptions(
        capability_id=capability_id, goal=goal, entry=entry, inputs=inputs, outputs=outputs,
        max_steps=max_steps, evidence_root=evidence_root, headed=headed or handoff,
        # With an operator queue there is somewhere to send a risky action; without one,
        # the person at the terminal decides.
        approve=None if handoff else _operator_approval,
        handoff=handoff,
        handoff_settings=HandoffSettings(state_db=state_db,
                                         notify=_announce_intervention(state_db)),
       
    ))
    t = result.transcript
    if recording is not None:
        recording.goal = t.goal
        recording.save(result.run_dir / "cassette.json")
    typer.secho(f"discovery {t.ending}: {len(t.steps)} actions; evidence in {result.run_dir}",
                err=True)
    if t.ending != "finished":
        typer.secho(t.ending_detail, err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)
    if not no_compile:
        _compile_and_write(t, result.run_dir, root, version)


@app.command("compile")
def compile_command(
    source: Annotated[Path, typer.Argument(help="A discovery run directory or transcript.json.")],
    version: Annotated[str | None, typer.Option(help="Defaults to the next free version.")] = None,
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """Recompile a saved discovery transcript into a draft artifact."""
    from waypoint.discovery.transcript import Transcript

    path = source / "transcript.json" if source.is_dir() else source
    _compile_and_write(Transcript.load(path), path.parent, root, version)


@app.command()
def replay(
    capability_id: str,
    inputs: Annotated[
        list[str], typer.Option("--input", "-i", help="An input as name=value; repeatable.")
    ] = [],  # noqa: B006 - typer requires a literal default here
    version: Annotated[str | None, typer.Option(help="Defaults to the highest release.")] = None,
    base_url: Annotated[
        str | None, typer.Option(help="Replay against another origin than the artifact's entry.")
    ] = None,
    inject: Annotated[
        str | None,
        typer.Option(help="Target-app fixture only: set a chaos injection after sign-on."),
    ] = None,
    headed: Annotated[
        bool | None,
        typer.Option("--headed/--headless",
                     help="Show the browser. Default: headless, or headed with --handoff."),
    ] = None,
    handoff: Annotated[
        bool,
        typer.Option(help="On escalation, pause for an operator on the live browser instead "
                     "of ending the run. Implies --headed unless --headless is given."),
    ] = False,
    state_db: Annotated[
        Path, typer.Option(help="Shared session state for --handoff (leases, interventions).")
    ] = DEFAULT_DB,
    assist: Annotated[
        bool,
        typer.Option(help="Allow one bounded model call to relocate a single safe step whose "
                     "target has moved. Only if the artifact declares assisted_fallback."),
    ] = False,
    assist_cassette: Annotated[
        Path | None,
        typer.Option(help="Reproduce a recorded assisted run offline, instead of calling a "
                     "model."),
    ] = None,
    evidence_root: Annotated[Path, typer.Option(help="Where run evidence goes.")] = Path(
        "evidence/runs"
    ),
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """Replay an approved capability deterministically, with no LLM.

    The JSON printed on stdout is the caller's channel and carries outputs in full.
    Evidence on disk carries them redacted. Exit codes: 0 success or business
    outcome, 1 failure, 3 escalated. With --handoff, an escalation opens an
    intervention and waits for `waypoint intervene take` / `return` from another shell.
    """
    import json
    from urllib.parse import quote

    from waypoint.artifact.schema import load, locate
    from waypoint.replay.engine import ReplayOptions
    from waypoint.replay.engine import replay as run_replay
    from waypoint.surface.ports import Action

    values = dict(_parse_inputs(inputs))

    def set_injection(surface: Any, origin: str) -> None:
        surface.act(Action("navigate", url=f"{origin}/console?inject={quote(inject or '')}"))

    from waypoint.replay.assist import Assistant, CassetteAssistant, RecordingAssistant

    assistant: Assistant | None = None
    recorder: RecordingAssistant | None = None
    if assist_cassette is not None:
        assistant = CassetteAssistant.load(assist_cassette)
    elif assist:
        from waypoint.replay.assist_claude import ClaudeAssistant

        recorder = RecordingAssistant(ClaudeAssistant())
        assistant = recorder

    cap = load(locate(root, capability_id, version))
    result = run_replay(
        cap,
        values,
        ReplayOptions(
            base_url=base_url,
            evidence_root=evidence_root,
            headed=handoff if headed is None else headed,
            approve=_operator_approval,  # asked only by attended capabilities
            after_preconditions=set_injection if inject else None,
            handoff=handoff,
            state_db=state_db,
            ledger_db=state_db,
            injected=inject,
            notify=_announce_intervention(state_db) if handoff else None,
            assist=assistant,
        ),
    )
    if recorder is not None and recorder.recorded and result.evidence_dir:
        from waypoint.policy.redactor import Redactor
        from waypoint.surface.sensitivity import Binding

        redactor = Redactor([Binding(n, value, cap.inputs.properties[n].sensitivity)
                             for n, value in values.items() if n in cap.inputs.properties])
        (Path(result.evidence_dir) / "assist_cassette.json").write_text(recorder.to_json(redactor))
    typer.echo(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    typer.secho(
        f"{result.status}: evidence in {result.evidence_dir}",
        fg=typer.colors.GREEN if result.exit_code == 0 else typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=result.exit_code)


@app.command()
def stability(
    capability_id: str,
    inputs: Annotated[
        list[str], typer.Option("--input", "-i", help="An input as name=value; repeatable.")
    ] = [],  # noqa: B006 - typer requires a literal default here
    cases: Annotated[
        Path | None,
        typer.Option(help="A YAML/JSON file of cases: inputs and what each should produce."),
    ] = None,
    runs: Annotated[int, typer.Option(help="Replays per case.")] = 5,
    version: Annotated[str | None, typer.Option(help="Defaults to the highest release.")] = None,
    base_url: Annotated[str | None, typer.Option(help="Sweep another origin.")] = None,
    allow_irreversible: Annotated[
        bool,
        typer.Option(help="Required to sweep a capability that commits something. Only "
                     "against a local fixture: a sweep repeats every step it measures."),
    ] = False,
    state_db: Annotated[Path, typer.Option(help="Where runs are recorded.")] = DEFAULT_DB,
    evidence_root: Annotated[Path, typer.Option(help="Where run evidence goes.")] = Path(
        "evidence/runs"
    ),
    report_root: Annotated[Path, typer.Option(help="Where the report goes.")] = Path(
        "evidence/stability"
    ),
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """Replay a capability many times and report how reliably it holds up.

    Every run is recorded, so `waypoint confidence` and an artifact's declared
    `min_confidence` gate are backed by measurements rather than by one green run.
    """
    import json

    from waypoint.artifact.schema import load, locate
    from waypoint.replay.engine import ReplayOptions
    from waypoint.replay.stability import Case, StabilityRefused, load_cases, run_sweep

    cap = load(locate(root, capability_id, version))
    declared = load_cases(cases) if cases is not None else []
    if inputs:
        declared.append(Case(inputs=dict(_parse_inputs(inputs)), name="cli"))
    if not declared:
        typer.secho("give --input or --cases: a sweep needs something to replay", err=True)
        raise typer.Exit(code=2)
    options = ReplayOptions(
        base_url=base_url, evidence_root=evidence_root,
        state_db=state_db, ledger_db=state_db,
        require_confidence=False,  # measuring is how a capability earns its confidence
       
    )
    try:
        report = run_sweep(cap, declared, runs=runs, options=options,
                           allow_irreversible=allow_irreversible, report_root=report_root)
    except StabilityRefused as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from None
    typer.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    typer.secho(report.summary(), err=True,
                fg=typer.colors.GREEN if report.verdict == "stable" else typer.colors.YELLOW)
    raise typer.Exit(code=0 if report.verdict in ("stable", "flaky") else 1)


@app.command()
def confidence(
    capability_id: str,
    version: Annotated[str | None, typer.Option(help="Defaults to the highest release.")] = None,
    state_db: Annotated[Path, typer.Option(help="Where runs are recorded.")] = DEFAULT_DB,
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """What this exact artifact has actually done: its recorded runs and what they support."""
    from waypoint.artifact.schema import load, locate
    from waypoint.replay.ledger import Ledger
    from waypoint.session.store import StateStore

    cap = load(locate(root, capability_id, version, release=False))
    ledger = Ledger(StateStore(state_db))
    status = ledger.confidence(cap)
    bar = cap.policy.min_confidence
    typer.echo(f"{cap.capability_id} {cap.version}: {status.summary()}")
    if status.runs:
        typer.echo(f"  median duration {status.median_ms} ms")
    for reason in status.reasons:
        typer.echo(f"  - {reason}")
    if bar is None:
        typer.echo("  no confidence bar declared; unattended replay is not gated")
    else:
        verdict = "meets" if status.meets(bar) else "does NOT meet"
        typer.echo(f"  declared bar {bar:.2f}: {verdict} it")
    raise typer.Exit(code=0 if bar is None or status.meets(bar) else 1)


@app.command()
def codegen(
    capability_id: str,
    target: Annotated[str, typer.Option(help="pytest (a regression test) or playwright "
                                             "(a readable page object).")] = "pytest",
    version: Annotated[str | None, typer.Option(help="Defaults to the highest release.")] = None,
    out: Annotated[Path | None, typer.Option(help="Write here instead of stdout.")] = None,
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """Emit runnable code from an approved artifact: a regression test, or a page object."""
    from waypoint.artifact.codegen import generate
    from waypoint.artifact.schema import load, locate

    cap = load(locate(root, capability_id, version, release=False))
    try:
        cases = root / cap.capability_id / "cases.yaml"
        if target == "pytest" and not cases.is_file():
            raise ValueError(f"no declared cases: add {cases} before generating regression tests")
        code = generate(cap, target, **({"cases_path": str(cases)} if target == "pytest" else {}))
    except ValueError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from None
    if out is None:
        typer.echo(code)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(code)
    typer.secho(f"written: {out}", err=True, fg=typer.colors.GREEN)


@app.command()
def approvals(
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
    db: Annotated[Path, typer.Option("--db", help="Shared session state (SQLite).")] = DEFAULT_DB,
) -> None:
    """The approval queue: drafts awaiting review, and runs waiting on a person's approval."""
    import re
    import time

    from waypoint.artifact.approval import approval_status
    from waypoint.artifact.schema import SEMVER, load

    typer.echo("drafts awaiting review:")
    drafts = 0
    for folder in sorted(p for p in root.glob("*") if p.is_dir()):
        for path in sorted(folder.glob("*.json")):
            if not re.match(SEMVER, path.stem):
                continue
            cap = load(path)
            status = approval_status(cap)
            if status.approved:
                continue
            drafts += 1
            gates = [r for r in status.reasons if "not approved" not in r]
            state = "approvable" if not gates else f"{len(gates)} open gate(s): {gates[0]}"
            typer.echo(f"  {cap.capability_id} {cap.version}  {state}")
            if not gates:
                typer.echo(f"    -> waypoint approve {cap.capability_id} --version {cap.version}")
    if not drafts:
        typer.echo("  none")

    typer.echo("runs waiting for approval:")
    waiting = []
    if db.exists():
        from waypoint.session.escalation import InterventionStore
        from waypoint.session.store import StateStore

        waiting = [iv for iv in InterventionStore(StateStore(db)).list(("open",))
                   if iv.reason_code in ("approval_required", "risk_exceeds_declared")]
    now = time.time()
    for iv in waiting:
        typer.echo(f"  {iv.id}  {iv.capability_id}@{iv.version}  {iv.step}  "
                   f"{iv.intent or ''!r}  {iv.message}  {int(now - iv.created_at)}s ago")
        typer.echo(f"    -> waypoint intervene take {iv.id}{_db_flag(db)}")
    if not waiting:
        typer.echo("  none")


intervene_app = typer.Typer(
    no_args_is_help=True,
    help="Operator queue: list, take, return, or abort an intervention.",
)
app.add_typer(intervene_app, name="intervene")

StateDb = Annotated[Path, typer.Option("--db", help="Shared session state (SQLite).")]


def _db_flag(db: Path) -> str:
    return "" if db == DEFAULT_DB else f" --db {db}"


def _queue(db: Path) -> Any:
    from waypoint.session.escalation import InterventionStore
    from waypoint.session.store import StateStore

    if not db.exists():
        typer.secho(f"no session state at {db}: nothing has escalated here", err=True)
        raise typer.Exit(code=1)
    return InterventionStore(StateStore(db))


def _refused(exc: Exception) -> typer.Exit:
    typer.secho(str(exc), fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


@intervene_app.command("list")
def intervene_list(
    everything: Annotated[bool, typer.Option("--all", help="Include closed ones.")] = False,
    db: StateDb = DEFAULT_DB,
) -> None:
    """Interventions waiting for, or held by, an operator."""
    import time
    from typing import get_args

    from waypoint.session.escalation import ACTIVE, Status

    rows = _queue(db).list(get_args(Status) if everything else ACTIVE) if db.exists() else []
    if not rows:
        typer.echo("no interventions")
        return
    now = time.time()
    for iv in rows:
        who = f"  by {iv.operator}" if iv.operator else ""
        typer.echo(f"{iv.id}  {iv.status:<8}  {iv.capability_id}@{iv.version}  "
                   f"{iv.step or '-'}  {iv.reason_code}  {int(now - iv.created_at)}s ago{who}")


@intervene_app.command("show")
def intervene_show(intervention_id: str, db: StateDb = DEFAULT_DB) -> None:
    """Everything an operator needs: where it stopped, why, and the evidence paths."""
    import json
    from dataclasses import asdict

    from waypoint.session.escalation import InterventionError

    queue = _queue(db)
    try:
        iv = queue.get(intervention_id)
    except InterventionError as exc:
        raise _refused(exc) from None
    data = {k: v for k, v in asdict(iv).items() if k != "operator_token"}
    lease = queue.leases.read(iv.session_id)
    if lease is not None:
        now = queue.store.clock()
        data["lease"] = {"holder": lease.effective_holder(now), "generation": lease.generation,
                         "expires_in_s": max(0, int(lease.expires_at - now))}
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


@intervene_app.command("take")
def intervene_take(
    intervention_id: str,
    operator: Annotated[str | None, typer.Option(help="Defaults to your login name.")] = None,
    ttl: Annotated[
        float, typer.Option(help="Seconds before your control lapses and the run escalates.")
    ] = 900.0,
    db: StateDb = DEFAULT_DB,
) -> None:
    """Take control of the live browser. The run waits, recording what you do."""
    import getpass

    from waypoint.session.escalation import InterventionError

    try:
        iv = _queue(db).take(intervention_id, operator or getpass.getuser(), ttl)
    except InterventionError as exc:
        raise _refused(exc) from None
    typer.echo(f"you have control of run {iv.run_id} ({iv.capability_id}@{iv.version}) "
               f"for {int(ttl)}s")
    typer.echo(f"  stopped at: {iv.step or '-'}  {iv.intent or ''}")
    typer.echo(f"  why:        {iv.reason_code}: {iv.message}")
    typer.echo(f"  when done:  waypoint intervene return {iv.id}{_db_flag(db)}")


@intervene_app.command("return")
def intervene_return(intervention_id: str, db: StateDb = DEFAULT_DB) -> None:
    """Hand control back. The run re-checks the screen and continues only where it can."""
    from waypoint.session.escalation import InterventionError

    try:
        _queue(db).give_back(intervention_id)
    except InterventionError as exc:
        raise _refused(exc) from None
    typer.echo("control returned; the run re-checks the screen before it continues")


@intervene_app.command("abort")
def intervene_abort(intervention_id: str, db: StateDb = DEFAULT_DB) -> None:
    """End the run. It exits escalated, with evidence."""
    from waypoint.session.escalation import InterventionError

    try:
        _queue(db).abort(intervention_id)
    except InterventionError as exc:
        raise _refused(exc) from None
    typer.echo("aborted; the run ends escalated")


def _intent_store(db: Path) -> Any:
    from waypoint.session.intents import IntentStore
    from waypoint.session.store import StateStore

    return IntentStore(StateStore(db))


@intervene_app.command("intents")
def intervene_intents(
    everything: Annotated[bool, typer.Option("--all", help="Include resolved ones.")] = False,
    db: StateDb = DEFAULT_DB,
) -> None:
    """Irreversible actions whose effect is unknown. Each blocks its operation."""
    import time
    from typing import get_args

    from waypoint.session.intents import UNRESOLVED, IntentState

    states = get_args(IntentState) if everything else UNRESOLVED
    rows = _intent_store(db).list(states) if db.exists() else []
    if not rows:
        typer.echo("no intents" if everything else "no unresolved intents")
        return
    now = time.time()
    for it in rows:
        how = f"  {it.resolution} by {it.resolved_by}" if it.resolved_by else ""
        how += "" if it.attempted_at_trusted else "  (attempt time untrusted: migrated)"
        typer.echo(f"{it.id}  {it.state:<11}  {it.capability_id}@{it.version}  {it.step}  "
                   f"run {it.run_id}  attempted {int(now - it.attempted_at)}s ago{how}")


@intervene_app.command("reconcile")
def intervene_reconcile(
    intent_id: str,
    outcome: Annotated[
        str, typer.Option(help="What you found in the application: completed or not-completed.")
    ],
    operator: Annotated[str | None, typer.Option(help="Defaults to your login name.")] = None,
    db: StateDb = DEFAULT_DB,
) -> None:
    """Record whether an unresolved irreversible action took effect. Unblocks it."""
    import getpass

    from waypoint.session.intents import IntentError

    if outcome not in ("completed", "not-completed"):
        typer.secho("--outcome must be completed or not-completed", err=True)
        raise typer.Exit(code=2)
    if not db.exists():
        typer.secho(f"no session state at {db}", err=True)
        raise typer.Exit(code=1)
    try:
        _intent_store(db).advance(intent_id, "reconciled", by=operator or getpass.getuser(),
                                  resolution=outcome.replace("-", "_"))
    except IntentError as exc:
        raise _refused(exc) from None
    typer.echo(f"reconciled as {outcome}; a new run with the same inputs will perform the "
               "action again")


catalog_app = typer.Typer(
    no_args_is_help=True,
    help="The agent-facing surface: approved capabilities an agent can call by name.",
)
app.add_typer(catalog_app, name="catalog")


def _catalog(root: Path, state_db: Path) -> Any:
    from waypoint.catalog.registry import Catalog
    from waypoint.replay.ledger import Ledger
    from waypoint.session.store import StateStore

    return Catalog(root, Ledger(StateStore(state_db)))


@catalog_app.command("list")
def catalog_list(
    state_db: Annotated[Path, typer.Option(help="Where runs are recorded.")] = DEFAULT_DB,
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """Every approved capability an agent may call, and what each needs and returns."""
    entries = _catalog(root, state_db).entries()
    if not entries:
        typer.echo("no approved capabilities; `waypoint approvals` shows what awaits review")
        return
    for entry in entries:
        flags = []
        if entry.requires_human:
            flags.append("stops for a person")
        if not entry.available:
            flags.append("UNAVAILABLE")
        typer.echo(f"{entry.name} {entry.version}  {entry.capability.name}"
                   + (f"  [{', '.join(flags)}]" if flags else ""))
        typer.echo(f"    takes    {', '.join(sorted(entry.capability.inputs.properties))}")
        typer.echo(f"    returns  {', '.join(sorted(entry.capability.outputs.properties))}"
                   or "    returns  nothing")
        if entry.capability.outcomes:
            typer.echo("    or       "
                       + ", ".join(o.name for o in entry.capability.outcomes))
        typer.echo(f"    runs     {entry.confidence.summary()}")
        if reason := entry.unavailable_reason():
            typer.secho(f"    blocked  {reason}", fg=typer.colors.YELLOW)


@catalog_app.command("tools")
def catalog_tools(
    fmt: Annotated[str, typer.Option("--format", help="anthropic or openai.")] = "anthropic",
    state_db: Annotated[Path, typer.Option(help="Where runs are recorded.")] = DEFAULT_DB,
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """The tool definitions an agent loads: typed arguments generated from each artifact."""
    import json

    if fmt not in ("anthropic", "openai"):
        typer.secho("--format must be 'anthropic' or 'openai'", err=True)
        raise typer.Exit(code=2)
    tools = _catalog(root, state_db).tools(fmt)
    typer.echo(json.dumps(tools, indent=2, ensure_ascii=False))


@catalog_app.command("invoke")
def catalog_invoke(
    name: str,
    args: Annotated[str, typer.Option(help='Arguments as JSON, e.g. \'{"member_id": "12345"}\'.')
                    ] = "{}",
    version: Annotated[str | None, typer.Option(help="Defaults to the highest release.")] = None,
    base_url: Annotated[str | None, typer.Option(help="Invoke against another origin.")] = None,
    headed: Annotated[bool | None, typer.Option("--headed/--headless",
                                              help="Show the browser; default with handoff.")
                      ] = None,
    handoff: Annotated[bool, typer.Option(help="Wait for an operator in the same session.")
                       ] = False,
    state_db: Annotated[Path, typer.Option(help="Session state and the run ledger.")] = DEFAULT_DB,
    evidence_root: Annotated[Path, typer.Option(help="Where run evidence goes.")] = Path(
        "evidence/runs"
    ),
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """Call a capability the way an agent would: by name, with typed arguments."""
    import json

    from waypoint.catalog.registry import Unavailable, UnknownCapability, result_for_agent
    from waypoint.replay.engine import ReplayOptions

    try:
        arguments = json.loads(args)
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a JSON object")
    except ValueError as exc:
        typer.secho(f"--args must be JSON: {exc}", err=True)
        raise typer.Exit(code=2) from None
    try:
        result = _catalog(root, state_db).invoke(
            name, arguments, version=version,
            options=ReplayOptions(base_url=base_url, headed=handoff if headed is None else headed,
                                  handoff=handoff,
                                  notify=_announce_intervention(state_db) if handoff else None,
                                  evidence_root=evidence_root, state_db=state_db,
                                  ledger_db=state_db),
        )
    except (UnknownCapability, Unavailable, ValueError) as exc:
        typer.secho(str(exc).strip("\'"), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2) from None
    typer.echo(json.dumps(result_for_agent(result), indent=2, ensure_ascii=False))
    raise typer.Exit(code=result.exit_code)


def main() -> None:
    from waypoint.envfile import load_dotenv

    load_dotenv()  # .env in the working directory; exported variables win
    app()


if __name__ == "__main__":
    main()
