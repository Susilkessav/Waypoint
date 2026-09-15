"""Code generated from an artifact: a regression test that runs, and a page object that reads.

The generated test is the artifact's contract in CI - so this file runs it, against the
fixture, and expects it to pass. The page object is for a person to read; it is checked for
the things a reader needs: the anchored locators spelled out, the frame nesting made
explicit, and a header saying plainly that it has none of the engine's guardrails.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from waypoint.artifact.codegen import generate, page_object, regression_test
from waypoint.artifact.schema import load

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "capabilities" / "lookup_member_balance" / "1.2.0.json"


@pytest.fixture(scope="module")
def capability():
    return load(ARTIFACT)


def test_the_page_object_is_valid_python_that_reads_like_the_flow(capability) -> None:
    code = page_object(capability)
    ast.parse(code)

    assert "class LookupMemberBalancePage:" in code
    assert 'content = self.page.frame_locator("frame[name=content]")' in code, "frames, named"
    assert 'accounts = self.page.frame_locator("frame[name=content]").frame_locator(' in code
    assert 'get_by_role(\'row\').filter(has=content.get_by_role("cell", name=self.member_id' \
        in code, "the anchored locator, not a brittle path"
    assert "expect(" in code, "checkpoints become assertions"
    assert "never point it at a real system" in code.lower()
    assert "$4,281.19" not in code and "changeme" not in code, "no values, ever"


def test_the_page_object_includes_sign_on_and_never_embeds_a_credential(capability) -> None:
    """Sign-on is a precondition remedy, not a step - a reader still needs to see it."""
    code = page_object(capability)
    assert "def establish_" in code, "the remedy the engine would run is generated too"
    assert 'self.secrets["meridian_user"]' in code
    assert "operator1" not in code and "changeme" not in code


def test_the_regression_test_is_generated_from_the_artifact(capability) -> None:
    code = regression_test(capability)
    ast.parse(code)
    assert "capabilities/lookup_member_balance/1.2.0.json" in code
    assert "capabilities/lookup_member_balance/cases.yaml" in code
    assert "expect_outcome" in code, "a business outcome is part of the contract"
    assert "member_not_found" in code


def test_an_unknown_target_is_refused(capability) -> None:
    with pytest.raises(ValueError, match="unknown target"):
        generate(capability, "selenium")


@pytest.mark.browser
@pytest.mark.parametrize("version", ["1.2.0", "1.3.0"])
def test_the_generated_regression_test_passes_against_the_fixture(version, live_server: str,
                                                                  tmp_path: Path) -> None:
    """The point of generating it: put it in CI and drift fails a build."""
    path = tmp_path / "test_generated_lookup.py"
    path.write_text(regression_test(load(ARTIFACT.with_name(f"{version}.json"))))
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(path), "-q", "-p", "no:cacheprovider",
         "--no-header"],
        cwd=REPO, capture_output=True, text=True,
        env={**os.environ, "WAYPOINT_BASE_URL": live_server, "MERIDIAN_USER": "operator1",
             "MERIDIAN_PASS": "changeme", "PYTHONPATH": str(REPO), "WAYPOINT_NO_DOTENV": "1"},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "passed" in completed.stdout


def test_codegen_without_declared_cases_reports_the_missing_contract():
    from typer.testing import CliRunner

    from waypoint.cli import app

    result = CliRunner().invoke(app, ["codegen", "open_sub_account", "--version", "1.0.0"])
    assert result.exit_code == 2
    assert "no declared cases" in result.output
