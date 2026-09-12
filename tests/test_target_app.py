"""A2 - the target app really has the hostile properties later milestones need.

These are fixture tests, not tests of Waypoint. They exist so that if the app
ever loses one of these properties, the failure shows up here rather than as a
mysteriously passing locator test in A4.

HTTP level only: A3 introduces the browser.
"""

from __future__ import annotations

import re
import time
import urllib.error
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

    def attempt(self, path: str) -> tuple[int, str]:
        """(status, body) - for the injections whose whole point is a failing status."""
        try:
            with self.opener.open(f"{self.base_url}{path}", timeout=10) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as failed:
            return failed.code, failed.read().decode()

    def review(self, member_id: str = "12345", kind: str = "Savings",
               deposit: str = "250.00") -> tuple[int, str]:
        """Fill the sub-account form and submit it. Nothing is committed by this."""
        body = urllib.parse.urlencode({
            "member_id": member_id, "ctl00$MainContent$ddlAccountType": kind,
            "ctl00$MainContent$txtDeposit": deposit,
        }).encode()
        try:
            with self.opener.open(f"{self.base_url}/console/subaccount/review", body,
                                  timeout=10) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as failed:
            return failed.code, failed.read().decode()

    def confirm(self, member_id: str = "12345", kind: str = "Savings",
                cents: int = 25000) -> tuple[int, str]:
        """The mutation: a GET that opens the sub-account."""
        return self.attempt(
            f"/console/subaccount/confirm?member_id={member_id}&type={kind}&cents={cents}")

    def accounts(self, member_id: str = "12345") -> str:
        return self.get(f"/console/member/accounts?member_id={member_id}")

    def inject(self, name: str) -> None:
        self.get(f"/console/content?inject={name}")


def reset_fixture_state(base_url: str) -> None:
    """Sub-accounts are server-side; every test starts from none."""
    request = urllib.request.Request(f"{base_url}/_fixture/reset", data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=10):
        pass


@pytest.fixture
def console(live_server: str) -> Console:
    reset_fixture_state(live_server)
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


def test_inject_wrong_member_renders_another_members_profile(console: Console) -> None:
    """Fixture for T7: the right screen, for the wrong person."""
    console.get("/console/content?inject=wrong_member")
    html = console.get("/console/member?member_id=12345")
    assert "Member Profile" in html
    assert "<b>12345</b>" not in html and "<b>18820</b>" in html


# --------------------------------------------------------------- B1: sub-accounts


def test_opening_a_sub_account_takes_three_screens_and_commits_on_a_get(console: Console) -> None:
    """The flow Milestone B reconciles: form, review, and a Confirm link that mutates."""
    form = console.get("/console/subaccount/new?member_id=12345")
    assert "New Sub-Account" in form and "Account type" in form
    assert "<label" not in markup(form) and "aria-label" not in markup(form)

    status, review = console.review(deposit="250.00")
    assert status == 200
    assert "Please review" in review
    assert "/console/subaccount/confirm?" in review, "Confirm resolves to a GET"
    assert console.accounts().count("<tr>") == 2, "nothing is committed by reviewing"

    status, confirmed = console.confirm()
    assert status == 200
    assert "Sub-Account Opened" in confirmed
    assert re.search(r"CN-12345-[0-9A-F]{4}", confirmed), "a confirmation number to bind to"
    assert "12345" in confirmed and "Savings" in confirmed

    grid = console.accounts()
    assert "SA-12345-01" in grid and "$250.00" in grid
    assert "Opened" in grid and "UTC" in grid, "the grid records when, for recency checks"


def test_the_unlabelled_icon_deletes_a_sub_account(console: Console) -> None:
    """Fixture for R-RISK-5: no name at all, and it destroys the record it sits beside."""
    console.confirm()
    _, review = console.review()
    assert 'alt=""' in review and "lnkDiscard" in review
    icon = re.search(r'<a id="ctl00_MainContent_lnkDiscard"[^>]*>(.*?)</a>', review, re.S)
    assert icon is not None and ">" not in icon.group(1).replace("<img", "").split(">")[-1].strip()

    console.get("/console/subaccount/delete?member_id=12345")
    assert "SA-12345-01" not in console.accounts(), "a GET behind an icon deleted it"


def test_a_restricted_member_is_refused_with_a_banner_not_an_error(console: Console) -> None:
    """member_id=99999: a business outcome. 200, named, and no flow to continue."""
    status, body = console.attempt("/console/subaccount/new?member_id=99999")
    assert status == 200
    assert "not authorized to service member 99999" in body


@pytest.mark.parametrize("deposit", ["10.00", "0", "not-an-amount"])
def test_the_application_refuses_a_deposit_it_does_not_like(console: Console, deposit: str
                                                            ) -> None:
    status, body = console.review(deposit=deposit)
    assert status == 200 and "New Sub-Account" in body
    assert "lblError" in body
    assert console.accounts().count("SA-12345") == 0


def test_inject_validation_refuses_input_that_would_otherwise_pass(console: Console) -> None:
    console.inject("validation")
    status, body = console.review(deposit="250.00")
    assert status == 200 and "must be at least" in body
    assert "SA-12345" not in console.accounts()


def test_inject_500_fails_hard_and_commits_nothing(console: Console) -> None:
    console.inject("500")
    status, body = console.review()
    assert status == 500 and "Server Error" in body
    assert "SA-12345" not in console.accounts()


def test_inject_interstitial_stands_in_the_way_once(console: Console) -> None:
    console.inject("interstitial")
    notice = console.get("/console/subaccount/new?member_id=12345")
    assert "Scheduled Maintenance Notice" in notice and "Continue" in notice
    assert "Account type" not in notice
    back = re.search(r'id="ctl00_MainContent_lnkContinue" href="([^"]+)"', notice)
    assert back is not None
    assert "Account type" in console.get(back.group(1)), "Continue leads to the real screen"
    assert "Account type" in console.get("/console/subaccount/new?member_id=12345")


def test_inject_session_lapses_once_and_the_flow_resumes_after_signing_in(console: Console
                                                                         ) -> None:
    console.inject("session")
    assert "Operator Sign On" in console.get("/console/subaccount/new?member_id=12345")
    console.login()
    assert "Account type" in console.get("/console/subaccount/new?member_id=12345")


def test_inject_slow_answers_late_but_correctly(console: Console) -> None:
    console.inject("slow")
    started = time.monotonic()
    body = console.get("/console/subaccount/new?member_id=12345")
    assert time.monotonic() - started >= 1.5
    assert "Account type" in body


def test_inject_drift_renames_the_control_but_keeps_its_id(console: Console) -> None:
    """Tier 1 (role plus name) stops matching; an anchored locator still does."""
    console.inject("drift")
    body = console.get("/console/subaccount/new?member_id=12345")
    assert 'id="ctl00_MainContent_btnReview"' in body
    assert 'value="Continue"' in body and 'value="Review"' not in body


def test_inject_commit_then_drop_commits_and_loses_the_answer(console: Console) -> None:
    """R-REC-3: from the caller's side this is identical to never having arrived."""
    console.inject("commit_then_drop")
    status, body = console.confirm()
    assert status == 502 and "invalid response" in body
    assert "SA-12345-01" in console.accounts(), "the server committed all the same"


def test_inject_stale_confirmation_shows_somebody_elses_receipt(console: Console) -> None:
    """R-REC-2: a confirmation page is not proof unless it is bound to this operation."""
    console.inject("stale_confirmation")
    status, body = console.confirm()
    assert status == 200 and "Sub-Account Opened" in body
    assert "CN-10233-4K2A" in body and "12345" not in body
    assert "SA-12345" not in console.accounts(), "nothing was committed for this member"


def test_every_documented_injection_is_implemented() -> None:
    """PLAN.md section 7.2 is the list; this fails when one of them is only a plan."""
    from target_app.chaos import SUPPORTED

    assert SUPPORTED == {
        "ambiguous", "row_missing", "reorder", "wrong_member", "slow", "500", "interstitial",
        "session", "validation", "drift", "commit_then_drop", "stale_confirmation",
    }


def test_a_sub_account_outlives_the_session_that_opened_it(console: Console,
                                                          live_server: str) -> None:
    """Reconciliation's premise: a fresh process, in a fresh browser, can see an earlier
    commit. Session-scoped state would have made T13 untestable."""
    console.inject("commit_then_drop")
    assert console.confirm()[0] == 502

    stranger = Console(live_server)
    stranger.login()
    assert "SA-12345-01" in stranger.accounts()


def test_the_accounts_tab_has_a_read_only_deep_link(console: Console) -> None:
    """The UI switches tabs by postback; a probe needs a GET that reaches the same grid."""
    html = console.get("/console/member?member_id=12345&tab=accounts")
    assert "ifrAccounts" in html and "Member Profile" in html
    assert "ifrAccounts" not in console.get("/console/member?member_id=12345")


def test_obstacles_also_meet_the_lookup_flow(console: Console) -> None:
    """Demo commands 6 and 7 run the lookup capability, so the member screen gets them too."""
    console.inject("interstitial")
    assert "Scheduled Maintenance Notice" in console.get("/console/member?member_id=12345")
    assert "Member Profile" in console.get("/console/member?member_id=12345")

    console.inject("500")
    status, body = console.attempt("/console/member?member_id=12345")
    assert status == 500 and "Server Error" in body

    console.inject("none")
    assert "not authorized to service member 99999" in console.get(
        "/console/member?member_id=99999")


def test_the_fixture_reset_is_off_the_route_allowlist() -> None:
    """No run can wipe its own evidence: the policy allowlist never admits this route."""
    from waypoint.policy.engine import PolicyConfig, PolicyEngine

    policy = PolicyEngine(PolicyConfig.for_origin("http://127.0.0.1:8080"))
    assert not policy.allowed_url("http://127.0.0.1:8080/_fixture/reset")
    assert policy.allowed_url("http://127.0.0.1:8080/console/member")
