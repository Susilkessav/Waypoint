"""Command-line entry point.

Every subcommand is declared here from the start so the command surface is
visible and documented, but each raises until its milestone lands. A stub that
exits loudly is preferable to a command that silently does nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from waypoint import __version__

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
    version: Annotated[str | None, typer.Option(help="Defaults to the highest release.")] = None,
    variant: Annotated[str, typer.Option(help="Tenant variant to approve.")] = "base",
    note: Annotated[str | None, typer.Option(help="Why this is approved.")] = None,
    approver: Annotated[str | None, typer.Option(help="Defaults to the OS user.")] = None,
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """Approve a draft artifact for replay, after checking every approval gate."""
    import getpass

    from waypoint.artifact.approval import ApprovalBlocked
    from waypoint.artifact.approval import approve as approve_artifact
    from waypoint.artifact.schema import content_hash, load, locate

    path = locate(root, capability_id, version)
    cap = load(path)
    try:
        approved = approve_artifact(
            cap, approver=approver or getpass.getuser(), note=note, variant=variant
        )
    except ApprovalBlocked as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None
    path.write_text(approved.to_json())
    typer.echo(f"approved {capability_id} {cap.version} [{variant}] {content_hash(approved)}")


_SENSITIVITIES = ("public", "internal", "pii", "secret")
_TYPES = ("string", "enum", "money")


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
        highest = locate(root, capability_id).stem.partition("-")[0]
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
            typer.secho(f"cannot use the Anthropic API ({type(exc).__name__}): export "
                        "ANTHROPIC_API_KEY, or reproduce a recorded run with --llm cassette",
                        err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from None
        recording = Cassette(model=model, goal=goal)
        decider = RecordingDecider(live, recording)
    else:
        typer.secho("--llm must be 'anthropic' or 'cassette'", err=True)
        raise typer.Exit(code=2)

    result = run_discovery(decider, DiscoveryOptions(
        capability_id=capability_id, goal=goal, entry=entry, inputs=inputs, outputs=outputs,
        max_steps=max_steps, evidence_root=evidence_root, headed=headed,
        approve=_operator_approval,
    ))
    if recording is not None:
        recording.save(result.run_dir / "cassette.json")
    t = result.transcript
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
    headed: Annotated[bool, typer.Option(help="Show the browser.")] = False,
    evidence_root: Annotated[Path, typer.Option(help="Where run evidence goes.")] = Path(
        "evidence/runs"
    ),
    root: Annotated[Path, typer.Option(help="Capabilities directory.")] = CAPABILITIES,
) -> None:
    """Replay an approved capability deterministically, with no LLM.

    The JSON printed on stdout is the caller's channel and carries outputs in full.
    Evidence on disk carries them redacted. Exit codes: 0 success or business
    outcome, 1 failure, 3 escalated.
    """
    import json
    from urllib.parse import quote

    from waypoint.artifact.schema import load, locate
    from waypoint.replay.engine import ReplayOptions
    from waypoint.replay.engine import replay as run_replay
    from waypoint.surface.ports import Action

    values: dict[str, str] = {}
    for item in inputs:
        name, sep, value = item.partition("=")
        if not sep or not name:
            typer.secho(f"--input expects name=value, got {name or item!r}", err=True)
            raise typer.Exit(code=2)
        values[name.strip()] = value

    def set_injection(surface: Any, origin: str) -> None:
        surface.act(Action("navigate", url=f"{origin}/console?inject={quote(inject or '')}"))

    cap = load(locate(root, capability_id, version))
    result = run_replay(
        cap,
        values,
        ReplayOptions(
            base_url=base_url,
            evidence_root=evidence_root,
            headed=headed,
            after_preconditions=set_injection if inject else None,
        ),
    )
    typer.echo(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    typer.secho(
        f"{result.status}: evidence in {result.evidence_dir}",
        fg=typer.colors.GREEN if result.exit_code == 0 else typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=result.exit_code)


@app.command()
def intervene() -> None:
    """Operator queue: list, take, return, or abort an intervention."""
    raise NotYetImplemented("intervene", "A7")


@app.command()
def catalog() -> None:
    """List and invoke approved capabilities."""
    raise NotYetImplemented("catalog", "C2")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
