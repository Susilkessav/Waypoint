"""Milestone A1 smoke tests: the package installs, the CLI resolves, the app serves."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

import waypoint

REQUEST_STALL_THRESHOLD = 1500
"""Well past the ~1000 requests that fill a 64 KiB pipe buffer (see conftest)."""


def _console_script() -> Path:
    """The installed `waypoint` executable that sits beside the running interpreter."""
    bin_dir = Path(sys.executable).parent
    return bin_dir / ("waypoint.exe" if os.name == "nt" else "waypoint")


def _run_console_script(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the installed console script from `cwd` with the repo hidden from sys.path.

    Running from a temporary directory with PYTHONPATH stripped is the point: if
    the test ran from the repo root, the source tree itself would satisfy the
    import and a broken installation would still pass.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run(
        [str(_console_script()), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_package_exposes_version() -> None:
    assert waypoint.__version__


def test_console_script_is_installed() -> None:
    exe = _console_script()
    assert exe.exists(), (
        f"`waypoint` console script missing at {exe}. Run `make install`. "
        "If it is present but failing to import, check that the editable .pth file "
        "in site-packages is not marked hidden -- CPython 3.13+ skips hidden .pth files."
    )


def test_installed_console_script_runs_outside_the_repo(tmp_path: Path) -> None:
    """The installed entry point must work with the source tree out of reach."""
    result = _run_console_script("version", cwd=tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.stdout.strip() == waypoint.__version__


def test_unimplemented_command_exits_loudly(tmp_path: Path) -> None:
    """A stub must fail visibly rather than appear to succeed."""
    result = _run_console_script("catalog", cwd=tmp_path)
    assert result.returncode == 2
    assert "not implemented" in result.stderr.lower()


def test_live_server_health(live_server: str) -> None:
    with urllib.request.urlopen(f"{live_server}/health", timeout=5) as resp:
        assert resp.status == 200
        payload = json.load(resp)
    assert payload["status"] == "ok"


@pytest.mark.timeout(120)
def test_live_server_survives_sustained_requests(live_server: str) -> None:
    """Regression: the server must not stall once its output exceeds a pipe buffer.

    Werkzeug logs a line per request. When output went to an undrained pipe the
    server blocked on write at roughly 1000 requests and every later request
    timed out. Logging to a file removes the ceiling.
    """
    for i in range(REQUEST_STALL_THRESHOLD):
        try:
            with urllib.request.urlopen(f"{live_server}/health", timeout=5) as resp:
                assert resp.status == 200
        except Exception as exc:  # noqa: BLE001 - the failure index is the diagnostic
            pytest.fail(f"server stopped responding after {i} requests: {exc!r}")
