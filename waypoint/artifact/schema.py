"""The capability artifact: a typed, versioned, reviewable contract (PLAN.md 6.8, 6.9).

Two kinds of rule live here, deliberately separated:

* Structural rules are model validators. An artifact that breaks one cannot even be
  loaded: a literal value in a step, a reference to an undeclared input or to a
  signature the artifact does not itself carry, a retried irreversible step with no
  way to check whether it already happened.
* Approval gates (R-PKG-3) are computed by ``approval_gates()`` from content, every
  time - at approval and again at every replay. A loadable draft may fail them; it
  just cannot be approved, or run as approved. No stored counter is ever trusted.

``content_hash()`` is what approval binds to (R-PKG-2). It covers everything that
affects execution - every key except ``provenance`` - so editing a step, a locator
or an inlined signature after approval makes the artifact unapproved.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waypoint.policy.engine import Risk
from waypoint.signatures.recognizers import ElementPredicate, Signature
from waypoint.surface.locators import Candidate, LocatorBundle, TextTarget
from waypoint.surface.ports import Sensitivity
from waypoint.surface.sensitivity import PATTERNS

SCHEMA_VERSION = "1.0.0"
IDENT = r"^[a-z][a-z0-9_]*$"
SEMVER = r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$"
_INPUT_REF = re.compile(r"^\$inputs\.([a-z][a-z0-9_]*)$")
_SECRET_REF = re.compile(r"^\$secrets\.([a-z][a-z0-9_]*)$")
_TEMPLATE_VAR = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_LITERAL_ID = re.compile(r"\d{3,}")
StepAction = Literal["navigate", "click", "type", "select", "key", "wait_for", "dismiss"]
ALLOWED_KEYS = frozenset({"Enter", "Tab", "Escape", "Space"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --------------------------------------------------------------------- contract


class InputSpec(_Model):
    type: Literal["string"] = "string"
    pattern: str | None = None
    enum: tuple[str, ...] | None = None
    format: Literal["money"] | None = None
    sensitivity: Sensitivity = "internal"
    description: str = ""

    @model_validator(mode="after")
    def _pattern_compiles(self) -> Self:
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"invalid input pattern: {exc}") from None
        return self


class InputSchema(_Model):
    type: Literal["object"] = "object"
    required: tuple[str, ...] = ()
    properties: dict[str, InputSpec]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        bad = [n for n in self.properties if not re.match(IDENT, n)]
        if bad:
            raise ValueError(f"input names must be identifiers: {bad}")
        missing = set(self.required) - set(self.properties)
        if missing:
            raise ValueError(f"required inputs are not declared: {sorted(missing)}")
        return self


class OutputSpec(_Model):
    type: Literal["string"] = "string"
    format: Literal["money"] | None = None
    enum: tuple[str, ...] | None = None
    sensitivity: Sensitivity = "internal"
    extraction: LocatorBundle
    """Located by labels and relations, never by the value itself (R-SENS-7)."""


class OutputSchema(_Model):
    type: Literal["object"] = "object"
    properties: dict[str, OutputSpec] = {}


class SurfaceSpec(_Model):
    kind: Literal["web", "desktop"] = "web"
    entry: str
    app_fingerprint: dict[str, str] = {}


# ------------------------------------------------------------------------ steps


class Checkpoint(_Model):
    signature: str
    weak: bool = False
    """Set by the compiler when a nomination lacks discriminating power (R-PKG-5)."""
    unverified: bool = False


class Retry(_Model):
    max: int = Field(default=0, ge=0, le=5)
    backoff_ms: int = Field(default=500, ge=0, le=10_000)


class Recency(_Model):
    """Binds a completed record to *this* operation's time, not an older identical one.

    Creation times are dates, so perception redacts them from every snapshot. The
    engine reads them through the raw layer - the channel outputs use - compares each
    with when the operation was attempted, and keeps only the verdict (R-REC-2).
    """

    cells: ElementPredicate
    """Selects the probe screen's cells holding when each matching record was created."""
    skew_s: int = Field(default=120, ge=0, le=3600)
    """Clock difference tolerated between the application and this machine."""


class Reconcile(_Model):
    """Three-way answer to "did this irreversible step already happen?" (R-REC-1..5).

    ``probe`` navigates to a read-only screen that shows the authoritative state. On it,
    ``completed_when`` is positive evidence the operation happened *for these inputs*;
    ``not_completed_when`` is positive evidence it did not - never mere absence. Neither
    holding is Unknown, and Unknown escalates.
    """

    probe: tuple[Step, ...] = Field(min_length=1)
    completed_when: str
    not_completed_when: str
    identity: tuple[str, ...]
    """Inputs ``completed_when`` must assert, so that someone else's record can never be
    adopted as ours (R-REC-2)."""
    recency: Recency | None = None
    extract: dict[str, LocatorBundle] = {}
    """Where each output is read on the probe screen when the operation is adopted."""

    @model_validator(mode="after")
    def _probe_is_read_only(self) -> Self:
        for i, step in enumerate(self.probe):
            if step.action not in ("navigate", "wait_for"):
                raise ValueError(
                    f"reconcile.probe[{i}] is a {step.action}: a probe may only navigate and "
                    "wait (R-REC-5) - checking must never be able to act"
                )
            if step.risk != "safe" or step.reconcile is not None:
                raise ValueError(f"reconcile.probe[{i}] must be a plain, safe step")
        return self


class Step(_Model):
    intent: str = Field(min_length=1)
    action: StepAction
    target: LocatorBundle | None = None
    value_ref: str | None = None
    url_template: str | None = None
    key: str | None = None
    risk: Risk = "safe"
    checkpoint: Checkpoint
    resume_point: bool = False
    retry: Retry = Field(default_factory=Retry)
    timeout_ms: int = Field(default=5000, ge=100, le=60_000)
    reconcile: Reconcile | None = None
    unreviewed_literal: bool = False
    """Set by the compiler for a typed literal matching no binding (PLAN.md 6.10)."""

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.action in ("click", "type", "select", "dismiss") and self.target is None:
            raise ValueError(f"a {self.action} step needs a target")
        if self.action in ("type", "select") and self.value_ref is None:
            raise ValueError("type and select take a value_ref, never a literal")
        if self.value_ref is not None and not (
            _INPUT_REF.match(self.value_ref) or _SECRET_REF.match(self.value_ref)
        ):
            raise ValueError("value_ref must be $inputs.<name> or $secrets.<name>")
        if self.action == "navigate":
            if not self.url_template or not self.url_template.startswith("/"):
                raise ValueError("navigate needs a url_template: a path on the app's own origin")
            if _LITERAL_ID.search(_TEMPLATE_VAR.sub("", self.url_template)):
                raise ValueError("url_template holds a literal identifier; use {input}")
        elif self.url_template is not None:
            raise ValueError("only a navigate step takes a url_template")
        if (self.action == "key") != (self.key is not None):
            raise ValueError("a key step needs exactly one key, and only key steps take one")
        if self.key is not None and self.key not in ALLOWED_KEYS:
            raise ValueError(f"key must be one of {sorted(ALLOWED_KEYS)}")
        if self.risk == "irreversible" and self.retry.max > 0 and self.reconcile is None:
            raise ValueError("an irreversible step may not retry without a reconcile check")
        return self


Reconcile.model_rebuild()
Step.model_rebuild()


class Remedy(_Model):
    """A bounded, declared sub-flow that establishes a precondition (e.g. sign-on)."""

    when: str
    """The state the remedy is written for. It runs only after this signature is
    positively recognised - "not signed in" alone is an absence, not evidence."""
    steps: tuple[Step, ...] = Field(min_length=1)
    max: int = Field(default=1, ge=1, le=2)


class Precondition(_Model):
    signature: str
    remedy: Remedy | None = None


class Postcondition(_Model):
    signature: str


class Outcome(_Model):
    name: str = Field(pattern=IDENT)
    class_: Literal["business", "hard_failure"] = Field(alias="class")
    signature: str
    terminal: Literal[True] = True
    description: str = ""


class RecoveryRule(_Model):
    """Declarative and bounded (R-OUT-3): on this state, do this, at most this often.

    ``dismiss`` clicks ``target`` (a notice's Continue); ``wait`` lets the page settle;
    ``reauth`` re-enters the application and replays the safe prefix. Nothing else - no
    open-ended logic and no model.
    """

    on: str
    do: Literal["dismiss", "wait", "reauth"]
    target: LocatorBundle | None = None
    max: int = Field(default=1, ge=1, le=3)
    backoff_ms: int = Field(default=0, ge=0, le=10_000)
    else_: Literal["escalate", "fail"] = Field(default="escalate", alias="else")

    @model_validator(mode="after")
    def _target_iff_dismiss(self) -> Self:
        if (self.do == "dismiss") != (self.target is not None):
            raise ValueError("a dismiss recovery needs a target, and only dismiss takes one")
        return self


class ArtifactPolicy(_Model):
    allowed_routes: tuple[str, ...] = ("/", "/console", "/console/*")
    max_steps: int = Field(default=25, ge=1, le=100)
    unattended: bool = True


class Provenance(_Model):
    discovered_by: dict[str, str] = {}
    authored_by: str | None = None
    compiled_at: str | None = None
    approval: dict[str, Literal["draft", "approved"]] = {"base": "draft"}
    approval_hash: dict[str, str | None] = {"base": None}
    approved_by: str | None = None
    approved_at: str | None = None
    approval_note: str | None = None
    approval_gates: dict[str, int] | None = None
    """A cache tools may write for display. Never read by any decision (R-PKG-3)."""


# ------------------------------------------------------------------- capability


class Capability(_Model):
    schema_version: Literal["1.0.0"]
    capability_id: str = Field(pattern=IDENT)
    version: str = Field(pattern=SEMVER)
    name: str
    description: str
    surface: SurfaceSpec
    inputs: InputSchema
    outputs: OutputSchema = Field(default_factory=OutputSchema)
    signatures: dict[str, Signature]
    preconditions: tuple[Precondition, ...] = ()
    postconditions: tuple[Postcondition, ...] = ()
    steps: tuple[Step, ...] = Field(min_length=1)
    outcomes: tuple[Outcome, ...] = ()
    recovery: tuple[RecoveryRule, ...] = ()
    overrides: dict[str, dict[str, Any]] = {}
    policy: ArtifactPolicy = Field(default_factory=ArtifactPolicy)
    provenance: Provenance = Field(default_factory=Provenance)

    # --- traversal

    def all_steps(self) -> list[tuple[str, Step]]:
        out: list[tuple[str, Step]] = []
        for i, s in enumerate(self.steps):
            out.append((f"steps[{i}]", s))
            if s.reconcile is not None:
                out += [(f"steps[{i}].reconcile.probe[{k}]", p)
                        for k, p in enumerate(s.reconcile.probe)]
        for i, pre in enumerate(self.preconditions):
            if pre.remedy is not None:
                where = f"preconditions[{i}].remedy"
                out += [(f"{where}[{j}]", s) for j, s in enumerate(pre.remedy.steps)]
        return out

    def bundles(self) -> Iterator[tuple[str, LocatorBundle]]:
        for where, step in self.all_steps():
            if step.target is not None:
                yield f"{where}.target", step.target
            if step.reconcile is not None:
                for key, bundle in step.reconcile.extract.items():
                    yield f"{where}.reconcile.extract.{key}", bundle
        for name, out in self.outputs.properties.items():
            yield f"outputs.{name}.extraction", out.extraction
        for i, rule in enumerate(self.recovery):
            if rule.target is not None:
                yield f"recovery[{i}].target", rule.target

    def signature_refs(self) -> Iterator[tuple[str, str]]:
        for where, step in self.all_steps():
            yield f"{where}.checkpoint", step.checkpoint.signature
            if step.reconcile is not None:
                yield f"{where}.reconcile.completed_when", step.reconcile.completed_when
                yield f"{where}.reconcile.not_completed_when", step.reconcile.not_completed_when
        for i, pre in enumerate(self.preconditions):
            yield f"preconditions[{i}]", pre.signature
            if pre.remedy is not None:
                yield f"preconditions[{i}].remedy.when", pre.remedy.when
        for i, post in enumerate(self.postconditions):
            yield f"postconditions[{i}]", post.signature
        for outcome in self.outcomes:
            yield f"outcomes.{outcome.name}", outcome.signature
        for i, rule in enumerate(self.recovery):
            yield f"recovery[{i}].on", rule.on

    def input_refs(self) -> Iterator[tuple[str, str]]:
        for where, step in self.all_steps():
            if step.value_ref and (m := _INPUT_REF.match(step.value_ref)):
                yield f"{where}.value_ref", m.group(1)
            for var in _TEMPLATE_VAR.findall(step.url_template or ""):
                yield f"{where}.url_template", var
            if step.reconcile is not None:
                for name in step.reconcile.identity:
                    yield f"{where}.reconcile.identity", name
                if step.reconcile.recency is not None:
                    for name in step.reconcile.recency.cells.refs():
                        yield f"{where}.reconcile.recency.cells", name
        for where, bundle in self.bundles():
            for candidate in (*bundle.candidates, *(d.candidate for d in bundle.diagnostics)):
                for target in _text_targets(candidate):
                    if target.text_ref is not None:
                        yield where, target.text_ref.removeprefix("$inputs.")
        for name, sig in self.signatures.items():
            for ref in sig.refs():
                yield f"signatures.{name}", ref

    # --- structural rules: a violation means the artifact cannot be loaded

    @model_validator(mode="after")
    def _self_contained(self) -> Self:
        for where, name in self.signature_refs():
            if name not in self.signatures:
                raise ValueError(
                    f"{where} names signature {name!r}, which this artifact does not carry "
                    "(R-PKG-1: artifacts are self-contained)"
                )
        declared = set(self.inputs.properties)
        for where, name in self.input_refs():
            if name not in declared:
                raise ValueError(f"{where} references undeclared input {name!r}")
        if len(self.all_steps()) > self.policy.max_steps:
            raise ValueError("more steps than policy.max_steps allows")
        names = [o.name for o in self.outcomes]
        if len(names) != len(set(names)):
            raise ValueError("outcome names must be unique")
        return self

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2) + "\n"


def _text_targets(c: Candidate) -> list[TextTarget]:
    out = [c.anchor] if c.anchor is not None else []
    if c.identity is not None:
        out.append(c.identity.target)
    return out


def keys_on_value(c: Candidate) -> bool:
    """Whether a candidate finds an output by the very text it is meant to read."""
    if c.kind == "role_name" or (c.kind == "anchored" and c.name is not None):
        return True
    return bool(c.identity and c.identity.relation == "self" and c.identity.target.text)


def _strings(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _strings(v, f"{path}[{i}]")


# ------------------------------------------------------------- approval gates


def _reconcile_problems(cap: Capability, where: str, step: Step) -> list[str]:
    """Why this irreversible step could not be reconciled unattended (R-REC, R-PKG-3)."""
    r = step.reconcile
    if r is None:
        return [f"{where}: irreversible step lacks a compliant reconcile (R-REC)"]
    problems: list[str] = []
    if not r.identity or not set(r.identity) <= cap.signatures[r.completed_when].refs():
        problems.append(f"{where}: irreversible step lacks a compliant reconcile (R-REC): "
                        "completed_when does not assert the identity inputs (R-REC-2)")
    if not cap.signatures[r.not_completed_when].match.asserts_content():
        problems.append(f"{where}: irreversible step lacks a compliant reconcile (R-REC): "
                        "not_completed_when asserts nothing on screen - absence is not "
                        "evidence (R-REC-3)")
    missing = sorted(set(cap.outputs.properties) - set(r.extract))
    if missing:
        problems.append(f"{where}: irreversible step lacks a compliant reconcile (R-REC): "
                        f"an adopted run could not return {missing}")
    return problems


def approval_gates(cap: Capability) -> list[str]:
    """Every reason this artifact may not be approved, recomputed from content."""
    reasons: list[str] = []
    for where, step in cap.all_steps():
        cp = step.checkpoint
        if cp.weak:
            reasons.append(f"{where}: checkpoint is weak (R-PKG-5)")
        if cp.unverified:
            reasons.append(f"{where}: checkpoint is unverified")
        if not cap.signatures[cp.signature].match.asserts_content():
            reasons.append(f"{where}: checkpoint {cp.signature!r} asserts nothing on screen")
        if step.unreviewed_literal:
            reasons.append(f"{where}: unreviewed literal")
        if step.risk == "irreversible" and cap.policy.unattended:
            reasons += _reconcile_problems(cap, where, step)
    for where, bundle in cap.bundles():
        if any(c.is_positional and c.identity is None for c in bundle.candidates):
            reasons.append(f"{where}: positional candidate lacks identity (R-LOC-5)")
    for name, out in cap.outputs.properties.items():
        if any(keys_on_value(c) for c in out.extraction.candidates):
            reasons.append(f"outputs.{name}: extraction keys on the value it reads (R-SENS-7)")
    body = cap.model_dump(mode="json", by_alias=True, exclude={"provenance"})
    for path, text in _strings(body):
        hits = [kind for kind, rx in PATTERNS.items() if rx.search(text)]
        if hits:
            reasons.append(f"{path}: sensitive-looking literal ({', '.join(hits)}) (R-SENS-9)")
    return reasons


def content_hash(cap: Capability, variant: str = "base") -> str:
    """What approval binds to: every execution-relevant key, canonically serialized."""
    if variant != "base":
        if variant not in cap.overrides:
            raise ValueError(f"unknown variant {variant!r}")
        raise NotImplementedError("per-tenant override application arrives in milestone C1")
    body = cap.model_dump(mode="json", by_alias=True, exclude={"provenance"})
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


# ------------------------------------------------------------------- storage


def load(path: Path) -> Capability:
    return Capability.model_validate_json(path.read_text())


def _semver_key(path: Path) -> tuple[int, int, int, int, str]:
    core, _, pre = path.stem.partition("-")
    major, minor, patch = (int(x) for x in core.split("."))
    return (major, minor, patch, 0 if pre else 1, pre)


def locate(root: Path, capability_id: str, version: str | None = None, *,
           release: bool = True) -> Path:
    """``capabilities/<id>/<semver>.json`` - the given version, else the highest release.

    ``release=False`` means the newest version whatever its state - what a reviewer about to
    approve means, where replay means the version in service.
    """
    folder = root / capability_id
    if version is not None:
        path = folder / f"{version}.json"
        if not path.exists():
            raise FileNotFoundError(f"no artifact {capability_id} {version} under {root}")
        return path
    found = sorted(
        (p for p in folder.glob("*.json") if re.match(SEMVER, p.stem)), key=_semver_key
    )
    if not found:
        raise FileNotFoundError(f"no artifacts for {capability_id!r} under {root}")
    from waypoint.artifact.approval import approval_status  # circular at module level

    def approved(path: Path) -> bool:
        try:
            return approval_status(load(path)).approved
        except (ValueError, OSError):
            return False

    # A freshly discovered draft sits at a higher version than the release in service.
    # Without a version the caller means the current release, not whatever is newest.
    if not release:
        return found[-1]
    return next((p for p in reversed(found) if approved(p)), found[-1])
