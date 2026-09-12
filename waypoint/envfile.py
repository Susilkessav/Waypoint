"""Load a local ``.env`` into the environment for the CLI.

Only names that are not already set: an exported variable always wins. Values are
never printed or logged - the file holds the API key. Empty values are skipped, so the
blank ``ANTHROPIC_API_KEY=`` a fresh copy of ``.env.example`` carries cannot mask a
real exported key. ``WAYPOINT_NO_DOTENV`` turns loading off; the test suite sets it so
no test depends on a developer's file.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse(text: str) -> dict[str, str]:
    """``NAME=value`` lines; comments, blank lines and malformed lines are ignored."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        name = name.strip()
        if not sep or not _NAME.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def load_dotenv(
    path: Path = Path(".env"), environ: MutableMapping[str, str] | None = None
) -> list[str]:
    """Set what the file defines and the environment lacks. Returns the names set."""
    env = os.environ if environ is None else environ
    if env.get("WAYPOINT_NO_DOTENV"):
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []  # no file (or unreadable): the environment is all there is
    loaded: list[str] = []
    for name, value in parse(text).items():
        if value and name not in env:
            env[name] = value
            loaded.append(name)
    return loaded
