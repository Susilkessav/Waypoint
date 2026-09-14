"""The surface enforces policy for any caller, before browser side effects."""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from tests.test_web_surface import find, login, open_member
from tests.test_web_surface import surface as surface
from waypoint.policy.engine import PolicyConfig, PolicyEngine, RunContext
from waypoint.surface.ports import Action
from waypoint.surface.web import WebSurface

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("caller", ["discovery", "replay"])
def test_both_callers_are_blocked_below_the_driver(surface, caller):
    result = surface.act(Action("navigate", url="http://outside.test/", intent=caller))
    assert not result.ok and result.error_code == "policy_block"
    surface.page.set_content("<a href='http://outside.test/'>View</a>")
    target = find(surface.observe(), "link", name="View")
    result = surface.act(Action("click", ref=target.ref, intent=caller))
    assert not result.ok and result.error_code == "policy_block"


def test_mutating_get_is_not_dispatched_unattended(surface):
    detail = open_member(surface)
    target = find(detail, "link", name="Mark for Review")
    surface.context = RunContext(unattended=True, state_known=True)
    result = surface.act(Action("click", ref=target.ref))
    assert result.error_code == "approval_required"
    assert "FLAGGED" not in surface.page.frame(name="content").inner_text("body")


def test_unlabelled_control_is_not_clicked_unattended(surface):
    detail = open_member(surface)
    target = find(detail, "link", name="")
    surface.context = RunContext(unattended=True, state_known=True)
    result = surface.act(Action("click", ref=target.ref))
    assert result.error_code == "approval_required" and not result.navigated


def test_enter_cannot_bypass_submit_approval(surface):
    surface.page.set_content(
        "<form action='/console/subaccount/confirm' method='post'>"
        "<label>Amount<input name='amount'></label><button>Go</button></form>"
    )
    target = find(surface.observe(), "textbox")
    assert surface.act(Action("type", ref=target.ref, value="25.00")).ok
    surface.context = RunContext(unattended=True, state_known=True)
    result = surface.act(Action("key", value="Enter"))
    assert result.error_code == "approval_required"


def test_enter_inside_child_frame_resolves_that_frames_form(surface):
    """Resolved in the content frame, the search form's route is a reviewed read-only
    POST. Resolved against the form-less frameset, the effect would be opaque and
    need approval - so success here proves the child frame's form was used."""
    login(surface)
    target = find(surface.observe(), "textbox", anchor="Member ID")
    assert surface.act(Action("type", ref=target.ref, value="12345")).ok
    surface.context = RunContext(unattended=True, state_known=True)
    result = surface.act(Action("key", value="Enter"))
    assert result.ok, result.error
    assert "/console/search" in surface.page.frame(name="content").url


def test_webforms_tab_postback_is_classified_by_its_form_route(surface):
    detail = open_member(surface)
    surface.context = RunContext(unattended=True, state_known=True)
    result = surface.act(Action("click", ref=find(detail, "cell", name="Accounts").ref))
    assert result.ok, result.error


def test_any_other_onclick_handler_stays_opaque(surface):
    surface.page.set_content(
        "<form method='post' action='/console/member'><table><tr>"
        "<td onclick=\"__doPostBack('a',''); fetch('/console/member/flag')\">Go</td>"
        "</tr></table></form>"
    )
    target = next(e for e in surface.observe().elements if e.name == "Go")
    surface.context = RunContext(unattended=True, state_known=True)
    assert surface.act(Action("click", ref=target.ref)).error_code == "approval_required"


def test_secret_literal_is_blocked_and_broker_value_cannot_be_extracted(surface):
    password = find(surface.observe(), "textbox", anchor="Password")
    assert (
        surface.act(Action("type", ref=password.ref, value="changeme")).error_code == "policy_block"
    )
    assert surface.act(Action("type", ref=password.ref, value="$secrets.meridian_password")).ok
    after = surface.observe()
    password = find(after, "textbox", anchor="Password")
    assert surface.extract_raw(password.ref) is None
    assert "changeme" not in repr(after) + repr(surface.events)


def test_refusal_and_changed_effect_cannot_reuse_approval(surface):
    detail = open_member(surface)
    target = find(detail, "link", name="Mark for Review")
    surface.approve = lambda _verdict, _action: False
    assert surface.act(Action("click", ref=target.ref)).error_code == "approval_required"

    def change_target(_verdict, _action):
        surface.page.frame(name="content").get_by_role("link", name="Mark for Review").evaluate(
            "e => e.href='/logout'"
        )
        return True

    surface.approve = change_target
    result = surface.act(Action("click", ref=target.ref))
    assert result.error_code == "approval_required"
    assert "state_changed" in result.error


def test_unknown_state_does_not_silently_allow_a_mutation(surface):
    detail = open_member(surface)
    surface.context = replace(surface.context, unattended=True, state_known=False)
    target = find(detail, "link", name="Mark for Review")
    assert surface.act(Action("click", ref=target.ref)).error == "unknown_state"


def test_field_changed_to_password_requires_broker_before_dispatch(surface):
    surface.page.set_content("<label>Entry<input></label>")
    target = find(surface.observe(), "textbox")
    surface.page.locator("input").evaluate("e => e.type='password'")
    result = surface.act(Action("type", ref=target.ref, value="must-not-be-filled"))
    assert result.error_code == "policy_block"
    assert surface.page.locator("input").input_value() == ""


@pytest.mark.parametrize("kind", ["click", "key", "dismiss"])
def test_submitter_override_cannot_bypass_route_allowlist(surface, kind):
    surface.page.set_content(
        "<form action='/console/search' method='get'>"
        "<label>Query<input name='q'></label>"
        "<button formaction='/outside'>Search</button></form>"
    )
    snapshot = surface.observe()
    target = find(snapshot, "textbox") if kind == "key" else find(snapshot, "button", name="Search")
    result = surface.act(Action(kind, ref=target.ref, value="Enter" if kind == "key" else None))
    assert result.error_code == "policy_block"
    assert surface.page.url.endswith("/")


def test_redirect_outside_allowed_routes_is_blocked_before_request():
    reached = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append(self.path)
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", "/forbidden")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"must not be reached")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with WebSurface.launch(policy=PolicyEngine(PolicyConfig.for_origin(base))) as s:
            result = s.act(Action("navigate", url=base + "/"))
            assert not result.ok and result.error_code == "policy_block"
            assert reached == ["/"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def _signed_in_origin(surface) -> str:
    open_member(surface)
    return surface.page.url.split("/console")[0]


def _sub_accounts(surface, origin: str) -> str:
    return surface.page.context.request.get(
        f"{origin}/console/member/accounts?member_id=12345").text()


EXPRESS = "/console/subaccount/express?member_id=12345&type=Money%20Market&cents=25000"


def test_a_redirect_into_a_commit_is_refused_while_the_agent_drives(surface, fresh_app):
    """Review finding P1: the action targets a harmless route; the server's redirect hop is
    the commit. Classified by its own URL alone, it ran unattended and reported success."""
    origin = _signed_in_origin(surface)
    surface.context = RunContext(unattended=True, state_known=True)
    result = surface.act(Action("navigate", url=f"{origin}{EXPRESS}"))
    assert not result.ok and result.error_code == "policy_block"
    assert result.error == "navigation_unauthorized_mutation"
    assert {"event": "navigation_blocked", "reason": "unauthorized_mutation"} in surface.events
    assert "SA-12345" not in _sub_accounts(surface, origin)


def test_a_page_that_navigates_itself_into_a_commit_is_refused(surface, fresh_app):
    """No action authorized it at all - a script, a meta refresh, a stray link."""
    from playwright.sync_api import Error as PlaywrightError

    origin = _signed_in_origin(surface)
    with pytest.raises(PlaywrightError):
        surface.page.goto(f"{origin}{EXPRESS}")
    assert "SA-12345" not in _sub_accounts(surface, origin)


def test_the_approved_commit_itself_still_goes_through(surface, fresh_app):
    """Authorization is per request: the Confirm an operator approved is exactly the
    request its action named, so it passes - and nothing else it causes would."""
    origin = _signed_in_origin(surface)
    surface.page.set_content(
        f"<a href='{origin}/console/subaccount/confirm?member_id=12345&type=Money Market"
        f"&amp;cents=25000'>Confirm</a>".replace("Money Market&amp;", "Money%20Market&"))
    target = find(surface.observe(), "link", name="Confirm")
    result = surface.act(Action("click", ref=target.ref))
    assert result.ok, result.error
    assert "SA-12345-01" in _sub_accounts(surface, origin)


def test_a_person_in_control_may_commit_by_hand(surface, fresh_app):
    """During a handoff the person drives: their Confirm is recorded and reconciled, not
    policed - the handed-over intent is what keeps it from being repeated."""
    origin = _signed_in_origin(surface)
    surface.human_in_control = True
    surface.page.goto(f"{origin}{EXPRESS}")
    assert "SA-12345-01" in _sub_accounts(surface, origin)


def test_one_approval_permits_one_request(surface, fresh_app):
    """Review finding P1: the commit answered 307 to itself and the browser sent it again
    under the same approval. Authorization is consumed as the first request is sent."""
    origin = _signed_in_origin(surface)
    surface.page.goto(f"{origin}/console/content?inject=resubmit")
    surface.page.set_content(
        f"<a href='{origin}/console/subaccount/confirm?member_id=12345"
        f"&type=Money%20Market&cents=25000'>Confirm</a>")
    target = find(surface.observe(), "link", name="Confirm")
    result = surface.act(Action("click", ref=target.ref))
    assert not result.ok and result.error == "navigation_unauthorized_mutation"
    accounts = _sub_accounts(surface, origin)
    assert "SA-12345-01" in accounts and "SA-12345-02" not in accounts


def test_row_reads_withhold_every_secret_cell(surface):
    """Review finding P1: starting from a public Type cell, the Password column came back
    raw. Every cell read is checked against the classified snapshot."""
    surface.page.set_content(
        "<table><tr><th>Type</th><th>Password</th><th>Balance</th></tr>"
        "<tr><td>Money Market</td><td>hunter2-not-real</td><td>$250.00</td></tr></table>")
    snap = surface.observe()
    kind = find(snap, "cell", name="Money Market")
    secret = next(e for e in snap.elements if e.role == "cell" and e.anchors[:1] == ("Password",))
    assert surface.extract_raw(secret.ref) is None
    values = surface.row_raw(kind.ref, ["Type", "Password", "Balance"])
    assert values == {"Type": "Money Market", "Password": None, "Balance": "$250.00"}
