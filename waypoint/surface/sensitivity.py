"""Sensitivity classification at perception time (PLAN.md R-SENS-1, R-SENS-2).

The Surface runs this on every element *before* any snapshot leaves it - before
the model, the evidence writer or the transcript. That is what makes redaction
possible on the very first discovery run, when no artifact schema exists yet to
say which fields are sensitive.

Data versus chrome
------------------
R-SENS-3 makes ``internal`` the default and redacts it in model prompts. Applied
to every accessible name, that would also redact "View", "Search" and "No records
found", and discovery could not navigate at all. So the internal default applies
to *data* - form-field values and data-table cells, which is where record content
lives - while UI chrome (labels, headers, headings, links, buttons, messages)
defaults to ``public``. Declared label rules, input types and content patterns
still raise chrome; patterns do so span by span, so a label stays readable:
"Balance: $4,281.19" becomes "Balance: <redacted>".

What counts as a data table is decided by header cells, not by Chromium's role.
The A3 spike found Chromium exposing the member-detail tab strip - a header-less
row of ``td onclick`` cells - as role ``cell``. Trusting the role would have
redacted "Summary / Accounts / Notes" and hidden the tabs from the model.

Known limit: a person's name rendered as chrome - a link or heading with no
sensitive label beside it - is not detected. Title-case heuristics on chrome
misfire on ordinary UI text ("Mark for Review", "Sign Off") and would break
navigation, so none is attempted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from waypoint.surface.ports import PUBLIC, Sensitivity, TextClass, max_sensitivity, rank

#: Roles whose *value* is data. Their accessible name is a label.
VALUE_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton", "slider", "listbox"})
#: Roles whose *name* is data - but only inside a table that has header cells.
DATA_CELL_ROLES = frozenset({"cell", "gridcell"})

# Declared label rules (R-SENS-2, step 1). Matched against an element's labels -
# its column or row header, the label cell beside it - and, for form fields,
# against the field's own accessible name, which is its label.
SECRET_LABEL_RE = re.compile(
    r"password|passcode|pass\s*phrase|\bpin\b|secret|\btoken\b|\bcvv\b|security code", re.I
)
PII_LABEL_RE = re.compile(
    r"\bssn\b|social security|tax\s*id|\btin\b|account\s*(?:number|no\b|#)|balance|"
    r"\bdob\b|date of birth|birth\s*date|routing|\bname\b|address|phone|e-?mail",
    re.I,
)
INTERNAL_LABEL_RE = re.compile(
    r"\bid\b|identifier|\b(?:member|customer|client)\s*(?:no\b|number|#)|"
    r"\breference\b|\bref\b|\baccount\b",
    re.I,
)
#: Categorical columns that both caller and model need in the clear - without them
#: a savings row is indistinguishable from a checking row. Declared, not guessed.
PUBLIC_LABEL_RE = re.compile(r"(?:type|kind|category|status|state|branch|currency)\s*:?", re.I)

# Content patterns (R-SENS-2, step 3), matched against the text itself.
PATTERNS: dict[str, re.Pattern[str]] = {
    "money": re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d{2})?"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "date": re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/(?:19|20)?\d{2}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "long_digits": re.compile(r"\d{9,}"),
}

SECRET_INPUT_TYPES = frozenset({"password"})
SECRET_AUTOCOMPLETE = frozenset(
    {"current-password", "new-password", "one-time-code", "cc-number", "cc-csc", "cc-exp"}
)
PII_AUTOCOMPLETE = frozenset(
    {"name", "given-name", "family-name", "email", "tel", "bday", "street-address", "postal-code"}
)

#: Bound values shorter than this match only exactly, never as substrings, so a
#: bound "12" does not rewrite "Page 12 of 40".
MIN_SUBSTRING_BINDING_LEN = 3


@dataclass(frozen=True)
class Binding:
    """A declared, named input value (PLAN.md section 6.10), e.g. member_id=12345."""

    name: str
    value: str
    sensitivity: Sensitivity


def redactable(binding: Binding) -> bool:
    """Only bindings above public are ever rewritten; a bound "savings" stays readable."""
    return rank(binding.sensitivity) >= rank("internal") and bool(binding.value.strip())


def binding_pattern(binding: Binding) -> re.Pattern[str]:
    """Matches the bound value as a whole token, so 12345 never matches inside 123456."""
    return re.compile(rf"(?<!\w){re.escape(binding.value.strip())}(?!\w)")


def pattern_level(text: str) -> Sensitivity:
    return "pii" if any(rx.search(text) for rx in PATTERNS.values()) else "public"


def label_verdict(labels: Iterable[str]) -> Sensitivity | None:
    """The most sensitive declared rule matched by any label, or None if none match."""
    found: list[Sensitivity] = []
    for label in labels:
        text = label.strip()
        if not text:
            continue
        if SECRET_LABEL_RE.search(text):
            found.append("secret")
        elif PII_LABEL_RE.search(text):
            found.append("pii")
        elif INTERNAL_LABEL_RE.search(text):
            found.append("internal")
        elif PUBLIC_LABEL_RE.fullmatch(text):
            found.append("public")
    return max_sensitivity(*found) if found else None


@dataclass(frozen=True)
class ElementFacts:
    """What perception knows about one element before classification."""

    role: str
    name: str
    value: str | None = None
    labels: tuple[str, ...] = ()
    input_type: str | None = None
    autocomplete: str | None = None
    in_data_table: bool = True
    """False for a ``cell`` in a table with no header cells (a layout strip).
    Defaults to True so that, absent evidence, a cell is treated as data."""


@dataclass(frozen=True)
class Classification:
    name: TextClass
    value: TextClass | None


class SensitivityClassifier:
    """Assigns a TextClass to an element's name and value, in R-SENS-2 order."""

    def __init__(self, bindings: Sequence[Binding] = ()) -> None:
        live = [b for b in bindings if redactable(b)]
        self._exact = {b.value.strip(): b for b in live}
        self._substring = [
            (b, binding_pattern(b))
            for b in live
            if len(b.value.strip()) >= MIN_SUBSTRING_BINDING_LEN
        ]

    def classify(self, facts: ElementFacts) -> Classification:
        return Classification(name=self._name(facts), value=self._value(facts))

    def remember_secret(self, value: str) -> None:
        if value:
            binding = Binding("credential", value, "secret")
            self._exact[value] = binding
            self._substring.append((binding, re.compile(re.escape(value))))

    def _name(self, f: ElementFacts) -> TextClass:
        if f.role in VALUE_ROLES:
            return self._chrome(f.name)  # a form field's accessible name is its label
        verdict = label_verdict(f.labels)
        if f.role in DATA_CELL_ROLES and f.in_data_table:
            return self._data(f.name, verdict if verdict is not None else "internal")
        if verdict is not None and verdict != "public" and f.name.strip():
            return self._data(f.name, verdict)  # chrome beside a sensitive label is data
        return self._chrome(f.name)

    def _value(self, f: ElementFacts) -> TextClass | None:
        if f.value is None and f.role not in VALUE_ROLES:
            return None
        verdict = label_verdict((*f.labels, f.name))
        base: Sensitivity = verdict if verdict is not None else "internal"
        input_type = (f.input_type or "").lower()
        autocomplete = (f.autocomplete or "").lower()
        if input_type in SECRET_INPUT_TYPES or autocomplete in SECRET_AUTOCOMPLETE:
            base = "secret"
        elif autocomplete in PII_AUTOCOMPLETE:
            base = max_sensitivity(base, "pii")
        return self._data(f.value or "", base)

    def _data(self, text: str, base: Sensitivity) -> TextClass:
        level = max_sensitivity(base, pattern_level(text))
        bound = self._exact.get(text.strip())
        if bound is not None:
            level = max_sensitivity(level, bound.sensitivity)
            return TextClass(level, whole=True, binding=bound.name)
        return TextClass(level, whole=True)

    def _chrome(self, text: str) -> TextClass:
        bound = self._exact.get(text.strip())
        if bound is not None:  # chrome that IS a bound value is that value
            level = max_sensitivity(bound.sensitivity, pattern_level(text))
            return TextClass(level, whole=True, binding=bound.name)
        levels: list[Sensitivity] = [pattern_level(text)]
        levels += [b.sensitivity for b, rx in self._substring if rx.search(text)]
        level = max_sensitivity(*levels)
        return PUBLIC if level == "public" else TextClass(level, whole=False)
