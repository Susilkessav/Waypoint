"""WebSurface against the live target app, driven through refs alone.

No CSS selectors, no element IDs: every action below targets an element the
surface perceived through the accessibility tree - including the eight identical
View links inside the frameset and a grid one iframe deeper.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest

from waypoint.policy.engine import PolicyConfig, PolicyEngine, RunContext
from waypoint.policy.secrets import SecretBroker
from waypoint.surface.ports import Action, InFlight, Settled, UIElement, UISnapshot
from waypoint.surface.sensitivity import Binding
from waypoint.surface.web import WebSurface

pytestmark = pytest.mark.browser

MEMBER = Binding("member_id", "12345", "internal")
BOUND = "‹$inputs.member_id›"


@pytest.fixture
def surface(live_server: str) -> Iterator[WebSurface]:
    with WebSurface.launch(
        bindings=(MEMBER,),
        policy=PolicyEngine(PolicyConfig.for_origin(live_server)),
        context=RunContext(unattended=False, state_known=True),
        secrets=SecretBroker(environ={"MERIDIAN_USER": "operator1", "MERIDIAN_PASS": "changeme"}),
        approve=lambda _verdict, _action: True,
    ) as s:
        assert s.act(Action("navigate", url=f"{live_server}/")).ok
        yield s


def find(
    snap: UISnapshot, role: str, *, name: str | None = None, anchor: str | None = None
) -> UIElement:
    hits = [
        e
        for e in snap.elements
        if e.role == role
        and (name is None or e.name == name)
        and (anchor is None or anchor in e.anchors)
    ]
    assert len(hits) == 1, f"{role} name={name!r} anchor={anchor!r}: {len(hits)} matches"
    return hits[0]


def login(s: WebSurface) -> None:
    snap = s.observe()
    assert s.act(
        Action(
            "type", ref=find(snap, "textbox", anchor="User ID").ref, value="$secrets.meridian_user"
        )
    ).ok
    assert s.act(
        Action(
            "type",
            ref=find(snap, "textbox", anchor="Password").ref,
            value="$secrets.meridian_password",
        )
    ).ok
    result = s.act(Action("click", ref=find(snap, "button", name="Sign On").ref))
    assert result.ok and result.navigated


def search(s: WebSurface, member_id: str) -> UISnapshot:
    snap = s.observe()
    assert s.act(
        Action("type", ref=find(snap, "textbox", anchor="Member ID").ref, value=member_id)
    ).ok
    result = s.act(Action("click", ref=find(snap, "button", name="Search").ref))
    assert result.ok and result.navigated
    return s.observe()


def open_member(s: WebSurface) -> UISnapshot:
    login(s)
    grid = search(s, "12345")
    assert len([e for e in grid.elements if e.role == "link" and e.name == "View"]) == 8
    view = find(grid, "link", name="View", anchor=BOUND)  # the one row the model may identify
    assert s.act(Action("click", ref=view.ref)).navigated
    return s.observe()


def test_the_anchored_view_link_opens_the_right_member(surface: WebSurface) -> None:
    detail = open_member(surface)
    assert any(e.name == "Member Profile" for e in detail.elements)
    content_url = dict(detail.frame_urls)[("main", "content")]
    assert content_url.endswith(f"member_id={BOUND}")


def test_snapshot_is_sanitized_but_extract_raw_is_real(surface: WebSurface) -> None:
    detail = open_member(surface)
    name_cell = find(detail, "LayoutTableCell", anchor="Name")
    assert name_cell.name == "‹redacted:17 chars›"
    assert surface.extract_raw(name_cell.ref) == "Dolores Whitfield"


def test_a_typed_password_never_reaches_the_snapshot(surface: WebSurface) -> None:
    snap = surface.observe()
    pw = find(snap, "textbox", anchor="Password")
    assert surface.act(Action("type", ref=pw.ref, value="$secrets.meridian_password")).ok
    after = surface.observe()
    assert find(after, "textbox", anchor="Password").value == "‹secret›"
    assert "changeme" not in repr(after)


def test_a_stale_ref_fails_loudly_instead_of_acting(surface: WebSurface) -> None:
    login(surface)
    old = find(surface.observe(), "button", name="Search")
    surface.act(Action("navigate", url=surface.page.url))  # new documents, stamps gone
    result = surface.act(Action("click", ref=old.ref))
    assert not result.ok and result.error is not None and "stale" in result.error


def test_the_nested_iframe_grid_is_perceived_and_boxed(surface: WebSurface) -> None:
    detail = open_member(surface)
    tab = find(detail, "cell", name="Accounts")
    assert surface.act(Action("click", ref=tab.ref)).ok
    accounts = surface.observe()
    balances = [
        e
        for e in accounts.elements
        if e.role == "cell" and e.frame_path[-1].startswith("iframe#") and "Balance" in e.anchors
    ]
    assert len(balances) == 2  # savings and checking rows
    assert all(
        b.bbox is not None and tab.bbox is not None and b.bbox[1] > tab.bbox[1] for b in balances
    )


def test_evidence_is_a_png_with_a_sanitized_snapshot(surface: WebSurface) -> None:
    open_member(surface)
    evidence = surface.capture_evidence()
    assert evidence.screenshot_png[:8] == b"\x89PNG\r\n\x1a\n"
    assert "Dolores" not in repr(evidence.snapshot)


def test_quiesce_settles_on_an_idle_page(surface: WebSurface) -> None:
    assert isinstance(surface.quiesce(), Settled)


def test_reobserving_does_not_reassign_an_old_ref(surface: WebSurface) -> None:
    surface.page.set_content("<button onclick=\"window.clicked='old'\">Old target</button>")
    old = find(surface.observe(), "button", name="Old target")
    surface.page.set_content("<button onclick=\"window.clicked='new'\">Different target</button>")
    new = find(surface.observe(), "button", name="Different target")
    result = surface.act(Action("click", ref=old.ref))
    assert old.ref != new.ref and not result.ok
    assert surface.page.evaluate("window.clicked") is None


def test_navigation_error_never_exposes_query_values(surface: WebSurface) -> None:
    surface.policy.config.allowlist.hosts.append("127.0.0.1:1")
    result = surface.act(Action("navigate", url="http://127.0.0.1:1/?token=test-secret-789"))
    assert not result.ok and result.error_code == "action_failed"
    assert "test-secret-789" not in repr(result)


def test_pending_request_is_not_reported_as_completed(surface: WebSurface) -> None:
    held = []
    surface.page.route("**/console/slow", lambda route: held.append(route))
    surface.page.set_content("<button onclick=\"fetch('/console/slow')\">Start</button>")
    button = find(surface.observe(), "button", name="Start")
    result = surface.act(Action("click", ref=button.ref))
    assert not result.ok and result.error_code == "in_flight"
    assert isinstance(result.quiescence, InFlight)
    assert surface.act(Action("click", ref=button.ref)).error_code == "in_flight"
    for route in held:
        route.fulfill(body="done")
    assert isinstance(surface.quiesce(), Settled)


def test_screenshot_pixels_cover_the_sensitive_cell(surface: WebSurface) -> None:
    surface.page.set_content(
        "<table><tr><th>Name</th></tr><tr><td>Synthetic Person</td></tr></table>"
    )
    evidence = surface.capture_evidence()
    cell = find(evidence.snapshot, "cell", anchor="Name")
    assert cell.bbox is not None
    pixels = surface.page.evaluate(
        """async ({png, box}) => {
        const img = new Image(); img.src = 'data:image/png;base64,' + png;
        await img.decode();
        const canvas = document.createElement('canvas');
        canvas.width = img.width; canvas.height = img.height;
        const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0);
        const [x,y,w,h] = box;
        const data = ctx.getImageData(x+2, y+2, w-4, h-4).data;
        return [...data].every((value,i) => value === (i%4 === 3 ? 255 : 0));
    }""",
        {"png": base64.b64encode(evidence.screenshot_png).decode(), "box": cell.bbox},
    )
    assert pixels, "sensitive cell must be an opaque black rectangle"
