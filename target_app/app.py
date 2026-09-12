"""Flask application factory for the Meridian Servicing Console fixture.

Hostility is the point (PLAN.md section 7.1). The console is a frameset with a
nested iframe, laid out in tables; grid element IDs encode row position and so
shift when the grid re-sorts; tabs are `__doPostBack` links that never change
the URL; form labels are adjacent table cells rather than `<label for>`; one
control has no accessible name at all; and one state-changing action is a GET.
Each of those exists to defeat a specific naive automation strategy, and each is
the fixture for a named test in PLAN.md section 9.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from target_app import chaos, subaccounts
from target_app.data import branch_roster, get_member

F = TypeVar("F", bound=Callable[..., Any])

SESSION_USER_KEY = "wp_user"


def _requires_login(view: F) -> F:
    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not session.get(SESSION_USER_KEY):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return cast(F, wrapper)


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("MERIDIAN_SECRET_KEY", "dev-only-not-a-real-secret")

    @app.before_request
    def _absorb_injection() -> None:
        chaos.absorb_query_param(request.args.get("inject"))

    # ---------------------------------------------------------------- health

    @app.get("/health")
    def health() -> Any:
        """Readiness probe. The test harness polls this before running a test."""
        return jsonify(status="ok", app="meridian-servicing-console")

    @app.post("/_fixture/reset")
    def fixture_reset() -> Any:
        """Test isolation for server-side state. Off the route allowlist, so no run can
        reach it - the automation under test never gets to wipe its own evidence."""
        subaccounts.reset()
        return jsonify(status="reset")

    # ------------------------------------------------------------ obstacles

    def _obstacles(member_id: str) -> Any | None:
        """Whatever stands between the operator and a sub-account screen, or None.

        Slowness, a lapsed sign-on, a notice page and a refused member are all *answers*
        the application gives; each is a fixture for a different class in PLAN.md 7.2.
        """
        time.sleep(chaos.delay_seconds())
        if chaos.session_lapses_now():
            session.pop(SESSION_USER_KEY, None)
            return redirect(url_for("login"))
        if chaos.interstitial_due():
            return render_template("interstitial.html", back=request.full_path)
        if not chaos.authorized(member_id):
            return render_template("not_authorized.html", member_id=member_id)
        return None

    # ----------------------------------------------------------------- login

    @app.route("/", methods=["GET", "POST"])
    def login() -> Any:
        error = None
        if request.method == "POST":
            expected_user = os.environ.get("MERIDIAN_USER", "operator1")
            expected_pass = os.environ.get("MERIDIAN_PASS", "changeme")
            if (
                request.form.get("txtUserId", "") == expected_user
                and request.form.get("txtPassword", "") == expected_pass
            ):
                session[SESSION_USER_KEY] = expected_user
                return redirect(url_for("console"))
            error = "The user ID or password you entered is not valid."
        return render_template("login.html", error=error)

    @app.get("/logout")
    def logout() -> Any:
        session.pop(SESSION_USER_KEY, None)
        return redirect(url_for("login"))

    # --------------------------------------------------------------- console

    @app.get("/console")
    @_requires_login
    def console() -> Any:
        """The frameset itself. Holds no content, so frame traversal is mandatory."""
        return render_template("console_frameset.html")

    @app.get("/console/nav")
    @_requires_login
    def console_nav() -> Any:
        return render_template("nav.html")

    @app.get("/console/content")
    @_requires_login
    def console_content() -> Any:
        return render_template("search_form.html")

    @app.route("/console/search", methods=["GET", "POST"])
    @_requires_login
    def console_search() -> Any:
        source = request.form if request.method == "POST" else request.args
        member_id = (source.get("ctl00$MainContent$txtMemberId") or "").strip()

        roster = branch_roster(member_id)
        rows = chaos.apply_to_roster(roster, member_id) if roster else []
        # An empty roster is a legitimate, expected business outcome - not an
        # error: HTTP 200 with a "No records found" banner.
        return render_template(
            "search_results.html",
            member_id=member_id,
            rows=rows,
            duplicates=lambda m: chaos.duplicates_view_link(m, member_id),
        )

    @app.route("/console/member", methods=["GET", "POST"])
    @_requires_login
    def console_member() -> Any:
        """Member detail. Tabs are postbacks, so the URL never reflects the tab."""
        source = request.form if request.method == "POST" else request.args
        member_id = (source.get("member_id") or "").strip()
        obstacle = _obstacles(member_id)
        if obstacle is not None:
            return obstacle
        if chaos.fails_hard():
            return render_template("server_error.html"), 500
        member = get_member(chaos.displayed_member_id(member_id))
        if member is None:
            return render_template("search_results.html", member_id=member_id, rows=[])

        event_target = request.form.get("__EVENTTARGET", "")
        # The UI only ever switches tabs by postback. The GET form is a deep link the
        # console never renders - the kind a read-only reconciliation probe navigates.
        tab = request.args.get("tab", "summary") if request.method == "GET" else "summary"
        if event_target.endswith("tabAccounts"):
            tab = "accounts"
        elif event_target.endswith("tabNotes"):
            tab = "notes"
        if tab not in ("summary", "accounts", "notes"):
            tab = "summary"

        return render_template(
            "member_detail.html",
            member=member,
            tab=tab,
            flagged=member.member_id in session.get("wp_flagged", []),
        )

    @app.get("/console/member/accounts")
    @_requires_login
    def console_member_accounts() -> Any:
        """Nested iframe body: the accounts grid lives one frame deeper."""
        member = get_member((request.args.get("member_id") or "").strip())
        if member is None:
            return "<html><body>No account data.</body></html>", 404
        rows = [*member.accounts, *subaccounts.opened_for(member.member_id)]
        return render_template("member_accounts.html", member=member, rows=rows)

    @app.get("/console/member/flag")
    @_requires_login
    def console_member_flag() -> Any:
        """A GET that mutates. Deliberate.

        The control is called "Mark for Review", which no irreversible-verb list
        would flag, and it is a link rather than a button. Only inspecting the
        resolved route reveals that it changes state - the fixture for T21
        (PLAN.md R-RISK-3).
        """
        member_id = (request.args.get("member_id") or "").strip()
        if get_member(member_id) is None:
            return redirect(url_for("console_content"))
        flagged = list(session.get("wp_flagged", []))
        if member_id not in flagged:
            flagged.append(member_id)
        session["wp_flagged"] = flagged
        return redirect(url_for("console_member", member_id=member_id))

    # ----------------------------------------------------- sub-account flow

    @app.get("/console/subaccount/new")
    @_requires_login
    def subaccount_new() -> Any:
        member_id = (request.args.get("member_id") or "").strip()
        obstacle = _obstacles(member_id)
        if obstacle is not None:
            return obstacle
        member = get_member(member_id)
        if member is None:
            return redirect(url_for("console_content"))
        return render_template(
            "subaccount_new.html", member=member, types=subaccounts.TYPES,
            submit=chaos.submit_label("Review"), error=None, values={},
        )

    @app.post("/console/subaccount/review")
    @_requires_login
    def subaccount_review() -> Any:
        member_id = (request.form.get("member_id") or "").strip()
        obstacle = _obstacles(member_id)
        if obstacle is not None:
            return obstacle
        member = get_member(member_id)
        if member is None:
            return redirect(url_for("console_content"))
        if chaos.fails_hard():
            return render_template("server_error.html"), 500

        kind = (request.form.get("ctl00$MainContent$ddlAccountType") or "").strip()
        typed = (request.form.get("ctl00$MainContent$txtDeposit") or "").strip()
        cents = subaccounts.parse_deposit(typed)
        minimum = subaccounts.MINIMUM_DEPOSIT_CENTS
        error = None
        if kind not in subaccounts.TYPES:
            error = "Select an account type."
        elif cents is None:
            error = "Initial deposit must be an amount, for example 250.00."
        elif cents < minimum or chaos.refuses_input():
            error = f"Initial deposit must be at least ${minimum // 100:,}.{minimum % 100:02d}."
        if error is not None:
            return render_template(
                "subaccount_new.html", member=member, types=subaccounts.TYPES,
                submit=chaos.submit_label("Review"), error=error,
                values={"kind": kind, "deposit": typed},
            )
        assert cents is not None
        return render_template(
            "subaccount_review.html", member=member, kind=kind, cents=cents,
            deposit=f"${cents // 100:,}.{cents % 100:02d}",
        )

    @app.get("/console/subaccount/confirm")
    @_requires_login
    def subaccount_confirm() -> Any:
        """The mutation, behind a GET (PLAN.md R-RISK-3)."""
        member_id = (request.args.get("member_id") or "").strip()
        obstacle = _obstacles(member_id)
        if obstacle is not None:
            return obstacle
        member = get_member(member_id)
        kind = (request.args.get("type") or "").strip()
        cents = request.args.get("cents", type=int)
        if member is None or kind not in subaccounts.TYPES or cents is None:
            return redirect(url_for("console_content"))
        if chaos.serves_stale_confirmation():
            # Nothing is committed: the page belongs to another member, days ago.
            return render_template("subaccount_confirm.html", opened=subaccounts.stale())
        opened = subaccounts.record(member_id, kind, cents)
        if chaos.drops_response_after_commit():
            return render_template("dropped.html"), 502
        return render_template("subaccount_confirm.html", opened=opened)

    @app.get("/console/subaccount/delete")
    @_requires_login
    def subaccount_delete() -> Any:
        """What the unlabelled icon does: deletes the member's most recent sub-account."""
        member_id = (request.args.get("member_id") or "").strip()
        subaccounts.discard_last(member_id)
        return redirect(url_for("subaccount_new", member_id=member_id))

    return app
