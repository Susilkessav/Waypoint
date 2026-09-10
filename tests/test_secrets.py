"""A4 secret injection, including errors and the persisted showcase boundary."""

from pathlib import Path

import pytest

from waypoint.policy.redactor import Redactor, Sink
from waypoint.policy.secrets import SecretBroker, SecretSpec
from waypoint.surface.sensitivity import ElementFacts, SensitivityClassifier


def test_broker_injects_only_into_its_named_field():
    broker = SecretBroker(
        {"test": SecretSpec("TEST_CREDENTIAL", ("Password",))},
        {"TEST_CREDENTIAL": "synthetic-credential"},
    )
    writes = []
    broker.inject("$secrets.test", ("Password",), writes.append)
    assert writes == ["synthetic-credential"]
    with pytest.raises(ValueError, match="not authorized"):
        broker.inject("$secrets.test", ("Search",), writes.append)
    assert len(writes) == 1


def test_missing_secret_and_browser_exception_never_echo_value():
    spec = {"test": SecretSpec("TEST_CREDENTIAL", ("Password",))}
    with pytest.raises(ValueError, match="unavailable"):
        SecretBroker(spec, {}).inject("$secrets.test", ("Password",), lambda v: None)

    def fail(value):
        raise RuntimeError(f"Failed typing {value}")

    with pytest.raises(ValueError) as error:
        SecretBroker(spec, {"TEST_CREDENTIAL": "dont-leak-me"}).inject(
            "$secrets.test", ("Password",), fail
        )
    assert "dont-leak-me" not in str(error.value)
    assert error.value.__suppress_context__


def test_remembered_credentials_are_hidden_at_every_sink():
    redactor, classifier = Redactor(), SensitivityClassifier()
    redactor.remember_secret("synthetic-credential")
    classifier.remember_secret("synthetic-credential")
    cls = classifier.classify(ElementFacts("status", "Echo synthetic-credential"))
    for sink in Sink:
        assert "synthetic-credential" not in redactor.text(
            "Echo synthetic-credential", cls.name, sink
        )


def test_credential_echoed_in_url_is_hidden_even_from_stdout_and_caller():
    redactor = Redactor()
    redactor.remember_secret("credential")
    for sink in Sink:
        result = redactor.url(
            "http://user:credential@localhost/console/%63redential?echo=credential#credential",
            sink,
        )
        assert "credential" not in result
        assert "%63redential" not in result
        assert "user:" not in result


def test_fixture_password_is_not_persisted_in_artifacts_or_evidence():
    repo = Path(__file__).resolve().parents[1]
    for directory in (repo / "capabilities", repo / "evidence"):
        for file in directory.rglob("*"):
            if file.is_file():
                assert b"changeme" not in file.read_bytes(), f"credential persisted in {file}"
