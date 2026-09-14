# Waypoint

**Record-once, replay-many automation for legacy back-office UIs that have no API.**

A model discovers a path through a legacy UI once. That path is compiled into a typed,
versioned **capability**. Production runs replay the capability deterministically, with no
model deciding anything. When replay meets a state it will not act on, control transfers to a
person on the same live session, and what they do is recorded as evidence.

Read the [design report](REPORT.md), inspect the [run evidence](evidence/README.md), or follow
the [recording script](docs/demo-script.md) for a narrated demonstration.

## Status

**Built and tested** (`make test`: **429 passed**, no API key needed; Ruff and mypy clean):

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

Prerequisites: Git, `uv`, and `make`. The project pins Python 3.13 in `.python-version`;
`uv` manages the project environment and installs the required Python version if needed.
The commands below use a Bash or Zsh terminal.

```bash
git clone https://github.com/Susilkessav/Waypoint.git
cd Waypoint
```

If the checkout is in an iCloud-synced folder on macOS, put the virtual environment outside
that folder. Set this **before installation**, and in each new terminal used for the demo:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.local/share/waypoint/venv"
```

Install dependencies and create the local fixture configuration, preserving any existing `.env`:

```bash
make install                  # locked dependencies and Playwright Chromium
if [ ! -e .env ]; then cp .env.example .env; fi
source "${UV_PROJECT_ENVIRONMENT:-.venv}/bin/activate"
make test                     # 429 unit and browser tests; no model API calls
make lint                     # Ruff and mypy
```

In each additional terminal, enter the repository directory and repeat the activation command.
Alternatively, prefix `waypoint` commands with `uv run`. Initial installation downloads packages
and Chromium; the tests and cassette demonstrations then run without a model service.

`make install` and CI use `uv sync --locked`; a stale lockfile fails installation instead of
silently resolving different dependencies. On Linux, if Chromium reports missing system
libraries, run `uv run playwright install --with-deps chromium` as CI does.

### Environment variables

The `waypoint` CLI reads `.env` from the working directory; exported variables take precedence.
The copied fixture configuration is sufficient for replay and cassette discovery. For a fresh
live discovery, set `ANTHROPIC_API_KEY` in `.env` or export it in the discovery terminal.

| Variable | Used for |
|---|---|
| `ANTHROPIC_API_KEY` | Live discovery only. Replay, handoff and cassette runs never need it. |
| `MERIDIAN_USER`, `MERIDIAN_PASS` | Fictional sign-on values (`operator1` / `changeme`), injected by the secret broker. If changed, also export them in the target-app terminal. |
| `PORT` | Export in the target-app terminal to change its port (default 8080). Update discovery's `--entry` and replay's `--base-url` accordingly. |
| `UV_PROJECT_ENVIRONMENT` | Where uv puts the virtual environment (see the macOS note). |

## Demo path

Terminal 1 runs the target app; the other commands run in terminal 2 unless noted.

```bash
make app                                   # terminal 1: target app on http://127.0.0.1:8080
```

**1. Discovery.** The model explores the app once and the compiler writes a draft artifact. It
is attended: a risky action is put to you at the terminal. Use a fresh demo directory so the
generated artifact has a known version and cannot be confused with the supplied releases.
Keep using terminal 2 for steps 1–3; rerun the directory assignment for each new demonstration.

```bash
WAYPOINT_DEMO_DIR="$(mktemp -d)"
waypoint discover --capability-id lookup_member_balance \
  --goal "Look up member {{member_id}} and read their current savings balance" \
  --entry http://127.0.0.1:8080/console \
  --bind member_id=12345:string:internal \
  --expect-output savings_balance:money:pii --expect-output account_status:string:internal \
  --root "$WAYPOINT_DEMO_DIR/capabilities" --version 1.0.0
```

Without an API key, run this complete alternative for step 1. It reproduces the saved model
decisions against the live local UI; it does not make new model calls.

```bash
WAYPOINT_DEMO_DIR="$(mktemp -d)"
waypoint discover --capability-id lookup_member_balance \
  --goal "Look up member {{member_id}} and read their current savings balance" \
  --entry http://127.0.0.1:8080/console \
  --bind member_id=12345:string:internal \
  --expect-output savings_balance:money:pii --expect-output account_status:string:internal \
  --llm cassette --cassette evidence/runs/showcase-discovery-haiku/cassette.json \
  --root "$WAYPOINT_DEMO_DIR/capabilities" --version 1.0.0
```

A cassette stops if the current screen differs from its recorded snapshot. It reproduces a
specific discovery run; parameterized reuse happens through the compiled artifact in step 3.

**2. Human approval.** Drafts do not run. The queue shows what awaits review; approving checks
every gate and binds approval to the artifact's content. Review the newly generated
`$WAYPOINT_DEMO_DIR/capabilities/lookup_member_balance/1.0.0.json` first. If live discovery
reports open gates, resolve them before approving; the supplied cassette reproduces a run
with no open gates.

```bash
waypoint approvals --root "$WAYPOINT_DEMO_DIR/capabilities"
waypoint approve lookup_member_balance --root "$WAYPOINT_DEMO_DIR/capabilities" \
  --version 1.0.0 --note "reviewed the newly discovered flow and extraction targets"
```

**3. Replay, no model.** Outputs go to stdout in full; the redacted record goes to
`evidence/runs/<run_id>/`. Exit codes: `0` success or business outcome, `1` run failure,
`2` command usage error, `3` escalated.

```bash
waypoint replay lookup_member_balance --root "$WAYPOINT_DEMO_DIR/capabilities" \
  --version 1.0.0 --input member_id=12345   # success: $4,281.19, active
waypoint replay lookup_member_balance --root "$WAYPOINT_DEMO_DIR/capabilities" \
  --version 1.0.0 --input member_id=67890   # the generated artifact: $912.04, dormant
```

The discovery transcript and both replay runs remain in `evidence/runs/`; each replay includes
an exact copy of the generated, approved artifact. This is the discovery → approval → replay
chain. The temporary capabilities directory can be discarded after reviewing those results.

**4. Things going wrong.** The remaining examples use the supplied, reviewed
`capabilities/lookup_member_balance/1.2.0.json`, built from the handwritten base with explicit
business outcomes and recovery rules. This is a separate demonstration of those features.
Failure injections are set with `--inject`.

```bash
waypoint replay lookup_member_balance --version 1.2.0 \
  --input member_id=00000  # business_outcome member_not_found, exit 0
waypoint replay lookup_member_balance --version 1.2.0 \
  --input member_id=12345 --inject interstitial  # success, recoveries: dismiss
waypoint replay lookup_member_balance --version 1.2.0 \
  --input member_id=12345 --inject session       # success, recoveries: reauth
waypoint replay lookup_member_balance --version 1.2.0 \
  --input member_id=12345 --inject 500           # failure, expected vs observed
waypoint replay lookup_member_balance --version 1.2.0 \
  --input member_id=12345 --inject wrong_member  # escalated: right screen, wrong member
```

**5. Handoff.** With `--handoff`, an escalation pauses instead of ending: the headed browser stays
open and the run waits for an operator.

```bash
waypoint replay lookup_member_balance --version 1.2.0 \
  --input member_id=12345 --inject ambiguous --handoff
```

```bash
# terminal 3 - the operator
waypoint intervene list                # the open intervention and why it stopped
waypoint intervene take INTERVENTION_ID
# In the same browser, open the intended member's record, then:
waypoint intervene return INTERVENTION_ID
```

Replace `INTERVENTION_ID` with the ID printed by the waiting run or `intervene list`.
The run continues only where a declared checkpoint or resume point holds, and escalates again
otherwise. What the operator did is in `evidence/runs/<run_id>/human/actions.jsonl`.

**6. The irreversible capability.** Opening a sub-account is committed by a GET. Unattended, the
Confirm step escalates for a person:

```bash
waypoint replay open_sub_account --version 1.0.0 --handoff --input member_id=12345 \
  --input "account_type=Money Market" --input initial_deposit=250.00
```

In terminal 3, `waypoint intervene take INTERVENTION_ID`, click **Confirm** in the browser, then
`waypoint intervene return INTERVENTION_ID`. The engine does not click Confirm again: it reads the member's
accounts grid, proves exactly one new account shows this deposit, and returns its ID. Add
`--inject commit_then_drop` and the confirmation page is lost after the commit - the result is
the same. With `--inject stale_confirmation` the page shows someone else's receipt, and the run
escalates again instead of adopting it.

## Running without live services

Replay, handoff, recovery and reconciliation need no API key. Discovery reproduces from a
cassette (step 1). To recompile a saved transcript without opening a browser:

```bash
WAYPOINT_COMPILE_DIR="$(mktemp -d)"
waypoint compile evidence/runs/showcase-discovery-haiku \
  --root "$WAYPOINT_COMPILE_DIR/capabilities"
```

## Evidence

[evidence/README.md](evidence/README.md) indexes two live discovery runs and nine replay
showcases, including failure, recovery and handoff. It explains each run's files and artifact
provenance. The handoff showcases use a scripted operator; step 5 demonstrates manual control.

```bash
uv run python scripts/showcase.py --list   # inspect scenario and approval status
uv run python scripts/showcase.py          # replace replay showcases and rebuild the index
```

The generator launches its own fixture instance and requires the scenario artifacts to be
approved. Ordinary demo runs are ignored by Git; `showcase-*` runs are retained for submission.
`make clean` deletes ordinary runs but preserves showcases. Preserve any run you intend to share
before cleaning.

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
