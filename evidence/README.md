# Evidence

These saved runs demonstrate discovery, deterministic replay, error handling and
handoff against the local fixture. Start with `showcase-discovery-haiku`, then
`showcase-replay-success` and `showcase-handoff-ambiguous`.

## Files in each run

- **Discovery:** `transcript.json`, `cassette.json`, the compiled draft in
  `artifact.json`, metadata, events and the final screenshot and snapshot. These
  recordings were made with a live Claude model; a cassette reproduces its decisions
  without another model call. Discovery uses a transcript ending, not `result.json`.
- **Replay:** the exact approved `artifact.json`, `meta.json`, `events.jsonl`,
  `result.json`, per-step screenshots and sanitized snapshots. Failure runs include
  the stopped screen and diagnostic context.
- **Handoff and reconciliation:** human actions in `human/actions.jsonl`, a
  `handoff1_diff.json`, and reconciliation evidence where applicable. The showcase
  script plays the operator through the same lease and intervention store used by
  the CLI. The [manual demo](../README.md#demo-path) uses a person in the live
  browser.

Sensitive outputs are redacted in saved evidence; replay returns full outputs only
to the caller. The target app and credentials are fictional fixtures.

## Artifact provenance

The lookup discovery produced draft **1.1.0**. The lookup replay showcases use
reviewed **1.2.0**, built from the handwritten base with outcomes and recovery.
The [README demo](../README.md#demo-path) separately discovers, approves and replays
the same newly generated artifact for two members. The sub-account showcases use
**1.0.0**, derived from live discovery and hardened with a reconciliation probe by
[the review script](../scripts/review_capabilities.py).

## Run index

| Run | Capability | Result | What it shows |
|---|---|---|---|
| [`showcase-assisted-drift`](runs/showcase-assisted-drift) | lookup_member_balance 1.3.0 | `success`  | Saved genuine live-model assisted relocation with a draft proposal |
| [`showcase-discovery-haiku`](runs/showcase-discovery-haiku) | discovery | - | a live Claude Haiku 4.5 discovery run (transcript.json, cassette.json, artifact.json) |
| [`showcase-discovery-open-sub-account`](runs/showcase-discovery-open-sub-account) | discovery | - | a live Claude Haiku 4.5 discovery run (transcript.json, cassette.json, artifact.json) |
| [`showcase-handoff-ambiguous`](runs/showcase-handoff-ambiguous) | lookup_member_balance 1.2.0 | `success`  | escalation handed to a person, who opens the record; the run resumes - human/actions.jsonl and handoff1_diff.json |
| [`showcase-irreversible-lost-response`](runs/showcase-irreversible-lost-response) | open_sub_account 1.0.0 | `success`  | a person confirms, the response is lost; the engine reconciles from the accounts grid and adopts - one account |
| [`showcase-irreversible-stale-receipt`](runs/showcase-irreversible-stale-receipt) | open_sub_account 1.0.0 | `escalated` not_completed_after_handoff | a person confirms and gets someone else's receipt; reconciliation finds no such account and escalates |
| [`showcase-replay-another-member`](runs/showcase-replay-another-member) | lookup_member_balance 1.2.0 | `success`  | the same artifact, another member, another answer |
| [`showcase-replay-business-outcome`](runs/showcase-replay-business-outcome) | lookup_member_balance 1.2.0 | `business_outcome` member_not_found | no such member: a named outcome, exit 0 |
| [`showcase-replay-escalated-wrong-member`](runs/showcase-replay-escalated-wrong-member) | lookup_member_balance 1.2.0 | `escalated` checkpoint_not_met | the right screen for the wrong member: escalated |
| [`showcase-replay-hard-failure`](runs/showcase-replay-hard-failure) | lookup_member_balance 1.2.0 | `failure` hard_failure | a server error: failure, expected vs observed |
| [`showcase-replay-recovered-interstitial`](runs/showcase-replay-recovered-interstitial) | lookup_member_balance 1.2.0 | `success`  | a notice dismissed: success with recoveries |
| [`showcase-replay-success`](runs/showcase-replay-success) | lookup_member_balance 1.2.0 | `success`  | clean replay, no model: the balance and status |

## Beyond single runs

| Recording | Provenance | Reproduce |
|---|---|---|
| [Upstream agent](agent/lookup.json) | Saved live Claude tool-use exchange; fictional fixture inputs and caller outputs are intentionally visible here. | `uv run python scripts/agent_demo.py --cassette evidence/agent/lookup.json` |
| [Assist choice](agent/assist.json) | Saved live Claude element selection, bound to the sanitized observation hash. | `waypoint replay lookup_member_balance --version 1.3.0 --input member_id=12345 --inject drift_search --assist-cassette evidence/agent/assist.json` |
| [Stability reports](stability/) | Fixture sweeps of every declared case; injected cases are reported but never counted. | `waypoint stability lookup_member_balance --cases capabilities/lookup_member_balance/cases.yaml` |

Runtime SQLite files are not submitted. Run IDs and captured absolute paths describe the original execution; use this index to inspect the retained copies.

## Regenerate replay evidence

From the repository root:

```bash
uv run python scripts/showcase.py --list
uv run python scripts/showcase.py
```

The second command starts a private fixture, replaces the replay showcases and
rebuilds this index. Scenarios with unapproved artifacts are skipped. It preserves
the two original live discovery recordings and makes no model API calls.
