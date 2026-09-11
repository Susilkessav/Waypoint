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


@app.command()
def discover() -> None:
    """Drive a goal with an LLM and compile the result into a draft artifact."""
    raise NotYetImplemented("discover", "A6")


@app.command()
def compile() -> None:  # noqa: A001 - matches the user-facing command name
    """Recompile a saved transcript into an artifact."""
    raise NotYetImplemented("compile", "A6")


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
