"""R-SENS-3..5: one redactor renders every level at every sink.

T25 (the snapshot sink), T26 (internal is redacted for the model and on disk) and
T27 (URL path segments, not just query values) live here.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from waypoint.policy.redactor import SECRET, SPAN, VISIBLE_AT, Redactor, Sink, visible
from waypoint.surface.ports import RawElement, RawSnapshot, RawText, TextClass
from waypoint.surface.sensitivity import Binding, ElementFacts, SensitivityClassifier

MEMBER = Binding("member_id", "12345", "internal")
R = Redactor([MEMBER])


class TestSinkTable:
    """T26 - R-SENS-3's table, level by level and sink by sink."""

    @pytest.mark.parametrize(
        ("level", "shown_at"),
        [
            ("secret", set()),
            ("pii", {Sink.CALLER}),
            ("internal", {Sink.STDOUT, Sink.CALLER}),
            ("public", set(Sink)),
        ],
    )
    def test_table(self, level: str, shown_at: set[Sink]) -> None:
        assert {s for s in Sink if visible(level, s)} == shown_at  # type: ignore[arg-type]

    def test_model_and_evidence_columns_are_identical(self) -> None:
        """Why one sanitized UISnapshot can serve both the model and evidence on disk."""
        for level in VISIBLE_AT:
            assert visible(level, Sink.MODEL) == visible(level, Sink.EVIDENCE)

    def test_internal_hidden_from_model_and_disk_shown_on_stdout(self) -> None:
        cls = TextClass("internal")
        assert R.text("Marion Vance", cls, Sink.MODEL) == "‹redacted:12 chars›"
        assert R.text("Marion Vance", cls, Sink.EVIDENCE) == "‹redacted:12 chars›"
        assert R.text("Marion Vance", cls, Sink.STDOUT) == "Marion Vance"

    def test_secret_is_never_shown_and_reveals_no_length(self) -> None:
        for sink in Sink:
            assert R.text("hunter2", TextClass("secret"), sink) == SECRET

    def test_bound_value_renders_as_its_binding(self) -> None:
        cls = TextClass("internal", binding="member_id")
        assert R.text("12345", cls) == "‹$inputs.member_id›"


class TestChromeSpans:
    def test_label_survives_value_does_not(self) -> None:
        cls = TextClass("pii", whole=False)
        assert R.text("Balance: $4,281.19", cls) == f"Balance: {SPAN}"

    def test_bound_value_inside_a_message(self) -> None:
        out = R.scrub('No member exists with ID "12345".')
        assert out == 'No member exists with ID "‹$inputs.member_id›".'

    def test_binding_respects_token_boundaries(self) -> None:
        assert R.scrub("ref 123456") == "ref 123456"

    def test_public_binding_is_not_substituted(self) -> None:
        r = Redactor([Binding("account_type", "savings", "public")])
        assert r.scrub("Open a savings account") == "Open a savings account"

    def test_short_binding_is_not_substituted_as_a_substring(self) -> None:
        r = Redactor([Binding("n", "12", "internal")])
        assert r.scrub("Page 12 of 40") == "Page 12 of 40"


class TestUrls:
    """T27 - identifier-like path segments, query values and fragments."""

    def test_identifier_path_segment(self) -> None:
        assert Redactor().url("/console/member/12345") == f"/console/member/{SPAN}"

    def test_ordinary_segments_are_kept(self) -> None:
        assert Redactor().url("/console/subaccount/new") == "/console/subaccount/new"

    def test_query_values_redacted_keys_kept(self) -> None:
        raw = "http://127.0.0.1:8080/console/member?member_id=67890&tab=accounts"
        expected = f"http://127.0.0.1:8080/console/member?member_id={SPAN}&tab={SPAN}"
        assert Redactor().url(raw) == expected

    def test_bound_values_render_as_bindings(self) -> None:
        out = R.url("http://h/console/member/12345?member_id=12345")
        assert out == "http://h/console/member/‹$inputs.member_id›?member_id=‹$inputs.member_id›"

    def test_fragment(self) -> None:
        assert Redactor().url("/a#secret-anchor") == f"/a#{SPAN}"

    def test_stdout_sees_the_real_url(self) -> None:
        raw = "/console/member?member_id=67890"
        assert Redactor().url(raw, Sink.STDOUT) == raw


# --------------------------------------------------------------------- T25


def _el(
    clf: SensitivityClassifier,
    ref: str,
    role: str,
    name: str,
    *,
    value: str | None = None,
    labels: tuple[str, ...] = (),
    input_type: str | None = None,
    anchors: tuple[str, ...] = (),
) -> RawElement:
    c = clf.classify(ElementFacts(role, name, value, labels, input_type))
    return RawElement(
        ref=ref,
        role=role,
        name=RawText(name, c.name),
        value=None if c.value is None else RawText(value or "", c.value),
        enabled=True,
        frame_path=("main", "content"),
        bbox=(0, 0, 10, 10),
        anchors=tuple(RawText(a, clf.classify(ElementFacts("cell", a)).name) for a in anchors),
    )


def results_page() -> RawSnapshot:
    clf = SensitivityClassifier([MEMBER])
    elements = (
        _el(clf, "r1", "columnheader", "Member ID"),
        _el(clf, "r2", "columnheader", "Name"),
        _el(clf, "r3", "columnheader", "Balance"),
        _el(clf, "r4", "cell", "10233", labels=("Member ID",)),
        _el(clf, "r5", "cell", "Marion Vance", labels=("Name",)),
        _el(clf, "r6", "cell", "$1,288.45", labels=("Balance",)),
        _el(clf, "r7", "link", "View", anchors=("10233", "Marion Vance")),
        _el(clf, "r8", "cell", "12345", labels=("Member ID",)),
        _el(clf, "r9", "cell", "Dolores Whitfield", labels=("Name",)),
        _el(clf, "r10", "cell", "$4,281.19", labels=("Balance",)),
        _el(clf, "r11", "link", "View", anchors=("12345", "Dolores Whitfield")),
        _el(clf, "r12", "textbox", "Password", value="hunter2", input_type="password"),
        _el(clf, "r13", "StaticText", 'No member exists with ID "12345".'),
    )
    url = "http://127.0.0.1:8080/console/member?member_id=12345"
    return RawSnapshot(url, "Member Search Results", elements)


class TestSanitizedSnapshot:
    """T25 for the snapshot sink - the one that feeds the model and evidence."""

    RAW_VALUES = (
        "10233",
        "12345",
        "Marion Vance",
        "Dolores Whitfield",
        "$1,288.45",
        "$4,281.19",
        "hunter2",
    )

    def test_no_raw_sensitive_string_survives(self) -> None:
        ui = R.snapshot(results_page())
        blob = json.dumps({k: v for k, v in dataclasses.asdict(ui).items() if k != "hash"})
        leaked = [s for s in self.RAW_VALUES if s in blob]
        assert not leaked, f"raw values leaked into the sanitized snapshot: {leaked}"

    def test_chrome_and_structure_survive(self) -> None:
        names = [e.name for e in R.snapshot(results_page()).elements]
        assert names.count("View") == 2
        assert {"Member ID", "Name", "Balance"} <= set(names)

    def test_the_bound_row_is_identifiable_the_others_are_not(self) -> None:
        """The model can find its target row without ever seeing the member ID."""
        views = [e for e in R.snapshot(results_page()).elements if e.name == "View"]
        assert views[1].anchors[0] == "‹$inputs.member_id›"
        assert views[0].anchors[0] == "‹redacted:5 chars›"

    def test_levels_travel_with_the_sanitized_element(self) -> None:
        ui = R.snapshot(results_page())
        pw = next(e for e in ui.elements if e.role == "textbox")
        assert (pw.sensitivity, pw.value, pw.name) == ("secret", SECRET, "Password")

    def test_hash_ignores_order_refs_and_boxes(self) -> None:
        a = results_page()
        moved = tuple(
            dataclasses.replace(e, ref=e.ref + "x", bbox=(1, 2, 3, 4)) for e in reversed(a.elements)
        )
        assert R.snapshot(a).hash == R.snapshot(dataclasses.replace(a, elements=moved)).hash

    def test_hash_changes_with_structure(self) -> None:
        a = results_page()
        fewer = dataclasses.replace(a, elements=a.elements[:-1])
        assert R.snapshot(a).hash != R.snapshot(fewer).hash
