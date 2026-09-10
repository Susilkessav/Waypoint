"""Typed locator contracts and the surface-independent R-LOC resolution ladder."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TextTarget(Model):
    role: str
    text: str | None = None
    text_ref: str | None = None
    exact: Literal[True] = True

    @model_validator(mode="after")
    def valid_text(self) -> Self:
        if (self.text is None) == (self.text_ref is None):
            raise ValueError("specify exactly one of text or text_ref")
        if self.text_ref is not None and not self.text_ref.startswith("$inputs."):
            raise ValueError("locator references must name inputs")
        return self

    def render(self, inputs: Mapping[str, str]) -> str:
        if self.text_ref is None:
            return self.text or ""
        key = self.text_ref.removeprefix("$inputs.")
        if key not in inputs:
            raise ValueError("missing locator input binding")
        return inputs[key]


class Identity(Model):
    relation: Literal["same_row", "self"] = "same_row"
    target: TextTarget


class Candidate(Model):
    tier: int = Field(ge=1, le=5)
    kind: Literal["role_name", "label", "anchored", "structural", "visual"]
    frame_path: tuple[str, ...] = ("main",)
    role: str | None = None
    name: str | None = None
    label: str | None = None
    anchor: TextTarget | None = None
    column: str | None = None
    path: str | None = None
    identity: Identity | None = None
    bbox: tuple[int, int, int, int] | None = None

    @property
    def is_positional(self) -> bool:
        return self.tier >= 4

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        kinds = {"role_name": 1, "label": 2, "anchored": 3, "structural": 4, "visual": 5}
        if kinds[self.kind] != self.tier:
            raise ValueError("kind and tier disagree")
        if self.kind == "role_name" and (not self.role or self.name is None):
            raise ValueError("role_name requires role and name")
        if self.kind == "label" and not self.label:
            raise ValueError("label candidate requires a label")
        if self.kind == "anchored" and (self.anchor is None or not self.role):
            raise ValueError("anchored candidate requires anchor and target role")
        if self.kind == "structural" and not self.path:
            raise ValueError("structural candidate requires a path")
        if self.kind == "visual" and self.bbox is None:
            raise ValueError("visual diagnostic requires a bounding box")
        return self


class Diagnostic(Model):
    candidate: Candidate
    matches_at_record: int
    reason: str


class LocatorBundle(Model):
    recorded_tier: int
    candidates: tuple[Candidate, ...]
    diagnostics: tuple[Diagnostic, ...] = ()

    @model_validator(mode="after")
    def validate_ladder(self) -> Self:
        if not self.candidates or not any(c.tier <= 3 for c in self.candidates):
            raise ValueError("a web bundle needs at least one semantic candidate")
        tiers = [c.tier for c in self.candidates]
        if tiers != sorted(tiers) or self.recorded_tier != tiers[0]:
            raise ValueError("candidates must be ordered by recorded tier")
        if any(c.is_positional and c.identity is None for c in self.candidates):
            raise ValueError("positional candidates require identity assertions")
        if any(c.kind == "visual" for c in self.candidates):
            raise ValueError("visual locators are diagnostics only on web")
        return self


@dataclass(frozen=True)
class Found:
    ref: str
    tier: int
    degraded: bool = False


@dataclass(frozen=True)
class Ambiguous:
    reason: str = "multiple_matches"
    tier: int | None = None


@dataclass(frozen=True)
class NotFound:
    reason: str = "no_match"


Resolution: TypeAlias = Found | Ambiguous | NotFound


class Matcher(Protocol):
    def matches(self, candidate: Candidate, inputs: Mapping[str, str]) -> list[str]: ...
    def identity_matches(
        self, candidate: Candidate, ref: str, inputs: Mapping[str, str]
    ) -> bool: ...


def resolve(
    bundle: LocatorBundle,
    matcher: Matcher,
    inputs: Mapping[str, str],
    event: Callable[[dict[str, object]], None],
) -> Resolution:
    ambiguous = False
    for candidate in bundle.candidates:
        if candidate.is_positional and ambiguous:
            return Ambiguous("positional_blocked_by_unresolved_ambiguity", candidate.tier)
        hits = matcher.matches(candidate, inputs)
        if len(hits) > 1:
            ambiguous = True
        elif len(hits) == 1:
            if candidate.is_positional and not matcher.identity_matches(candidate, hits[0], inputs):
                event({"event": "positional_identity_failed", "tier": candidate.tier})
                continue
            degraded = candidate.tier > bundle.recorded_tier
            if degraded:
                event(
                    {
                        "event": "locator_degradation",
                        "recorded_tier": bundle.recorded_tier,
                        "resolved_tier": candidate.tier,
                    }
                )
            return Found(hits[0], candidate.tier, degraded)
    return Ambiguous() if ambiguous else NotFound()


def compile_bundle(
    candidates: list[Candidate], target_ref: str, matcher: Matcher, inputs: Mapping[str, str]
) -> LocatorBundle:
    usable: list[Candidate] = []
    diagnostics: list[Diagnostic] = []
    for candidate in sorted(candidates, key=lambda c: c.tier):
        hits = matcher.matches(candidate, inputs) if candidate.kind != "visual" else []
        identity_ok = not candidate.is_positional or (
            candidate.identity is not None
            and matcher.identity_matches(candidate, target_ref, inputs)
        )
        if candidate.kind != "visual" and hits == [target_ref] and identity_ok:
            usable.append(candidate)
        else:
            diagnostics.append(
                Diagnostic(
                    candidate=candidate,
                    matches_at_record=len(hits),
                    reason="not_unique_target_or_unverified_identity",
                )
            )
    if not usable:
        raise ValueError("no unique verified locator for target")
    return LocatorBundle(
        recorded_tier=usable[0].tier, candidates=tuple(usable), diagnostics=tuple(diagnostics)
    )
