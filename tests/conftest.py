"""Shared fixtures.

The live-server fixture runs the target app in a subprocess on a random free
port. A subprocess rather than a thread because later milestones drive this app
with Playwright, and a real process boundary avoids the threading interactions
that make browser tests flaky.

Server output goes to a log file, never to an unread pipe. Werkzeug logs one
line per request, so a pipe nobody drains fills its ~64 KiB buffer after roughly
a thousand requests and the server then blocks forever on write. A file has no
such limit, and it keeps the output for diagnosing startup failures.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

# The CLI loads a developer's .env (API key, credentials); no test may depend on one.
os.environ.setdefault("WAYPOINT_NO_DOTENV", "1")

READY_TIMEOUT_S = 20.0
LOG_TAIL_CHARS = 4000


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _log_tail(log_path: Path) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(server log unreadable)"
    return text[-LOG_TAIL_CHARS:] if text else "(server log empty)"


def _wait_until_ready(proc: subprocess.Popen[bytes], base_url: str, log_path: Path) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"target_app exited with code {proc.returncode} during startup.\n"
                f"--- {log_path} ---\n{_log_tail(log_path)}"
            )
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=0.5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.1)
    raise RuntimeError(
        f"target_app was not ready within {READY_TIMEOUT_S}s at {base_url}.\n"
        f"--- {log_path} ---\n{_log_tail(log_path)}"
    )


@pytest.fixture(scope="session")
def target_app_log(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Path to the target app's log file, retained for the session."""
    return tmp_path_factory.mktemp("target_app") / "server.log"


@pytest.fixture(scope="session")
def live_server(target_app_log: Path) -> Iterator[str]:
    """Start the target app on a random free port; yield its base URL."""
    port = _free_port()
    env = {
        **os.environ,
        "PORT": str(port),
        "MERIDIAN_USER": os.environ.get("MERIDIAN_USER", "operator1"),
        "MERIDIAN_PASS": os.environ.get("MERIDIAN_PASS", "changeme"),
    }
    base_url = f"http://127.0.0.1:{port}"

    with target_app_log.open("wb") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "target_app"],
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_until_ready(proc, base_url, target_app_log)
            yield base_url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


@pytest.fixture
def fresh_app(live_server: str) -> str:
    """The live server with its server-side state cleared - sub-accounts outlive sessions."""
    request = urllib.request.Request(f"{live_server}/_fixture/reset", data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=10):
        pass
    return live_server


@pytest.fixture(autouse=True)
def _server_state_is_per_test(request: pytest.FixtureRequest) -> None:
    """Every test that touches the live server starts with no sub-accounts.

    Server-side state is the point of the fixture - a later run must see an earlier
    commit - so without this, a test that opens accounts silently changes what the next
    test's grids contain, and results depend on file order.
    """
    if "live_server" not in request.fixturenames:
        return
    base = request.getfixturevalue("live_server")
    reset = urllib.request.Request(f"{base}/_fixture/reset", data=b"", method="POST")
    with urllib.request.urlopen(reset, timeout=10):
        pass
