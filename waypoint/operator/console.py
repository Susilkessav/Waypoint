"""A browser view of the operator queue - the same rows `waypoint intervene` works on.

This adds no authority. R-PROC-3 is absolute: there is no remote-control channel, so the
console can show and change SQLite state (leases, interventions, intents) and nothing else.
Taking control here means exactly what taking it at the CLI means - the lease moves, and the
person still has to drive the real headed browser the run owns (R-PROC-1).

Every action calls the same ``InterventionStore``/``IntentStore`` method the CLI calls, so
the two interfaces cannot drift into disagreeing about who holds control.
"""

from __future__ import annotations

import getpass
import math
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from waypoint.session.escalation import ACTIVE, InterventionError, InterventionStore, Status
from waypoint.session.intents import UNRESOLVED, IntentError, IntentStore, Resolution
from waypoint.session.lease import LeaseStore
from waypoint.session.store import StateStore

DEFAULT_TTL_S = 900.0
EVERY_STATUS: tuple[Status, ...] = (
    "open", "taken", "returned", "aborted", "expired", "resolved")
OPERATOR_RESOLUTIONS: tuple[Resolution, ...] = ("completed", "not_completed")
"""What a person may record. ``confirmed_after_handoff`` is the engine's to write, not a
person's - it means the return ladder verified the state itself (R-REC-4)."""


def create_console(state_db: Path, secret_key: str | None = None) -> Flask:
    app = Flask(__name__)
    # The cookie carries flash messages and nothing else - no identity, no authorization -
    # but a per-process random key still beats a constant somebody could predict.
    app.secret_key = secret_key or secrets.token_urlsafe(32)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")

    @app.before_request
    def protect_browser_session() -> None:
        if urlsplit(request.host_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            abort(400)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if origin is not None and origin != request.host_url.rstrip("/"):
                abort(403)
            token = request.form.get("csrf_token", "")
            expected = session.get("csrf_token")
            if not expected or not secrets.compare_digest(token, expected):
                abort(403)
        session.setdefault("csrf_token", secrets.token_urlsafe(32))
    store = StateStore(state_db)
    queue = InterventionStore(store)
    intents = IntentStore(store)
    leases = LeaseStore(store)

    def lease_for(session_id: str) -> dict[str, Any] | None:
        lease = leases.read(session_id)
        if lease is None:
            return None
        now = time.time()
        return {
            "holder": lease.effective_holder(now),
            "generation": lease.generation,
            "expires_in_s": max(0, int(lease.expires_at - now)),
        }

    def act(what: str, run: Any) -> Any:
        """Run one store call, turning its refusal into a message instead of a traceback."""
        try:
            run()
        except (InterventionError, IntentError) as exc:
            flash(f"{what} refused: {exc}", "error")
        else:
            flash(what, "ok")
        return redirect(url_for("dashboard"))

    @app.get("/")
    def dashboard() -> str:
        show_all = request.args.get("all") == "1"
        return render_template(
            "dashboard.html",
            interventions=queue.list(EVERY_STATUS if show_all else ACTIVE),
            intents=intents.list(UNRESOLVED),
            show_all=show_all,
            now=time.time(),
            db=str(state_db),
        )

    @app.get("/interventions/<intervention_id>")
    def intervention(intervention_id: str) -> Any:
        try:
            found = queue.get(intervention_id)
        except InterventionError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))
        return render_template(
            "intervention.html", iv=found, lease=lease_for(found.session_id), now=time.time()
        )

    @app.post("/interventions/<intervention_id>/take")
    def take(intervention_id: str) -> Any:
        operator = (request.form.get("operator") or "").strip() or getpass.getuser()
        try:
            ttl = float(request.form.get("ttl") or DEFAULT_TTL_S)
            if not math.isfinite(ttl) or not 0 < ttl <= 3600:
                raise ValueError
        except ValueError:
            abort(400, "lease duration must be between 0 and 3600 seconds")
        return act(
            f"control taken by {operator}; drive the run's own browser window",
            lambda: queue.take(intervention_id, operator, ttl),
        )

    @app.post("/interventions/<intervention_id>/return")
    def give_back(intervention_id: str) -> Any:
        return act(
            "control returned; the run re-checks the screen before it continues",
            lambda: queue.give_back(intervention_id),
        )

    @app.post("/interventions/<intervention_id>/abort")
    def abort_run(intervention_id: str) -> Any:
        return act("aborted; the run ends escalated", lambda: queue.abort(intervention_id))

    @app.post("/intents/<intent_id>/reconcile")
    def reconcile(intent_id: str) -> Any:
        outcome = next(
            (r for r in OPERATOR_RESOLUTIONS if r == request.form.get("outcome")), None
        )
        if outcome is None:
            flash(f"outcome must be one of {', '.join(OPERATOR_RESOLUTIONS)}", "error")
            return redirect(url_for("dashboard"))
        operator = (request.form.get("operator") or "").strip() or getpass.getuser()
        return act(
            f"intent {intent_id} recorded as {outcome}",
            lambda: intents.advance(intent_id, "reconciled", by=operator, resolution=outcome),
        )

    return app
