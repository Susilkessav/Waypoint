# Waypoint — Design Report

Waypoint lets a model explore a legacy back-office UI once, compiles what it did into a typed,
versioned capability, replays that capability with no model in the loop, and hands the live
session to a person when it meets a state it will not act on. Built and tested: discovery with
two genuine Claude Haiku 4.5 runs, compilation and approval, deterministic replay with recovery
and typed outcomes, live handoff, and three-way reconciliation of irreversible steps. Not built:
tenant variants and the capability catalog (§4, §7).

## 1. Architecture

Four ports — `Surface`, `PolicyEngine`, `EvidenceSink`, `LeaseStore` — are the boundaries that
would survive a split into services. Building those services now would be premature.

**One spine, two drivers.** Discovery and replay share the surface adapter, policy engine,
state recognizers and evidence format. Only the chooser of the next action differs: the model,
or the artifact. A recognition bug is a bug in both paths and is fixed once.

**Process model.** `waypoint replay` (or `discover`) owns the browser for the whole run and is
the only caller of `act()`. `waypoint intervene`, the operator CLI, touches only SQLite: leases,
interventions, intent records. A person drives the real headed window, so "take control" moves
a lease and nothing more. A paused run polls for the lease rather than exiting. That beats a
single process, because the operator must act while the run is blocked, and a daemon, which is
scaling infrastructure the brief rules out.

**Perception, decided by a spike.** The Chromium accessibility tree over CDP, fetched per
frame and bridged to Playwright by stamping a reference attribute on each node. Against the
hostile fixture, a single tree call saw none of the eight `View` links inside its frameset;
per-frame snapshots took about 10 ms and gave row anchors and column headers directly. The
`Surface` port would have hidden a DOM-walk fallback had one been needed.

## 2. Artifact schema

The artifact is a contract, not a recording:

- **No literals.** Values are `$inputs.x` or `$secrets.x`, and a whole-document scan rejects
  sensitive-looking strings anywhere, because locator names and URL templates can leak too.
- **A checkpoint on every step, asserting identity** — *which* member, not merely a member
  profile. Postconditions are separate from checkpoints; business outcomes such as "no such
  member" are declared, typed return values that exit 0.
- **Self-contained.** Every signature is inlined. A shared library could silently change how
  an approved version runs.
- **Approval binds to a content hash** recomputed at every replay. Its gates are recomputed
  too: weak or unverified checkpoints, unreviewed literals, positional locators without an
  identity proof, outputs located by their own value, and irreversible steps without a
  compliant reconcile block.

**Compilation** has three declared sources — launch bindings, the model's `finish` outputs and
its success condition — and verifies every nominated checkpoint: true after the action, false
elsewhere, about on-screen content. The live runs shaped it. The first Haiku run compiled to
nothing, having named elements that were on no screen; discovery now checks each expectation
against the screen its action produced, and puts `finish` through the compiler while the page
is still open, with a no-op `recheck` tool for corrections. The model's success condition "the
status is active" held for one member only, so the compiler refuses any checkpoint asserting an
output's own value. The second run exposed a locator bug: values in label/value rows — every
confirmation page — never had a unique locator.

## 3. Determinism & error handling

**Locators.** A candidate enters a target's ladder only if it uniquely identified the recorded
element at record time, so with eight identical `View` links the name-only tier never enters.
Ambiguity is a verdict of the whole ladder, never a coin flip. A positional candidate is usable
only with an identity proof — "the row containing this member's ID" — because a departed
member's row position now belongs to someone else. A renamed control is not guessed at; the run
escalates. Resolving below the recorded tier is logged, and the per-capability tier histogram is
the drift detector, with no drift infrastructure built.

**Four statuses.** `success`; `business_outcome`, a successful run returning a named expected
result; `failure`; `escalated`. Declared outcomes and global recognizers are checked before
every step, since a session can expire anywhere. Recovery is declarative and bounded —
`on → do → max` — with three verbs: dismiss a notice, wait, or re-authenticate and replay the
safe prefix. Re-authentication refuses once an irreversible action has been sent, because
replaying could repeat it.

**Evidence per run.** A screenshot and sanitized snapshot after every confirmed step, an event
log, the artifact copy, and on failure the expected signature beside the signatures actually
observed — enough to debug without a transcript.

## 4. Heterogeneity & multi-tenant

**Across surfaces.** The accessibility tree is the one representation every relevant surface
exposes — Chromium, UIA on Windows, AXAPI on macOS, AT-SPI on Linux — with the same nouns:
roles, accessible names, relations. Locator bundles are surface-agnostic JSON rather than
Playwright selectors; the web adapter compiles them one way and a desktop adapter would compile
the same JSON another. `DesktopSurface` is a stub that maps each port method to its UIA/AXAPI
equivalent. What differs is only the bridge from a perceived node to an actionable handle.

**Across tenants.** Hundreds of institutions run the same vendor product, configured and branded
differently. The design is a sparse override patch per tenant over one base artifact — including
the route allowlist, so a tenant on a different prefix does not force a permissive base — with
approval per variant, because overrides change execution. Tenants matching the base run it
unchanged; drifted tenants carry a few lines rather than a re-recording, and drift shows up in
the tier histogram.

**Built so far:** artifacts carry `overrides`, and approval status and hashes are keyed by
variant. Applying overrides, and a second-tenant fixture to prove it, are not built; hashing a
non-base variant raises rather than approving something that was never applied.

## 5. Escalation & handoff

**Who is in control.** A lease with a holder, owner token and monotonic generation, checked
inside `act()`. A stale grant cannot act after a handoff; while a person holds control, the run
holds no token at all.

**Handing over.** Never mid-action: the session quiesces first, and an irreversible action that
cannot be awaited ends the run as `escalated / indeterminate`. Before a person gets control at
an irreversible step, its intent is recorded, so a run that dies while they confirm still leaves
the next run something to check.

**Coming back.** A ladder with no default branch: a declared outcome, the postcondition, the
escalated step's checkpoint, then only explicitly declared resume points — otherwise escalate
again. Resume points must be state-complete, because a generic "form visible" check matches an
empty form and would skip the steps that fill it.

**Not repeating work.** An irreversible step is never repeated to learn whether it worked. An
intent is written immediately before dispatch, with an immutable attempt time, and an unresolved
intent makes the next run reconcile before it executes anything. A read-only probe then judges
each record: it counts as this operation only if it shows every operation input, was created
wholly after the attempt at the precision the application displays, and is the only one that
does. Anything less is Unknown, and Unknown escalates. A review found why records, not screens:
an earlier $100 account matched a failed $250 request on member, type and time, and was adopted.
An input the reconcile block never checks now blocks approval. In the demo, a person confirms
by hand during a handoff, and the engine proves it from the accounts grid instead of clicking
again.

**What the person does is logged**, redacted: clicks, field changes (length only), submits and
navigations. Navigation alone would record nothing on a console whose tabs are postbacks.

## 6. Safety

**Policy is a code path below every caller.** `check()` runs inside `Surface.act()`, so neither
the model nor the engine can route around it. Off-allowlist navigation is blocked outright.

**Risk is classified by effect, not by name.** The fixture contains a GET that mutates, an Enter
key that submits a form, and a destructive control with no accessible name. Classification
therefore uses the resolved route and form action, treats an empty name as `unknown`, and allows
only safe actions in an unrecognized state. Irreversible or unknown, unattended, escalates.
Declared risk is a reviewed claim: a page that computes higher escalates and cannot be approved
inline. Every document request is checked while the agent drives, redirect hops included: a
mutating request must be the one its action was approved for, and that authorization is spent
when the request is sent, so a redirect into a commit or a 307 that repeats one is refused.
WebForms consoles POST for everything, so a short reviewed list of read-only routes avoids
making every lookup need a human. A false positive costs a ping; a false negative moves money.

**Sensitive data is classified at perception time**, so redaction works in the very first
discovery run, before any schema exists. Raw values stay in a private in-memory snapshot that is
never serialized. The model, logs and evidence see sanitized values; outputs go only to the
caller; reconciliation reads each table cell through the same classification, and secrets never
leave. Unlabeled data defaults to `internal` and is redacted. A test matrix scans every sink —
prompts, events, transcripts, snapshots, the human action log, artifacts, state — and fails if a
sink was never written.

**The honest limit.** A closed action set is containment, not injection-proofing: a hostile page
can still steer the agent toward an action that is already permitted. That residual risk is one
reason irreversible steps need a human.

## 7. Cuts

| Cut | State | Why |
|---|---|---|
| Tenant variants, capability catalog | Schema fields exist; override application and catalog not built | The single-tenant path covers every core requirement |
| Desktop surface | Port defined; stub maps methods to UIA/AXAPI | The seam is the deliverable |
| Operator web console | CLI over the same tables a page would use | The control-transfer model is what matters |
| Assisted LLM fallback on replay failure | Designed, not built | Would be one bounded, policy-checked step, proposed as a patch |
| Human log → artifact patch | Log built; auto-patch not | Turning interventions into reviewed improvements is the next step |
| Browser reattachment after a crash | Not built | Intents and the probe let a fresh run find out what happened |
| Adoption mid-flow | An adopted operation must be the last step | Continuing past it needs state the probe does not read |
| Timestamp precision | With no operation reference to bind to, two identical requests in the same displayed moment are Unknown | Ambiguous evidence escalates |
| Credential vault | Environment variables behind `SecretBroker` | A vault drops into the same interface |

**Next:** tenant B with one override block and per-variant approval, then the catalog; after
that, assisted single-step fallback and human-log-to-artifact patches.
