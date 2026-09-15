# Recording Waypoint

Run `docs/present.sh` from the repository root **before recording**. It prepares a fresh take
and starts a private fixture. Start your recording when it says **Ready**. Press Enter to
advance; `q` stops the presenter and its fixture. No commands or intervention IDs need typing.

Keep the terminal and the browser visible. Review the generated artifact at scene 2 before
approving at scene 4. Handoff scenes require actual clicks in the browser; the presenter tells
you when to take and return control. Do not repeat Confirm. A lost response is deliberate.

## Opening, about 30 seconds

> “Waypoint lets an agent use a legacy application that has no business API. A model discovers
> the workflow, a person reviews the resulting capability, and ordinary calls replay reviewed
> steps without asking the model to decide each click. It also knows when to stop for a person.”

The optional [slides](demo-slides.html) introduce that sequence. Use arrow keys to advance.
Switch to the terminal before the first demonstration scene.

## Core recording, about five minutes

| Scene | What happens | Suggested narration |
|---|---|---|
| 1 · Discover | Saved Claude decisions drive a live fixture and compile a draft. | “This reproduces a genuine discovery run recorded earlier. It makes no new model calls. The member becomes an input.” |
| 2 · Review | Inputs, outputs, steps and the artifact path are shown. | “The result is inspectable: what it accepts, what it returns, what it clicks and how it checks progress.” |
| 3 · Draft refusal | Replay exits 1 with `artifact_not_approved`. | “A generated flow cannot be invoked until it passes review.” |
| 4 · Approve | Enter explicitly approves the reviewed copy. | “Approval is bound to these contents. Editing the execution contract invalidates it.” |
| 5 · Replay | Member 12345 returns $4,281.19 and active. | “The engine now follows the artifact and verifies each checkpoint. No model is involved.” |
| 6 · Another member | The same generated artifact returns $912.04 and dormant for 67890. | “This proves reuse: another input, the same workflow, a different correct answer.” |
| 7 · Missing member | Hardened 1.2.0 returns `member_not_found`. | “This release declares exceptional outcomes. A missing member is a business answer, not an automation error.” |
| 8 · Recovery | A maintenance notice is dismissed through a declared rule. | “Known interruptions have bounded recovery. The result records that help was needed.” |
| 9 · Server failure | An injected error exits 1 with diagnostic context. | “An unexpected server failure is reported with the stopped step and observed state.” |
| 10 · Live handoff | Ambiguous View links pause the live session. | “The engine cannot prove the target, so it releases control instead of guessing.” |
| 11 · Irreversible action | A human confirms; the response is lost; reconciliation adopts the account. | “A lost confirmation does not mean the operation failed. The engine checks authoritative records before deciding what happened.” |
| 12 · Evidence | Saved run directories and results are listed. | “The artifact, checks, human actions and stopped screens are retained with sensitive values redacted.” |

Scene 10: press Enter to take control, click **View** in member **12345**'s row, then press Enter
to return. Let the engine finish before advancing.

Scene 11: press Enter to take control, click **Confirm once**, then press Enter to return. The
fixture may show an error after committing. Expected result: success with `ADOPTED: true`.

The generated artifact in scenes 1–6 is an isolated 1.0.0. Scenes 7–10 use reviewed lookup 1.2.0;
scene 11 uses reviewed `open_sub_account` 1.0.0. Say this distinction explicitly if asked: the
exceptional rules were added during review, not inferred by the recorded lookup discovery.

## Optional extensions, after the core recording

Choose the features relevant to the discussion rather than fitting every extension into the
main video. Each command starts its own private fixture.

```bash
uv run python scripts/feature_demo.py
uv run python scripts/agent_demo.py --cassette evidence/agent/lookup.json
```

The feature demo prints a pass for each scenario and writes [retained evidence](../evidence/features/README.md):

- **Tenant reuse:** riverbank renames Search to Continue; an approved sparse override finds it.
- **Assisted relocation:** a saved live-model choice is accepted only on its matching screen,
  consumes one attempt, and writes a draft repair for review.
- **Catalog and console:** an agent-style invocation pauses; protected console routes transfer
  the same session; the run resumes after checking the screen.
- **Discovery demonstration:** a scripted decider requests help, a scripted operator demonstrates
  a click, and the resulting step replays for another member.
- **Crash recovery:** a worker actually exits after a durable checkpoint; a new worker reconstructs
  a safe prefix and finishes; the original run cannot be claimed twice.

These operators and the discovery decider are **scripted fixture actors**. The main recording's
handoff is performed by you. Neither path should be described as a new live model call.

The upstream agent demo first measures the current tool against declared cases in its own
fresh database. It then reproduces saved tool calls and repeats the recorded answer only when
the actual result matches. Suggested narration: “The caller sees a typed capability. It does
not need to understand frames, selectors or the UI's recovery rules.”

## Closing

> “The core is discovery, an inspectable approved artifact, deterministic replay and verified
> human handoff. The extensions add measured confidence, tenant reuse and bounded recovery
> from drift or process failure. Desktop execution and hosted multi-user infrastructure are
> deliberate cuts.”

## Rehearsal and troubleshooting

`uv run python scripts/present.py --rehearse` executes the automatic recording scenes headless.
Manual handoff and reconciliation are separately exercised by `scripts/showcase.py`; the
extension suite is `scripts/feature_demo.py`.

If preparation says another take is active, finish or abort its handoff and stop its APP terminal
before trying again. Existing takes are preserved under `.waypoint/recording/`; nothing clears
your normal state database. An unexpected exit stops the presenter so you can inspect the
reported evidence. Expected failures at scenes 3 and 9 are part of the demonstration.

For individual scenes, use `./demo prepare`, `./demo app`, and the short commands documented in
[the README](../README.md#demo-path). There is no environment activation or shell-variable setup.
