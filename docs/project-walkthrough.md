# Explaining Waypoint

Waypoint separates **learning a workflow** from **executing a reviewed workflow**. A model is
useful when discovering an unfamiliar UI. Repeating the learned workflow should have a typed
contract, bounded behavior and inspectable checks. That distinction is the project's central
design choice.

## The story in one minute

“An agent wants a member's savings balance. It should call `lookup_member_balance(member_id)`
and receive a balance, a known business answer, or a reason the work stopped. It should not
need to navigate nested frames or decide which of eight View links belongs to the member.

Waypoint learns that navigation during discovery, compiles it into an artifact, and requires
review before invocation. Replay follows the approved artifact without model calls. If the
screen changes or an action needs judgment, the engine stops. With handoff enabled, the same
browser remains open for a person, and the engine verifies the state when control returns.”

## Follow one request through the system

1. **Discovery receives a goal and input bindings.** The goal references `{{member_id}}`; the
   actual value becomes an input reference before model-visible text is stored or sent.
2. **The browser exposes a sanitized observation.** Per-frame accessibility data supplies roles,
   names, adjacent labels, table relationships and frame paths. Data classification happens here.
3. **The decider proposes one action and an expected result.** The surface checks route and risk
   policy before acting. Discovery observes again and records the transition.
4. **Compilation builds the capability.** Locators are synthesized while elements still exist.
   Inputs become references; outputs use label/row relationships; checkpoints are validated.
5. **Review approves a content hash.** Weak checks, missing demonstrated steps, unsafe extraction
   locators and unsupported irreversible actions block approval.
6. **Replay accepts typed arguments.** It establishes preconditions, executes reviewed steps,
   verifies checkpoints and returns outputs or a structured terminal result.

The artifact is the shared boundary between a caller, a reviewer and the executor. It makes the
workflow inspectable without requiring the caller to understand the application's UI.

## Features and the reasoning behind them

| Feature | How it works | Why this design |
|---|---|---|
| Model discovery | A bounded observe/decide/act loop with structured decisions and compile feedback. | Exploration benefits from model interpretation; execution benefits from a reviewed contract. |
| Offline cassettes | Recorded decisions must match normalized sanitized screen hashes. | A reviewer can reproduce behavior without credentials for a model service; stale recordings stop. |
| Typed artifacts | Versioned inputs, outputs, steps, checks, outcomes and recovery declarations. | Callers know what they can request and how to interpret results. |
| Content-bound approval | Recompute gates and hashes at approval and replay. | A stored “approved” flag must not survive a changed execution contract. |
| Locator bundles | Semantic names, labels and anchors precede constrained structural fallback. | A row position can change while the member's identity remains stable. |
| Identity checkpoints | Verify both page state and the intended member. | The right-looking page for the wrong person is still a failed check. |
| Business outcomes | Named answers such as not-found are successful terminal results. | A caller can branch on a business answer instead of parsing error text. |
| Bounded recovery | Recognized notices, delays and expired sessions have declared remedies and limits. | Known interruptions should be handled without unlimited retries or new model decisions. |
| Human handoff | Quiesce, release lease, retain browser, take/return control, verify state. | The person must receive the real session, and automation must not resume on stale assumptions. |
| Human demonstration | Supported discovery-time actions become locators and verified steps. | Help can make the learned workflow reusable; unrecordable actions remain visible approval gaps. |
| Reconciliation | Durable intents plus authoritative read-only UI probes. | A lost response can follow a successful commit; retrying blindly could duplicate it. |
| Tenant variants | Sparse target/checkpoint/route overrides with separate approval and scoring. | Institutions can share a flow while reviewing their actual differences. |
| Stability and confidence | Persist case verdicts, timing, drift, outcomes and consistency; score recent eligible runs. | One successful run is weak evidence of reliable reuse. |
| Assisted relocation | One attempted safe-step element selection, deterministic validation, draft repair. | A narrow relocation can help with drift without granting a model general replay authority. |
| Catalog | Approved capability schemas and invocation through the same engine. | An upstream agent selects business tools rather than inventing UI instructions. |
| Generated pytest | Cases run through the engine, with fixture injections and confidence disabled for measurement. | Tests retain the real policy and outcome semantics; they can establish reliability. |
| Progress and resume | Durable completed-step index, atomic source claim, linked successor, verified fresh-session entry. | A crash should be recoverable without two workers resuming the same source or repeating mutations. |
| Operator console | Loopback queue and actions backed by the same stores as the CLI, protected against cross-origin forms. | An easier interface should preserve the existing control model. |
| Secret providers | Environment or OS keychain lookup, then injection into an authorized field. | Storage choice should not alter where a credential may be released. |

## Explain the difficult boundaries with examples

**Ambiguity:** “View” appears repeatedly. The artifact identifies the link within the row whose
member ID matches the input. If that cannot identify one control, escalation is the correct
result. Position is not a sufficient substitute.

**Lost confirmation:** the fixture's Confirm action is a mutating GET. Waypoint classifies the
effect, writes an intent before dispatch or human handover, and checks authoritative account
records if the response is lost. Completed means adopt, not click again. NotCompleted requires
positive evidence; Unknown escalates. This is conditional reconciliation, not a universal
exactly-once promise.

**Tenant isolation:** intents belong to a normalized origin and variant, with a contract hash.
A pending riverbank operation cannot be settled using base's browser. Legacy records without
that identity require operator reconciliation; a changed artifact cannot silently supply a new
probe for an old contract.

**Confidence:** stable means at least five eligible runs, all contracts kept and no assistance
or recovery. The score is a conservative statistical lower bound, not the percentage of green
runs. A 0.5 bar is used in the fixture demonstration so a small sample can demonstrate gating;
it is not a recommended production threshold. Deliberate failure injections prove handling
and are excluded. Wrong declared outcomes or inconsistent outputs remain failures in both the
report and the persisted gate.

**Assistance:** the model can name a control on the current sanitized screen. It cannot change
the requested action or value, introduce a URL, or repair an irreversible step. An invalid
choice or provider error still spends the one-attempt budget. A saved assist recording must
match the screen and one unique control before the usual checks apply.

**Crash recovery:** the original process must actually be gone; an operator chooses to resume.
The system claims its progress row once and creates a linked successor before starting work.
A fresh browser may repeat only a fully safe prefix from verified entry conditions. It cannot
reconstruct a prefix containing a mutation. Unresolved intents are reconciled before new work.
If another crash happens immediately after the claim, the linked successor is the recovery ID.

## What the demonstrations prove

The [recording script](demo-script.md) follows discovery → artifact review → refusal → approval
→ replay for two members, then outcomes, recovery, failure, human handoff and reconciliation.
The newly generated artifact is used unchanged for the two-member reuse proof. Hardened
artifacts supply the separately authored exceptional behavior.

[Feature evidence](../evidence/features/README.md) additionally shows a renamed tenant control,
a protected console controlling a catalog-invoked session, a captured human step reused for a
different member, matching-screen assisted playback, and a real worker exit followed by success.
These are scripted fixture demonstrations, not fresh model calls or a deployment validation.
Two saved live discovery recordings and saved agent/assist exchanges retain actual model
provenance. [The evidence index](../evidence/README.md) keeps those categories separate.

## Limits to explain plainly

- Only the Chromium adapter executes. The desktop interface and mapping design are provided,
  but desktop lifecycle and capture integration are unfinished.
- The console is a local operator tool without user authentication; it does not remotely drive
  the replay browser. Catalog invocation is synchronous. A returned escalation describes a
  stopped run, not an open browser still waiting for a person.
- Confidence is scoped to content and variant, not every tenant deployment origin. Use a
  separate state database where deployment-specific measurement isolation is needed.
- Redaction uses bindings, structure, labels and patterns. Unlabelled sensitive content and
  hostile instructions in otherwise permitted UI text remain application-specific risks.
- The keychain adapter is tested with fake backends; actual OS provisioning is a local concern.
- Generated Playwright page objects are for reading/debugging and lack engine guardrails.
- There is no hosted service, automatic process-death detector, browser reattachment or general
  autonomous repair of an arbitrary workflow.

## A short code tour

Start with `waypoint/discovery/agent.py`, then `compiler/compile.py` and `artifact/schema.py`.
Follow execution in `replay/engine.py`; inspect `surface/web.py` for policy-enforced actions and
`session/handoff.py` for control transfer. The named rules in [RULES.md](../RULES.md) connect
implementation comments to the design contract. [PROJECT_STATUS.md](../PROJECT_STATUS.md)
provides the submission inventory; the old PLAN.md is historical planning context.
