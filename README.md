# Waypoint

**Record-once, replay-many automation for legacy back-office UIs that have no API.**

An LLM discovers a path through a legacy UI once. That path is compiled into a typed,
versioned **capability**. Production runs replay the capability deterministically, with no
model deciding anything. When replay meets a state it doesn't recognize, control transfers
to a human on the same live session, and what the human does is recorded as evidence.

Built for the interface.ai take-home (Computer-Use Automation System).

---

## Status: A1–A5 implemented

The target app, browser surface, locator ladder, policy engine, redactor, secret broker,
capability artifact (schema, approval) and deterministic replay engine are implemented and
covered by unit and live-browser tests. Discovery (A6) and operator handoff (A7) remain
planned: `discover`, `compile`, `intervene` and `catalog` exit with “not implemented.”
Until A7, an escalation ends the run with evidence rather than handing the session to a person.

| Component | Status | Milestone |
|---|---|---|
| Design and execution rules | ✅ complete — [PLAN.md](PLAN.md) §6 | — |
| Repo scaffold, CI, test harness | ✅ complete | A1 |
| Target app (hostile legacy fixture) | ✅ complete | A2 |
| Surface port, AX perception, sensitivity classifier | ✅ complete | A3 |
| Locator ladder, policy engine, redactor, secret broker | ✅ complete | A4 |
| Artifact schema, replay engine, approval | ✅ complete | A5 |
| Discovery loop (real LLM), compiler | ⬜ not started | A6 |
| Control lease, escalation, live handoff | ⬜ not started | A7 |
| Reconciliation, recovery, outcomes | ⬜ not started | B |
| Tenant overrides, capability catalog | ⬜ conditional | C |

`PLAN.md` is a working document and is not part of the submission (it is gitignored).
The design write-up is [REPORT.md](REPORT.md).

---

## What this is meant to demonstrate

Banks and credit unions run a long tail of back-office applications with no API — core
banking screens, servicing tools, admin consoles — where the only way in is to drive the UI
the way a human operator would. Waypoint is the integration layer that lets an AI agent do
that **reliably and cheaply**, by using a model to figure the flow out once and then never
again.

The design is built around one safety posture, which the rules in `PLAN.md` §6 all follow
from:

> **The system never acts on the absence of evidence.**
> Not finding a confirmation is not proof the operation didn't happen.
> Not finding a row is not permission to click whatever occupies its old position.
> Not finding a mismatch is not proof the right member is on screen.

---

## Run the implemented components

```bash
make install                  # uv sync --locked --extra dev  +  playwright chromium
make test                     # unit tests and Chromium integration tests; no API key
make lint                     # Ruff and mypy
make app                      # target app on http://localhost:8080
uv run waypoint version       # installed CLI smoke check
```

`uv.lock` is committed, and both `make install` and CI run `uv sync --locked` — which
fails rather than silently re-resolving if the lock is stale. Dependencies cannot drift
without a change to this repository. Use `make lock` to update them deliberately.

A4 verification: **168 tests passed**, Ruff and mypy passed (22 source files), and the
source distribution/wheel built successfully with the policy YAML included. This was
verified using a clean virtual environment outside Desktop; no live LLM was used.

On macOS, keep the virtual environment outside iCloud-synced Desktop/Documents folders.
Hidden `.pth` files can break the installed CLI even while imports from the project folder
work. For a repository on Desktop, set `UV_PROJECT_ENVIRONMENT` to an absolute path outside
those folders before running the commands above, for example
`export UV_PROJECT_ENVIRONMENT="$HOME/.local/share/waypoint/venv"`.

`WebSurface.launch()` exposes the implemented Python adapter. `observe()` returns sanitized,
generation-scoped references; `synthesize()` creates a serializable locator bundle;
`resolve()` returns `Found`, `Ambiguous` or `NotFound`; and `act()` applies policy before dispatch.
An action result reports browser quiescence, not application-level success. An `in_flight`
result blocks further writes until quiescence; checkpoints and reconciliation arrive in A5/B.

Policy defaults live in [`waypoint/policy/policy.yaml`](waypoint/policy/policy.yaml).
Host and port must match the allowlist, including document redirects. Tests explicitly
configure their ephemeral server origin. `RunContext` defaults to unattended and unknown
state; a trusted caller will supply recognized state and declared risk. Attended approval
currently uses an injected callback. The operator workflow and durable approval records
are later milestones. Arbitrary JavaScript effects are not inferred: known mutating routes,
form effects, control names and opaque inline handlers drive the current classifier.

Credentials use `$secrets.meridian_user` / `$secrets.meridian_password`, resolved directly
into authorized fields from `MERIDIAN_USER` / `MERIDIAN_PASS`. Export those variables when
using the adapter; `.env` is not loaded automatically. Raw credentials are withheld from
observations, extraction, error messages and screenshots. Public UI chrome remains visible;
classification of previously unknown application layouts still needs tenant-specific review.

## Usage

### Replay (implemented)

Replay runs an **approved** artifact with no model in the loop. The committed
`lookup_member_balance` 1.0.0 is approved; any edit to it revokes that approval until
`waypoint approve` is run again after review.

```bash
make app                                   # terminal 1: target app on :8080 (blocking)
```

```bash
# terminal 2 - fixture credentials from .env.example; .env is not loaded automatically
export MERIDIAN_USER=operator1 MERIDIAN_PASS=changeme

waypoint replay lookup_member_balance --input member_id=12345   # success: $4,281.19, active
waypoint replay lookup_member_balance --input member_id=67890   # same artifact, another member
waypoint replay lookup_member_balance --input member_id=00000   # business_outcome, exit 0
waypoint replay lookup_member_balance --input member_id=12345 --inject wrong_member  # escalated
waypoint replay lookup_member_balance --input member_id=12345 --inject ambiguous     # escalated
```

Add `--headed` to watch the browser. stdout is the caller's channel and carries outputs in
full; `evidence/runs/<run_id>/` holds the redacted record (artifact copy, events, result,
screenshots). Exit codes: `0` success or business outcome, `1` failure, `3` escalated.
`waypoint approve <id> --note "..."` re-approves after a reviewed edit; it refuses while any
approval gate is open.

### Planned

Live discovery (A6) will require `ANTHROPIC_API_KEY`; a recorded run will replay offline
from a **cassette** keyed by snapshot hash, with no key and no network:

```bash
waypoint discover --capability-id lookup_member_balance --llm cassette   # PLANNED (A6)
watch -n 2 'waypoint intervene list'                                     # PLANNED (A7)
waypoint replay lookup_member_balance --input member_id=12345 --inject interstitial  # B1
waypoint replay lookup_member_balance --input member_id=12345 --inject 500           # B1
```

The `interstitial` and `500` injections arrive with milestone B1; until then the target app
ignores unknown injection names, so those two commands would run as ordinary replays.

The full ten-command path, including the irreversible capability and the live-session
handoff, is in [PLAN.md](PLAN.md) §11.

---

## Repository tour

| Path | What lives there |
|---|---|
| `waypoint/surface/` | The `Surface` port, CDP accessibility-tree perception, sensitivity classification, the locator ladder, and a desktop stub |
| `waypoint/policy/` | Allowlist and risk classification, the redactor, the secret broker |
| `waypoint/signatures/` | Declarative state recognizers, shared by discovery and replay |
| `waypoint/discovery/` | The LLM loop, prompts, transcript writer, cassette |
| `waypoint/compiler/` | Transcript → artifact |
| `waypoint/artifact/` | The capability schema and approval gates — the focal point |
| `waypoint/replay/` | The deterministic executor, recovery, reconciliation |
| `waypoint/session/` | Control lease, escalation, human action log, intent records |
| `waypoint/catalog/` | Approved-capability registry and invocation API |
| `target_app/` | The hostile legacy fixture. **Nothing in `waypoint/` imports it** — enforced by a test |
| `capabilities/` | Compiled, versioned capability artifacts |
| `evidence/` | Per-run logs, screenshots, accessibility snapshots, results |

---

## What is deliberately mocked or stubbed

Full reasoning in [REPORT.md](REPORT.md) §7.

- **Desktop surface** — the port is defined and `DesktopSurface` raises `NotImplementedError`
  with each method's UIA/AXAPI equivalent documented. The seam is real; the implementation is absent.
- **Operator console** — a planned CLI (`waypoint intervene`) rather than a web UI;
  control-transfer storage and the operator workflow are not implemented yet.
- **Credential storage** — environment variables behind a `SecretBroker` interface that a real
  vault would drop into.
- **Process-death recovery** — write-ahead intent records and reconciliation are planned;
  automatic browser reattachment is outside the current scope.
- **No queues, workers, or multi-tenant infrastructure** — the brief explicitly does not reward it.

---

## License

Unlicensed take-home submission.
