"""Command-line entry point.

Every subcommand is declared here from the start so the command surface is
visible and documented, but each raises until its milestone lands. A stub that
exits loudly is preferable to a command that silently does nothing.
"""

from __future__ import annotations

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


@app.command()
def approve() -> None:
    """Promote a draft artifact to approved after review."""
    raise NotYetImplemented("approve", "A5")


@app.command()
def replay() -> None:
    """Execute an approved capability deterministically, with no LLM."""
    raise NotYetImplemented("replay", "A5")


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
