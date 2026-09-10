"""A2 - the target app really has the hostile properties later milestones need.

These are fixture tests, not tests of Waypoint. They exist so that if the app
ever loses one of these properties, the failure shows up here rather than as a
mysteriously passing locator test in A4.

HTTP level only: A3 introduces the browser.
"""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any

import pytest

VIEW_LINK_RE = re.compile(
    r'<a id="(ctl00_MainContent_gvMembers_ctl\d+_lnkView[^"]*)"[^>]*>View</a>'
)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def markup(html: str) -> str:
    """Markup with comments removed.

    The templates explain their own hostility in comments, and those comments
    mention the very constructs these tests assert are absent.
    """
    return COMMENT_RE.sub("", html)


class Console:
    """A cookie-carrying HTTP client for the fixture."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def get(self, path: str) -> str:
        with self.opener.open(f"{self.base_url}{path}", timeout=10) as resp:
            return resp.read().decode()

    def post(self, path: str, data: dict[str, Any]) -> str:
        body = urllib.parse.urlencode(data).encode()
        with self.opener.open(f"{self.base_url}{path}", body, timeout=10) as resp:
            return resp.read().decode()

    def final_url(self, path: str) -> str:
        with self.opener.open(f"{self.base_url}{path}", timeout=10) as resp:
            return str(resp.geturl())

    def login(self) -> None:
        self.post("/", {"txtUserId": "operator1", "txtPassword": "changeme"})

    def search(self, member_id: str) -> str:
        return self.post("/console/search", {"ctl00$MainContent$txtMemberId": member_id})


@pytest.fixture
def console(live_server: str) -> Console:
    c = Console(live_server)
    c.login()
    return c


def test_console_requires_login(live_server: str) -> None:
    anon = Console(live_server)
    assert "Operator Sign On" in anon.get("/console")


def test_console_is_a_frameset_with_no_content_of_its_own(console: Console) -> None:
    html = console.get("/console")
    assert "<frameset" in html
    assert 'src="/console/nav"' in html
    assert 'src="/console/content"' in html
    assert "txtMemberId" not in html, "the search field must live one frame deeper"


def test_search_field_has_no_label_for_or_test_id(console: Console) -> None:
    """It is identifiable only by the adjacent cell's text."""
    html = markup(console.get("/console/content"))
    assert "<label" not in html
    assert "data-testid" not in html
    assert "aria-label" not in html
    assert "Member ID" in html and "txtMemberId" in html


def test_results_grid_has_eight_identically_named_view_links(console: Console) -> None:
    ids = VIEW_LINK_RE.findall(console.search("12345"))
    assert len(ids) == 8, f"expected 8 View links, got {len(ids)}"
    assert len(set(ids)) == 8, "element IDs must be distinct even though names collide"


def test_view_link_ids_encode_row_position(console: Console) -> None:
    """IDs number the row, not the member, so re-sorting repoints every one of them.

    This is precisely why a tier-4 structural locator needs an identity assertion
    (PLAN.md R-LOC-5): after a re-sort the same path selects a different person.
    """

    def row_index_of(html: str, member_id: str) -> int:
        rows = re.findall(r"<tr[^>]*>.*?</tr>", html, re.S)
        return next(i for i, row in enumerate(rows) if f"<td>{member_id}</td>" in row)

    console.get("/console/content?inject=none")
    plain = console.search("12345")
    console.get("/console/content?inject=reorder")
    shuffled = console.search("12345")

    assert VIEW_LINK_RE.findall(plain) == VIEW_LINK_RE.findall(shuffled), (
        "the ID sequence is a property of position, so it is unchanged by re-sorting"
    )
    assert row_index_of(plain, "12345") != row_index_of(shuffled, "12345"), (
        "but the target member moved, so those IDs now point at different people"
    )


def test_unknown_member_is_a_business_outcome_not_an_error(console: Console) -> None:
    html = console.search("00000")
    assert "No records found" in html
    assert "Traceback" not in html


def test_inject_ambiguous_duplicates_the_target_row_control(console: Console) -> None:
    console.get("/console/content?inject=ambiguous")
    html = console.search("12345")
    assert len(VIEW_LINK_RE.findall(html)) == 9, "target row should carry a second View link"


def test_inject_row_missing_removes_the_row_without_a_banner(console: Console) -> None:
    """The dangerous case: the row is gone but nothing says so."""
    console.get("/console/content?inject=row_missing")
    html = console.search("12345")
    assert "<td>12345</td>" not in html
    assert "No records found" not in html, "silence is what makes this dangerous"
    assert len(VIEW_LINK_RE.findall(html)) == 7


def test_injection_persists_across_frame_navigations(console: Console) -> None:
    console.get("/console/content?inject=ambiguous")
    console.get("/console/nav")
    assert len(VIEW_LINK_RE.findall(console.search("12345"))) == 9
    console.get("/console/content?inject=none")
    assert len(VIEW_LINK_RE.findall(console.search("12345"))) == 8


def test_member_detail_tabs_are_postbacks_that_never_change_the_url(console: Console) -> None:
    html = markup(console.get("/console/member?member_id=12345"))
    assert "__doPostBack" in html
    for tab in ("Summary", "Accounts", "Notes"):
        assert re.search(rf'<td onclick="__doPostBack\([^)]*\)"[^>]*>{tab}</td>', html), (
            f"{tab} must be a cell with an onclick, not a link or button"
        )
    accounts = console.post(
        "/console/member",
        {"member_id": "12345", "__EVENTTARGET": "ctl00$MainContent$tabAccounts"},
    )
    assert "ifrAccounts" in accounts, "Accounts tab renders the nested iframe"


def test_accounts_grid_is_one_frame_deeper(console: Console) -> None:
    html = console.get("/console/member/accounts?member_id=12345")
    assert "$4,281.19" in html
    assert "Savings" in html


def test_member_detail_carries_an_unlabelled_control(console: Console) -> None:
    """Fixture for T22: a link whose accessible name is empty."""
    html = console.get("/console/member?member_id=12345")
    assert 'alt=""' in html and "lnkIcon" in html


def test_mark_for_review_is_a_mutating_get(console: Console) -> None:
    """Fixture for T21: benign name, link not button, and it changes state."""
    before = console.get("/console/member?member_id=12345")
    assert "Mark for Review" in before
    assert "FLAGGED" not in before

    console.get("/console/member/flag?member_id=12345")
    after = console.get("/console/member?member_id=12345")
    assert "FLAGGED" in after, "a plain GET mutated server state"


def test_seeded_balances_match_the_documented_demo_path(console: Console) -> None:
    """PLAN.md section 11 quotes these values; keep them true."""
    assert "$4,281.19" in console.get("/console/member/accounts?member_id=12345")
    assert "$912.04" in console.get("/console/member/accounts?member_id=67890")
