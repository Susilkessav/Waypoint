# Waypoint — Design Report

Waypoint turns a goal into a reusable capability for a legacy application with no API.
A model discovers the flow; a compiler creates a typed artifact; deterministic replay
executes it; and a person can take over the same live session when automation cannot proceed.
The implementation uses a local banking fixture with framesets, postback tabs and ambiguous
controls. [Evidence](evidence/README.md) includes two live Claude discovery runs and nine replay
showcases. The [README](README.md) provides setup and an end-to-end demonstration.

## 1. Architecture

Discovery and replay share perception, policy, locators, state recognizers and evidence writing.
The model proposes actions during discovery; the artifact supplies them during replay. Both
use the browser adapter, so policy checks apply regardless of who selected an action.

A Python process owns each browser session. Separate operator CLI commands update SQLite
leases, interventions and operation intents; the browser process observes those changes and
pauses or resumes. This keeps control transfer real without requiring a daemon or distributed
queue. SQLite and environment-backed credentials keep the local demonstration reproducible.

Perception reads Chromium's accessibility tree separately for each frame. Semantic roles,
labels and table relationships work on the fixture's legacy markup. An internal reference maps
each observed node back to an actionable browser element. Playwright handles browser lifecycle
and actions; the fixture exposes no API that Waypoint uses to perform business operations.

## 2. Artifact schema

A capability declares its version, entry point, typed inputs and outputs, ordered actions,
locator bundles, checkpoints, preconditions, postconditions, outcomes and recovery rules.
Irreversible actions also require reconciliation instructions. Inputs and credentials are
referenced as `$inputs.x` and `$secrets.x`; public labels remain literal. Sensitive-looking
values and unreviewed literals block approval.

Compilation uses declared bindings, observed transitions and the model's nominated outputs
and success condition. It verifies nominations against recorded screens and synthesizes
locators while targets are still live. Discovery checks whether a proposed finish can compile
and returns correctable problems to the model before closing the session. Checkpoints include
member identity where available; extraction targets cannot depend on the value being read.

Artifacts inline their state signatures so library changes cannot silently alter an approved
flow. Approval binds to a content hash and is rechecked before replay. Editing execution
content requires another review. Automatic version allocation includes existing drafts, while
replay defaults to the highest approved release. The schema favors explicit, reviewable rules
over compact recordings that depend on hidden runtime knowledge.

## 3. Determinism & error handling

Replay makes no model calls. It resolves each target through a declared locator ladder,
executes the action, waits within a bound, and verifies the checkpoint. Candidates must uniquely
identify their target at recording time. Positional fallbacks need an identity proof; ambiguity
or a missing target causes escalation. Resolved tiers are logged for later drift analysis.

The result contract separates four states:

| Status | Meaning |
|---|---|
| `success` | Postconditions hold; declared outputs are returned. |
| `business_outcome` | A recognized result such as `member_not_found`; exit 0. |
| `failure` | The contract could not be met; includes step and diagnostic context. |
| `escalated` | Automation stopped because a safe next action could not be established. |

Declared recovery can dismiss a known notice, wait, or re-authenticate and replay a safe
prefix. Attempts are bounded. Re-authentication refuses after an irreversible action has been
sent, and uncertain completion is never retried to discover whether it succeeded. Replay
evidence includes the artifact, events, per-step screenshots and snapshots, plus expected and
observed states when a check fails.

## 4. Heterogeneity & multi-tenant

The `Surface` protocol separates observation, actions, target resolution and evidence capture
from the artifact's semantic representation. A desktop adapter would map roles and labels to
Windows UIA or macOS accessibility elements instead of browser nodes. `DesktopSurface`
documents those mappings but is a stub. Browser startup, human-action capture and some engine
helpers still use `WebSurface` directly; another adapter would also require that wiring to be
generalized. This is an extension design, not a claim of working desktop support.

Tenant reuse is designed as a base artifact plus sparse overrides for a vendor version or
institution, including route allowlists. Each effective variant would require its own approval.
Recorded locator tiers and checkpoint failures provide signals for detecting drift and deciding
when to review a variant. The schema stores overrides and variant approval metadata, but
override application is not built; non-base hashing raises rather than approving an unapplied
configuration.

## 5. Escalation & handoff

An escalation records the capability, step, reason and current evidence. With `--handoff`, the
browser remains open. The operator takes a lease, drives that same session and signals return.
The browser adapter checks an owner token and lease generation before acting, preventing stale
automation from continuing after control transfers. Human clicks, field changes and navigations
are recorded with sensitive values removed.

Handoff waits for in-flight work to settle. If an irreversible action's completion remains
uncertain, the run stops instead of transferring an unsafe session. On return, the engine checks
declared outcomes, postconditions, the stopped step's checkpoint and explicit resume points.
It resumes only where a recognized state justifies doing so.

Before dispatching an irreversible action, or handing it to a person, the engine persists an
intent with an immutable attempt time. A later run reconciles unresolved intents before new
work. A read-only probe returns Completed, NotCompleted or Unknown. Adoption requires a unique
record matching every operation input and a creation interval wholly after the attempt at the
displayed timestamp precision. An authoritative grid can establish NotCompleted; unreadable,
ambiguous or untrusted evidence yields Unknown and escalation. This avoids duplicating an
operation whose confirmation response was lost.

## 6. Safety

The browser adapter enforces allowed routes and action types. Risk classification considers
the resolved destination and form action, including mutating GETs and unlabeled controls.
Risky or unknown actions require approval or escalation. Document requests are checked through
redirects, and authorization is consumed on the first mutating request to prevent a redirect
from repeating it.

Perception classifies sensitive fields before rendering model-visible snapshots. Raw values
stay in memory; the secret broker injects credentials into verified fields. Goals are rendered
and scrubbed before entering prompts, transcripts, metadata or cassettes. Screenshots mask
sensitive regions, and evidence redacts outputs while the caller receives the full result.
Tests cover these sinks, approval enforcement, redirect handling and reconciliation ambiguity.

These controls limit actions and data exposure; they do not make the model immune to prompt
injection. A hostile screen may still influence an otherwise permitted action. Conservative
policy and human review remain necessary, and redaction depends on declared bindings and
perception's classification rules.

## 7. Cuts

Tenant override execution, the capability catalog and a working desktop adapter are deferred
to keep the core flow complete. The operator UI is a CLI, and credential storage uses environment
variables instead of a vault. Assisted model recovery and turning human actions into artifact
patches are also deferred; replay currently remains fully deterministic.

The system does not reattach to a browser after a process crash. A fresh run uses persisted
intents to reconcile prior work. Adoption is limited to a final operation, and indistinguishable
requests within the application's timestamp precision escalate instead of guessing.

Next, I would demonstrate one tenant variant with explicit overrides and approval, then expose
the verified capabilities through a small catalog. Broader infrastructure would follow a
demonstrated need, not precede the correctness of those paths.
