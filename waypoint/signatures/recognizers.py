"""Declarative state signatures (R-OUT-1, R-RESUME-5).

One vocabulary serves as step checkpoints, pre- and postconditions and outcome
recognizers, for discovery and replay alike. Signatures read only the sanitized
``UISnapshot`` - never raw values - and can still assert *identity*: a bound input
reaches the sanitized snapshot as its binding placeholder, so ``name_ref:
$inputs.member_id`` matches exactly when the screen shows this member, and a
correct-looking screen for a different member does not match (T7). The recognizer
never sees 12345.

A missing input reference is an error, never a silent ``False`` or ``True``: an
``element_absent`` that passed because its reference could not be rendered would
be acting on the absence of evidence.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from waypoint.policy.redactor import binding_placeholder
from waypoint.surface.ports import Sensitivity, UIElement, UISnapshot, rank

INPUT_PREFIX = "$inputs."


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _input_name(ref: str) -> str:
    if not ref.startswith(INPUT_PREFIX) or len(ref) == len(INPUT_PREFIX):
        raise ValueError(f"references must name an input: {ref!r}")
    return ref.removeprefix(INPUT_PREFIX)


class ElementPredicate(_Model):
    """Matches one element of the sanitized snapshot. Every set field must hold."""

    role: str | None = None
    name: str | None = None
    name_ref: str | None = None
    name_contains: str | None = None
    anchor: str | None = None
    anchor_ref: str | None = None
    value_ref: str | None = None
    filled: bool | None = None
    frame: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not any((self.role, self.name is not None, self.name_ref, self.name_contains)):
            raise ValueError("an element predicate needs a role or a name constraint")
        for ref in (self.name_ref, self.anchor_ref, self.value_ref):
            if ref is not None:
                _input_name(ref)
        return self

    def refs(self) -> Iterator[str]:
        for ref in (self.name_ref, self.anchor_ref, self.value_ref):
            if ref is not None:
                yield _input_name(ref)

    def matches(self, e: UIElement, rendered: Mapping[str, str]) -> bool:
        def form(ref: str) -> str:
            name = _input_name(ref)
            if name not in rendered:
                raise KeyError(f"input {name!r} is not bound for this run")
            return rendered[name]

        checks = (
            self.frame is None or e.frame_path == self.frame,
            self.role is None or e.role == self.role,
            self.name is None or e.name == self.name,
            self.name_ref is None or e.name == form(self.name_ref),
            self.name_contains is None or self.name_contains in e.name,
            self.anchor is None or self.anchor in e.anchors,
            self.anchor_ref is None or form(self.anchor_ref) in e.anchors,
            self.value_ref is None or e.value == form(self.value_ref),
            self.filled is None or bool(e.value) == self.filled,
        )
        return all(checks)


class Predicate(_Model):
    """Exactly one of these keys is set; ``all``/``any``/``none`` nest."""

    element_exists: ElementPredicate | None = None
    element_absent: ElementPredicate | None = None
    text_contains: str | None = None
    url_matches: str | None = None
    all: tuple[Predicate, ...] | None = None
    any: tuple[Predicate, ...] | None = None
    none: tuple[Predicate, ...] | None = None

    @model_validator(mode="after")
    def _one_key(self) -> Self:
        set_keys = [k for k, v in self.__dict__.items() if v is not None]
        if len(set_keys) != 1:
            raise ValueError(f"a predicate sets exactly one key, got {set_keys or 'none'}")
        if self.url_matches is not None:
            try:
                re.compile(self.url_matches)
            except re.error as exc:  # not a ValueError, so pydantic would not catch it
                raise ValueError(f"invalid url_matches pattern: {exc}") from None
        for group in (self.all, self.any, self.none):
            if group is not None and not group:
                raise ValueError("all/any/none need at least one predicate")
        return self

    def children(self) -> tuple[Predicate, ...]:
        return self.all or self.any or self.none or ()

    def walk(self) -> Iterator[Predicate]:
        yield self
        for child in self.children():
            yield from child.walk()

    def asserts_content(self) -> bool:
        """True if some positive branch checks what is on screen (R-PKG-5 (c)).

        URL changes and absences prove something moved, not that the intended
        thing happened, so they do not count on their own.
        """
        if self.element_exists is not None or self.text_contains is not None:
            return True
        if self.all is not None or self.any is not None:
            branches = self.all if self.all is not None else self.any
            assert branches is not None
            checks = [b.asserts_content() for b in branches]
            return any(checks) if self.all is not None else all(checks)
        return False

    def evaluate(self, snap: UISnapshot, rendered: Mapping[str, str]) -> bool:
        if self.element_exists is not None:
            return any(self.element_exists.matches(e, rendered) for e in snap.elements)
        if self.element_absent is not None:
            return not any(self.element_absent.matches(e, rendered) for e in snap.elements)
        if self.text_contains is not None:
            needle = self.text_contains
            return needle in snap.title or any(needle in e.name for e in snap.elements)
        if self.url_matches is not None:
            urls = [snap.url, *(u for _, u in snap.frame_urls)]
            return any(re.search(self.url_matches, u) for u in urls)
        if self.all is not None:
            return all(p.evaluate(snap, rendered) for p in self.all)
        if self.any is not None:
            return any(p.evaluate(snap, rendered) for p in self.any)
        assert self.none is not None
        return not any(p.evaluate(snap, rendered) for p in self.none)


class Signature(_Model):
    """A named, reviewable state. The name lives in the artifact's ``signatures`` map."""

    description: str = ""
    match: Predicate

    def refs(self) -> set[str]:
        return {
            ref
            for p in self.match.walk()
            for ep in (p.element_exists, p.element_absent)
            if ep is not None
            for ref in ep.refs()
        }

    def evaluate(self, snap: UISnapshot, rendered: Mapping[str, str]) -> bool:
        return self.match.evaluate(snap, rendered)


def rendered_inputs(
    values: Mapping[str, str], sensitivities: Mapping[str, Sensitivity]
) -> dict[str, str]:
    """How each input appears in a sanitized snapshot.

    Inputs above public are rewritten to their binding placeholder by the redactor,
    so that is what a signature must compare against; public inputs appear as-is.
    """
    return {
        name: binding_placeholder(name)
        if rank(sensitivities.get(name, "internal")) >= rank("internal")
        else value
        for name, value in values.items()
    }
