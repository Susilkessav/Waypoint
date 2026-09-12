"""The CLI's .env loader: an exported variable wins, and blank values never mask one."""

from __future__ import annotations

from pathlib import Path

from waypoint.envfile import load_dotenv, parse


def test_parse_handles_comments_quotes_and_export() -> None:
    text = ("# a comment\n\nexport A=1\nB=\"two words\"\nC='x'\nD=plain # trailing\n"
            "not a line\n9BAD=x\nE=\n")
    assert parse(text) == {"A": "1", "B": "two words", "C": "x", "D": "plain", "E": ""}


def test_an_exported_variable_wins_and_blank_values_are_skipped(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("KEEP=from-file\nNEW=from-file\nANTHROPIC_API_KEY=\n")
    env = {"KEEP": "from-shell"}
    assert load_dotenv(env_file, env) == ["NEW"]
    assert env == {"KEEP": "from-shell", "NEW": "from-file"}


def test_loading_can_be_turned_off(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("NEW=from-file\n")
    env = {"WAYPOINT_NO_DOTENV": "1"}
    assert load_dotenv(env_file, env) == []
    assert "NEW" not in env


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path / "absent", {}) == []
