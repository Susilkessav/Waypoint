"""Environment-backed credentials, delivered directly to a verified field."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretSpec:
    env_var: str
    labels: tuple[str, ...]


DEFAULT_SPECS = {
    "meridian_user": SecretSpec("MERIDIAN_USER", ("User ID",)),
    "meridian_password": SecretSpec("MERIDIAN_PASS", ("Password",)),
}


class SecretBroker:
    def __init__(
        self,
        specs: Mapping[str, SecretSpec] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.specs = dict(DEFAULT_SPECS if specs is None else specs)
        self._environ = os.environ if environ is None else environ

    def _lookup(self, spec: SecretSpec) -> str | None:
        return self._environ.get(spec.env_var)

    def inject(self, reference: str, labels: tuple[str, ...], write: Callable[[str], None]) -> None:
        if not reference.startswith("$secrets."):
            raise ValueError("credential must use a secret reference")
        spec = self.specs.get(reference.removeprefix("$secrets."))
        if spec is None or not set(spec.labels).intersection(labels):
            raise ValueError("secret reference is not authorized for this field")
        value = self._lookup(spec)
        if not value:
            raise ValueError("required credential is unavailable")
        try:
            write(value)
        except Exception:
            # Browser exceptions can echo typed values. Never preserve that payload.
            raise ValueError("credential injection failed") from None


class VaultSecretBroker(SecretBroker):
    """Reads from the OS keychain via ``keyring`` first, falling back to the environment.

    Same authorization and non-leaking error contract as ``SecretBroker`` - only ``_lookup``
    changes, so a machine with nothing provisioned in its keychain still works off ``.env``.
    """

    def __init__(
        self,
        specs: Mapping[str, SecretSpec] | None = None,
        environ: Mapping[str, str] | None = None,
        service: str = "waypoint",
    ) -> None:
        super().__init__(specs, environ)
        self.service = service

    def _lookup(self, spec: SecretSpec) -> str | None:
        import keyring

        try:
            value = keyring.get_password(self.service, spec.env_var)
        except Exception:
            # A locked/unavailable backend is different from a missing entry. Fail closed,
            # and never echo a provider payload that may contain credential material.
            raise ValueError("credential provider unavailable") from None
        return value if value else super()._lookup(spec)
