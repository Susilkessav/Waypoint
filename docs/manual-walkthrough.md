# Waypoint: complete manual walkthrough

Use this guide to operate and test every delivered feature yourself. Work from top to bottom
for the first pass. Every exercise gives the action and the result to look for. An expected
refusal is a **passing test** when it matches the result described here.

The main path uses the fictional Meridian application and saved decisions. It makes **no new
model calls**. Live model checks are optional at the end. Allow a few sessions if you want to
inspect all the evidence; you can stop between exercises without deleting it.

This guide follows the current simplified project: lookup **1.2.0** for baseline behavior and
**1.3.0** for confidence and assistance. The feature inventory at the end distinguishes
implemented features from features that were removed or remain design proposals.

For a shorter recording, use [the presenter and narration](demo-script.md). For the design
explanation, use [Explaining Waypoint](project-walkthrough.md).

## Route through the features

| Order | Exercise | What you prove |
|---|---|---|
| [1–3](#1-prepare-one-private-test-session) | Setup, fixture, discover → review → approve → reuse | The complete core workflow |
| [4–5](#4-replay-typed-inputs-and-business-outcomes) | Input validation, outcomes, drift and recovery | Correct results and deliberate stops |
| [6](#6-real-human-handoff-through-the-cli) | CLI handoff | A person controls the same live browser |
| [7](#7-irreversible-actions-and-reconciliation) | Sub-account creation and reconciliation | Irreversible work is checked before retrying |
| [8](#8-human-help-during-discovery) | Discovery escalation | A stuck discovery gets help; the person's work blocks approval |
| [9](#9-stability-reports-and-confidence-gating) | Confidence gating | Measured reuse |
| [10–11](#10-catalog-tools-and-an-upstream-agent) | Catalog, upstream agent and assisted relocation | Business tools and bounded repair |
| [12](#12-generate-tests-and-readable-playwright-code) | Generated code | Regression generation and readable code |
| [13–15](#13-credentials-and-optional-live-model-checks) | Secrets, evidence and final checks | What is retained and what remains optional |

## 1. Prepare one private test session

### 1.1 Name your terminals

Use these terminal roles throughout the guide:

| Terminal | Purpose |
|---|---|
| **RUN** | Run one exercise at a time. It may wait while you act in a browser. |
| **APP** | Keep the base fixture running. |
| **OPERATOR** | Take, return or abort a paused run. |

In **every new terminal**, change into the repository. On this machine:

```bash
cd /Users/susil/Desktop/Waypoint
```

On another machine, substitute your checkout directory. Run one command block at a time;
do not paste the entire document into a terminal. Some blocks intentionally exit nonzero,
so do not wrap the walkthrough in `set -e`.

### 1.2 Install and prepare — RUN

```bash
uv sync --locked --extra dev
uv run playwright install chromium
uv run waypoint version
./demo prepare
source docs/manual-session.sh
```

**Expect:** version `0.1.0`, a new `take-…` directory, and the app address printed by
the setup file. On Linux, browser dependencies may require
`uv run playwright install --with-deps chromium`.

The setup file supplies `operator1` / `changeme`, ignores `.env` for this offline session, and
defines three short commands. It does not start a browser, approve anything or call a model.

| Shortcut | Actual operation |
|---|---|
| `wp_lookup MEMBER [options]` | `uv run waypoint replay lookup_member_balance --version 1.2.0 …` |
| `wp_current MEMBER [options]` | The same replay using **1.3.0**, with its confidence gate |
| `wp_account [options]` | Replay `open_sub_account` **1.0.0**, member 12345, Money Market, deposit 250.00 |

The shortcuts use the session's origin, database and evidence folder, show the browser, pass
extra options through to replay, and preserve its exit code. Inspect one with `type wp_lookup`.
Use `--headless` on a shortcut if you do not need to watch it. Keep handoff exercises headed.

### 1.3 Start the fixture — APP

```bash
source docs/manual-session.sh
./demo app
```

**Expect:** Flask listening on the base address chosen by `./demo prepare`. Leave APP running.
In **OPERATOR**, load the same settings:

```bash
source docs/manual-session.sh
```

Each terminal has its own variables. Source the file in each terminal that uses `$WP_…` or
`wp_lookup`. It reads the existing take; it does not create a different session.

### 1.4 Check that the app is reachable — RUN

```bash
curl --fail --silent --show-error "$WP_BASE/console" > /dev/null
echo $?
```

**Expect:** `0`. Do not assume port 8080.

**Session rule:** run `./demo prepare` once. To start a completely fresh pass later, finish or
abort every pending run, stop APP with Control-C, run prepare again, and source
the setup file again in every terminal. Previous evidence is retained.

## 2. Understand the application you are automating

In RUN, print the address and open it in your regular browser:

```bash
printf '%s/console\n' "$WP_BASE"
```

1. Sign in as **operator1**, password **changeme**.
2. Search for **12345**.
3. Click **View in the row for 12345**. The repeated View links belong to different members.
4. Open **Accounts**. Find the Savings row: **$4,281.19**, **active**.
5. Search for **67890** and inspect its Accounts tab: **$912.04**, **dormant**.

This shows why the workflow needs frame awareness, row anchors and member identity checks.
Your regular browser is separate from each browser Waypoint launches. During a handoff, act
in **the browser opened by that run**.

## 3. Discover, compile, review, approve and reuse

### 3.1 Discover — RUN

```bash
./demo discover
```

Watch sign-on, member search, member selection and account inspection. **Expect:** discovery
finishes and writes a **draft 1.0.0** under this take's `capabilities` directory. The saved
genuine discovery cassette supplies decisions while a real browser executes them. This is
offline reproduction, not a new model discovery.

### 3.2 Review the generated artifact

```bash
./demo review
uv run python -m json.tool "$WP_TAKE/capabilities/lookup_member_balance/1.0.0.json"
```

Check these parts of the JSON:

- `inputs`: member ID is an argument; steps use `$inputs.member_id`.
- Secret references use `$secrets.…`; the artifact does not contain the password.
- `steps`: targets include semantic information, anchors and frame paths.
- `signatures` and checkpoints: execution checks the resulting UI state.
- `outputs`: balance and status have extraction rules and sensitivity declarations.
- `policy`: route and execution limits are explicit.
- `provenance`: source discovery and approval information are inspectable.

### 3.3 Prove approval is required

```bash
./demo draft
echo $?
```

**Expect:** refusal, exit **1**, because the draft is unapproved. No successful balance result.

```bash
uv run waypoint approvals --root "$WP_TAKE/capabilities" --db "$WP_DB"
```

**Expect:** the generated 1.0.0 is awaiting review. After you have reviewed it:

```bash
./demo approve
./demo replay
./demo other
```

**Expect:** both runs succeed using **that exact generated artifact**. Member 12345 returns
`$4,281.19` / `active`; member 67890 returns `$912.04` / `dormant`. No model calls are required
to replay it for the second member.

### 3.4 Compile a saved transcript independently

Find the discovery directory without copying a run ID:

```bash
export WP_DISCOVERY="$(uv run python - <<'PY'
import os
from pathlib import Path
matches = list((Path(os.environ['WP_TAKE']) / 'runs').glob('*/transcript.json'))
assert len(matches) == 1, 'Use the transcript path printed by the discovery you want.'
print(matches[0].parent)
PY
)"
uv run waypoint compile "$WP_DISCOVERY" --version 1.0.0 --root "$WP_LAB/recompiled"
```

**Expect:** another draft in `manual/recompiled/lookup_member_balance/1.0.0.json`. Run the
compile command again: it must **refuse to overwrite** that version, exit **1**. Compilation
does not grant approval. This exercise reuses the saved transcript and opens no new browser.

### 3.5 Prove editing content invalidates approval

Make an isolated copy of the reviewed baseline, changing its step budget:

```bash
uv run python - <<'PY'
import json, os
from pathlib import Path
data = json.loads(Path('capabilities/lookup_member_balance/1.2.0.json').read_text())
data['policy']['max_steps'] += 1
dest = Path(os.environ['WP_LAB']) / 'tampered/lookup_member_balance/1.2.0.json'
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(data, indent=2) + '\n')
PY
wp_lookup 12345 --root "$WP_LAB/tampered"
```

**Expect:** `failure`, `artifact_not_approved`, exit **1**. A retained approval label cannot
authorize changed content. Leave this copy unapproved. The repository artifact is unchanged.

## 4. Replay, typed inputs and business outcomes

The remaining core exercises use the separately reviewed **lookup 1.2.0**, which adds authored
outcomes and recovery to the discovery-derived workflow. They do not imply that discovery
automatically invented all exceptional handling.

Run these separately in RUN:

| Command | Expected result |
|---|---|
| `wp_lookup 12345` | `success`, balance `$4,281.19`, status `active`, exit 0 |
| `wp_lookup 67890` | `success`, balance `$912.04`, status `dormant`, exit 0 |
| `wp_lookup 00000` | `business_outcome`, `member_not_found`, exit 0 |
| `wp_lookup 99999` | `business_outcome`, `not_authorized`, exit 0 |
| `wp_lookup abc` | `failure`, `invalid_input`, exit 1, before browser work |
| `wp_lookup 12345 --input unexpected=value` | `failure`, `invalid_input`, exit 1 |

Test a missing required input with the direct CLI:

```bash
uv run waypoint replay lookup_member_balance --version 1.2.0 \
  --base-url "$WP_BASE" --state-db "$WP_DB" --evidence-root "$WP_LAB/runs"
```

**Expect:** `invalid_input`, exit **1**. The caller must provide a member ID.

The four replay statuses are **success (0)**, **business_outcome (0)**, **failure (1)** and
**escalated (3)**. CLI usage errors and some catalog/resume refusals use **2**. Read the
structured result as well as the exit code; not-found is a valid business answer.

## 5. Locator safety, recovery and hard failures

Run each command separately. These injections only affect the local fixture session opened
by that replay. A later run starts a new browser session.

| Command | What to observe | Expected result |
|---|---|---|
| `wp_lookup 12345 --inject reorder` | Result rows change position; the same member is selected. | Success with the original balance |
| `wp_lookup 12345 --inject ambiguous` | Two matching View links appear in the target row. | Escalated, `ambiguous_locator`, exit 3 |
| `wp_lookup 12345 --inject row_missing` | The target row vanishes without a not-found banner. | Escalated; no other member's balance |
| `wp_lookup 12345 --inject wrong_member` | The detail page shows the wrong member. | Escalated, `checkpoint_not_met`, exit 3 |
| `wp_lookup 12345 --inject interstitial` | A maintenance notice appears and is dismissed. | Success; recovery recorded |
| `wp_lookup 12345 --inject session` | Session expires; the run signs in again and reconstructs safe work. | Success; reauthentication recorded |
| `wp_lookup 12345 --inject 500` | The application returns its error screen. | Failure, `hard_failure`, exit 1 |
| `wp_lookup 12345 --inject drift_search` | Search has become Continue. The baseline has no matching target. | Escalated; no guessed click |

In the result, inspect `failure` for the stopped step and expected/observed state, and
`telemetry.recoveries` for recoveries. The system only applies declared remedies with limits.
It does not keep retrying any arbitrary error.

Do not invent injection names. An unsupported name is ignored by the fixture and therefore
does not prove a failure-handling feature. Section 7 exercises the sub-account injections.

## 6. Real human handoff through the CLI

### 6.1 Resolve ambiguity

In **RUN**:

```bash
wp_lookup 12345 --inject ambiguous --handoff
```

Wait for an **open intervention** announcement. RUN remains occupied and its browser stays
open. In **OPERATOR**:

```bash
uv run waypoint intervene list --db "$WP_DB"
./demo take
```

Now perform exactly one browser action: click **View in member 12345's row** in the run's
Chromium window. On the resulting profile, check that Member ID is **12345**. Then in OPERATOR:

```bash
./demo return
```

**Expect:** RUN verifies the screen, opens Accounts and finishes successfully. Inspect
`telemetry.handoffs`, the `human/` action log and `handoff1_diff.json` in the evidence directory.
Automation should not advance while you hold control.

For a full queue record, copy the intervention ID from the list into this assignment:

```bash
export WP_IV='paste-the-intervention-id-here'
uv run waypoint intervene show "$WP_IV" --db "$WP_DB"
uv run waypoint intervene list --all --db "$WP_DB"
```

The ID is the **intervention ID**, not the run ID. `show` includes state, reason, evidence
paths and lease information without exposing the operator token.

### 6.2 Return without doing the work

Start the same ambiguous handoff again. Take control, click nothing, and return it.

**Expect:** no balance is returned. Waypoint cannot validate a continuation point and opens a
new intervention with `unrecognized_state_after_handoff` while its handoff budget permits.
This new intervention has a new ID. Take it, click the correct View link and return to finish,
or use `./demo abort` to end the run.

### 6.3 Return from the wrong member

Start another ambiguous handoff. Take control, click View for a **different visible member**,
and return.

**Expect:** the wrong identity is rejected, with no successful balance. Abort the follow-up
intervention. Do not count a familiar-looking Member Profile page as successful verification.

### 6.4 Abort and lease expiry

For abort: start another handoff, wait for the open intervention, then run `./demo abort`.
**Expect:** RUN exits **3**, with `aborted_by_operator`; the browser closes.

For expiry: start another handoff and use the new ID instead of `./demo take`:

```bash
export WP_IV='paste-the-new-intervention-id-here'
uv run waypoint intervene take "$WP_IV" --ttl 5 --db "$WP_DB"
```

Wait more than five seconds without acting. **Expect:** `lease_timeout`, exit **3**. A later
return of that expired intervention must be refused:

```bash
uv run waypoint intervene return "$WP_IV" --db "$WP_DB"
```

For normal demonstrations, the default control lease is 15 minutes. Finish each test before
starting another; the `./demo take/return/abort` shortcuts require exactly one eligible item.

## 7. Irreversible actions and reconciliation

### 7.1 Prove an unattended run stops before Confirm

```bash
wp_account
```

**Expect:** the form is filled and reviewed, but the irreversible Confirm step requires a
person. The run ends `escalated`, `approval_required`, exit **3**. No account is created.
The artifact's approval is not blanket permission to execute an irreversible action.

### 7.2 Create one account with a real human

Start with an empty **fictional fixture account list**, only when no account run is active:

```bash
curl --fail --silent --show-error -X POST "$WP_BASE/_fixture/reset"
wp_account --handoff
```

At the Confirm intervention:

1. In OPERATOR, run `./demo take`.
2. In the run's browser, verify **member 12345**, **Money Market**, **250.00**.
3. Click **Confirm once**. It is a mutating GET despite looking like a link.
4. In OPERATOR, run `./demo return`.

**Expect:** success and `account_id: SA-12345-01` after the fixture reset. Human confirmation
is verified through reconciliation; an adopted result is expected on this handoff path.

In your regular fixture browser, revisit 12345 → Accounts and confirm that exactly one
sub-account was added. Keep that browser separate from the run's browser.

```bash
uv run waypoint intervene intents --all --db "$WP_DB"
```

Inspect the resolved intent. It records the operation before the human can confirm it.

### 7.3 Lose the response after the commit

```bash
curl --fail --silent --show-error -X POST "$WP_BASE/_fixture/reset"
wp_account --inject commit_then_drop --handoff
```

Take control, verify the details, click **Confirm once**, and wait for the fixture's gateway
error. **Do not retry Confirm or reload its URL.** Return control.

**Expect:** success, `telemetry.adopted: true`, a `completed` reconciliation and the account ID
read from the authoritative account grid. Refresh the account list in your regular browser:
exactly one new account should exist. A failed response did not mean a failed commit.

### 7.4 Return without confirming

```bash
curl --fail --silent --show-error -X POST "$WP_BASE/_fixture/reset"
wp_account --handoff
```

Take control, **do not click Confirm**, and return. **Expect:** another intervention with
`not_completed_after_handoff`, and no sub-account. The CLI allows two handoffs: take this new
intervention and return without acting again. RUN then ends `escalated`, exit **3**, with
`not_completed_after_handoff`. Verify it has ended before starting the next exercise.

### 7.5 Reject a stale confirmation

```bash
curl --fail --silent --show-error -X POST "$WP_BASE/_fixture/reset"
wp_account --inject stale_confirmation --handoff
```

Take control, click Confirm once, and return. The fixture displays an unrelated old
confirmation. **Expect:** no successful adoption; the authoritative probe finds no matching
new account. Expect another `not_completed_after_handoff` intervention. Take and return this
second intervention without acting; the run then ends with that code, exit **3**.

### 7.6 Other sub-account cases

Run each separately. For a committing case, reset the fictional accounts first, then use the
same take → verify details → Confirm once → return sequence.

| Command | Expected observation |
|---|---|
| `wp_account --inject validation` | `business_outcome`, `deposit_rejected`, exit 0; no Confirm |
| `wp_account --inject 500` | Failure before commit; no account created |
| `wp_account --inject slow --handoff` | Delayed form pages settle; eventually a Confirm handoff and success |
| `wp_account --inject drift --handoff` | Review is renamed Continue; inspect locator-tier telemetry for fallback, then complete Confirm |
| `wp_account --inject resubmit --handoff` | Fixture attempts a 307 repeat; verify one account, with reconciliation after return |

`slow` affects sub-account pages, so a slow lookup would not demonstrate it. `drift` affects
Review; `drift_search` affects Search. The fallback result should be checked in evidence,
not inferred from a green result alone.

### 7.7 Test an unresolved human-owned operation and manual reconciliation

Reset the fixture, start `wp_account --handoff`, take control and **do not click Confirm**.
In OPERATOR, run `./demo abort`. Wait for RUN to end.

```bash
uv run waypoint intervene intents --db "$WP_DB"
```

**Expect:** an unresolved intent, because the engine handed over authority and an abort did
not prove whether an effect occurred. Inspect 12345's Accounts in your regular browser to
confirm that no new account exists. Copy the **intent ID**, which is different from an
intervention ID:

```bash
export WP_INTENT='paste-the-intent-id-here'
uv run waypoint intervene reconcile "$WP_INTENT" --outcome not-completed --db "$WP_DB"
uv run waypoint intervene intents --db "$WP_DB"
```

**Expect:** the intent is resolved and no longer blocks that operation. Use `completed` only
when you have verified a commit in the application. Manual reconciliation clears a block;
it does **not** make a later new invocation idempotent. A new invocation can create another account.

Unknown probe results, migrated intents without trusted origin/time, and changed reconciliation
contracts are exercised by the focused regression checks in section 15. The fixture has no
`probe_missing` injection. Do not claim that a nonexistent switch tests those boundaries.

## 8. Human help during discovery

This exercise makes discovery stop at a predictable point, so you can perform the missing
action yourself. It uses an explicitly edited **private cassette copy**. The original live
recording is preserved, and this modified exercise must be described as scripted.

### 8.1 Prepare the deliberate stop — RUN

```bash
uv run python - <<'PY'
import json, os
from pathlib import Path
from waypoint.discovery.decisions import Decision
source = Path('evidence/runs/showcase-discovery-haiku/cassette.json')
data = json.loads(source.read_text())
turn = data['turns'][7]
assert turn['decision']['kind'] == 'click' and 'View' in turn['decision']['intent']
turn['decision'] = Decision('give_up', reason='Manual exercise: open the record for 12345').to_dict()
# The human gap occupies turn 8. Move later model decisions forward by one turn.
for entry in data['turns'][8:]:
    entry['turn'] += 1
data['model'] = 'scripted-manual-handoff'
data['provenance'] = 'Modified cassette for a manual exercise; not a new live model recording'
dest = Path(os.environ['WP_LAB']) / 'discovery-handoff.json'
dest.write_text(json.dumps(data, indent=2) + '\n')
print(dest)
PY
```

### 8.2 Start discovery and help it

```bash
uv run waypoint discover --capability-id lookup_member_balance \
  --goal 'Look up member {{member_id}} and read their current savings balance' \
  --entry "$WP_BASE/console" --bind member_id=12345:string:internal \
  --expect-output savings_balance:money:pii --expect-output account_status:string:internal \
  --llm cassette --cassette "$WP_LAB/discovery-handoff.json" \
  --handoff --headed --state-db "$WP_DB" \
  --root "$WP_LAB/escalated" --version 1.0.0 \
  --evidence-root "$WP_LAB/discovery-escalated"
```

At the discovery intervention, use OPERATOR:

```bash
./demo take
```

Click only **View in 12345's row**, wait for the profile, then:

```bash
./demo return
```

**Expect:** control returns and the saved decisions carry on from the profile screen. Discovery
finishes and writes a draft with a human-action gate. The private cassette reserves a turn for
the gap; it retains the original screen checks. Inspect the evidence directory it prints:

- `human/actions.jsonl`: your click, recorded with redacted names.
- `transcript.json`: a step with `"action": "gap"` where you acted.

### 8.3 Why the gap blocks approval

What a person does during discovery is **not** turned into a replayable step. The compiled draft
contains a `wait_for` step with `unrecorded_human_action`. Inspect it and try approval:

```bash
uv run waypoint approvals --root "$WP_LAB/escalated" --db "$WP_DB"
uv run waypoint approve lookup_member_balance --version 1.0.0 --root "$WP_LAB/escalated"
```

**Expect:** the queue lists an open gate, and approval is blocked, exit **1**. A reviewer must
author the missing action, locator and verified checkpoint before approval. Do not remove the
gap marker just to make the test green. `tests/test_discovery_handoff.py` covers this path with
a scripted operator; section 15 gives the command. If playback reports a screen mismatch,
inspect it as a failed reproduction instead of marking the exercise complete.

## 9. Stability reports and confidence gating

Use **1.3.0** here. The successful 1.2.0 and generated 1.0.0 runs do not earn confidence for it.

### 9.1 Observe the fresh gate

```bash
uv run waypoint confidence lookup_member_balance --version 1.3.0 --state-db "$WP_DB"
wp_current 12345
```

On the first pass, **expect:** insufficient measurements and `confidence_too_low`, exit **1**.
Even `wp_current 12345 --handoff` cannot bypass this gate. If you have already completed this
section in the same take, the gate may already be satisfied; start a fresh take for a cold test.

### 9.2 Measure the complete declared case set

```bash
uv run waypoint stability lookup_member_balance --version 1.3.0 \
  --cases capabilities/lookup_member_balance/cases.yaml --runs 2 \
  --base-url "$WP_BASE" --state-db "$WP_DB" \
  --evidence-root "$WP_LAB/measurements" --report-root "$WP_LAB/stability"
```

**Expect:** **16 executions**: eight declared cases, repeated twice. The deliberately injected
500 and wrong-member cases count as correct case behavior when their expected statuses match.
With the declared contracts satisfied and the eligible runs clean, expect **stable**, exit
**0**. The injected recovery cases are reported but do not lower the confidence classification.
An unmet case contract would instead make the report **broken**, exit **1**.

Open the report directory printed by the command. Inspect `report.json` for case verdicts,
output consistency, timings, locator degradation and evidence references. There should be
**eight eligible clean runs** from the four non-injected cases. Injected executions remain in
the report but are excluded from confidence.

```bash
uv run waypoint confidence lookup_member_balance --version 1.3.0 --state-db "$WP_DB"
wp_current 12345
```

**Expect immediately after the sweep:** eight eligible passing runs, a score around **0.6756**,
meeting the declared **0.5** bar. Replay now succeeds. Later clean runs change the score.
Confidence is a conservative statistical lower bound over recent eligible evidence, not the
percentage of green runs. Five eligible runs are the minimum for a stable classification.

To test reporting without case-file expectations, use a separate database:

```bash
uv run waypoint stability lookup_member_balance --version 1.2.0 \
  --input member_id=12345 --runs 5 --base-url "$WP_BASE" \
  --state-db "$WP_LAB/single-case.db" \
  --evidence-root "$WP_LAB/single-case-runs" --report-root "$WP_LAB/single-case-reports"
```

**Expect:** five consistent successful executions and a stable report. A sweep of
`open_sub_account` is refused unless `--allow-irreversible` is explicitly supplied; the manual
confirmation exercises are the useful way to test that capability here.

## 10. Catalog tools and an upstream agent

### 10.1 Inspect the catalog after measurement

```bash
uv run waypoint catalog list --state-db "$WP_DB"
uv run waypoint catalog tools --format anthropic --state-db "$WP_DB"
uv run waypoint catalog tools --format openai --state-db "$WP_DB"
```

**Expect:** approved capabilities, their typed arguments and return fields. Lookup 1.3.0 is
available after section 9. Before measurement it is unavailable and omitted from callable
tool definitions. The retained discovery draft 1.1.0 is not advertised as an approved tool.
The two formats represent the same capability contracts using different provider schemas.

### 10.2 Invoke tools by name

```bash
uv run waypoint catalog invoke lookup_member_balance --args '{"member_id":"67890"}' \
  --base-url "$WP_BASE" --headed --state-db "$WP_DB" --evidence-root "$WP_LAB/catalog"
uv run waypoint catalog invoke lookup_member_balance --args '{"member_id":"00000"}' \
  --base-url "$WP_BASE" --state-db "$WP_DB" --evidence-root "$WP_LAB/catalog"
```

**Expect:** the agent-facing result returns success with the requested member's balance, then
the named not-found business outcome. These calls use the normal engine.

Test argument-shape refusal:

```bash
uv run waypoint catalog invoke lookup_member_balance --args '[]' --state-db "$WP_DB"
```

**Expect:** CLI error, exit **2**, because arguments must be a JSON object.

### 10.3 Human handoff from a catalog invocation

First reset the idle base fixture account list:

```bash
curl --fail --silent --show-error -X POST "$WP_BASE/_fixture/reset"
uv run waypoint catalog invoke open_sub_account --version 1.0.0 \
  --args '{"member_id":"12345","account_type":"Money Market","initial_deposit":"250.00"}' \
  --base-url "$WP_BASE" --handoff --state-db "$WP_DB" --evidence-root "$WP_LAB/catalog"
```

In OPERATOR, run `./demo take`, verify the details, click Confirm once in the run's browser,
then run `./demo return`. **Expect:** success with the new account ID and intervention
history. The catalog command waits synchronously until the run finishes.

Repeat without `--handoff` if you want the terminal escalation result: expect exit **3**,
`requires_intervention: true` and `waiting_on_a_person: false`. The returned result describes
a stopped run; it is not a handle to a browser that remains open.

### 10.4 Reproduce the upstream agent demonstration offline

```bash
uv run python scripts/agent_demo.py --cassette evidence/agent/lookup.json \
  --workspace "$WP_LAB/agent"
```

**Expect:** the script starts its own private fixture, measures the current lookup release
in its own database, executes saved tool calls, validates their results and prints the saved
answer only when the results match. Inspect `manual/agent/result.json` and its run evidence.
This exercise reproduces tool use from a saved model interaction. It is not a new live agent
decision. Do not give this script your already-running APP port; it manages its own fixture.

## 11. Assisted relocation and a proposed repair

Complete section 9 first. Keep this test against the **base** fixture; the cassette matches
that fixture's injected drift.

```bash
wp_current 12345 --inject drift_search
wp_current 12345 --inject drift_search --assist-cassette evidence/agent/assist.json
```

**Expect:** the first call escalates because Search is now Continue. The second reuses a
recorded live-model element choice, validates it against the current sanitized screen, checks
the normal step and returns the balance.

Inspect the successful run's result and evidence:

- `telemetry.assist_attempts`: exactly one attempt.
- `telemetry.assisted`: the accepted relocation and validation.
- `proposal/lookup_member_balance-1.3.1.json`: a proposed repaired artifact.
- The proposal's approval state is draft; the installed 1.3.0 is unchanged.

The artifact must permit assistance **and** the caller must opt in. Test the artifact side:

```bash
wp_lookup 12345 --inject drift_search --assist-cassette evidence/agent/assist.json
```

**Expect:** escalation without an accepted assist, because 1.2.0 does not enable it. An invalid
choice still consumes the attempt budget. Assistance cannot change values, choose arbitrary
URLs, or repair an irreversible step. Rejection and budget boundaries have focused regression
coverage in section 15.

Do not replace the installed artifact with the proposal during this pass. A repair needs
review, its own approval and fresh measurement before normal use.

## 12. Generate tests and readable Playwright code

### 12.1 Generate and execute the full declared regression set

```bash
uv run waypoint codegen lookup_member_balance --version 1.3.0 \
  --target pytest --out "$WP_LAB/test_generated_lookup.py"
WAYPOINT_BASE_URL="$WP_BASE" uv run pytest "$WP_LAB/test_generated_lookup.py" -q
```

**Expect:** **eight tests pass**, including recovery and expected failure/escalation cases.
Set `WAYPOINT_BASE_URL` exactly as shown: the generated file defaults to port 8080 otherwise.
Run from the repository root so its artifact and case-file paths resolve.

The generated tests use the real engine and separate temporary state. They disable confidence
enforcement for measurement, retain approval/policy checks, and do not populate `$WP_DB`.

### 12.2 Refuse a regression suite without declared cases

```bash
uv run waypoint codegen open_sub_account --version 1.0.0 --target pytest \
  --out "$WP_LAB/test_generated_account.py"
```

**Expect:** exit **2**, explaining that this capability has no `cases.yaml`. A generated empty
or incomplete suite must not be presented as coverage.

### 12.3 Generate readable code for inspection

```bash
uv run waypoint codegen lookup_member_balance --version 1.3.0 \
  --target playwright --out "$WP_LAB/lookup_page.py"
```

Open the file and inspect frame locators, member anchors and checkpoints. This output is a
reading/debugging aid. It is not a drop-in replacement for the engine's policy, outcome,
handoff or reconciliation behavior.

## 13. Credentials and optional live model checks

### 13.1 Missing environment credential

In RUN:

```bash
env -u MERIDIAN_PASS WAYPOINT_NO_DOTENV=1 uv run waypoint replay lookup_member_balance \
  --version 1.2.0 --input member_id=12345 \
  --base-url "$WP_BASE" --state-db "$WP_DB" --evidence-root "$WP_LAB/credentials"
```

**Expect:** no successful sign-on; a sanitized failure/escalation without the credential value.
This only removes the variable for that child process. A subsequent `wp_lookup 12345` should
succeed using the shell's fixture credentials.

### 13.2 Optional new live discovery

This section uses your model account and can incur cost. Supply `ANTHROPIC_API_KEY` through your
existing private environment or `.env`; do not put its value into this document or a command
you intend to record. If using `.env`, `WAYPOINT_NO_DOTENV=` below enables loading it for this
one process. Exported values still take precedence.

```bash
WAYPOINT_NO_DOTENV= uv run waypoint discover --capability-id lookup_member_balance \
  --goal 'Look up member {{member_id}} and read their current savings balance' \
  --entry "$WP_BASE/console" --bind member_id=12345:string:internal \
  --expect-output savings_balance:money:pii --expect-output account_status:string:internal \
  --llm anthropic --model claude-haiku-4-5 --max-steps 20 --headed \
  --root "$WP_LAB/live-capabilities" --version 1.0.0 --evidence-root "$WP_LAB/live-discovery"
```

**Success criterion:** a finished transcript, saved model interaction/cassette and a compiled
draft. Actual model behavior can differ from the saved demonstration and compilation may
report gates. Review those gates; do not assume a live run is automatically approvable.
The accepted CLI provider name is **anthropic**, not `claude`.

For a live upstream agent:

```bash
WAYPOINT_NO_DOTENV= uv run python scripts/agent_demo.py \
  --question 'What is the savings balance for member 67890?' --workspace "$WP_LAB/live-agent"
```

For one bounded live relocation attempt, after confidence has been earned:

```bash
WAYPOINT_NO_DOTENV= wp_current 12345 --inject drift_search --assist
```

Compare the saved assist cassette and proposed draft with the offline exercise. A provider
failure or invalid choice should not turn into an unrestricted sequence of repair attempts.

## 14. Inspect the evidence yourself

```bash
./demo evidence
printf 'Additional manual files: %s\n' "$WP_LAB"
```

`./demo evidence` summarizes the original recording scenes. The additional CLI exercises print
their own evidence directories under `manual/`. Open a successful run, a failed run, a handoff
and a reconciliation side by side.

| File or area | Inspect for |
|---|---|
| `artifact.json`, `meta.json` | Exact contract/version and run identity |
| `result.json` | Terminal status, redacted outputs, failure details and telemetry |
| `events.jsonl` | Actions, checkpoints, recovery, assistance and reconciliation sequence |
| Snapshot JSON and PNG files | Sanitized UI observations and masked screenshots |
| `human/` and `handoff*_diff.json` | Human action record, control transfers and verified return |
| `transcript.json`, `compile_report.json` | Discovery steps, human provenance and compile gates |
| `proposal/` | Proposed repair, separate from the installed artifact |
| Stability `report.json` | Case verdicts, consistency, timing and references to the measured runs |

Not every run has every file. A pre-browser refusal has no page screenshot; an interrupted
worker has no terminal result; discovery writes a transcript instead of a replay result.

Check that the password, operator ID, member names and savings values are absent from persisted
run evidence. Inputs marked internal may remain by design; classification controls redaction.
The direct caller's stdout intentionally contains full outputs, so a screen recording or a
redirected terminal result can contain them. Do not confuse that channel with redacted evidence.

To search the run evidence text for the known fixture canaries (with `rg` installed):

```bash
rg -n 'changeme|operator1|4,281\.19|Dolores' "$WP_TAKE/runs" "$WP_LAB/runs"
```

**Expect:** no matches; `rg` exits **1** when it finds none. Inspect screenshots visually too:
a text search cannot establish image masking. Do not scan the entire take and label the fixture
server log or the caller-facing agent result as redacted engine evidence.

## 15. Finish the checks, then stop the session

### 15.1 Repeatable verification for boundaries that need controlled timing

These are automated regressions you launch manually, in addition to the hands-on exercises:

```bash
uv run pytest tests/test_discovery_handoff.py tests/test_irreversible.py \
  tests/test_resume.py tests/test_assist.py \
  tests/test_intents_replay.py tests/test_feature_boundaries.py -q
```

They cover discovery gaps, authoritative/unknown reconciliation, return-ladder restrictions
and other boundaries that are impractical to demonstrate reliably by racing a live browser.

Run the complete offline project checks from a clean checkout:

```bash
uv run ruff check .
uv run mypy waypoint
uv run pytest -m 'not llm' -q
```

**Expect:** clean lint/types and a passing offline suite. Browser tests start their own fixtures.
Do not include accidental duplicate source/test files such as `test_resume 2.py` in a submission
or treat failures from such extra copies as failures of the tracked project. Review and move
unrelated copies aside separately; this walkthrough does not delete your files.

`uv run python scripts/showcase.py` regenerates the repository's curated core showcase evidence.
It is optional for this walkthrough because the manual exercises already cover those scenes;
running it intentionally changes files under `evidence/runs/showcase-*`.

### 15.2 Completion checklist

- [ ] Discovery produced a draft; unapproved execution was refused.
- [ ] Reviewed artifact replayed for two different members.
- [ ] Recompilation refused an existing version; a tampered approval was rejected.
- [ ] Required/invalid inputs and both business outcomes behaved correctly.
- [ ] Reordered rows worked; ambiguous, missing and wrong-member states stopped safely.
- [ ] Notice/session recovery succeeded; a hard server error ended with evidence.
- [ ] CLI take/return worked on the run's actual browser.
- [ ] Wrong/unchanged handoff return, abort and lease expiry were rejected correctly.
- [ ] Confirm created one account; a lost response was adopted without a duplicate.
- [ ] No-confirm/stale-confirmation tests did not report success.
- [ ] An unresolved intent was inspected and reconciled using observed fixture state.
- [ ] A stuck discovery got a person's help, and its gap blocked approval.
- [ ] A fresh 1.3.0 failed its confidence gate, then passed after measurements.
- [ ] Both catalog schemas, invocation, outcomes and catalog handoff were checked.
- [ ] Saved upstream agent calls executed against a real private fixture.
- [ ] Assisted relocation made one attempt and wrote a draft proposal.
- [ ] Generated pytest ran all eight cases; absent cases were refused.
- [ ] Missing credentials failed without leakage; evidence and screenshots were inspected.
- [ ] Offline checks passed; optional live checks are labelled run or not run.

The desktop adapter is a stub, not a runnable feature. Hosted services, remote browser control,
tenant variants, a graphical operator console, keychain storage, crash resumption and automatic
conversion of human actions into replayable steps are not delivered in the current project.
See [the feature inventory](../PROJECT_STATUS.md) for these limits.

### 15.3 Stop cleanly

1. Finish or abort any active intervention. Check
   `uv run waypoint intervene list --db "$WP_DB"`.
2. Review any unresolved intents with `uv run waypoint intervene intents --db "$WP_DB"`.
3. Stop APP with Control-C in its terminal.
4. Keep the take directory if you need its evidence. It is ignored by Git.
5. Close these terminals to discard the fixture environment and shell shortcuts.

## Troubleshooting while following the guide

| Symptom | Next action |
|---|---|
| `command not found: waypoint` | Use `uv run waypoint …`. The shortcuts already do this. |
| `Failed to spawn: WAYPOINT_…=…` | Assign shell variables separately, or put `VAR=value` **before** `uv run`. |
| `command not found: wp_lookup` or empty `$WP_BASE` | From the repo root, `source docs/manual-session.sh` in this terminal. |
| App connection refused | Check APP is still running and use the printed `$WP_BASE`, not a hard-coded port. |
| Address already in use | Stop only your previous fixture. For a new pass, stop all session servers and prepare a new take; do not kill an unrelated service. |
| Missing Chromium executable | Run `uv run playwright install chromium` in this project's environment. |
| Default lookup fails confidence | Use 1.2.0 for baseline tests, or measure 1.3.0 using the same database as its invocation. |
| A correct-looking test exits 1 or 3 | Read the exercise's expected status; refusal and escalation are intentional in negative tests. |
| `./demo take` finds zero items | Wait until RUN announces the intervention; check both terminals loaded the same take. |
| `./demo take` finds multiple items | List the queue, inspect each ID, and use the explicit CLI take/return/abort command for the intended item. |
| RUN still waits after your browser action | Return control in OPERATOR. Acting in the browser alone does not return the lease. |
| Returning control is refused | Check that it was taken, has not expired, and the ID belongs to the current intervention. |
| Cassette screen mismatch | Use the correct base fixture, bindings and unmodified saved recording. A mismatch is a deliberate stop; do not remove hash checks. |
| Draft/version already exists | Use the existing draft for review/replay, or select a new version/private root. Do not overwrite it. |
| Generated tests connect to 8080 | Set `WAYPOINT_BASE_URL="$WP_BASE"` on the pytest command. |
| Account ID differs from `SA-12345-01` | Existing fixture accounts affect numbering. Reset only the idle fictional fixture before that exercise. |
| An intent still blocks work | Inspect the application and intent; reconcile the actual effect. Deleting the database is not a reconciliation test. |
| Full checks discover files with ` 2` in their names | Review those extra workspace copies or use a clean checkout for submission checks. |
