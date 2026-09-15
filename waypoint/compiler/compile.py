"""Transcript -> draft capability (REPORT.md §2).

Transcripts are evidence; artifacts are contracts, and a reviewer should never have to
read a transcript to approve a capability. The compiler has exactly three declared
sources and infers nothing else:

1. Inputs - the discovery launch bindings (name, type, sensitivity; never values).
2. Outputs - the model's ``finish`` call, whose locator bundles were synthesized while
   the page was live, in extraction mode (R-SENS-7).
3. Goal success - ``finish.success``, verified against the final screen.

Pipeline: prune failed actions and detours -> steps with locator bundles recorded at
discovery time -> checkpoints from each nomination, verified (R-PKG-5) and given
identity (R-RESUME-5) -> outputs -> postcondition -> inline every signature (R-PKG-1)
-> whole-document scan (R-SENS-9) -> ``1.0.0``, draft.

What fails compilation outright: an unfinished run, a step with no unique verified
locator, an output keyed on its own value (R-SENS-7), a goal-success condition that
was never observed or is true everywhere, and any sensitive-looking literal in the
result (R-SENS-9). Everything else that a human must look at - a weak checkpoint, an
unbound literal - compiles, and blocks approval instead.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from waypoint.artifact.schema import Capability, approval_gates, keys_on_value
from waypoint.discovery.transcript import Step, Transcript, snapshot_from_dict
from waypoint.policy.redactor import binding_placeholder
from waypoint.signatures.recognizers import ElementPredicate, Predicate, Signature
from waypoint.surface.locators import LocatorBundle
from waypoint.surface.ports import UISnapshot

_PLACEHOLDER = re.compile(r"^‹\$inputs\.([a-z][a-z0-9_]*)›$")
# How a model actually writes the marks around a placeholder or redaction: the real
# characters, a literal escape, or a JSON escape that lost its "u" (the live open_sub_account
# run wrote "\n2039$inputs.member_id\n203a" - a correct expectation that never matched).
_MANGLED = re.compile(r"^(?:‹|\\u2039|\\n2039|\n2039)(.+?)(?:›|\\u203a|\\n203a|\n203a)$", re.S)
_BARE_INPUT = re.compile(r"^\$inputs\.[a-z][a-z0-9_]*$")
_REDACTED = re.compile(r"‹redacted(?::\d+ chars)?›|‹secret›")
_ACTIONS = {"click": "click", "type": "type", "select": "select", "key": "key", "gap": "wait_for"}
_RISKS = {"safe", "secret_write", "unknown", "irreversible"}


class CompileError(Exception):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("compile failed:\n  - " + "\n  - ".join(reasons))


@dataclass(frozen=True)
class CompileReport:
    capability: Capability
    notes: tuple[str, ...]
    """Every transformation, so a reviewer can audit what the compiler did."""
    open_gates: tuple[str, ...]
    """Approval gates the draft still fails; empty means it can be approved as is."""


def _is_data(text: str) -> bool:
    return bool(_REDACTED.search(text) or _PLACEHOLDER.match(text))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:32] or "step"


def compile_transcript(
    t: Transcript,
    *,
    version: str = "1.0.0",
    name: str | None = None,
    description: str | None = None,
    now: datetime | None = None,
) -> CompileReport:
    return _Compiler(t).run(version, name, description, now or datetime.now(UTC))


def _repaired(text: str) -> str:
    """A placeholder or redaction marker as the model meant it; anything else unchanged."""
    mangled = _MANGLED.match(text)
    if mangled is not None:
        return f"‹{mangled.group(1).strip()}›"
    return f"‹{text}›" if _BARE_INPUT.match(text) else text


def nominated_predicates(
    expect: dict[str, Any], post: UISnapshot | None, rendered: Mapping[str, str],
    notes: list[str] | None = None, where: str = "", values: frozenset[str] = frozenset(),
) -> list[Predicate]:
    """The model's nomination as checkable predicates, dropping what must not be checked.

    ``values`` are the on-screen texts the model designated as outputs. Asserting one is
    asserting this record's data - "the status is active" passes for one member and fails
    for a dormant one - so those are dropped even when the text is not sensitive.
    """
    preds: list[Predicate] = []
    for e in expect.get("elements") or []:
        role = e.get("role") or None
        name, anchor = _repaired(e.get("name") or ""), _repaired(e.get("anchor") or "")
        if _REDACTED.search(anchor) or (_REDACTED.search(name) and not anchor):
            if notes is not None:
                notes.append(
                    f"{where}: dropped an expectation on redacted data - "
                    "it would identify one record, not the step"
                )
            continue
        if _REDACTED.search(name):
            # "The value beside Account" is about the step; the value itself is one record's.
            if notes is not None:
                notes.append(f"{where}: kept an expectation beside {anchor!r} without its "
                             "redacted value")
            name = ""
        if name and name in values:
            if notes is not None:
                notes.append(
                    f"{where}: dropped an expectation on an output's own value - "
                    "it would hold for this record only, not for the next one"
                )
            continue
        kw: dict[str, Any] = {"role": role} if role else {}
        for value, plain, ref in ((name, "name", "name_ref"), (anchor, "anchor", "anchor_ref")):
            m = _PLACEHOLDER.match(value)
            if m and m.group(1) in rendered:
                kw[ref] = f"$inputs.{m.group(1)}"
            elif m:
                if notes is not None:
                    notes.append(f"{where}: ignored an unknown placeholder {value!r}")
            elif value:
                kw[plain] = value
        if not ({"role", "name", "name_ref"} & kw.keys()):
            continue
        ep = ElementPredicate(**kw)
        if post is not None:
            hits = [x for x in post.elements if ep.matches(x, rendered)]
            if len(hits) == 1:
                ep = ElementPredicate(**kw, frame=hits[0].frame_path)
        preds.append(Predicate(element_exists=ep))
    text = expect.get("text") or ""
    if text and not _REDACTED.search(text):
        preds.append(Predicate(text_contains=text))
    return preds


_DIFF_ROLES = ("heading", "columnheader", "tab", "link", "button", "LayoutTableCell", "cell",
               "StaticText")


def diff_expectation(pre: UISnapshot, post: UISnapshot, rendered: Mapping[str, str],
                     limit: int = 2) -> dict[str, Any]:
    """A checkpoint nomination for a step nobody nominated one for: what the step made appear.

    A person demonstrating a step during discovery says nothing about what it should
    achieve, so the elements present after it and absent before it stand in for the
    model's expectation. An input's placeholder first, since it asserts *which* record
    (R-RESUME-5), then headings and table headers, which describe a screen rather than one
    record's data. Redacted data is never nominated. The nomination goes through the
    same promotion gate as the model's (R-PKG-5), so a poor one blocks approval instead of
    passing silently.
    """
    before = {(e.role, e.name) for e in pre.elements}
    picks: list[tuple[int, int, dict[str, str]]] = []
    for i, e in enumerate(post.elements):
        if not e.name or (e.role, e.name) in before or _REDACTED.search(e.name):
            continue
        placeholder = _PLACEHOLDER.match(e.name)
        if placeholder and placeholder.group(1) not in rendered:
            continue
        if e.role not in _DIFF_ROLES and not placeholder:
            continue
        # An input's placeholder first: it asserts *which* record, which is what stops a
        # step passing on the wrong member's screen (R-RESUME-5). Then screen furniture.
        rank = -1 if placeholder else (
            _DIFF_ROLES.index(e.role) if e.role in _DIFF_ROLES else len(_DIFF_ROLES))
        picks.append((rank, i, {"role": e.role, "name": e.name, "anchor": ""}))
    chosen = [p for _, _, p in sorted(picks)[:limit]]
    return {"elements": chosen, "text": ""} if chosen else {}


def nomination_holds(
    expect: dict[str, Any], snapshot: UISnapshot, rendered: Mapping[str, str]
) -> bool:
    """Is what the model said to expect actually true on this screen?

    Discovery asks after every action: an expectation that is false compiles as an
    unverified checkpoint, which blocks approval, and by then nobody can fix it.
    """
    preds = nominated_predicates(expect, snapshot, rendered)
    if not preds:
        return False
    sig = Signature(match=preds[0] if len(preds) == 1 else Predicate(all=tuple(preds)))
    return sig.evaluate(snapshot, rendered)


def finish_problems(t: Transcript) -> list[str]:
    """What compilation would reject about ``t.finish``: its outputs and its goal success.

    Discovery asks at the model's finish, so a condition that is false on the screen or an
    output with no stable locator goes back to the model as a correction - instead of
    surfacing after the run, when nothing can be fixed.
    """
    compiler = _Compiler(t)
    reasons: list[str] = []
    compiler._outputs(reasons)
    compiler._postcondition(reasons)
    return reasons


def locator_problem(exc: ValueError) -> str:
    """A short reason from a bundle error; pydantic's own text is for neither people nor models."""
    if isinstance(exc, ValidationError):
        return "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
    return str(exc)


class _Compiler:
    def __init__(self, t: Transcript) -> None:
        self.t = t
        self.notes: list[str] = []
        self.sens = {b.name: b.sensitivity for b in t.bindings}
        self.rendered = {n: binding_placeholder(n) for n, s in self.sens.items() if s != "public"}
        self.keyed: dict[str, UISnapshot] = {
            str(s.turn): snapshot_from_dict(s.pre) for s in t.steps
        }
        self.final_turn = max((s.turn for s in t.steps), default=-1) + 1
        if t.finish is not None:
            self.keyed["final"] = snapshot_from_dict(t.finish.snapshot)
        self.signatures: dict[str, Signature] = {}
        self.extra_inputs: dict[str, dict[str, Any]] = {}
        self.demonstrated: list[str] = []
        finish = t.finish.outputs if t.finish is not None else {}
        self.output_values = frozenset(
            str((entry.get("element") or {}).get("name") or "")
            for entry in finish.values()
        ) - {""}

    # ------------------------------------------------------------ helpers

    def _post_key(self, turn: int) -> str | None:
        """The screen this turn produced: the next *recorded* step's, else the final one.

        Turns that record no step - a recheck, a rejected finish - leave gaps, so the
        next key is not always ``turn + 1``.
        """
        later = [int(k) for k in self.keyed if k.isdigit() and int(k) > turn]
        if later:
            return str(min(later))
        return "final" if "final" in self.keyed else None

    def _verify(self, sig: Signature, post_key: str | None) -> tuple[bool, bool]:
        """(verified: true on the post-state, discriminating: false somewhere else)."""
        post = self.keyed.get(post_key) if post_key else None
        verified = post is not None and sig.evaluate(post, self.rendered)
        others = [s for k, s in self.keyed.items() if k != post_key]
        return verified, any(not sig.evaluate(s, self.rendered) for s in others)

    def _name(self, base: str, sig: Signature) -> str:
        for existing, other in self.signatures.items():
            if other.match == sig.match:
                return existing
        name, n = base, 2
        while name in self.signatures:
            name, n = f"{base}_{n}", n + 1
        self.signatures[name] = sig
        return name

    def _nominated(
        self, expect: dict[str, Any], post: UISnapshot | None, where: str
    ) -> list[Predicate]:
        return nominated_predicates(expect, post, self.rendered, self.notes, where,
                                    self.output_values)

    def _identity(
        self, post: UISnapshot | None, preds: list[Predicate], where: str
    ) -> list[Predicate]:
        """R-RESUME-5: if the screen shows which record it is about, the check must say so."""
        if post is None:
            return []
        have = Signature(match=Predicate(all=tuple(preds))).refs() if preds else set()
        extra: list[Predicate] = []
        for e in post.elements:
            m, field = _PLACEHOLDER.match(e.name), "name_ref"
            if not m and e.value:
                m, field = _PLACEHOLDER.match(e.value), "value_ref"
            if not m or m.group(1) not in self.rendered or m.group(1) in have:
                continue
            kw: dict[str, Any] = {
                "role": e.role,
                "frame": e.frame_path,
                field: f"$inputs.{m.group(1)}",
                "anchor": next((a for a in e.anchors if not _is_data(a)), None),
            }
            extra.append(Predicate(element_exists=ElementPredicate(**kw)))
            have.add(m.group(1))
            self.notes.append(f"{where}: added an identity check on {m.group(1)!r} (R-RESUME-5)")
        return extra

    # -------------------------------------------------------------- stages

    def _prune(self) -> list[Step]:
        kept: list[Step] = []
        for s in self.t.steps:
            if s.ok:
                kept.append(s)
            else:
                self.notes.append(f"turn {s.turn}: dropped - {s.error_code or 'failed'}")
        i = 0
        while i < len(kept):
            post = kept[i].post_hash
            back = next((j for j in range(i) if kept[j].pre.get("hash") == post), None)
            if back is not None and any(s.navigated for s in kept[back : i + 1]):
                self.notes.append(
                    f"turns {kept[back].turn}-{kept[i].turn}: dropped - a detour "
                    "that returned to an earlier screen"
                )
                del kept[back : i + 1]
                i = back
                continue
            i += 1
        return kept

    def _step(self, s: Step, index: int) -> dict[str, Any]:
        where = f"turn {s.turn}"
        human = s.performed_by == "human"
        if human and s.action != "gap" and s.action != "key" and s.bundle is None:
            # A person's click with no replayable locator is still a place a person acted.
            s = replace(s, action="gap", unrecorded=(
                f"{s.decision.get('intent') or s.action}: no unique, verified locator "
                f"({s.bundle_error or 'none recorded'})"))
        kind = _ACTIONS[s.action]
        d = s.decision
        if human:
            self.demonstrated.append(f"steps[{index}]")
            self.notes.append(f"{where}: demonstrated by a person during discovery")
        if s.action == "gap":
            return self._gap(s, index, where)
        if kind != "key" and s.bundle is None:
            raise CompileError(
                [
                    f"{where}: no unique, verified locator for the target "
                    f"({s.bundle_error or 'none recorded'})"
                ]
            )
        out: dict[str, Any] = {
            "intent": d.get("intent") or f"{kind} step",
            "action": kind,
            "risk": s.risk if s.risk in _RISKS else "safe",
        }
        if s.bundle is not None:
            out["target"] = s.bundle
        if kind == "key":
            out["key"] = d.get("key")
        if kind in ("type", "select"):
            if s.value_ref:
                out["value_ref"] = s.value_ref
                if s.value_provenance and "rewritten" in s.value_provenance:
                    self.notes.append(f"{where}: {s.value_provenance}")
            else:
                name = f"unbound_{s.turn}"
                self.extra_inputs[name] = {
                    "sensitivity": "internal",
                    "description": f"A literal typed during discovery ({s.value_provenance}). "
                    "Decide what this input should be.",
                }
                out["value_ref"], out["unreviewed_literal"] = f"$inputs.{name}", True
                self.notes.append(
                    f"{where}: unbound literal became input {name!r} - "
                    "blocks approval until reviewed"
                )

        post_key = self._post_key(s.turn)
        post = self.keyed.get(post_key) if post_key else None
        preds = self._nominated(d.get("expect") or {}, post, where)
        target = s.target
        if kind in ("type", "select") and target is not None:
            # Typing's effect is the field's value; "the field exists" was true before too.
            ref = out.get("value_ref", "")
            by_ref = ref.startswith("$inputs.") and ref[len("$inputs.") :] in self.rendered
            anchor = next((a for a in target["anchors"] if not _is_data(a)), None)
            preds.insert(
                0,
                Predicate(
                    element_exists=ElementPredicate(
                        role=target["role"],
                        anchor=anchor,
                        frame=tuple(target["frame_path"]),
                        **({"value_ref": ref} if by_ref else {"filled": True}),
                    )
                ),
            )
        preds += self._identity(post, preds, where)
        if not preds:
            raise CompileError([f"{where}: the model nominated nothing checkable on screen"])
        sig = Signature(
            description=f"After: {out['intent']}",
            match=preds[0] if len(preds) == 1 else Predicate(all=tuple(preds)),
        )
        verified, discriminating = self._verify(sig, post_key)
        if not verified:
            self.notes.append(f"{where}: checkpoint was not true after the action - unverified")
        elif not discriminating:
            self.notes.append(f"{where}: checkpoint is true on every screen - weak")
        out["checkpoint"] = {
            "signature": self._name(f"step{index}_{_slug(out['intent'])}", sig),
            "weak": verified and not discriminating,
            "unverified": not verified,
        }
        if sig.refs() & set(self.rendered):
            out["resume_point"] = True
            self.notes.append(f"{where}: proposed as a resume point - confirm at approval")
        return out

    def _gap(self, s: Step, index: int, where: str) -> dict[str, Any]:
        """Where a person acted and nothing replayable was recorded: a visible, blocking hole.

        The step waits for the screen the person reached, so a reviewer sees where the flow
        continues, and ``unrecorded_human_action`` blocks approval until the step is authored.
        """
        intent = s.decision.get("intent") or "A person acted here"
        post_key = self._post_key(s.turn)
        post = self.keyed.get(post_key) if post_key else None
        preds = self._nominated(s.decision.get("expect") or {}, post, where)
        preds += self._identity(post, preds, where)
        if not preds:  # nothing to wait for: an honest check that cannot pass unreviewed
            preds = [Predicate(text_contains="‹a step a person performed›")]
        sig = Signature(description=f"After: {intent}",
                        match=preds[0] if len(preds) == 1 else Predicate(all=tuple(preds)))
        verified, discriminating = self._verify(sig, post_key)
        self.notes.append(f"{where}: a person's action could not be recorded ({s.unrecorded}) - "
                          "blocks approval until the step is authored")
        return {
            "intent": intent,
            "action": "wait_for",
            "checkpoint": {
                "signature": self._name(f"step{index}_{_slug(intent)}", sig),
                "weak": verified and not discriminating,
                "unverified": not verified,
            },
            "unrecorded_human_action": s.unrecorded or "not recorded",
        }

    def _outputs(self, reasons: list[str]) -> dict[str, Any]:
        assert self.t.finish is not None
        props: dict[str, Any] = {}
        for spec in self.t.expected_outputs:
            entry = self.t.finish.outputs.get(spec.name) or {}
            raw = entry.get("bundle")
            if raw is None:
                reasons.append(
                    f"output {spec.name!r}: {entry.get('bundle_error') or 'no element recorded'}"
                )
                continue
            try:
                bundle = LocatorBundle.model_validate(raw)
            except ValidationError as exc:
                reasons.append(f"output {spec.name!r}: {locator_problem(exc)}")
                continue
            if any(keys_on_value(c) for c in bundle.candidates):
                reasons.append(
                    f"output {spec.name!r}: extraction keys on the value it reads (R-SENS-7)"
                )
                continue
            body: dict[str, Any] = {"sensitivity": spec.sensitivity, "extraction": raw}
            if spec.format:
                body["format"] = spec.format
            props[spec.name] = body
        return props

    def _postcondition(self, reasons: list[str]) -> str | None:
        assert self.t.finish is not None
        final = self.keyed.get("final")
        preds = self._nominated(self.t.finish.success, final, "finish")
        preds += self._identity(final, preds, "finish")
        if not preds:
            reasons.append("finish: the success condition names nothing checkable on screen")
            return None
        sig = Signature(
            description=f"Goal met: {self.t.finish.summary}",
            match=preds[0] if len(preds) == 1 else Predicate(all=tuple(preds)),
        )
        verified, discriminating = self._verify(sig, "final")
        if not verified:
            reasons.append("finish: the success condition was not true on the final screen")
            return None
        if not discriminating:
            reasons.append("finish: the success condition is true on every screen")
            return None
        return self._name("goal_met", sig)

    # ----------------------------------------------------------------- run

    def run(
        self, version: str, name: str | None, description: str | None, now: datetime
    ) -> CompileReport:
        t = self.t
        if t.ending != "finished" or t.finish is None:
            raise CompileError([f"discovery did not finish: {t.ending} ({t.ending_detail})"])
        reasons: list[str] = []
        steps = [self._step(s, i) for i, s in enumerate(self._prune())]
        if not steps:
            reasons.append("no successful steps survived pruning")
        outputs = self._outputs(reasons)
        goal = self._postcondition(reasons)
        if reasons:
            raise CompileError(reasons)
        props = {b.name: {"sensitivity": b.sensitivity} for b in t.bindings}
        props.update(self.extra_inputs)
        body = {
            "schema_version": "1.0.0",
            "capability_id": t.capability_id,
            "version": version,
            "name": name or f"{t.capability_id} (discovered)",
            "description": description or t.goal,
            "surface": {"kind": "web", "entry": t.entry},
            "inputs": {"required": list(props), "properties": props},
            "outputs": {"properties": outputs},
            "signatures": {n: s.model_dump(mode="json") for n, s in self.signatures.items()},
            "postconditions": [{"signature": goal}],
            "steps": steps,
            "provenance": {
                "discovered_by": {"model": t.model, "run_id": t.run_id},
                "compiled_at": now.isoformat(timespec="seconds"),
                "demonstrated_steps": self.demonstrated,
            },
        }
        try:
            cap = Capability.model_validate(body)
        except ValidationError as exc:
            raise CompileError([f"the compiled artifact is invalid: {exc}"]) from None
        gates = approval_gates(cap)
        leaks = [g for g in gates if "R-SENS-9" in g]
        if leaks:
            raise CompileError(leaks)
        return CompileReport(cap, tuple(self.notes), tuple(gates))
