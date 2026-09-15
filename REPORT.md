# Waypoint — Design Report

Waypoint learns a legacy UI workflow once, packages it for review, and replays it with typed
inputs. The submitted implementation targets Chromium and a deliberately hostile local banking
fixture. [README.md](README.md) provides the reproducible demonstration;
[RULES.md](RULES.md) defines the detailed execution rules.

## 1. Architecture

One Python process owns each Playwright browser. Discovery and replay share perception,
locators, policy, signatures and evidence writing. During discovery, a model proposes actions;
during normal replay, a reviewed artifact supplies them. Every action passes through the same
browser adapter, so changing the caller does not remove policy checks.

Perception reads each frame's Chromium accessibility tree, classifies sensitive content, and
exposes sanitized roles, labels and relationships. Internal references connect observations to
browser elements. The fixture deliberately contains nested frames, duplicate links, changing
row IDs and postbacks; business operations are performed through its UI.

Separate operator commands change SQLite leases, interventions and intents. The browser owner
polls those records. This provides actual control transfer with a
small, inspectable process model; no daemon or distributed queue is needed for this scope.

## 2. Artifact schema

A capability contains a semantic version, entry point, typed inputs/outputs, preconditions,
actions, locator candidates, checkpoints, postconditions, outcomes and bounded recovery rules.
Irreversible actions additionally declare reconciliation. Inputs and credentials use references
such as `$inputs.member_id` and `$secrets.meridian_password`; extraction locators describe a
label or relationship rather than the recorded balance.

The compiler checks nominated outputs and transitions against observations, synthesizes locators
while targets remain live, and marks weak checks, unreviewed literals and work a person did by hand
as approval blockers. State signatures are inlined so later library edits cannot silently alter a release.
Approval records a content hash and is revalidated at execution. Version allocation includes
drafts; ordinary replay selects the highest approved release. Explicit structure makes review
and change detection more reliable than an opaque recording.

## 3. Determinism & error handling

Default replay makes no model calls. It resolves recorded targets, dispatches actions, waits
within a bound and verifies checkpoints. Candidates must uniquely identify the recorded target;
positional fallback requires identity proof and cannot override unresolved semantic ambiguity.
Member-scoped checks verify the member, not merely the page layout.

Results are `success`, `business_outcome`, `failure`, or `escalated`; the first two exit 0.
Recovery can dismiss a recognized notice, wait or reauthenticate. Reauthentication only repeats
a safe prefix before irreversible work. Logs, screenshots and snapshots retain the stopped step
and expected versus observed state.

Opt-in assisted relocation is restricted to one attempted safe-step target selection per run.
The model chooses only an element; role, identity, policy and the original checkpoint still
apply. Rejection or provider failure consumes the budget. Cassettes require the same normalized
screen hash and a unique target. Accepted relocation produces an unapproved repair proposal.

Stability sweeps persist case verdicts alongside run telemetry. Wrong outcomes and inconsistent
answers cannot earn confidence. Injected failures are reported but excluded from scoring.
Confidence uses the lower bound of a 95% Wilson interval, with at least five observations;
“stable” additionally requires no fallback, recovery, handoff or assistance. Measurements disable
the gate they are establishing; normal unattended calls enforce the artifact's declared bar.

## 4. Heterogeneity & multi-tenant

The `Surface` protocol separates observation and actions from semantic artifacts. A desktop
implementation would map those concepts to Windows UIA or macOS accessibility. The supplied
stub describes that mapping; browser lifecycle and human-capture wiring would also need
adaptation. Desktop execution is not implemented.

Multi-tenant reuse is designed, not built. Many institutions run the same vendor product, so
one artifact would carry sparse per-tenant overrides - a renamed control's target, a different
checkpoint, a tenant's route allowlist - rather than being re-recorded. Each variant would be
reviewed and approved separately over the merged content, and measured separately, because a
change that is safe for one tenant says nothing about another. The schema reserves `overrides`
for this. Drift per tenant shows up as locator tier degradation and falling confidence in the
run ledger, which is already recorded per artifact.

Operation intents record the normalized application origin and the reviewed contract hash, so
an operation attempted against one instance is never reconciled in another's browser.

## 5. Escalation & handoff

Replay and discovery use one control-transfer mechanism. Discovery can ask for help when stuck;
replay stops when it cannot establish a safe action. With handoff enabled, the owner quiesces,
captures evidence, releases its lease and keeps the browser alive. A person takes control,
operates that exact session, and returns it. Owner tokens and increasing lease generations
prevent stale automation from acting. Expiry and abort terminate conservatively.

On return, outcomes, postconditions, the stopped checkpoint and declared resume points determine
where execution may continue. During discovery the model continues from the screen the person left; what they did is
logged and compiled into a visible gap that blocks approval until someone authors the step.

Before an irreversible dispatch or handover, an intent is durably written. A read-only probe
returns Completed, NotCompleted or Unknown. Adoption requires a unique authoritative record
matching all operation inputs and a creation interval after the attempt; ambiguous evidence
escalates. This handles a committed operation whose confirmation response was lost.

## 6. Safety

The adapter enforces routes, redirects, action types and effect-based risk, including mutating
GETs. Each authorized mutation consumes its authorization. A page computing higher risk than
the reviewed label escalates. Perception classifies data before model exposure; a shared
redactor handles evidence and screenshots. Full outputs go only to the caller.

Credentials come from the environment through a broker that releases them only to a field whose
label the spec authorizes, and never echoes a value in an error.

These controls bound authority and exposure. They do not make a model immune to prompt
injection, detect every possible sensitive value, or establish production compliance. Tests exercise concrete failure and redaction boundaries on the fixture.

## 7. Cuts

Deliberately left out: desktop execution (the port is defined, the adapter is a stub), tenant
variants (designed in §4), a graphical operator console (the CLI moves the same records), a
credential vault (the environment broker is the seam), and resuming a run after its process
dies (a fresh run reconciles any operation the dead one left open). Reconciliation depends on
authoritative UI evidence and timestamp precision; it is not a universal exactly-once guarantee.

Discovery escalation logs what a person does but does not turn it into replayable steps; a
reviewer authors those. Generated Playwright page objects are for reading, without the engine's
guardrails; generated pytest runs the real engine. Live-model recordings are preserved, and
reproducible demonstrations use cassettes or clearly labelled scripted operators.

Next: validate one tenant variant against a second fixture before building override application,
then the operator console, driven by what reviewers of real handoffs actually need.
