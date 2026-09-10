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
    snap = surface.observe()
    target = find(snap, "textbox", anchor="Password")
    assert surface.act(Action("type", ref=target.ref, value="$secrets.meridian_password")).ok
    surface.context = RunContext(unattended=True, state_known=True)
    result = surface.act(Action("key", value="Enter"))
    assert result.error_code == "approval_required"
    assert surface.page.url.endswith("/")


def test_enter_inside_child_frame_checks_that_frames_form(surface):
    login(surface)
    target = find(surface.observe(), "textbox", anchor="Member ID")
    assert surface.act(Action("type", ref=target.ref, value="12345")).ok
    surface.context = RunContext(unattended=True, state_known=True)
    result = surface.act(Action("key", value="Enter"))
    assert result.error_code == "approval_required"
    assert surface.page.frame(name="content").url.endswith("/console/content")


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
