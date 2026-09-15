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

Separate operator commands and a local web console change SQLite leases, interventions and
intents. The browser owner polls those records. This provides actual control transfer with a
small, inspectable process model; no daemon or distributed queue is needed for this scope.

## 2. Artifact schema

A capability contains a semantic version, entry point, typed inputs/outputs, preconditions,
actions, locator candidates, checkpoints, postconditions, outcomes and bounded recovery rules.
Irreversible actions additionally declare reconciliation. Inputs and credentials use references
such as `$inputs.member_id` and `$secrets.meridian_password`; extraction locators describe a
label or relationship rather than the recorded balance.

The compiler checks nominated outputs and transitions against observations, synthesizes locators
while targets remain live, and marks weak checks or missing demonstrated actions as approval
blockers. State signatures are inlined so later library edits cannot silently alter a release.
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

Tenant variants apply sparse replacements to route allowlists, step targets and checkpoints,
then revalidate the effective capability. Lookup 1.4.0 demonstrates `riverbank`, where Search
is renamed Continue. Approval and confidence are per variant. Hashes cover the whole artifact,
so editing any override requires renewed approvals: conservative, but easy to audit.

Operation intents include normalized application origin, variant and the reviewed contract hash.
One tenant cannot reconcile another tenant's pending work. Legacy unscoped records and changed
contracts require explicit operator reconciliation. Deployment selection remains explicit;
confidence represents the configured variant, not every installation of a vendor's application.

## 5. Escalation & handoff

Replay and discovery use one control-transfer mechanism. Discovery can ask for help when stuck;
replay stops when it cannot establish a safe action. With handoff enabled, the owner quiesces,
captures evidence, releases its lease and keeps the browser alive. A person takes control,
operates that exact session, and returns it. Owner tokens and increasing lease generations
prevent stale automation from acting. Expiry and abort terminate conservatively.

On return, outcomes, postconditions, the stopped checkpoint and declared resume points determine
where execution may continue. Discovery captures supported human actions as reusable steps;
unsupported or credential actions leave visible gaps that block approval.

Before an irreversible dispatch or handover, an intent is durably written. A read-only probe
returns Completed, NotCompleted or Unknown. Adoption requires a unique authoritative record
matching all operation inputs and a creation interval after the attempt; ambiguous evidence
escalates. This handles a committed operation whose confirmation response was lost.

Crash recovery persists completed steps without raw inputs. Resumption atomically retires the
source and creates one linked successor. A fresh browser verifies state and may reconstruct an
entirely safe prefix. Already completed mutations are never reconstructed. The retained demo
terminates a worker, resumes successfully and refuses a second claim of its original ID.

## 6. Safety

The adapter enforces routes, redirects, action types and effect-based risk, including mutating
GETs. Each authorized mutation consumes its authorization. A page computing higher risk than
the reviewed label escalates. Perception classifies data before model exposure; a shared
redactor handles evidence and screenshots. Full outputs go only to the caller.

Credentials are injected into verified fields. Environment lookup is the default; an optional
keychain broker shares the same authorization and sanitized-error behavior. Missing entries
can fall back; locked/unavailable backends fail closed. The operator console validates local
hosts, same-origin requests and session CSRF tokens, with local redirects.

These controls bound authority and exposure. They do not make a model immune to prompt
injection, detect every possible sensitive value, authenticate local console users, or establish
production compliance. Tests exercise concrete failure and redaction boundaries on the fixture.

## 7. Cuts

Desktop execution, hosted multi-user infrastructure, automatic process-death detection and
browser reattachment are omitted. Reconciliation depends on authoritative UI evidence and
available timestamp precision; it is not a universal exactly-once guarantee. Adoption is limited
to a final operation. Tenant overrides do not redesign arbitrary workflows.

Generated pytest executes the real engine, including negative cases. Generated Playwright page
objects are explanatory code without equivalent guardrails. OS keychain behavior is covered
with fake providers; real backend provisioning remains environment-specific. Live-model
recordings are preserved, while reproducible demonstrations use cassettes or clearly labelled
scripted actors. Further work should validate additional real application variants before adding
infrastructure or broader autonomous repair.
