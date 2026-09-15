# Waypoint

Waypoint turns a workflow in a legacy application into a reviewed capability that an agent can
call with typed inputs. Discovery uses a model to explore the UI. Compilation produces a
versioned artifact. Normal replay follows that artifact without model calls, checks each step,
and returns an answer or a structured reason it stopped.

The supplied Meridian Servicing Console is a deliberately awkward local fixture: frames inside
frames, repeated **View** links, changing row IDs, postbacks, adjacent labels and a mutating GET.
All members, credentials and accounts are fictional.

Start with this README, the concise [design report](REPORT.md), and the
[evidence index](evidence/README.md). The [feature inventory](PROJECT_STATUS.md) maps implemented
features to demonstrations and limits; the [walkthrough](docs/project-walkthrough.md) explains
why the pieces work this way.

To run and test every feature yourself, follow the [complete manual walkthrough](docs/manual-walkthrough.md).
It includes ordered setup, short commands, exact browser actions, expected results and cleanup.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). From the repository root:

```bash
uv sync --locked --extra dev
uv run playwright install chromium
```

On Linux, use `uv run playwright install --with-deps chromium` when browser system dependencies
are missing. The automated path uses saved decisions and needs no API key.

The recording helper supplies the fixture credentials itself. For direct CLI use, create
`.env` from [`.env.example`](.env.example) if you do not already have one. Exported variables
override that file. A live discovery or live assisted call additionally requires
`ANTHROPIC_API_KEY`; saved cassettes do not.

Always use `uv run waypoint …` for direct commands. The shorter `waypoint` executable is only
available when the project environment is activated. Run shell variable assignments separately
from `uv run`.

## Demo path

For recording, run:

```bash
docs/present.sh
```

The presenter starts its own fixture, uses a new private state directory, and advances when you
press Enter. It shows the artifact before asking you to approve it. At handoff, it tells you
when to take control, what to click in the actual browser, and when to return control. It stops
on unexpected results and stops its fixture on exit. The [recording script](docs/demo-script.md)
provides matching narration. Optional [slides](docs/demo-slides.html) introduce the project.

For a manual demonstration, open two terminals in this repository:

```bash
# RUN terminal: create a fresh take first
./demo prepare
```

```bash
# APP terminal: leave this running
./demo app
```

Then, in RUN, execute these in order:

```bash
./demo discover
./demo review
./demo draft
./demo approve
./demo replay
./demo other
./demo missing
./demo recover
./demo failure
```

`discover` reuses decisions from the saved genuine Claude discovery against the live fixture;
it is an offline reproduction, not a new model run. It writes an isolated **1.0.0 draft**.
`draft` deliberately exits **1** because approval is missing. Review the displayed artifact
before `approve`. Both `replay` and `other` execute that exact generated artifact:

| Scene | Expected result |
|---|---|
| `replay`: member 12345 | `success`: savings $4,281.19, active |
| `other`: member 67890 | `success`: savings $912.04, dormant |
| `missing` | `business_outcome`: `member_not_found`, exit 0 |
| `recover` | `success` with a recorded maintenance-notice recovery |
| `failure` | `failure`: `hard_failure`, exit 1 |

The exceptional lookup scenes use separately reviewed **1.2.0**, whose artifact declares
outcomes and recovery. This is distinct from the newly discovered flow.

For a real human handoff, run `./demo handoff` in RUN. When it pauses, use a third OPERATOR
terminal to run `./demo take`, click **View** in member **12345**'s row in the run's browser,
then run `./demo return`. The engine checks the resulting screen before continuing.

For the irreversible case, run `./demo commit`, take control the same way, click **Confirm
once**, then return control. The fixture loses the confirmation response; Waypoint checks the
account grid and adopts the committed result. It does not click Confirm again. This scene uses
`open_sub_account` **1.0.0**. Use `./demo abort` to end a pending handoff and `./demo evidence` to
locate the saved files. Stop APP with Control-C when finished.

## Feature demonstrations

These commands start private fixtures and require no API key:

```bash
# Deterministic replay, business outcomes, recovery, live handoff and reconciliation
uv run python scripts/showcase.py

# Upstream agent's saved tool calls; first measures the tool in its own fresh ledger
uv run python scripts/agent_demo.py --cassette evidence/agent/lookup.json

# Rehearse the presenter's automatic scenes; manual handoffs are covered by showcase.py
uv run python scripts/present.py --rehearse
```

The showcase script plays the operator and says so in its evidence. The agent demo
replays saved model tool calls, executes the tools against a real fixture, and repeats the saved
answer only if status, outputs and outcome match. A mismatch returns a nonzero exit.

Lookup **1.3.0** declares a confidence bar of **0.5** and allows assisted relocation. It is
unavailable for unattended use until a sweep has measured it. The baseline **1.2.0** remains
available for demonstrations that do not need that measurement first.

## Direct CLI use

Inspect commands with `uv run waypoint --help`. These examples assume the local fixture is
running and `.env` supplies its fictional credentials.

```bash
uv run waypoint approvals
uv run waypoint replay lookup_member_balance --version 1.2.0 --input member_id=12345
uv run waypoint stability lookup_member_balance --version 1.3.0 \
  --cases capabilities/lookup_member_balance/cases.yaml --runs 2
uv run waypoint replay lookup_member_balance --version 1.3.0 --input member_id=12345 \
  --inject drift_search --assist-cassette evidence/agent/assist.json
uv run waypoint catalog list
uv run waypoint catalog tools --format openai
uv run waypoint catalog invoke lookup_member_balance --args '{"member_id":"67890"}'
uv run waypoint codegen lookup_member_balance --version 1.3.0 \
  --target pytest --out .waypoint/test_generated_lookup.py
uv run pytest .waypoint/test_generated_lookup.py -q
```

The sweep and catalog share `.waypoint/state.db` by default. An unattended confidence-gated
release is unavailable until measurements meet its bar; `--handoff` does not bypass that gate.
A sweep and a generated regression test explicitly disable confidence enforcement to measure
behavior, while retaining approval and action-policy checks. Injected cases are executed and
reported but excluded from confidence. Failed case expectations and inconsistent outputs
cannot earn passing confidence.

`replay` and `catalog invoke` also accept `--base-url` and `--handoff`. With `--handoff`, an
escalation pauses and prints the `waypoint intervene take` / `return` commands for a second
terminal. `discover --handoff` does the same when discovery gets stuck; what the person does is
logged and becomes a gap in the draft that blocks approval until someone authors the step.

## Verification and evidence

```bash
uv run ruff check .
uv run mypy waypoint
uv run pytest -m 'not llm' -q
```

CI installs the committed lockfile and Chromium, then runs these checks. Browser tests exercise
real fixture sessions. Saved live discovery, agent and assist recordings provide model provenance;
regression checks do not call paid services.

Run evidence contains the exact artifact, metadata, events, sanitized snapshots and masked
screenshots. Failure evidence includes the stopped step and expected versus observed state.
Human actions and reconciliation decisions are retained. Runtime databases stay private.
Caller outputs are intentionally full values; persisted evidence is redacted.

The [execution rules](RULES.md) specify the boundaries. Classification depends on observed labels,
structure, bindings and patterns; it is not a general guarantee against prompt injection or all
possible sensitive content. Desktop execution, a hosted catalog and remote browser control are
outside this submission. Generated Playwright page objects are reading/debugging aids without
the engine's guardrails; generated pytest runs the real engine.

## Repository tour

| Path | Responsibility |
|---|---|
| `waypoint/discovery/`, `compiler/`, `artifact/` | Exploration, compilation, schema, approval and generation |
| `waypoint/surface/`, `policy/`, `signatures/` | Browser perception, actions, safety and state recognition |
| `waypoint/replay/`, `session/` | Execution, recovery, confidence, leases, intents and handoff |
| `waypoint/catalog/` | Agent tool interface |
| `target_app/`, `capabilities/` | Fictional legacy application and versioned workflows |
| `scripts/`, `tests/`, `evidence/` | Reproducible demonstrations, checks and retained proof |

Unlicensed take-home submission.
