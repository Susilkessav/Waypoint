# Evidence

Each `runs/showcase-*` directory is one complete run: `artifact.json` (the exact
artifact that ran), `meta.json`, `events.jsonl`, `result.json`, per-step screenshots and
sanitized accessibility snapshots, and - where a person took part -
`human/actions.jsonl` and a before/after `handoff1_diff.json`. Raw values never appear:
outputs are redacted on disk and returned in full only to the caller.

Replay showcases are produced by `scripts/showcase.py` from approved artifacts; in the
handoff runs, the operator's part is played by that script through the same state
store `waypoint intervene` uses.

| Run | Capability | Result | What it shows |
|---|---|---|---|
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
