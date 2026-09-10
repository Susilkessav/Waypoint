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

from target_app import chaos
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
        member = get_member(member_id)
        if member is None:
            return render_template("search_results.html", member_id=member_id, rows=[])

        event_target = request.form.get("__EVENTTARGET", "")
        tab = "summary"
        if event_target.endswith("tabAccounts"):
            tab = "accounts"
        elif event_target.endswith("tabNotes"):
            tab = "notes"

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
        return render_template("member_accounts.html", member=member)

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

    return app
