# Waypoint execution rules

The code cites these rules by stable ID (`R-LOC-5`, `R-REC-3`, …). Each rule states behaviour that
is implemented and tested; the design reasoning behind them is in [REPORT.md](REPORT.md).

| Family | Governs |
|---|---|
| [R-PROC](#r-proc--process-model-and-control-leases) | Which process owns the browser; control leases |
| [R-LOC](#r-loc--locators) | How a recorded target is found again |
| [R-RISK](#r-risk--risk-classification) | How an action's risk is decided |
| [R-SENS](#r-sens--sensitivity-and-redaction) | What may be seen, stored or returned |
| [R-REC](#r-rec--irreversible-actions-and-reconciliation) | Irreversible actions and "did it already happen?" |
| [R-RESUME](#r-resume--handoff-and-resume) | Handing a live session to a person and back |
| [R-OUT](#r-out--outcomes-and-recovery) | Result statuses and declared recovery |
| [R-PKG](#r-pkg--artifact-packaging-and-approval) | What an artifact contains and what approval means |
| [R-ASSIST](#r-assist--the-one-model-call-inside-replay) | The single bounded model call replay may make |

---

## R-PROC — Process model and control leases

| Process | Owns | Never does |
|---|---|---|
| `waypoint replay` / `waypoint discover` | The browser; the only caller of `act()` | Serve requests |
| `waypoint intervene` (operator) | Rows in SQLite: leases, interventions, intents | Touch the browser |
| `target_app` | The test fixture | Anything else |

- **R-PROC-1** — The run that launched the browser owns it for its whole life. No other process attaches.
- **R-PROC-2** — While waiting for a person, the run keeps the browser open and polls shared state. It
  does not exit.
- **R-PROC-3** — The person drives the real, headed window. Taking control changes lease state only;
  there is no remote-control channel.
- **R-PROC-4** — A lease has a holder (`AGENT`, `HUMAN`, `NONE`), an owner token and a **generation**
  that increases on every transfer. The surface checks the run's token at the top of every `act()`
  (`WebSurface.lease_guard`); a stale token raises `LeaseLost`. While a person holds control, the run
  holds no token at all.
- **R-PROC-5** — An expired lease ends the run as `escalated/lease_timeout`; an intervention nobody takes
  ends it as `escalated/intervention_timeout`. No silent resumption.

## R-LOC — Locators

Candidate tiers: 1 `role_name` · 2 `label` · 3 `anchored` (a relation to other text, e.g. "the View link in the row showing this member") · 4 `structural` · 5 `visual`. Tiers 4–5 are **positional**.

- **R-LOC-1** — A candidate enters a locator bundle only if, on the recorded screen, it matched **exactly
  one** element and that element was the target. Non-unique candidates are kept as diagnostics and never
  used to resolve.
- **R-LOC-2** — `Ambiguous` is the verdict of the whole ladder: a tier matching several elements is
  skipped, and if nothing lower resolves uniquely the step escalates.
- **R-LOC-3** — After any candidate matched more than one element, no positional candidate may resolve.
- **R-LOC-4** — Resolving below the recorded tier emits `locator_degradation` and continues. Per-step
  tier events distinguish actual drift from a flow that legitimately uses different tiers; the histogram
  summarizes overall usage.
- **R-LOC-5** — A positional candidate is usable only with an identity assertion (e.g. "same row as the
  cell showing `$inputs.member_id`") that passes on the element it selected. Position alone could select
  the wrong member after a re-sort.
- **R-LOC-6** — Extraction locators never key on the value they extract (see R-SENS-7).

## R-RISK — Risk classification

Classification is by effect, configured in [`waypoint/policy/policy.yaml`](waypoint/policy/policy.yaml), and runs inside `WebSurface.act()` so no caller can skip it.

- **R-RISK-1** — Navigation off the host/route allowlist is blocked unconditionally, including every
  redirect hop.
- **R-RISK-2** — Reading, waiting and asserting are `safe`.
- **R-RISK-3** — An action whose resolved request matches `mutating_routes` is `irreversible`, whatever
  the control is called. Reviewed `readonly_routes` exempt specific postbacks that change no record.
  Every mutating request needs its own authorization, consumed when sent.
- **R-RISK-4** — A click whose name contains an `irreversible_verbs` entry is `irreversible`.
- **R-RISK-5** — A click on a control with no accessible name is `unknown`. Enter inside a form inherits
  that form's classification.
- **R-RISK-6** — On a screen matching no known signature, only `safe` actions run.
- **R-RISK-7** — At replay, the artifact's declared risk is a baseline and the classifier still runs. If
  it computes a higher risk that needs approval, the run escalates `risk_exceeds_declared`: a stale
  `safe` label never executes a riskier page.

`irreversible` and `unknown` need approval. Unattended, that escalates to a person; attended, the operator is asked.

## R-SENS — Sensitivity and redaction

| Level | Model prompt | Evidence on disk | Returned to caller |
|---|---|---|---|
| `secret` | never | never | never |
| `pii` | redacted | redacted | full |
| `internal` | redacted | redacted | full |
| `public` | full | full | full |

- **R-SENS-1** — Every element is classified at perception time, before any snapshot reaches the model,
  evidence or a transcript.
- **R-SENS-2** — Classification order: declared label/role rules → input type → value, name and anchor
  patterns (money, SSN, dates, long digit runs, person names under a name column) → input bindings →
  default.
- **R-SENS-3** — Data (form values, cells of tables with headers) defaults to `internal`, which is
  redacted. UI chrome ("View", "Search") defaults to `public` unless a rule raises it.
- **R-SENS-4** — URLs are redacted in query values and identifier path segments.
- **R-SENS-5** — One redactor serves every sink: prompts, completions, events, transcripts, snapshots,
  screenshots, the human action log and artifacts.
- **R-SENS-6** — Raw values never leave the surface. `observe()` returns sanitized snapshots; `extract()`
  reads one raw value for the replay engine, which returns it to the caller and writes a redacted copy to
  evidence.
- **R-SENS-7** — An extraction locator keys on labels and relations ("the Savings row's Balance cell"),
  never on the recorded value or its redacted form. The compiler rejects one that does.
- **R-SENS-8** — Credentials come from the `SecretBroker` at type time and never enter a snapshot,
  transcript, prompt or artifact. A credential is released only to a field whose label the spec
  authorizes, and a failure to write one reports no value, not even in a chained exception.
- **R-SENS-9** — Before an artifact is written, every string in it is scanned; a sensitive-looking hit
  fails compilation.

## R-REC — Irreversible actions and reconciliation

- **R-REC-1** — Reconciliation answers `Completed` (adopt what the screen shows, do not repeat),
  `NotCompleted`, or `Unknown` (escalate). Never two-way.
- **R-REC-2** — `Completed` must be bound to *this* operation. A record qualifies only if it shows every
  input in `records.fields`, its creation time lies wholly after the attempt, and it is the only such
  record. Someone else's or an older record is never adopted.
- **R-REC-3** — Absence is not `NotCompleted`: a missing confirmation looks the same as a committed
  request whose response was lost. `NotCompleted` needs positive evidence; blank dates, unreadable
  values, several candidates or a time too close to call are `Unknown`.
- **R-REC-4** — Immediately before an irreversible action is dispatched, after policy and approval, an
  intent row is written and flushed (`dispatching` → `dispatched` → `observed`/`reconciled`). Its attempt
  time is immutable. An unresolved intent is scoped to the normalized application origin and bound to
  the reviewed contract hash. Another scope is never reconciled in this browser; legacy unscoped
  or changed-contract records require operator reconciliation. Compatible unresolved intents are
  reconciled before new work, and one is written before an irreversible step is handed to a person.
- **R-REC-5** — The `reconcile.probe` may only navigate and wait. It visits a read-only screen showing
  authoritative state; `completed_when` and `not_completed_when` are judged there, and neither holding is
  `Unknown`.

An irreversible step may not retry without a reconcile block, and an unattended artifact with an irreversible step and no compliant reconcile cannot be approved.

## R-RESUME — Handoff and resume

- **R-RESUME-1** — Never hand over mid-action: quiesce, capture the screen (`handoff<N>_before`), then
  release the lease.
- **R-RESUME-2** — If an action's completion cannot be determined, the run ends `escalated/indeterminate`
  for a person to reconcile.
- **R-RESUME-3** — When control returns, the run re-observes, writes a before/after diff, and takes the
  first match of: a declared outcome → the postcondition → the escalated step's checkpoint → a declared
  resume point → otherwise escalate `unrecognized_state_after_handoff`. There is no default branch.
- **R-RESUME-4** — Only steps marked `resume_point` may be resumed to, and their checkpoints must be
  state-complete (the right member, the fields already filled).
- **R-RESUME-5** — A checkpoint on a member-scoped screen asserts *which* member. The right screen for
  the wrong member is not an accepted state.
- **R-RESUME-6** — What the person does is recorded to `human/actions.jsonl` through the same redactor:
  control transfers, navigations, clicks (role, redacted name, frame), field changes (field and length,
  **never the characters**) and submits.

- **R-RESUME-7** — During discovery, a run that is going nowhere (an unchanged screen, two screens
  alternating, refused actions, the step limit, or the model giving up) hands the live browser to a
  person the same way. What they did is logged (R-RESUME-6) and recorded in the transcript as a gap: it
  compiles into a step that blocks approval until someone authors it, so a draft never silently skips
  work a person did.

## R-OUT — Outcomes and recovery

- **R-OUT-1** — Declared outcomes and global recognizers are checked before every step.
- **R-OUT-2** — Four statuses: `success` (exit 0), `business_outcome` (a successful run returning a
  named, expected answer, exit 0), `failure` (exit 1), `escalated` (exit 3). Recoveries on a success are
  a flakiness signal.
- **R-OUT-3** — Recovery is declarative and bounded: `on` a recognised state, `do` one of `dismiss`,
  `wait` or `reauth`, at most `max` times, `else` escalate or fail. No open-ended logic and no model.

## R-PKG — Artifact packaging and approval

- **R-PKG-1** — Artifacts are self-contained: every signature they use is inlined. Nothing at replay
  reads the shared signature library.
- **R-PKG-2** — Approval records a hash of the artifact's execution-relevant content. Replay recomputes
  it; any change makes the artifact unapproved.
- **R-PKG-3** — Approval gates are recomputed from content at approval and at every replay. Approval is
  blocked by a weak or unverified checkpoint, an unreviewed literal, a gap where a person acted during
  discovery (R-RESUME-7), a positional candidate without identity, an extraction
  keyed on its value, or an unattended irreversible step without a compliant reconcile.
- **R-PKG-4** — *(Design; not built.)* Approval would be per tenant variant, each with its own hash
  over the artifact with that variant's overrides applied. The schema reserves `overrides`; nothing
  applies it.
- **R-PKG-5** — A checkpoint the model nominates is accepted only if it holds after the action, asserts
  content (an element or text, not just a URL change), and — for steps that change the screen — is false
  on at least one other recorded screen. Otherwise it is marked `weak` or `unverified`, which blocks
  approval.
- **R-PKG-6** — Confidence is measured, not assumed. Every replay is recorded against the artifact's
  content hash; runs against a deliberately injected failure, refusals decided before the browser
  started, and runs where the application was unreachable, never count. The score is the lower bound of a
  95% Wilson interval on "kept its contract", so few runs cannot look like certainty, and a capability is
  `stable` only when every counted run also needed no fallback, recovery, handoff or assisted step. An
  artifact may declare `policy.min_confidence`; below it, the capability does not run unattended.
  Stability case verdicts and mismatch reasons are persisted; an unevaluated sweep row, a wrong declared
  result or inconsistent answers cannot earn confidence. Generated tests and sweeps disable the
  confidence prerequisite while retaining approval and policy checks.

## R-ASSIST — The one model call inside replay

Replay decides nothing with a model. One narrow exception exists, off unless the artifact declares `policy.assisted_fallback` **and** the caller asks for it.

- **R-ASSIST-1** — It may be used only when a target is not found or matches several elements, only on a
  `safe` step, only one attempted decision per run, and never after this run has sent an irreversible
  action. The attempt is consumed before the provider is called, including rejection, abstention and
  provider error.
- **R-ASSIST-2** — The model may only name an element on the current, sanitized screen. The action, any
  value and every other step come from the artifact.
- **R-ASSIST-3** — Its answer is checked deterministically before use: the element must exist, be
  enabled, be the kind of control the step recorded, and - where the recorded locator identified a record
  - sit with that record (R-LOC-5). The action then passes through the policy engine, and the step's
  checkpoint must hold afterwards. There is no second attempt.
- **R-ASSIST-4** — An assisted run is never silent: the exchange and every check go to evidence, the
  result is marked assisted, the run counts as degraded for confidence (R-PKG-6), and a repaired draft is
  written for review so the next approved version needs no model.

---

Assisted cassette playback requires the recorded normalized sanitized screen hash and exactly one matching target. Saved reasons are redacted. Attempt telemetry is separate from accepted relocation and repair-proposal telemetry.

## The test fixture

`target_app/` is a local Flask app standing in for a 2006-era core banking console. Nothing in `waypoint/` imports it.

**Hostile on purpose:**
- a frameset with a nested iframe;
- search results that return a whole branch, with eight identical **View** links;
- table layouts with `__doPostBack` tabs that never change the URL;
- control IDs that shift with row order, and no test IDs;
- labels only by table adjacency;
- a mutating GET behind "Mark for Review";
- an unnamed icon control;
- a sub-account flow whose Confirm is a mutating GET.

**Injections** (`?inject=` after sign-on, or `--inject` on replay):

| Trigger | Exercises |
|---|---|
| `member_id=00000` | business outcome: not found |
| `member_id=99999` | business outcome: not authorized |
| `ambiguous` | duplicate control → escalate (R-LOC-2) |
| `row_missing`, `reorder` | positional identity (R-LOC-5) |
| `wrong_member` | identity in checkpoints (R-RESUME-5) |
| `interstitial`, `slow`, `session` | declared recovery (R-OUT-3) |
| `500` | hard failure |
| `validation` | business outcome from a rejected deposit |
| `drift` | the sub-account submit control renamed → tier degradation (R-LOC-4) |
| `drift_search` | the search control renamed → the assisted fallback has something to relocate (R-ASSIST) |
| `commit_then_drop` | lost response after commit (R-REC-3) |
| `stale_confirmation` | someone else's receipt (R-REC-2) |
| `resubmit` | a repeated mutation under one approval (R-RISK-3) |
