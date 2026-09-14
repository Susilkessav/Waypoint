# Waypoint

**Record-once, replay-many automation for legacy back-office UIs that have no API.**

A model discovers a path through a legacy UI once. That path is compiled into a typed,
versioned **capability**. Production runs replay the capability deterministically, with no
model deciding anything. When replay meets a state it will not act on, control transfers to a
person on the same live session, and what they do is recorded as evidence.

Built for the interface.ai take-home (Computer-Use Automation System). The design write-up is
[REPORT.md](REPORT.md); curated run evidence is indexed in [evidence/README.md](evidence/README.md).

> **The system never acts on the absence of evidence.** Not finding a confirmation is not proof
> the operation didn't happen. Not finding a row is not permission to click whatever occupies
> its old position. Not finding a mismatch is not proof the right member is on screen.

## Status

**Built and tested** (`make test`: **423 passed**, no API key needed; Ruff and mypy clean):

- Discovery with a live model, verified while the page is open, compiled into a draft artifact;
  two genuine Claude Haiku 4.5 runs are in `evidence/`.
- Artifact schema with content-bound approval and recomputed approval gates.
- Deterministic replay: locator ladder, checkpoints asserting identity, typed business outcomes,
  bounded recovery (dismiss, wait, re-authenticate).
- Effect-based risk policy inside the browser surface, perception-time redaction, a secret broker.
- Live handoff to a person through a control lease, with a recorded human action log.
- Three-way reconciliation of irreversible steps: write-ahead intents and a read-only probe.
- A deliberately hostile target app (frameset, postback tabs, a mutating GET, an unlabeled
  destructive control) with every failure injection used below.

**Not built:** tenant variants (overrides are in the schema, not applied), the capability
catalog, and a desktop surface beyond its stub. See [REPORT.md §7](REPORT.md#7-cuts).

## Setup

```bash
make install                  # uv sync --locked --extra dev, plus Playwright Chromium
make test                     # unit and Chromium integration tests; no API key
make lint                     # Ruff and mypy
```

`uv.lock` is committed, and `make install` and CI both run `uv sync --locked`, which fails
rather than re-resolving if the lock is stale.

The demo below calls `waypoint` directly. That command lives in the project's virtual
environment, so activate it once per terminal (or prefix each command with `uv run`):

```bash
source .venv/bin/activate     # or: source "$UV_PROJECT_ENVIRONMENT/bin/activate"
```

**macOS:** keep the virtual environment outside iCloud-synced folders. iCloud hides `.pth` files
there, and the installed `waypoint` command then cannot import its own package:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.local/share/waypoint/venv"
```

### Environment variables

The CLI reads `.env` from the working directory (copy `.env.example`); variables already
exported take precedence.

| Variable | Used for |
|---|---|
| `ANTHROPIC_API_KEY` | Live discovery only. Replay, handoff and cassette runs never need it. |
| `MERIDIAN_USER`, `MERIDIAN_PASS` | The target app's fictional operator sign-on (`operator1` / `changeme`), injected by the secret broker. |
| `PORT` | Port for `make app` (default 8080). |
| `UV_PROJECT_ENVIRONMENT` | Where uv puts the virtual environment (see the macOS note). |

## Demo path

Terminal 1 runs the target app; the other commands run in terminal 2 unless noted.

```bash
make app                                   # terminal 1: target app on http://127.0.0.1:8080
```

**1. Discovery.** The model explores the app once and the compiler writes a draft artifact. It
is attended: a risky action is put to you at the terminal.

```bash
waypoint discover --capability-id lookup_member_balance \
  --goal "Look up member {{member_id}} and read their current savings balance" \
  --entry http://127.0.0.1:8080/console \
  --bind member_id=12345:string:internal \
  --expect-output savings_balance:money:pii --expect-output account_status:string:internal
```

Without an API key, reproduce the recorded run from its cassette - the same command with
`--llm cassette --cassette evidence/runs/showcase-discovery-haiku/cassette.json`. A cassette
stops, rather than clicking stale decisions, if a screen no longer matches the recording.

**2. Human approval.** Drafts do not run. The queue shows what awaits review; approving checks
every gate and binds approval to the artifact's content.

```bash
waypoint approvals
waypoint approve lookup_member_balance --version 1.2.0 --note "reviewed recovery and outcomes"
waypoint approve open_sub_account --version 1.0.0 --note "reviewed the reconcile probe"
```

**3. Replay, no model.** Outputs go to stdout in full; the redacted record goes to
`evidence/runs/<run_id>/`. Exit codes: `0` success or business outcome, `1` failure, `3` escalated.

```bash
waypoint replay lookup_member_balance --input member_id=12345   # success: $4,281.19, active
waypoint replay lookup_member_balance --input member_id=67890   # same artifact: $912.04, dormant
waypoint replay lookup_member_balance --input member_id=00000   # business_outcome member_not_found, exit 0
```

**4. Things going wrong.** Failure injections are set with `--inject`.

```bash
waypoint replay lookup_member_balance --input member_id=12345 --inject interstitial  # success, recoveries: dismiss
waypoint replay lookup_member_balance --input member_id=12345 --inject session       # success, recoveries: reauth
waypoint replay lookup_member_balance --input member_id=12345 --inject 500           # failure, expected vs observed
waypoint replay lookup_member_balance --input member_id=12345 --inject wrong_member  # escalated: right screen, wrong member
```

**5. Handoff.** With `--handoff`, an escalation pauses instead of ending: the headed browser stays
open and the run waits for an operator.

```bash
waypoint replay lookup_member_balance --input member_id=12345 --inject ambiguous --handoff
```

```bash
# terminal 3 - the operator
waypoint intervene list                # the open intervention and why it stopped
waypoint intervene take <id>           # control moves to you: open the member's record in the browser
waypoint intervene return <id>         # the run re-checks the screen and resumes
```

The run continues only where a declared checkpoint or resume point holds, and escalates again
otherwise. What the operator did is in `evidence/runs/<run_id>/human/actions.jsonl`.

**6. The irreversible capability.** Opening a sub-account is committed by a GET. Unattended, the
Confirm step escalates for a person:

```bash
waypoint replay open_sub_account --handoff --input member_id=12345 \
  --input "account_type=Money Market" --input initial_deposit=250.00
```

In terminal 3, `waypoint intervene take <id>`, click **Confirm** in the browser, then
`waypoint intervene return <id>`. The engine does not click Confirm again: it reads the member's
accounts grid, proves exactly one new account shows this deposit, and returns its ID. Add
`--inject commit_then_drop` and the confirmation page is lost after the commit - the result is
the same. With `--inject stale_confirmation` the page shows someone else's receipt, and the run
escalates again instead of adopting it.

## Running without live services

Replay, handoff, recovery and reconciliation need no API key. Discovery reproduces from a
cassette (step 1), and `waypoint compile evidence/runs/<run_id>` recompiles a saved transcript.
The whole test suite runs offline.

## Evidence

[evidence/README.md](evidence/README.md) indexes one showcase run per scenario above, plus the
two live discovery runs. Each run holds the exact artifact that ran, its events, results,
per-step screenshots and sanitized snapshots. `uv run python scripts/showcase.py` regenerates the
replay showcases from approved artifacts.

## Repository tour

| Path | What lives there |
|---|---|
| `waypoint/surface/` | The `Surface` port, accessibility-tree perception, sensitivity classification, locators, a desktop stub |
| `waypoint/policy/` | Allowlist and effect-based risk classification, the redactor, the secret broker |
| `waypoint/signatures/` | Declarative state recognizers, shared by discovery and replay |
| `waypoint/discovery/` | The model loop, prompts, transcript writer, cassette |
| `waypoint/compiler/` | Transcript to draft artifact |
| `waypoint/artifact/` | The capability schema and approval gates |
| `waypoint/replay/` | The deterministic engine, recovery, the return ladder, reconciliation |
| `waypoint/session/` | Control lease, interventions, human action log, intent records |
| `waypoint/catalog/`, `waypoint/operator/` | Placeholders; the catalog is not built |
| `target_app/` | The hostile fixture. Nothing in `waypoint/` imports it (enforced by a test) |
| `capabilities/` | Versioned capability artifacts, approved and draft |
| `scripts/` | The capability review (`review_capabilities.py`) and evidence generation (`showcase.py`) |
| `evidence/` | Curated showcase runs |
| `tests/` | Unit, browser and end-to-end tests |

## What is mocked or stubbed

- **Desktop surface:** the port is defined; `DesktopSurface` raises `NotImplementedError` with
  each method's UIA/AXAPI equivalent.
- **Operator console:** a CLI (`waypoint intervene`, `waypoint approvals`) over the same SQLite
  tables a web page would read.
- **Credential storage:** environment variables behind a `SecretBroker` a vault would replace.
- **The legacy application:** `target_app/`, a local Flask fixture with fictional members.

## License

Unlicensed take-home submission.
