# Waypoint — Design Report

> **Status: in progress.** Decisions are recorded here as they are made and validated.
> Each section is marked **DECIDED** (settled, rationale final), **PENDING** (choice made,
> not yet validated against running code), or **OPEN**. Nothing here is written as though
> it has been proven until it has. Target length for submission: 1–3 pages.

**Implementation checkpoint (A7):** A1–A7 are implemented: the fixture, browser surface,
perception/redaction, locator ladder, policy, secret broker, artifact approval, replay,
discovery/compiler, and live operator handoff with leases, write-ahead intents and a human
action log. One genuine Claude Haiku 4.5 discovery run is recorded in `evidence/`. The
automatic reconciliation probe (§5, "Not repeating work") is milestone B.

---

## 1. Architecture

**Status: DECIDED.**

Single logical system, four ports: `Surface`, `PolicyEngine`, `EvidenceSink`, `LeaseStore`.
Those boundaries — not the file layout — are what would survive being split into services.
Building the services now would be premature, and the brief says so.

The load-bearing idea is that **discovery and replay share one spine**: same surface adapter,
same policy engine, same recognizers, same evidence format. The only difference is who picks
the next action — the model, or the artifact. A bug in state recognition is therefore a bug
in both paths, and is fixed once.

**Process model.** Three processes, with one owner of the browser:

- `waypoint replay` / `discover` owns the browser for the life of the run and is the only
  process that ever calls `act()`.
- `waypoint intervene` (the CLI operator) only reads and writes SQLite: leases, interventions,
  intent records. It never touches the browser.
- The human drives the real headed window directly, so there is no remote-control channel
  to build — "take control" flips lease state, nothing more.

While paused, the replay process polls the lease row and does not exit. This was chosen over
a single-process design because the operator must be able to act while a run is blocked, and
over a daemon design because a daemon is the scaling infrastructure the brief tells us not to build.

**Perception mechanism — decided by a time-boxed spike.** The Chromium accessibility
tree via CDP `Accessibility.getFullAXTree`, bridged to Playwright by resolving
`backendDOMNodeId` and stamping a `data-wp-ref` attribute. Measured against the hostile
fixture: the tree must be fetched **per frame** (one call saw none of the eight `View`
links inside the frameset); every node maps to a frame path, nested iframe included;
stamping and clicking works inside child frames; a cross-frame snapshot takes ~10 ms and
stamping 85 nodes ~75 ms; row anchors and column headers come from the tree alone. Two
findings changed the design: Chromium exposed the header-less tab strip as data-table
`cell`s, so data-ness is decided by header cells rather than role; and a frame's `load`
event can resolve against the document being replaced, so the surface waits for request
quiescence and reports unfinished work explicitly. Application checkpoints and goal
postconditions remain A5 work. References expire at each observation, and browser errors
are redacted before return. The DOM-walk fallback was not needed — and the `Surface` port would have
hidden the choice either way, which is the argument for having it.

---

## 2. Artifact schema

**Status: DECIDED.**

The artifact is a contract, not a recording. Six decisions:

1. **No literals in steps.** Every value is `$inputs.x` or `$secrets.x`. Regulated data cannot
   leak into an artifact if the artifact structurally cannot hold it. Enforced beyond
   `value_ref` by a whole-document scan at emit time, because `url_template`, locator names
   and checkpoint predicates are equally capable of carrying a member ID.
2. **A checkpoint on every step, asserting identity.** Replay asserts state rather than
   assuming a click worked. A checkpoint for a member-scoped screen must assert *which*
   member — "correct screen, wrong member" is a failure mode, not an accepted state.
3. **`postconditions` distinct from step checkpoints.** A step checkpoint says the action
   landed; the postcondition says the capability achieved its goal. Conflating them yields a
   replay that executes every step and returns nothing useful.
4. **`outcomes` is first-class.** "No such member" is a declared, typed, terminal return value
   the caller switches on — not an exception. The brief's own glossary names conflating these
   as the most common mistake in this problem.
5. **Artifacts are self-contained.** Every signature the artifact references is inlined into it.
   An earlier draft compiled signatures into a shared mutable library, which meant editing that
   library could change how an already-approved v1.0.0 executed without changing its version.
   Reproducibility beats DRY: a contract that points at mutable external state is not a contract.
6. **Approval binds to content, per variant.** `approval_hash` covers the artifact *with a given
   tenant's overrides applied*; replay recomputes it and refuses to run as approved on a mismatch.
   Approving the base does not approve tenant B, because overrides change execution.

---

**Compilation.** A discovery transcript is evidence; the artifact is a contract, and a
reviewer should never need the transcript to approve it. The compiler has three declared
sources and infers nothing else: inputs from the launch bindings, outputs from the model's
`finish` call, goal success from `finish.success`. Every checkpoint the model nominates is
verified against the recorded screens - true after the action, false somewhere else, about
on-screen content - and a check that merely proves "a page loaded" is marked weak, which
blocks approval. Where the screen shows which record it is about, the compiler adds that
identity check even if the model forgot to. What fails compilation outright: an unfinished
run, a step without a unique verified locator, an output located by its own value, and any
sensitive-looking literal in the result.

**What the live model run changed.** The first genuine Haiku 4.5 run compiled to nothing.
It declared success on "a cell called `\tstatus`", which was on no screen, and pointed the
`account_status` output at a member-profile cell no stable locator could identify. Both were
found after the run, when the page was gone and nobody could fix them. So discovery now
verifies what it can while the page is still open: every expectation is checked against the
screen its action produced, a `finish` is put through the compiler's own checks, and what
fails goes back to the model - with `recheck`, a tool that touches nothing and only restates
an expectation, kept solely if it is true. That took the run from five unverifiable
checkpoints to none.

One class of error survives that, and it is the instructive one. The model's success
condition was "the status is active" - true for the member it had just read, false for a
dormant one. No amount of checking against *this* screen can catch it, because everything a
discovery run sees is one record. The compiler refuses it structurally instead: a checkpoint
may not assert the value of an output, since an output is by definition per-record. With
that, the discovered artifact returns `$4,281.19 / active` for one member and
`$912.04 / dormant` for another. What it honestly cannot do is declare business outcomes the
model never saw: it never searched for a member who does not exist, so `member_not_found` is
absent, and that input escalates rather than returning an outcome.

## 3. Determinism & error handling

**Status: DECIDED.**

Determinism comes from two mechanisms, not from avoiding timing.

**Locator resolution.** Each target carries an ordered ladder of candidates. The rules that
matter:

- A candidate enters the ladder only if it uniquely identified the recorded target *at record
  time*. With eight identically-named `View` links, tier 1 is therefore never in the ladder at
  all — ambiguity is resolved by compilation, not by escalation at runtime.
- Ambiguity is a verdict of the whole ladder, never a coin flip.
- **Positional candidates require an identity assertion.** An nth-child path is only usable as
  *position plus proof*. If a member has left the results table, their old row position now
  belongs to someone else, and a positional locator would open the wrong person's record. The
  assertion — "the row containing this member's ID" — is checked against whatever the position
  selected, and a failure means no match rather than a wrong match.
- Degradation is a signal, not a failure: resolving below the recorded tier is recorded, and the
  per-capability tier histogram across runs *is* the drift detector, with no drift infrastructure built.

**Error taxonomy.** Four statuses: `success`, `business_outcome` (a successful run returning a
named expected result, exit 0), `failure`, `escalated`. Global recognizers run before every step,
because a session can expire at any step and encoding that per-step is unmaintainable. Recovery
is declarative and bounded — `on → do → max` — so a reviewer reads the entire recovery behaviour
in six lines.

---

## 4. Heterogeneity & multi-tenant

**Status: DECIDED, validation PENDING (Milestone C is conditional).**

**Across surfaces.** Perception is the accessibility tree, chosen because it is the one
representation present on every surface that matters: Chromium exposes it, Windows exposes UIA,
macOS exposes AXAPI, Linux exposes AT-SPI. Roles and accessible names are the same nouns
everywhere. Locator bundles are surface-agnostic JSON, never Playwright selectors — the web
adapter compiles them one way and a desktop adapter would compile the same JSON another. What
differs between surfaces is the *bridge from a perceived node to an actionable handle*; on
desktop that bridge is simpler, because a UIA element is directly actionable.

**Across tenants.** Hundreds of institutions run the same vendor product, configured and branded
differently. The answer is a sparse per-tenant override patch over a shared base artifact —
including the route allowlist, so a tenant served from a different prefix carries that in its
override rather than forcing a permissive base. Tenants matching the base run the base
unchanged; tenants that have drifted carry a few lines, not a re-recording. Drift is detected
by the same tier histogram described in §3: a vendor upgrade that renames a control shows up as
loss of the top tier.

---

## 5. Escalation & handoff

**Status: DECIDED; implemented in A7, except reconciliation (B).**

**Who is in control.** A lease with a holder, an owner token and a monotonic generation counter.
The surface checks the caller's token and generation inside `act()` itself, so a stale
coroutine resuming after a handoff cannot act — a controller label alone would not prevent
that. While the operator holds control, the run holds no token at all.

**Handing over.** Never mid-action: the in-flight action completes or aborts, the session
quiesces, pre-handoff state is captured, and only then does the lease transfer. If an
*irreversible* action cannot be awaited, the run ends as `escalated / indeterminate` and a human
must reconcile. The system refuses to decide, on purpose.

**Coming back.** An ordered ladder with **no default branch**: terminal outcomes, then the
capability postcondition, then the current step's checkpoint, then explicitly declared **resume
points** — and otherwise escalate again. Resume points are a small, deliberate set whose
checkpoints are state-complete: correct member, expected field values, prerequisites satisfied.
An earlier draft resumed at the highest satisfied future checkpoint, which is unsafe: a generic
"form visible" checkpoint matches an *empty* form, so the engine would have skipped the steps
that fill it and confirmed a blank submission. A run allows two handoffs; an operator who
never takes control, or whose lease lapses, ends it escalated rather than resumed.

**Not repeating work.** Before executing or re-executing an irreversible step, reconciliation
asks a three-way question — completed, definitely not completed, or unknown. `Completed` requires
a confirmation bound to *this* member and *this* operation, so a stale confirmation page cannot
be adopted. **Absence of a confirmation is `Unknown`, never `NotCompleted`**, because a server
that committed and then lost the response looks identical to one that never received the request.
`Unknown` escalates. A write-ahead intent record, flushed before dispatch, is what makes this
hold across process death too. It is written in the surface's `before_dispatch` hook - after
policy and any approval, immediately before the click - so a refused action leaves no record
to reconcile. An action that was sent but did not complete cleanly is never retried, and any
intent left unresolved stops the next run of that same operation *before a browser starts*.
Resolving one is an attested act: `waypoint intervene reconcile <id> --outcome
completed|not-completed` records who decided and what they found. The automatic probe that
would answer the question without a human is B.

**What the human's actions produce.** A redacted log of control transfers, navigations, clicks,
field changes and submissions. Navigation-only logging was rejected because the target app's
controls are postback links that never change the URL — the exact case where it would silently
record nothing. The recording callbacks never call back into the browser driver: they queue,
and the run's own thread writes the log. The first version resolved frame paths inside the
callback and deadlocked the driver's event dispatch.

---

## 6. Safety

**Status: DECIDED.**

**Policy is a code path, not a prompt.** `check()` is wired into `Surface.act()` itself rather
than into its callers, so the model cannot route around it. Off-allowlist navigation is blocked
unconditionally.

The A4 implementation checks actual form actions (including submitter overrides), Enter
inside child frames, mutating GET routes, unknown controls and document redirects. Its
per-minute, per-run and repeated-action limits are deterministic. Attended approval is a
trusted callback; unattended calls return `approval_required`. State recognition and durable
escalation are not implemented by this callback. Browser tests verify blocked actions do
not reach their destinations, stale refs do not select another row, and screenshot masks
contain black pixels over classified data.

**Risk classification is effect-based.** Matching button names alone misses an Enter key that
submits a form, an unlabeled destructive control, and a GET that mutates — all three of which
exist in the target fixture deliberately. So classification also considers the resolved target
*route*, treats an empty accessible name as `unknown` rather than safe, and permits only safe
actions when the current state matches no known signature. At replay the artifact's declared risk
is a baseline and the runtime classifier still runs: **a declaration can never downgrade an
observed risk.** Irreversible or unknown, unattended, means escalate. A false positive costs a
human a ping; a false negative moves money. Legacy WebForms consoles POST for
everything - sign-on, search, switching a tab - so a blanket "POST is a mutation" rule makes
every read-only flow need a human. The answer is a short, reviewed list of read-only routes
per application, matched only on exact canonical paths, with control names still checked on
top; the residual risk is a mutating postback whose control carries no irreversible verb.

**Sensitive data.** Classification happens at *perception time*, before anything leaves the
surface — which is what makes redaction possible during the very first discovery run, when no
schema exists yet. Raw values live in a private in-memory snapshot that is never serialized;
sanitized observations go to the model, the logs and the evidence, while extraction reads the
raw layer and returns values only to the caller. The caller is entitled to the balance; the log
file isn't. Default classification is `internal`, and `internal` is redacted in prompts and on
disk — an earlier draft defaulted to `internal` but redacted only `pii`, which would have leaked
an unlabeled person's name. The default applies to *data* — field values and cells of
headered tables — not to UI chrome; applied to every label it would redact the very
"View" and "No records found" text that discovery navigates by. Extraction locators key on stable labels and relationships, never on
the value being extracted, which would neither generalize nor survive redaction.

**The honest limit.** A closed action set means page content cannot become a *novel* operation —
there is no free-form command string to inject into. That is a containment boundary, not an
injection-proof barrier. What actually bounds a hostile page is the allowlist, risk
classification with unattended approval, and a secret broker the model never sees through. The
residual risk — a page steering the agent toward an action that is already permitted — is
bounded, not eliminated, and is one reason irreversible steps require a human.

---

## 7. Cuts

**Status: DECIDED.**

| Cut | State | Why |
|---|---|---|
| Desktop surface | Port defined; stub raises `NotImplementedError` with per-method UIA/AXAPI mappings | The seam is the deliverable |
| Operator web console | CLI instead; same tables a web UI would use | The brief permits a mocked operator UI; the control-transfer model is what's graded |
| Assisted LLM fallback on replay failure | Designed, not built | Would be bounded to one step, policy-checked, recorded as a proposed patch requiring approval — never open-ended |
| Human log → auto artifact patch | Designed, not built | Recording the human is planned core scope; auto-compiling it into a patch is the extra |
| Confidence / success-rate gating | Not built | Plain draft→approved is what the catalog needs; statistical gating would be a third stretch goal |
| Tier-5 visual locators | Recorded as diagnostics, never resolved against | Present because desktop and canvas surfaces will need them |
| Process-death browser reattachment | Intent records written and enforced; the automatic probe is B; reconnection out of scope | The intent record forces reconciliation after a crash |
| Credential vault | Env vars behind a `SecretBroker` | A vault drops into the same interface |
| Queues, workers, multi-tenant plumbing | Not built | Explicitly not rewarded |

**Next milestone:** B adds a second capability with an irreversible step, the reconciliation
probe that reads the intent records, bounded recovery, the remaining chaos injections and
handoff hardening. After core delivery, assisted single-step fallback and
human-log-to-artifact patches could turn interventions into reviewed capability improvements.
