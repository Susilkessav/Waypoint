"""R-SENS-1..3: sensitivity is assigned at perception time, before anything leaves
the surface. HTTP- and browser-free: these run on plain facts."""

from __future__ import annotations

import pytest

from waypoint.surface.ports import TextClass
from waypoint.surface.sensitivity import (
    Binding,
    ElementFacts,
    SensitivityClassifier,
    label_verdict,
)

PLAIN = SensitivityClassifier()
BOUND = SensitivityClassifier(
    [Binding("member_id", "12345", "internal"), Binding("account_type", "savings", "public")]
)


def name_of(facts: ElementFacts, clf: SensitivityClassifier = PLAIN) -> TextClass:
    return clf.classify(facts).name


def value_of(facts: ElementFacts, clf: SensitivityClassifier = PLAIN) -> TextClass:
    value = clf.classify(facts).value
    assert value is not None
    return value


class TestChromeStaysReadable:
    """Navigation depends on labels, headers and messages reaching the model."""

    @pytest.mark.parametrize(
        ("role", "text"),
        [
            ("link", "View"),
            ("button", "Search"),
            ("StaticText", "No records found"),
            ("heading", "Member Profile"),
            ("columnheader", "Balance"),
            ("columnheader", "Name"),
        ],
    )
    def test_chrome_is_public(self, role: str, text: str) -> None:
        assert name_of(ElementFacts(role, text)).level == "public"

    def test_a_form_fields_name_is_its_label(self) -> None:
        assert name_of(ElementFacts("textbox", "Password")).level == "public"

    def test_header_less_table_cells_are_chrome(self) -> None:
        """The A3 spike: Chromium exposes the td-onclick tab strip as role=cell."""
        tab = ElementFacts("cell", "Accounts", in_data_table=False)
        assert name_of(tab).level == "public"


class TestDataDefaultsToInternal:
    """R-SENS-3: record data with no rule attached is internal, not public."""

    def test_unlabelled_data_cell(self) -> None:
        cls = name_of(ElementFacts("cell", "Marion Vance"))
        assert (cls.level, cls.whole) == ("internal", True)

    def test_field_value(self) -> None:
        assert value_of(ElementFacts("textbox", "", value="anything")).level == "internal"

    def test_empty_field_is_still_classified(self) -> None:
        assert PLAIN.classify(ElementFacts("textbox", "")).value is not None

    def test_non_field_without_value_has_no_value_class(self) -> None:
        assert PLAIN.classify(ElementFacts("link", "View")).value is None


class TestDeclaredLabels:
    @pytest.mark.parametrize(
        ("header", "level"),
        [
            ("Balance", "pii"),
            ("Name", "pii"),
            ("Account Number", "pii"),
            ("Member ID", "internal"),
            ("Type", "public"),
            ("Status", "public"),
        ],
    )
    def test_data_cell_takes_its_column_headers_level(self, header: str, level: str) -> None:
        assert name_of(ElementFacts("cell", "x1", labels=(header,))).level == level

    def test_layout_value_beside_a_sensitive_label_is_data(self) -> None:
        facts = ElementFacts("LayoutTableCell", "Dolores Whitfield", labels=("Name",))
        cls = name_of(facts)
        assert (cls.level, cls.whole) == ("pii", True)

    def test_the_label_cell_itself_stays_public(self) -> None:
        assert name_of(ElementFacts("LayoutTableCell", "Name")).level == "public"

    def test_field_named_password_holds_a_secret(self) -> None:
        facts = ElementFacts("textbox", "Password", value="hunter2")
        assert value_of(facts).level == "secret"

    def test_password_label_found_by_adjacency(self) -> None:
        facts = ElementFacts("textbox", "", value="x", labels=("Password",))
        assert value_of(facts).level == "secret"


class TestInputTypeAndAutocomplete:
    def test_password_type_is_secret_whatever_the_label(self) -> None:
        facts = ElementFacts("textbox", "Code", value="x", input_type="password")
        assert value_of(facts).level == "secret"

    def test_card_number_autocomplete_is_secret(self) -> None:
        facts = ElementFacts("textbox", "Card", value="x", autocomplete="cc-number")
        assert value_of(facts).level == "secret"

    def test_email_autocomplete_is_pii(self) -> None:
        facts = ElementFacts("textbox", "Contact", value="x", autocomplete="email")
        assert value_of(facts).level == "pii"


class TestPatterns:
    @pytest.mark.parametrize(
        "text", ["$4,281.19", "123-45-6789", "dolores@example.com", "123456789012", "2001-07-22"]
    )
    def test_sensitive_shapes_in_chrome_are_pii_spans(self, text: str) -> None:
        cls = name_of(ElementFacts("StaticText", f"Value: {text}"))
        assert (cls.level, cls.whole) == ("pii", False)

    def test_patterns_beat_a_public_column(self) -> None:
        cls = name_of(ElementFacts("cell", "$4,281.19", labels=("Type",)))
        assert (cls.level, cls.whole) == ("pii", True)


class TestBindings:
    def test_exact_match_carries_the_binding_name(self) -> None:
        cls = name_of(ElementFacts("cell", "12345"), BOUND)
        assert (cls.level, cls.binding) == ("internal", "member_id")

    def test_a_bound_value_in_chrome_becomes_data(self) -> None:
        cls = name_of(ElementFacts("LayoutTableCell", "12345"), BOUND)
        assert (cls.level, cls.whole, cls.binding) == ("internal", True, "member_id")

    def test_a_bound_value_inside_a_message_raises_the_message(self) -> None:
        cls = name_of(ElementFacts("StaticText", 'No member with ID "12345".'), BOUND)
        assert (cls.level, cls.whole) == ("internal", False)

    def test_public_bindings_are_ignored(self) -> None:
        assert name_of(ElementFacts("StaticText", "savings"), BOUND).binding is None

    def test_a_near_miss_is_not_a_binding(self) -> None:
        assert name_of(ElementFacts("cell", "123456"), BOUND).binding is None


def test_label_verdict_most_sensitive_wins() -> None:
    assert label_verdict(["Status", "Balance"]) == "pii"
    assert label_verdict(["Summary"]) is None
