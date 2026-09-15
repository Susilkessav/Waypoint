"""VaultSecretBroker: same contract as SecretBroker, sourced from the OS keychain first.

Mirrors tests/test_secrets.py's pinned behaviors (authorization by label, non-leaking errors)
against the vault-backed broker, plus the one new behavior: falling back to the environment
when keyring has nothing provisioned.
"""

from __future__ import annotations

import pytest

from waypoint.policy.secrets import SecretSpec, VaultSecretBroker

SPEC = {"test": SecretSpec("TEST_CREDENTIAL", ("Password",))}


class _FakeKeyring:
    def __init__(self, passwords: dict[tuple[str, str], str]) -> None:
        self._passwords = passwords

    def get_password(self, service: str, key: str) -> str | None:
        return self._passwords.get((service, key))


def _patch_keyring(monkeypatch, password: str | None) -> None:
    import keyring

    passwords = {("waypoint", "TEST_CREDENTIAL"): password} if password else {}
    monkeypatch.setattr(keyring, "get_password", _FakeKeyring(passwords).get_password)


def test_keyring_value_is_preferred_over_environ(monkeypatch) -> None:
    _patch_keyring(monkeypatch, "from-keyring")
    broker = VaultSecretBroker(SPEC, {"TEST_CREDENTIAL": "from-environ"})
    writes: list[str] = []
    broker.inject("$secrets.test", ("Password",), writes.append)
    assert writes == ["from-keyring"]


def test_falls_back_to_environ_when_keyring_has_nothing(monkeypatch) -> None:
    _patch_keyring(monkeypatch, None)
    broker = VaultSecretBroker(SPEC, {"TEST_CREDENTIAL": "from-environ"})
    writes: list[str] = []
    broker.inject("$secrets.test", ("Password",), writes.append)
    assert writes == ["from-environ"]


def test_wrong_label_is_still_refused(monkeypatch) -> None:
    _patch_keyring(monkeypatch, "from-keyring")
    broker = VaultSecretBroker(SPEC, {})
    with pytest.raises(ValueError, match="not authorized"):
        broker.inject("$secrets.test", ("Search",), lambda v: None)


def test_missing_everywhere_and_browser_exception_never_echo_value(monkeypatch) -> None:
    _patch_keyring(monkeypatch, None)
    with pytest.raises(ValueError, match="unavailable"):
        VaultSecretBroker(SPEC, {}).inject("$secrets.test", ("Password",), lambda v: None)

    def fail(value: str) -> None:
        raise RuntimeError(f"Failed typing {value}")

    _patch_keyring(monkeypatch, "dont-leak-me")
    with pytest.raises(ValueError) as error:
        VaultSecretBroker(SPEC, {}).inject("$secrets.test", ("Password",), fail)
    assert "dont-leak-me" not in str(error.value)
    assert error.value.__suppress_context__


def test_locked_provider_fails_closed_without_echoing_payload(monkeypatch):
    import keyring

    def unavailable(*args):
        raise RuntimeError("backend exposed dont-leak-me")

    monkeypatch.setattr(keyring, "get_password", unavailable)
    writes = []
    with pytest.raises(ValueError, match="credential provider unavailable") as error:
        VaultSecretBroker(SPEC, {"TEST_CREDENTIAL": "fallback"}).inject(
            "$secrets.test", ("Password",), writes.append)
    assert "dont-leak-me" not in str(error.value)
    assert writes == []
