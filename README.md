# Waypoint

**Record-once, replay-many automation for legacy back-office UIs that have no API.**

An LLM discovers a path through a legacy UI once. That path is compiled into a typed,
versioned **capability**. Production runs replay the capability deterministically, with no
model deciding anything. When replay meets a state it doesn't recognize, control transfers
to a human on the same live session, and what the human does is recorded as evidence.

Built for the interface.ai take-home (Computer-Use Automation System).

---

## Status: A1–A7 implemented

The target app, browser surface, locator ladder, policy engine, redactor, secret broker,
capability artifact (schema, approval), deterministic replay engine, discovery and compiler
(A6), and live operator handoff (A7) are implemented and covered by unit and live-browser
tests. The genuine model-driven discovery run the brief requires has been recorded with
Claude Haiku 4.5 and is kept, with its cassette, in
[`evidence/runs/showcase-discovery-haiku/`](evidence/runs/showcase-discovery-haiku).
`catalog` (C2) exits with “not implemented.”

| Component | Status | Milestone |
|---|---|---|
| Design and execution rules | ✅ complete — [PLAN.md](PLAN.md) §6 | — |
| Repo scaffold, CI, test harness | ✅ complete | A1 |
| Target app (hostile legacy fixture) | ✅ complete | A2 |
| Surface port, AX perception, sensitivity classifier | ✅ complete | A3 |
| Locator ladder, policy engine, redactor, secret broker | ✅ complete | A4 |
| Artifact schema, replay engine, approval | ✅ complete | A5 |
| Discovery loop (real LLM), compiler | ✅ complete — genuine Haiku 4.5 run recorded | A6 |
| Control lease, escalation, live handoff | ✅ complete | A7 |
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

Verification: `make test` - **307 passed**; `make lint` - Ruff and mypy clean
(43 source files). Every test runs without an API key; the one live model run is recorded
as evidence rather than repeated in the suite.

On macOS, keep the virtual environment outside iCloud-synced Desktop/Documents folders.
Hidden `.pth` files can break the installed CLI even while imports from the project folder
work. For a repository on Desktop, set `UV_PROJECT_ENVIRONMENT` to an absolute path outside
those folders before running the commands above, for example
`export UV_PROJECT_ENVIRONMENT="$HOME/.local/share/waypoint/venv"`.

`WebSurface.launch()` exposes the implemented Python adapter. `observe()` returns sanitized,
generation-scoped references; `synthesize()` creates a serializable locator bundle;
`resolve()` returns `Found`, `Ambiguous` or `NotFound`; and `act()` applies policy before dispatch.
An action result reports browser quiescence, not application-level success. An `in_flight`
result blocks further writes until quiescence; reconciliation of irreversible steps is B.

Policy defaults live in [`waypoint/policy/policy.yaml`](waypoint/policy/policy.yaml).
Host and port must match the allowlist, including document redirects. Tests explicitly
configure their ephemeral server origin. `RunContext` defaults to unattended and unknown
state; a trusted caller will supply recognized state and declared risk. Attended approval
uses an injected callback; an escalated run hands off through `waypoint intervene` (below).
Arbitrary JavaScript effects are not inferred: known mutating routes,
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
# terminal 2 - the CLI reads .env from the working directory (copy .env.example);
# anything already exported wins over it.
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

### Discovery (implemented)

Discovery drives the target app with a model, then compiles what it did into a **draft**
artifact that must be reviewed and approved before it can replay. It is attended: an action
the policy marks risky is put to you at the terminal, and refused when there is none.

Discovery is verified as it goes, because a nomination can only be checked while the page
is still open: each expectation is tested against the screen its action produced, a `finish`
is put through the compiler's own checks, and what fails goes back to the model to correct
(`recheck`). What still cannot be verified from one run - that a check will hold for the
*next* record - is enforced by the compiler, which refuses a checkpoint asserting an
output's own value.

```bash
export ANTHROPIC_API_KEY=...   # or put it in .env; live runs only
waypoint discover --capability-id lookup_member_balance \
  --goal "Look up member {{member_id}} and read their current savings balance" \
  --entry http://127.0.0.1:8080/console \
  --bind member_id=12345:string:internal \
  --expect-output savings_balance:money:pii --expect-output account_status:string:internal
```

A live run uses Claude Haiku 4.5 (`claude-haiku-4-5`), the cheapest current model;
`--model` selects a more capable one. It records a **cassette** in its evidence directory. The
same command with `--llm cassette --cassette evidence/runs/<run_id>/cassette.json`
reproduces it with no key and no network, as long as the screens still match - a changed
application stops the replay rather than clicking stale decisions. `waypoint compile
evidence/runs/<run_id>` recompiles a saved transcript. Neither command overwrites an
existing artifact; the draft gets the next free version, and `waypoint replay <id>` keeps
running the highest *approved* version, so a new draft never takes over by existing.

The recorded Haiku run is committed with its evidence; it compiled with no open gates into
[`capabilities/lookup_member_balance/1.1.0.json`](capabilities/lookup_member_balance/1.1.0.json),
which is **left unapproved on purpose** - approving it is the human review step. Approved in
a scratch copy it returns `$4,281.19 / active` for 12345 and `$912.04 / dormant` for 67890
from the same artifact. It declares no business outcomes: the model never searched a member
who does not exist, so `member_not_found` is not in it, and 00000 escalates instead of
returning an outcome. Reproduce the run without a key or network:

```bash
waypoint discover --capability-id lookup_member_balance \
  --goal "Look up member {{member_id}} and read their current savings balance" \
  --entry http://127.0.0.1:8080/console \
  --bind member_id=12345:string:internal \
  --expect-output savings_balance:money:pii --expect-output account_status:string:internal \
  --llm cassette --cassette evidence/runs/showcase-discovery-haiku/cassette.json
```

### Operator handoff (implemented)

With `--handoff`, an escalation pauses the run instead of ending it. The browser window stays
open (`--handoff` implies `--headed`), an intervention is written to `.waypoint/state.db`, and
the run releases its control lease and polls every 500 ms.

```bash
# terminal 2
waypoint replay lookup_member_balance --input member_id=12345 --inject ambiguous --handoff
```

```bash
# terminal 3 - the operator
waypoint intervene list                 # id, status, capability, step, reason
waypoint intervene show <id>            # expected vs observed, screenshot and snapshot paths
waypoint intervene take <id>            # the lease moves to you; drive the headed window
waypoint intervene return <id>          # hand back; the run re-checks the screen
waypoint intervene abort <id>           # or end the run: exit 3, with evidence
```

On return the run takes control back under a new lease generation - anything still holding
the old one is refused by the surface - and walks a ladder with no default branch: a declared
outcome, the postcondition, the escalated step's checkpoint, then a declared **resume
point**; otherwise it escalates again (at most two handoffs per run). In the `ambiguous`
demo, open the member's record from their row and return: the run resumes at the Accounts
tab and prints the balance. Returning without doing anything escalates again - the results
page satisfies an earlier checkpoint, but that step is not a resume point, so the run does
not guess.

What the operator did is logged, redacted, in `evidence/runs/<run_id>/human/actions.jsonl`:
control transfers, clicks, field changes (length only, none for passwords), submits and
navigations, next to a before-screenshot and a before/after diff (`handoff1_diff.json`). If
nobody takes control within 30 minutes, or the operator's lease lapses (`take --ttl`,
default 900 s), the run ends escalated instead of resuming.

### Planned

Still to come:

```bash
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
- **Operator console** — a CLI (`waypoint intervene`) rather than a web UI. It reads and
  writes the same SQLite tables a web page would, so a page would be a view over an unchanged
  model: `list`, `show`, `take`, `return`, `abort`, plus `intents` and `reconcile`.
- **Credential storage** — environment variables behind a `SecretBroker` interface that a real
  vault would drop into.
- **Process-death recovery** — write-ahead intent records are written before any irreversible
  dispatch and block a repeat of the same operation until an operator reconciles it
  (`waypoint intervene intents` / `reconcile`). The automatic probe that would answer
  "did it happen?" without a human arrives in B; browser reattachment is out of scope.
- **No queues, workers, or multi-tenant infrastructure** — the brief explicitly does not reward it.

---

## License

Unlicensed take-home submission.
