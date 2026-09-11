"""Authoring convenience: a shared library of signatures (PLAN.md R-PKG-1).

Nothing reads this at replay. Authors and the compiler *copy* definitions into each
artifact's own ``signatures`` block, so a later edit to the library cannot change
how an artifact that was already approved behaves (T17). Reproducibility beats DRY:
a contract that points at mutable external state is not a contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

import yaml

from waypoint.signatures.recognizers import Signature

LIBRARY_PATH = Path(__file__).with_name("library.yaml")


def load_library(path: Path = LIBRARY_PATH) -> dict[str, Signature]:
    raw = yaml.safe_load(path.read_text()) or {}
    return {name: Signature.model_validate(body) for name, body in raw.items()}


def inline(names: Iterable[str], library: Mapping[str, Signature]) -> dict[str, Signature]:
    """Copies of the named signatures, ready to embed in an artifact."""
    wanted = list(dict.fromkeys(names))
    missing = [n for n in wanted if n not in library]
    if missing:
        raise KeyError(f"not in the signature library: {missing}")
    return {n: library[n].model_copy(deep=True) for n in wanted}
