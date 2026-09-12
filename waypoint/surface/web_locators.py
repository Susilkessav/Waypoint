"""Web realization of declarative locators. DOM details stay below the port."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from playwright.sync_api import Frame, Locator

from waypoint.surface.locators import Candidate, Identity, LocatorBundle, TextTarget, compile_bundle
from waypoint.surface.ports import Action

SIBLING_CELLS = "xpath=preceding-sibling::td | following-sibling::td"

if TYPE_CHECKING:
    from waypoint.surface.web import WebSurface


def role_locator(scope: Frame | Locator, role: str, name: str | None = None) -> Locator:
    if role in ("cell", "LayoutTableCell"):
        loc = scope.locator("td")
        return (
            loc if name is None else loc.filter(has_text=re.compile(rf"^\s*{re.escape(name)}\s*$"))
        )
    if name is None:
        return scope.get_by_role(role)  # type: ignore[arg-type]
    return scope.get_by_role(role, name=name, exact=True)  # type: ignore[arg-type]


class WebMatcher:
    def __init__(self, surface: WebSurface) -> None:
        self.surface = surface

    def frames(self, path: tuple[str, ...]) -> list[Frame]:
        return [f for f in self.surface.page.frames if self.surface.frame_path(f) == path]

    def selectors(self, c: Candidate, inputs: Mapping[str, str]) -> list[Locator]:
        result: list[Locator] = []
        for frame in self.frames(c.frame_path):
            if c.kind == "role_name":
                result.append(role_locator(frame, c.role or "", c.name))
            elif c.kind == "label":
                result.append(frame.get_by_label(c.label or "", exact=True))
            elif c.kind == "structural":
                result.append(frame.locator(c.path or ""))
            elif c.kind == "anchored" and c.anchor is not None:
                anchor = role_locator(frame, c.anchor.role, c.anchor.render(inputs))
                # Derive the nearest row from each anchor; outer layout rows
                # containing nested tables must never broaden a semantic match.
                for a in anchor.all():
                    row = a.locator("xpath=ancestor::tr[1]")
                    if row.count() != 1:
                        continue
                    if c.column:
                        table = row.locator("xpath=ancestor::table[1]")
                        headers = table.locator("tr").first.locator("th")
                        for i, header in enumerate(headers.all()):
                            if header.inner_text().strip() == c.column:
                                result.append(row.locator(":scope > td, :scope > th").nth(i))
                    elif c.name is None and c.role in ("cell", "LayoutTableCell"):
                        # A label/value row: the value sits beside its label and is never
                        # the label itself. Selecting every cell of the row would count the
                        # anchor too, so no such candidate could ever be unique - and every
                        # confirmation page is built of exactly these rows.
                        result.append(a.locator(SIBLING_CELLS))
                    else:
                        result.append(role_locator(row, c.role or "", c.name))
        return result

    def matches(self, candidate: Candidate, inputs: Mapping[str, str]) -> list[str]:
        hits: list[str] = []
        for selector in self.selectors(candidate, inputs):
            for loc in selector.all():
                refs = (loc.get_attribute("data-wp-ref") or "").split()
                for ref in refs:
                    p = self.surface._perceived.get(ref)
                    if p is None:
                        continue
                    role = p.element.role
                    expected = candidate.role
                    if (
                        expected
                        and role != expected
                        and {role, expected} != {"cell", "LayoutTableCell"}
                    ):
                        continue
                    hits.append(ref)
        return list(dict.fromkeys(hits))

    def identity_matches(self, candidate: Candidate, ref: str, inputs: Mapping[str, str]) -> bool:
        identity = candidate.identity
        if identity is None:
            return False
        loc = self.surface._locate(Action("read", ref=ref))
        expected = identity.target.render(inputs)
        if identity.relation == "self":
            p = self.surface._perceived[ref].element
            return p.role == identity.target.role and p.name.text == expected
        row = loc.locator("xpath=ancestor::tr[1]")
        return row.count() == 1 and role_locator(row, identity.target.role, expected).count() == 1

    def synthesize(
        self, ref: str, inputs: Mapping[str, str], *, extraction: bool = False
    ) -> LocatorBundle:
        p = self.surface._perceived.get(ref)
        if p is None:
            raise ValueError("target is not in the current observation")
        e = p.element
        # An output is located by its labels and row, never by the value it holds
        # (R-SENS-7): a locator keyed on "active" can never find a dormant member.
        name = None if extraction else (e.name.text if e.name.cls.level == "public" else None)
        candidates: list[Candidate] = []
        if name is not None:
            candidates.append(
                Candidate(tier=1, kind="role_name", frame_path=e.frame_path, role=e.role, name=name)
            )
        if e.value is not None:
            for a in e.anchors:
                if a.cls.level == "public":
                    candidates.append(
                        Candidate(
                            tier=2, kind="label", frame_path=e.frame_path, role=e.role, label=a.text
                        )
                    )
        anchors: list[TextTarget] = []
        for a in e.anchors:
            if a.cls.binding and a.cls.binding in inputs:
                anchors.append(TextTarget(role="cell", text_ref=f"$inputs.{a.cls.binding}"))
            elif a.cls.level == "public" and a.text:
                anchors.append(TextTarget(role="cell", text=a.text))
        column = e.anchors[0].text if name is None and e.anchors and e.role == "cell" else None
        for anchor in anchors:
            candidates.append(
                Candidate(
                    tier=3,
                    kind="anchored",
                    frame_path=e.frame_path,
                    role=e.role,
                    name=name,
                    anchor=anchor,
                    column=column,
                )
            )
            if name is not None:
                candidates.append(
                    Candidate(
                        tier=3, kind="anchored", frame_path=e.frame_path, role=e.role, anchor=anchor
                    )
                )
        identity = (
            Identity(target=anchors[0])
            if anchors
            else (
                Identity(relation="self", target=TextTarget(role=e.role, text=name))
                if name
                else None
            )
        )
        path = self.surface._locate(Action("read", ref=ref)).evaluate("""e => {
            const parts = [];
            for (; e && e.nodeType === 1; e = e.parentElement) {
                const tag = e.tagName.toLowerCase();
                const siblings = e.parentElement ? [...e.parentElement.children]
                    .filter(s => s.tagName === e.tagName) : [e];
                parts.unshift(tag + ':nth-of-type(' + (siblings.indexOf(e) + 1) + ')');
            }
            return parts.join(' > ');
        }""")
        candidates.append(
            Candidate(
                tier=4,
                kind="structural",
                frame_path=e.frame_path,
                role=e.role,
                path=path,
                identity=identity,
            )
        )
        if e.bbox:
            candidates.append(
                Candidate(
                    tier=5,
                    kind="visual",
                    frame_path=e.frame_path,
                    role=e.role,
                    bbox=e.bbox,
                    identity=identity,
                )
            )
        return compile_bundle(candidates, ref, self, inputs)
